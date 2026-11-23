"""Entities an app instance declares through hassette's ``self.integration``, and how the platforms add them.

A declared entity belongs to one instance of an app, not to the app: it sits on the instance's own device (a
child of the app's device), is available while that instance runs, and is identified by app, instance and key.
"""

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from hassette_wire import AppInstance, AppSummary, DeclaredEntity, ResourceStatus
from homeassistant.const import EntityCapabilityAttribute, EntityStateAttribute, Platform
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import ChildDeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    HassetteConfigEntry,
    HassetteCoordinator,
    declarations,
    declared_unique_id,
    instance_identifier,
    may_add_declared,
)
from .entity import run_command

_LOGGER = logging.getLogger(__name__)

# Attribute names Home Assistant sets on every entity. HA merges extra attributes over an entity's capability
# attributes, and writes these only when the entity has a value for them, so an app attribute with one of these
# names would override or stand in for the entity's own. Latitude and longitude stay: apps set them to put an
# entity on the map. A platform adds its own names in ``owned_attributes``.
HA_OWNED_ATTRIBUTES = frozenset({*EntityStateAttribute, *EntityCapabilityAttribute}) - {
    EntityStateAttribute.LATITUDE,
    EntityStateAttribute.LONGITUDE,
}

type DeclaredEntityFactory = Callable[[HassetteCoordinator, str, AppInstance, DeclaredEntity, str], Entity]


class HassetteDeclaredEntity(CoordinatorEntity[HassetteCoordinator]):
    """An entity one app instance declares, on that instance's device.

    Available only while hassette has released app startup and the instance is running and still declares
    it, whether or not the instance is in hassette's current config: hassette takes commands for any
    running instance it tracks.
    """

    _attr_has_entity_name = True
    # The attribute names Home Assistant sets on this entity, which the app's attributes can't override.
    owned_attributes: frozenset[str] = HA_OWNED_ATTRIBUTES

    def __init__(
        self,
        coordinator: HassetteCoordinator,
        app_key: str,
        instance: AppInstance,
        declared: DeclaredEntity,
        parent_device_id: str,
    ) -> None:
        super().__init__(coordinator)
        self.app_key = app_key
        self.instance_name = instance.instance_name
        self.key = declared.key
        # Not "platform": Entity uses that name for its EntityPlatform.
        self.declared_platform = declared.platform
        self.declared_name = declared.name
        self._attr_unique_id = declared_unique_id(app_key, instance.instance_name, declared.key)
        if declared.default_entity_id:
            # The entity registry keeps the entity_id of a unique_id it already has, so this applies only once.
            self.entity_id = declared.default_entity_id
        self._attr_device_info = ChildDeviceInfo(
            identifiers={(DOMAIN, instance_identifier(app_key, instance.instance_name))},
            name=instance_device_name(instance.instance_name),
            parent_device_id=parent_device_id,
        )
        # The problems with this entity's declaration already warned about, so each is logged once.
        self.warned_keys: set[str] = set()

    @property
    def instance(self) -> AppInstance | None:
        """The instance as of the latest poll, or ``None`` when hassette no longer lists it."""
        app = self.coordinator.data.apps.get(self.app_key)
        if app is None:
            return None
        return next((instance for instance in app.instances if instance.instance_name == self.instance_name), None)

    @property
    def declared(self) -> DeclaredEntity | None:
        """This entity's declaration as of the latest poll, or ``None`` when its instance no longer lists it."""
        instance = self.instance
        if instance is None:
            return None
        return next(
            (
                entity
                for entity in instance.entities or []
                if entity.key == self.key and entity.platform == self.declared_platform
            ),
            None,
        )

    @property
    def name(self) -> str:
        """The declared name as of the latest poll, so a rename in the app follows; the first one seen otherwise."""
        declared = self.declared
        return self.declared_name if declared is None else declared.name

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """The attributes the app last set with the value, less any Home Assistant owns (``owned_attributes``)."""
        declared = self.declared
        if declared is None or not declared.attributes:
            return None
        for name in sorted(self.owned_attributes.intersection(declared.attributes)):
            self.warn_once(f"attribute:{name}", f"attribute {name!r} belongs to Home Assistant; leaving it out")
        return {name: value for name, value in declared.attributes.items() if name not in self.owned_attributes} or None

    @property
    def available(self) -> bool:
        instance = self.instance
        return (
            self.coordinator.app_list_trusted
            and instance is not None
            and instance.status == ResourceStatus.RUNNING
            and self.declared is not None
        )

    def warn_once(self, key: str, message: str) -> None:
        """Log a warning about this entity's declaration the first time ``key`` is seen."""
        if key in self.warned_keys:
            return
        self.warned_keys.add(key)
        # HA reads some properties while adding the entity, before it has an entity_id.
        _LOGGER.warning("%s: %s", self.entity_id or self.unique_id, message)

    async def run_command(self, command: Awaitable[Any], what: str) -> None:
        """Await a command for this entity, sent through the polling client: hassette answers once it is dispatched."""
        await run_command(self.coordinator, command, what, self.entity_id)


def instance_device_name(instance_name: str) -> str:
    """An instance's device name: its config name in sentence case (``living_room`` → "Living room")."""
    return instance_name.replace("_", " ").capitalize()


def add_declared_entities(
    entry: HassetteConfigEntry,
    add_entities: Callable[[Iterable[Entity]], None],
    platform: Platform,
    entity_class: DeclaredEntityFactory,
) -> None:
    """Add an entity for each declaration on ``platform`` in hassette's listing, now and on every later poll.

    A declaration not added yet this session is added when ``may_add_declared`` admits it. One added this
    session is added again only after its registry entry is gone (the user deleted it, or the coordinator
    removed it when its instance stopped declaring it) and its instance is in hassette's config. So a
    deleted device of an instance outside the config stays deleted, and a key declared again comes back.
    Entities are added whatever the instance's status, since hassette lists a stopped instance's declarations.
    """
    coordinator = entry.runtime_data
    registry = er.async_get(coordinator.hass)
    added: set[str] = set()

    def wanted(app: AppSummary, instance: AppInstance, declared: DeclaredEntity, unique_id: str) -> bool:
        if unique_id not in added:
            return may_add_declared(registry, app.app_key, instance, declared)
        return instance.in_current_config and registry.async_get_entity_id(platform, DOMAIN, unique_id) is None

    @callback
    def add_new() -> None:
        new: list[Entity] = []
        devices = dr.async_get(coordinator.hass)
        for app in coordinator.data.apps.values():
            # The coordinator registered the app device of every app with a declaration wanted() admits. An app
            # with no device (a departed one, or one the user deleted) has nothing wanted() can admit.
            app_device = devices.async_get_device_by_identifier((DOMAIN, app.app_key), entry.entry_id)
            if app_device is None:
                continue
            for instance, declared in declarations(app):
                if declared.platform != platform:
                    continue
                unique_id = declared_unique_id(app.app_key, instance.instance_name, declared.key)
                if not wanted(app, instance, declared, unique_id):
                    continue
                added.add(unique_id)
                new.append(entity_class(coordinator, app.app_key, instance, declared, app_device.id))
        if new:
            add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
