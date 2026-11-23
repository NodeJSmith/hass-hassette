"""The reload button on each hassette app's device, and the buttons app instances declare."""

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HassetteConfigEntry
from .declared import HassetteDeclaredEntity, add_declared_entities
from .entity import HassetteControlEntity, add_app_entities

# No client-side limit: actions on different apps are independent, and hassette itself rejects a
# second concurrent action on one app (action_in_progress). A declared entity's command returns as
# soon as hassette dispatches it.
PARALLEL_UPDATES = 0

RELOAD = ButtonEntityDescription(
    key="reload",
    translation_key="reload",
    device_class=ButtonDeviceClass.RESTART,
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HassetteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a reload button for every hassette app, and each button an app instance declares."""
    add_app_entities(entry, async_add_entities, Platform.BUTTON, RELOAD, HassetteReloadButton)
    add_declared_entities(entry, async_add_entities, Platform.BUTTON, HassetteDeclaredButton)


class HassetteReloadButton(HassetteControlEntity, ButtonEntity):
    """Reload the app: stop it, re-read its config, and start it again."""

    async def async_press(self) -> None:
        """Reload the app."""
        await self.run_action("reload")


class HassetteDeclaredButton(HassetteDeclaredEntity, ButtonEntity):
    """A button an app instance declares; pressing it runs the instance's handler."""

    async def async_press(self) -> None:
        """Press the button in hassette."""
        await self.run_command(
            self.coordinator.client.press_button(self.app_key, self.instance_name, self.key), "press"
        )
