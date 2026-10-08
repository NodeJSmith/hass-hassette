"""The reload button on each hassette app's device."""

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HassetteConfigEntry
from .entity import HassetteControlEntity, add_app_entities

# No client-side limit: actions on different apps are independent, and hassette itself rejects a
# second concurrent action on one app (action_in_progress).
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
    """Add a reload button for every hassette app."""
    add_app_entities(entry, async_add_entities, Platform.BUTTON, RELOAD, HassetteReloadButton)


class HassetteReloadButton(HassetteControlEntity, ButtonEntity):
    """Reload the app: stop it, re-read its config, and start it again."""

    async def async_press(self) -> None:
        """Reload the app."""
        await self.run_action("reload")
