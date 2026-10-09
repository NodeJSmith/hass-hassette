"""Base entities for a hassette app, and how the platforms add them as apps appear."""

import logging
from collections.abc import Callable, Iterable

from hassette_client import HassetteClientError
from hassette_wire import AppAction, AppStatus, AppSummary
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, SERVER_DEVICE_ID
from .coordinator import HassetteConfigEntry, HassetteCoordinator
from .errors import action_error, is_completed_action

_LOGGER = logging.getLogger(__name__)

# Statuses an app can be started, stopped or reloaded from. Disabled and blocked apps can't be
# started from Home Assistant, and an unknown status is newer than this integration understands.
CONTROLLABLE_STATUSES = frozenset({AppStatus.RUNNING, AppStatus.DEGRADED, AppStatus.STOPPED, AppStatus.FAILED})


class HassetteAppEntity(CoordinatorEntity[HassetteCoordinator]):
    """An entity on one hassette app's device.

    Available only while hassette has released app startup and the app is in its current config.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: HassetteCoordinator, description: EntityDescription, app: AppSummary) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.app_key = app.app_key
        self._attr_unique_id = app_unique_id(app.app_key, description)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, app.app_key)},
            # The coordinator keeps name and model in step with hassette (update_app_devices).
            name=app.display_name,
            model=app.class_name,
            manufacturer=MANUFACTURER,
            # Every successful poll registers the hub device, and entities are only built after one.
            via_device_id=dr.async_get_device_id_by_identifier(
                coordinator.hass, (DOMAIN, SERVER_DEVICE_ID), config_entry_id=coordinator.config_entry.entry_id
            ),
            configuration_url=f"{coordinator.url}/apps/{app.app_key}",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def app(self) -> AppSummary | None:
        """The app as of the latest poll, or ``None`` when hassette no longer lists it."""
        return self.coordinator.data.apps.get(self.app_key)

    @property
    def available(self) -> bool:
        app = self.app
        return (
            super().available
            and self.coordinator.data.health.bootstrap_released
            and app is not None
            and app.in_current_config
        )


class HassetteControlEntity(HassetteAppEntity):
    """An entity that starts, stops or reloads its app; unavailable while the app can't be controlled."""

    @property
    def available(self) -> bool:
        app = self.app
        return super().available and app is not None and app.status in CONTROLLABLE_STATUSES

    async def run_action(self, action: AppAction) -> None:
        """Send ``action`` for this app, then refresh whatever the outcome, since a failure may still have acted."""
        try:
            await self.coordinator.action_client.action(self.app_key, action)
        except HassetteClientError as err:
            if not is_completed_action(err):
                raise action_error(err) from err
            _LOGGER.warning("hassette ran %s on %s but its response was unreadable: %s", action, self.app_key, err)
        finally:
            await self.coordinator.async_request_refresh()


def app_unique_id(app_key: str, description: EntityDescription) -> str:
    """The unique_id of an app's entity: ``-`` is outside the app_key alphabet, so it can't be ambiguous."""
    return f"{app_key}-{description.key}"


def add_app_entities(
    entry: HassetteConfigEntry,
    add_entities: Callable[[Iterable[Entity]], None],
    platform: Platform,
    description: EntityDescription,
    entity_class: Callable[[HassetteCoordinator, EntityDescription, AppSummary], Entity],
) -> None:
    """Add a platform's entity for each app hassette lists, now and on every later poll.

    An app gets an entity when it is in hassette's config or already has one in the entity registry.
    hassette keeps listing removed apps from its history, so a departed app the user deleted (its
    registry entry gone) gets a new entity only once it is back in hassette's config. A renamed or
    disabled entity keeps its registry entry, so it is never added twice.
    """
    coordinator = entry.runtime_data
    registry = er.async_get(coordinator.hass)
    added: set[str] = set()

    def wanted(app: AppSummary) -> bool:
        registered = registry.async_get_entity_id(platform, DOMAIN, app_unique_id(app.app_key, description))
        if app.app_key in added:
            return registered is None and app.in_current_config
        return registered is not None or app.in_current_config

    @callback
    def add_new_apps() -> None:
        new = [app for app in coordinator.data.apps.values() if wanted(app)]
        if not new:
            return
        added.update(app.app_key for app in new)
        add_entities(entity_class(coordinator, description, app) for app in new)

    add_new_apps()
    entry.async_on_unload(coordinator.async_add_listener(add_new_apps))
