"""Poll a hassette server for its health and apps."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime

from hassette_client import (
    HassetteClient,
    HassetteClientError,
    ResponseValidationError,
    UnsupportedServerVersionError,
    check_server_version,
)
from hassette_wire import API_SCHEMA_VERSION, AppSummary, SystemStatusResponse
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    ACTION_REFRESH_COOLDOWN,
    DOMAIN,
    ISSUE_UNSUPPORTED_VERSION,
    MANUFACTURER,
    SCAN_INTERVAL,
    SERVER_DEVICE_ID,
    SERVER_DEVICE_NAME,
    TOLERANCE_WINDOW,
    UPGRADE_DOCS_URL,
)
from .errors import TRANSIENT_POLL_ERRORS, poll_error, version_placeholders

_LOGGER = logging.getLogger(__name__)

# hassette's app_key alphabet, as hassette's web API validates it. The "{app_key}-{key}" unique_ids and
# the "-server" hub identifier rely on "-" being outside it, so an app whose key isn't in it gets no entities.
APP_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")

type HassetteConfigEntry = ConfigEntry[HassetteCoordinator]


@dataclass(frozen=True, slots=True)
class HassetteData:
    """One successful poll: the server's health and its apps keyed by app_key."""

    health: SystemStatusResponse
    apps: dict[str, AppSummary]


class HassetteCoordinator(DataUpdateCoordinator[HassetteData]):
    """Read ``/api/health`` then ``/api/apps`` every 30 seconds.

    Health is in-memory on the server and says whether apps can run yet (``bootstrap_released``) and
    which API schema the server speaks, so it is read first on every poll. A transient failure within
    ``TOLERANCE_WINDOW`` of a good poll keeps the previous data, so one stalled poll doesn't flip
    every entity. Refreshes requested after actions share the poll's tolerance without using it up.
    """

    config_entry: HassetteConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HassetteConfigEntry,
        client: HassetteClient,
        action_client: HassetteClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
            request_refresh_debouncer=Debouncer(hass, _LOGGER, cooldown=ACTION_REFRESH_COOLDOWN, immediate=True),
        )
        self.client = client
        self.action_client = action_client
        self.url: str = config_entry.data[CONF_URL]
        # When the last poll succeeded, which transient failures are tolerated against.
        self.last_success: datetime | None = None
        # The last server version and bootstrap state seen, to act only when they change.
        self.server_version: str | None = None
        self.bootstrap_released: bool | None = None
        # Log each newer server version and each unusable app_key once, not every 30 s.
        self.warned_newer_versions: set[str] = set()
        self.skipped_app_keys: set[str] = set()
        # Unreadable responses logged since the last good poll, by (endpoint, error type): not by message,
        # which can embed the body size and so change on every poll.
        self.logged_validation_errors: set[tuple[str | None, str]] = set()

    async def _async_update_data(self) -> HassetteData:
        try:
            health = await self.client.get_health()
            check_server_version(health)
            # Withdrawn as soon as the version passes, even if reading the apps then fails.
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_UNSUPPORTED_VERSION)
            apps = await self.client.get_apps()
        except UnsupportedServerVersionError as err:
            self.create_version_issue(err)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unsupported_version",
                translation_placeholders=version_placeholders(err),
            ) from err
        except TRANSIENT_POLL_ERRORS as err:
            # Returning the previous data counts as a successful poll to HA, so entities stay available.
            if self.within_tolerance():
                _LOGGER.debug("Keeping the previous hassette data after one failed poll: %s", err)
                return self.data
            raise poll_error(err) from err
        except HassetteClientError as err:
            if isinstance(err, ResponseValidationError):
                key = (err.endpoint, type(err).__name__)
                if key not in self.logged_validation_errors:
                    self.logged_validation_errors.add(key)
                    _LOGGER.warning("hassette sent a response this integration can't read: %s", err)
            raise poll_error(err) from err

        self.last_success = dt_util.utcnow()
        self.logged_validation_errors.clear()
        # Every poll, so app entities always find the hub to link under; unchanged fields write nothing.
        hub_id = self.register_server_device(health.version)
        self.track_version(health)
        self.note_bootstrap(health.bootstrap_released)
        usable = [app for app in apps.apps if self.usable_app_key(app.app_key)]
        self.update_app_devices(usable, hub_id)
        return HassetteData(health=health, apps={app.app_key: app for app in usable})

    def usable_app_key(self, app_key: str) -> bool:
        """Whether ``app_key`` is in hassette's alphabet; logs each one that isn't, once."""
        if APP_KEY_PATTERN.fullmatch(app_key):
            return True
        if app_key not in self.skipped_app_keys:
            self.skipped_app_keys.add(app_key)
            _LOGGER.warning("Ignoring hassette app %r: its key is outside the app_key alphabet", app_key)
        return False

    def update_app_devices(self, apps: list[AppSummary], hub_id: str) -> None:
        """Keep each existing app device's name, model and hub link in step with hassette and the hub device.

        Devices are created by the platforms with their first entity; a user's own rename is kept,
        since it is stored separately as ``name_by_user``. The hub link is restored after the hub
        device was deleted and registered again.
        """
        devices = dr.async_get(self.hass)
        for app in apps:
            device = devices.async_get_device_by_identifier((DOMAIN, app.app_key), self.config_entry.entry_id)
            # The same fields HassetteAppEntity's DeviceInfo creates the device with.
            wanted = (app.display_name, app.class_name, hub_id)
            if device is not None and (device.name, device.model, device.via_device_id) != wanted:
                devices.async_update_device(
                    device.id, name=app.display_name, model=app.class_name, via_device_id=hub_id
                )

    def within_tolerance(self) -> bool:
        """Whether a transient failure may keep the previous data: entities are up and the last good poll is recent."""
        return (
            self.data is not None
            and self.last_update_success
            and self.last_success is not None
            and dt_util.utcnow() - self.last_success < TOLERANCE_WINDOW
        )

    def track_version(self, health: SystemStatusResponse) -> None:
        """On the first poll and each version change, warn if the server serves a newer API schema."""
        if health.version == self.server_version:
            return
        self.server_version = health.version
        if health.api_schema_version > API_SCHEMA_VERSION and health.version not in self.warned_newer_versions:
            self.warned_newer_versions.add(health.version)
            _LOGGER.warning(
                "hassette %s serves API schema %d, newer than this integration's %d; "
                "it keeps working, but update the integration to use newer features",
                health.version,
                health.api_schema_version,
                API_SCHEMA_VERSION,
            )

    def create_version_issue(self, err: UnsupportedServerVersionError) -> None:
        """Tell the user in Repairs that hassette needs an upgrade."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_UNSUPPORTED_VERSION,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_UNSUPPORTED_VERSION,
            translation_placeholders=version_placeholders(err),
            learn_more_url=UPGRADE_DOCS_URL,
        )

    def note_bootstrap(self, released: bool) -> None:
        """Log once each time hassette's app bootstrap is held or released."""
        if released == self.bootstrap_released:
            return
        if not released:
            _LOGGER.info("hassette hasn't released app startup yet; its apps are unavailable until it does")
        elif self.bootstrap_released is not None:
            _LOGGER.info("hassette released app startup; its apps are available again")
        self.bootstrap_released = released

    def register_server_device(self, version: str) -> str:
        """Create or update the hub device every app device is linked under, and return its registry id."""
        return (
            dr.async_get(self.hass)
            .async_get_or_create(
                config_entry_id=self.config_entry.entry_id,
                identifiers={(DOMAIN, SERVER_DEVICE_ID)},
                name=SERVER_DEVICE_NAME,
                manufacturer=MANUFACTURER,
                sw_version=version or None,
                configuration_url=self.url,
                entry_type=DeviceEntryType.SERVICE,
            )
            .id
        )
