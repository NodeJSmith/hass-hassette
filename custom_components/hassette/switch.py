"""The running switch: the main control on each hassette app's device."""

from typing import Any

from hassette_wire import AppStatus
from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity, SwitchEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HassetteConfigEntry
from .entity import HassetteControlEntity, add_app_entities

# No client-side limit: actions on different apps are independent, and hassette itself rejects a
# second concurrent action on one app (action_in_progress).
PARALLEL_UPDATES = 0

ON_STATUSES = frozenset({AppStatus.RUNNING, AppStatus.DEGRADED})

RUNNING = SwitchEntityDescription(
    key="running", translation_key="running", name=None, device_class=SwitchDeviceClass.SWITCH
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HassetteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a running switch for every hassette app."""
    add_app_entities(entry, async_add_entities, Platform.SWITCH, RUNNING, HassetteRunningSwitch)


class HassetteRunningSwitch(HassetteControlEntity, SwitchEntity):
    """On while the app runs (degraded counts); turning it on starts the app, off stops it."""

    @property
    def is_on(self) -> bool:
        app = self.app
        return app is not None and app.status in ON_STATUSES

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the app."""
        await self.run_action("start")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the app until hassette next restarts."""
        await self.run_action("stop")
