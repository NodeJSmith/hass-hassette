"""Poll a hassette server for its health and apps."""

import logging
from dataclasses import dataclass

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
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    ISSUE_UNSUPPORTED_VERSION,
    MANUFACTURER,
    SCAN_INTERVAL,
    SERVER_DEVICE_ID,
    SERVER_DEVICE_NAME,
    UPGRADE_DOCS_URL,
)
from .errors import TRANSIENT_POLL_ERRORS, poll_error, version_placeholders

_LOGGER = logging.getLogger(__name__)

type HassetteConfigEntry = ConfigEntry[HassetteCoordinator]


@dataclass(frozen=True, slots=True)
class HassetteData:
    """One successful poll: the server's health and its apps keyed by app_key."""

    health: SystemStatusResponse
    apps: dict[str, AppSummary]


class HassetteCoordinator(DataUpdateCoordinator[HassetteData]):
    """Read ``/api/health`` then ``/api/apps`` every 30 seconds.

    Health is in-memory on the server and says whether apps can run yet (``bootstrap_released``) and
    which API schema the server speaks, so it is read first on every poll. A single transient failure
    after a good poll keeps the previous data, so one stalled request doesn't flip every entity.
    """

    config_entry: HassetteConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: HassetteConfigEntry,
        client: HassetteClient,
        action_client: HassetteClient,
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=config_entry, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self.client = client
        self.action_client = action_client
        self.url: str = config_entry.data[CONF_URL]
        # True while the latest poll failed transiently and returned the previous data in its place.
        self.tolerated_failure = False
        # The last server version and bootstrap state seen, to act only when they change.
        self.server_version: str | None = None
        self.bootstrap_released: bool | None = None
        # Log each newer server version and each unreadable-response message once, not every 30 s.
        self.warned_newer_versions: set[str] = set()
        self.logged_validation_messages: set[str] = set()

    async def _async_update_data(self) -> HassetteData:
        try:
            health = await self.client.get_health()
            check_server_version(health)
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
            if self.data is not None and self.last_update_success and not self.tolerated_failure:
                self.tolerated_failure = True
                _LOGGER.debug("Keeping the previous hassette data after one failed poll: %s", err)
                return self.data
            raise poll_error(err) from err
        except HassetteClientError as err:
            if isinstance(err, ResponseValidationError) and str(err) not in self.logged_validation_messages:
                self.logged_validation_messages.add(str(err))
                _LOGGER.warning("hassette sent a response this integration can't read: %s", err)
            raise poll_error(err) from err

        self.tolerated_failure = False
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_UNSUPPORTED_VERSION)
        self.track_version(health)
        self.note_bootstrap(health.bootstrap_released)
        return HassetteData(health=health, apps={app.app_key: app for app in apps.apps})

    def track_version(self, health: SystemStatusResponse) -> None:
        """On the first poll and each version change, register the hub device and warn about a newer schema."""
        if health.version == self.server_version:
            return
        self.register_server_device(health.version)
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

    def register_server_device(self, version: str) -> None:
        """Create or update the hub device every app device is linked under."""
        dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            identifiers={(DOMAIN, SERVER_DEVICE_ID)},
            name=SERVER_DEVICE_NAME,
            manufacturer=MANUFACTURER,
            sw_version=version or None,
            configuration_url=self.url,
            entry_type=DeviceEntryType.SERVICE,
        )
