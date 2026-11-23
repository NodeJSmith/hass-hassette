"""Poll a hassette server for its health and apps."""

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import TypedDict

from hassette_client import (
    HassetteClient,
    HassetteClientError,
    ResponseValidationError,
    UnsupportedServerVersionError,
    check_server_version,
)
from hassette_wire import (
    API_SCHEMA_VERSION,
    AppInstance,
    AppListResponse,
    AppSummary,
    DeclaredEntity,
    EntityStateChangedData,
    ResourceStatus,
    SystemStatusResponse,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .addon import async_read_announced_connection
from .const import (
    ACTION_REFRESH_COOLDOWN,
    CONF_ADDON_SLUG,
    DOMAIN,
    ISSUE_UNSUPPORTED_VERSION,
    MANUFACTURER,
    SCAN_INTERVAL,
    SERVER_DEVICE_ID,
    SERVER_DEVICE_NAME,
    TOLERANCE_WINDOW,
    UPGRADE_DOCS_URL,
)
from .errors import (
    ADDON_FOLLOW_ERRORS,
    TRANSIENT_POLL_ERRORS,
    is_mapped_poll_error,
    poll_error,
    version_placeholders,
)

_LOGGER = logging.getLogger(__name__)

# hassette's app_key alphabet, as hassette's web API validates it. The "{app_key}-{key}" unique_ids and
# the "-server" hub identifier rely on "-" being outside it, so an app whose key isn't in it gets no entities.
APP_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")

# The platforms an app instance can declare entities on. A declaration on any other platform is skipped.
DECLARED_PLATFORMS = frozenset({Platform.BUTTON, Platform.SENSOR, Platform.SWITCH})

type HassetteConfigEntry = ConfigEntry[HassetteCoordinator]


@dataclass(frozen=True, slots=True)
class HassetteData:
    """The server's health and its apps keyed by app_key, from the latest good poll and the pushes applied since.

    ``entity_marks`` orders declared entity values: the newest ``entity_revision`` applied to each instance's
    entities, keyed by ``instance_identifier``, whether it came from a listing or a push. Revisions compare only
    within ``boot_id``, the hassette boot the listing came from.
    """

    health: SystemStatusResponse
    apps: dict[str, AppSummary]
    boot_id: str
    entity_marks: dict[str, int]


class PushOutcome(Enum):
    """What applying a pushed entity value did: applied, or the reason it was skipped."""

    APPLIED = auto()
    OTHER_BOOT = auto()
    STALE = auto()
    UNKNOWN_TARGET = auto()


# Why a push wasn't applied, as the debug log says it.
PUSH_SKIP_REASONS: dict[PushOutcome, str] = {
    PushOutcome.OTHER_BOOT: "it isn't from the held listing's hassette boot",
    PushOutcome.STALE: "its revision isn't newer than the one held",
    PushOutcome.UNKNOWN_TARGET: "the held listing lacks its app, instance or entity",
}


class AppDeviceFields(TypedDict):
    """The app device fields hassette owns, keyed by their ``DeviceInfo`` and ``async_update_device`` names."""

    name: str
    model: str
    model_id: str | None


class AppDeviceSpec(AppDeviceFields):
    """Every field of an app device, keyed by the names ``DeviceInfo`` and ``async_get_or_create`` share."""

    identifiers: set[tuple[str, str]]
    manufacturer: str
    via_device_id: str
    configuration_url: str
    entry_type: DeviceEntryType


class HassetteCoordinator(DataUpdateCoordinator[HassetteData]):
    """Read ``/api/health`` then ``/api/apps`` every ``SCAN_INTERVAL``.

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
        # Each refresh numbers its fetch when it starts, and a fetch whose listing was applied records its number.
        # The /ws stream compares them to tell that a fetch it asked for landed: a tolerated failure keeps the old
        # data, a poll already in flight may have read its listing before the stream subscribed, and two good
        # polls can share a timestamp.
        self.fetches_started = 0
        self.last_applied_fetch = 0
        # The last server version and bootstrap state seen, to act only when they change.
        self.server_version: str | None = None
        self.bootstrap_released: bool | None = None
        # Log each newer server version and each unusable app_key once, not on every poll.
        self.warned_newer_versions: set[str] = set()
        self.skipped_app_keys: set[str] = set()
        # Unreadable responses and unexpected errors logged since the last good poll, by (endpoint, error
        # type): not by message, which can embed the body size and so change on every poll.
        self.logged_poll_errors: set[tuple[str | None, str]] = set()

    async def _async_update_data(self) -> HassetteData:
        self.fetches_started += 1
        fetch = self.fetches_started
        try:
            health = await self.client.get_health()
            check_server_version(health)
            # Withdrawn as soon as the version passes, even if reading the apps then fails.
            ir.async_delete_issue(self.hass, DOMAIN, ISSUE_UNSUPPORTED_VERSION)
            listing = await self.client.get_apps()
        except UnsupportedServerVersionError as exc:
            self.create_version_issue(exc)
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unsupported_version",
                translation_placeholders=version_placeholders(exc),
            ) from exc
        except TRANSIENT_POLL_ERRORS as exc:
            # Returning the previous data counts as a successful poll to HA, so entities stay available.
            if self.within_tolerance():
                _LOGGER.debug("Keeping the previous hassette data after one failed poll: %s", exc)
                return self.data
            await self.follow_addon(exc)
            raise poll_error(exc) from exc
        except HassetteClientError as exc:
            await self.follow_addon(exc)
            self.log_poll_error_once(exc)
            raise poll_error(exc) from exc

        # Every poll, so app entities always find the hub to link under; unchanged fields write nothing.
        hub_id = self.register_server_device(health.version)
        self.track_version(health)
        self.note_bootstrap(health.bootstrap_released)
        usable = [app for app in listing.apps if self.usable_app_key(app.app_key)]
        self.update_app_devices(usable, hub_id)
        for app in usable:
            log_unknown_platforms(app)
        self.register_declaring_app_devices(usable, hub_id)
        if health.bootstrap_released:
            self.remove_undeclared(usable)
        # After the last await, so no push can replace the data this merges with before HA assigns the result.
        data = self.merge_listing(health, listing, usable)
        # Only once the data is built: a step above that raises fails the poll, and must not count as good.
        self.last_success = dt_util.utcnow()
        self.last_applied_fetch = fetch
        self.logged_poll_errors.clear()
        return data

    def merge_listing(
        self, health: SystemStatusResponse, listing: AppListResponse, usable_apps: list[AppSummary]
    ) -> HassetteData:
        """Combine a fresh listing (``usable_apps`` is its apps with a usable key) with the held data.

        Everything follows the listing except declared entity values the held data has newer, from a push
        applied while this poll's HTTP await was pending (see ``merge_instance``). A listing from a new boot is
        taken whole.
        """
        held = self.data if self.data is not None and self.data.boot_id == listing.boot_id else None
        merged: dict[str, AppSummary] = {}
        marks: dict[str, int] = {}
        for app in usable_apps:
            held_app = held.apps.get(app.app_key) if held is not None else None
            instances: list[AppInstance] = []
            for instance in app.instances:
                identifier = instance_identifier(app.app_key, instance.instance_name)
                held_instance = find_instance(held_app, instance.instance_name) if held_app is not None else None
                held_mark = held.entity_marks.get(identifier, 0) if held is not None else 0
                merged_instance, marks[identifier] = merge_instance(
                    instance, held_instance, held_mark, listing.entity_revision
                )
                instances.append(merged_instance)
            merged[app.app_key] = app.model_copy(update={"instances": instances})
        return HassetteData(health=health, apps=merged, boot_id=listing.boot_id, entity_marks=marks)

    def apply_entity_push(self, push: EntityStateChangedData) -> PushOutcome:
        """Apply a pushed entity value to the held data and update the entities, unless it is skipped.

        It applies only to an entity the held listing has, from the listing's boot, with a revision newer than
        its instance's mark. ``self.data`` is replaced, not mutated, and ``async_set_updated_data`` isn't used,
        because it would push the next poll back a full interval on every push.
        """
        held = self.data
        if held is None or push.boot_id != held.boot_id:
            return PushOutcome.OTHER_BOOT
        identifier = instance_identifier(push.app_key, push.instance_name)
        if (mark := held.entity_marks.get(identifier)) is not None and push.entity_revision <= mark:
            return PushOutcome.STALE
        app = held.apps.get(push.app_key)
        instance = find_instance(app, push.instance_name) if app is not None else None
        entity = push.entity
        if app is None or instance is None or find_entity(instance.entities or [], entity.key, entity.platform) is None:
            return PushOutcome.UNKNOWN_TARGET
        entities = [
            entity if (e.key, e.platform) == (entity.key, entity.platform) else e for e in instance.entities or []
        ]
        instances = [
            i.model_copy(update={"entities": entities}) if i.instance_name == push.instance_name else i
            for i in app.instances
        ]
        self.data = HassetteData(
            health=held.health,
            apps={**held.apps, app.app_key: app.model_copy(update={"instances": instances})},
            boot_id=held.boot_id,
            entity_marks={**held.entity_marks, identifier: push.entity_revision},
        )
        self.async_update_listeners()
        return PushOutcome.APPLIED

    def log_poll_error_once(self, exc: HassetteClientError) -> None:
        """Warn about an unreadable response or an unexpected error once per endpoint and type until a good poll.

        Home Assistant logs only the translated message, so an error without its own message would
        otherwise leave no cause in the log.
        """
        unreadable = isinstance(exc, ResponseValidationError)
        if not unreadable and is_mapped_poll_error(exc):
            return
        # The base HassetteClientError carries no endpoint.
        key = (getattr(exc, "endpoint", None), type(exc).__name__)
        if key in self.logged_poll_errors:
            return
        self.logged_poll_errors.add(key)
        if unreadable:
            _LOGGER.warning("hassette sent a response this integration can't read: %s", exc)
        else:
            _LOGGER.warning("Unexpected error polling hassette: %s", exc, exc_info=exc)

    async def follow_addon(self, exc: HassetteClientError) -> None:
        """After a rejected token or an unreachable server, repoint a discovered entry at what its add-on announces.

        Only an entry created from discovery holds a slug, and only that add-on's info is read, so no other
        add-on can repoint the entry. When the announced URL or token differs from the stored one, the entry
        is updated, a reload is scheduled to connect with it, and ``UpdateFailed`` ends this poll. Otherwise
        this returns and the caller reports ``exc`` as usual, which starts reauth for a 401.
        """
        entry = self.config_entry
        if not isinstance(exc, ADDON_FOLLOW_ERRORS) or (slug := entry.data.get(CONF_ADDON_SLUG)) is None:
            return
        announced = await async_read_announced_connection(self.hass, slug)
        if announced is None or all(entry.data.get(key) == value for key, value in announced.items()):
            return
        _LOGGER.info("The hassette add-on %s announces a new URL or token; reconnecting with it", slug)
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, **announced})
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        raise UpdateFailed(translation_domain=DOMAIN, translation_key="addon_refreshed") from exc

    def usable_app_key(self, app_key: str) -> bool:
        """Whether ``app_key`` is in hassette's alphabet; logs each one that isn't, once."""
        if APP_KEY_PATTERN.fullmatch(app_key):
            return True
        if app_key not in self.skipped_app_keys:
            self.skipped_app_keys.add(app_key)
            _LOGGER.warning("Ignoring hassette app %r: its key is outside the app_key alphabet", app_key)
        return False

    def update_app_devices(self, apps: list[AppSummary], hub_id: str) -> None:
        """Keep each existing app device's name, model, model ID and hub link in step with hassette and the hub device.

        Devices are created by the platforms with their first entity; a user's own rename is kept,
        since it is stored separately as ``name_by_user``. The hub link is restored after the hub
        device was deleted and registered again.
        """
        devices = dr.async_get(self.hass)
        for app in apps:
            device = devices.async_get_device_by_identifier((DOMAIN, app.app_key), self.config_entry.entry_id)
            if device is None:
                continue
            # The registry writes, and fires an update event, only when a value differs.
            devices.async_update_device(device.id, **app_device_fields(app), via_device_id=hub_id)

    def register_declaring_app_devices(self, apps: list[AppSummary], hub_id: str) -> None:
        """Create the app device of each app with a declared entity that may be added, before any platform adds one.

        A declared entity's instance device is a child of its app device, and Home Assistant refuses a child
        whose parent isn't registered yet. The platforms set up concurrently, so the coordinator registers the
        parent here, in the poll, rather than leaving it to whichever v0.1 entity happens to be added first.
        """
        devices = dr.async_get(self.hass)
        registry = er.async_get(self.hass)
        for app in apps:
            if any(may_add_declared(registry, app.app_key, instance, entity) for instance, entity in declarations(app)):
                devices.async_get_or_create(
                    config_entry_id=self.config_entry.entry_id, **app_device_spec(app, self.url, hub_id)
                )

    def remove_undeclared(self, apps: list[AppSummary]) -> None:
        """Remove the entities a running instance no longer declares, and the device of one that declares none.

        Only a running instance is compared: hassette serves a stopped or failed instance's last declarations,
        and an instance with no ``entities`` was listed without them. Called only for a listing taken after
        app startup was released, which is complete. One listing is enough: a running instance's declarations
        change only when it reinitializes, and Home Assistant restores a removed entity's id, name, area and
        disabled state when its unique_id comes back.
        """
        running = {
            instance_identifier(app.app_key, instance.instance_name): instance
            for app in apps
            for instance in app.instances
            if instance.status == ResourceStatus.RUNNING and instance.entities is not None
        }
        entities = er.async_get(self.hass)
        for entry in er.async_entries_for_config_entry(entities, self.config_entry.entry_id):
            if (parts := split_declared_unique_id(entry.unique_id)) is None:
                continue
            app_key, instance_name, key = parts
            if (instance := running.get(instance_identifier(app_key, instance_name))) is None:
                continue
            if find_entity(known_declarations(instance), key, entry.domain) is None:
                _LOGGER.info("Removing %s: its hassette app instance no longer declares it", entry.entity_id)
                entities.async_remove(entry.entity_id)
        devices = dr.async_get(self.hass)
        for identifier, instance in running.items():
            if known_declarations(instance):
                continue
            device = devices.async_get_child_device_by_identifier((DOMAIN, identifier), self.config_entry.entry_id)
            if device is not None:
                _LOGGER.info("Removing the device of %s: it no longer declares any entities", identifier)
                devices.async_remove_device(device.id)

    @property
    def app_list_trusted(self) -> bool:
        """Whether the app list can be acted on: the poll is good (or tolerated) and app startup was released.

        Before release, hassette's list can be missing apps it hasn't written yet. Entity availability and
        device removal both gate on this, so they never disagree about whether the list is complete.
        """
        return self.last_update_success and self.data.health.bootstrap_released

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

    def create_version_issue(self, exc: UnsupportedServerVersionError) -> None:
        """Tell the user in Repairs that hassette needs an upgrade."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            ISSUE_UNSUPPORTED_VERSION,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_UNSUPPORTED_VERSION,
            translation_placeholders=version_placeholders(exc),
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


def app_device_fields(app: AppSummary) -> AppDeviceFields:
    """The ``name``, ``model`` and ``model_id`` an app's device shows.

    The keys are the keyword names of both ``DeviceInfo`` and ``async_update_device``, so the entity
    that creates the device and the coordinator that keeps it in step share one list of fields.

    With a description, the device reads ``<description> (<ClassName>)``; without one, the class name
    alone, so the card never shows the class name twice.
    """
    if app.description:
        model, model_id = app.description, app.class_name
    else:
        model, model_id = app.class_name, None
    return AppDeviceFields(name=app.display_name, model=model, model_id=model_id)


def app_device_spec(app: AppSummary, url: str, hub_id: str) -> AppDeviceSpec:
    """Every field an app's device is created with, by its first v0.1 entity or by the coordinator."""
    return AppDeviceSpec(
        identifiers={(DOMAIN, app.app_key)},
        # The coordinator keeps these fields in step with hassette (update_app_devices).
        **app_device_fields(app),
        manufacturer=MANUFACTURER,
        configuration_url=f"{url}/apps/{app.app_key}",
        via_device_id=hub_id,
        entry_type=DeviceEntryType.SERVICE,
    )


def declared_unique_id(app_key: str, instance_name: str, key: str) -> str:
    """The unique_id of a declared entity.

    None of the parts can contain ``-``: app keys are in ``APP_KEY_PATTERN``, and hassette restricts instance names
    and entity keys to lowercase letters, digits and ``_``. So it splits back apart, and can't collide with a v0.1
    ``{app_key}-{key}`` unique_id.
    """
    return f"{app_key}-{instance_name}-{key}"


def split_declared_unique_id(unique_id: str) -> tuple[str, str, str] | None:
    """The app key, instance name and key of a declared entity's unique_id; ``None`` for any other unique_id."""
    match unique_id.split("-"):
        case [app_key, instance_name, key]:
            return app_key, instance_name, key
        case _:
            return None


def instance_identifier(app_key: str, instance_name: str) -> str:
    """The device identifier of an app instance that declares entities (``-`` is in neither part)."""
    return f"{app_key}-{instance_name}"


def split_instance_identifier(identifier: str) -> tuple[str, str] | None:
    """The app key and instance name of an instance device's identifier; ``None`` for an app device's."""
    app_key, _, instance_name = identifier.partition("-")
    return (app_key, instance_name) if instance_name else None


def find_instance(app: AppSummary, instance_name: str) -> AppInstance | None:
    """The app's instance named ``instance_name``, or ``None`` when hassette doesn't list it."""
    return next((instance for instance in app.instances if instance.instance_name == instance_name), None)


def find_entity(entities: Iterable[DeclaredEntity], key: str, platform: str) -> DeclaredEntity | None:
    """The declared entity with ``key`` on ``platform``: an entity's key is unique only within its platform."""
    return next((entity for entity in entities if entity.key == key and entity.platform == platform), None)


def merge_instance(
    listed: AppInstance, held: AppInstance | None, held_mark: int, listing_revision: int
) -> tuple[AppInstance, int]:
    """An instance from a fresh listing merged with the held copy, and the instance's new mark.

    When the held mark is newer than the listing, every entity the listing also lists keeps its held declaration,
    value and attributes: pushes apply in revision order, so each held value is at least as new as the listing's.
    Which entities exist, and every other field, follow the listing. An instance listed without ``entities`` keeps
    the held list and mark, since the listing carried no values for it.
    """
    if held is not None and listed.entities is None:
        return listed.model_copy(update={"entities": held.entities}), held_mark
    mark = max(held_mark, listing_revision)
    if held is None or held.entities is None or held_mark <= listing_revision:
        return listed, mark
    entities = [find_entity(held.entities, e.key, e.platform) or e for e in listed.entities or []]
    return listed.model_copy(update={"entities": entities}), mark


def known_declarations(instance: AppInstance) -> list[DeclaredEntity]:
    """The instance's declared entities on platforms this integration has."""
    return [entity for entity in instance.entities or [] if entity.platform in DECLARED_PLATFORMS]


def log_unknown_platforms(app: AppSummary) -> None:
    """Log each declared entity on a platform this integration doesn't have; it is skipped."""
    for instance in app.instances:
        for entity in instance.entities or []:
            if entity.platform not in DECLARED_PLATFORMS:
                _LOGGER.debug(
                    "Skipping %s on %s/%s: this integration has no %s platform",
                    entity.key,
                    app.app_key,
                    instance.instance_name,
                    entity.platform,
                )


def declarations(app: AppSummary) -> list[tuple[AppInstance, DeclaredEntity]]:
    """Every declared entity on a known platform across the app's instances, with its instance.

    An instance listed without ``entities`` (``None``) is skipped: that means "not included", not "none".
    """
    return [(instance, entity) for instance in app.instances for entity in known_declarations(instance)]


def may_add_declared(registry: er.EntityRegistry, app_key: str, instance: AppInstance, entity: DeclaredEntity) -> bool:
    """Whether a declared entity may be added: its instance is in hassette's config, or it is already registered.

    The coordinator registers app devices by this rule and the platforms add entities by it (narrowed by what
    they added this session), so every entity a platform adds has an app device to be a child of.
    """
    if instance.in_current_config:
        return True
    unique_id = declared_unique_id(app_key, instance.instance_name, entity.key)
    return registry.async_get_entity_id(entity.platform, DOMAIN, unique_id) is not None
