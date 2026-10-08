"""The Hassette integration: each hassette app as a Home Assistant device."""

from hassette_client import HassetteClient
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry

from .const import ACTION_TIMEOUT, DOMAIN, ISSUE_UNSUPPORTED_VERSION, SERVER_DEVICE_ID
from .coordinator import HassetteConfigEntry, HassetteCoordinator

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: HassetteConfigEntry) -> bool:
    """Connect to hassette, then register the hub device before the platforms add app devices."""
    session = async_get_clientsession(hass, verify_ssl=entry.data[CONF_VERIFY_SSL])
    url = entry.data[CONF_URL]
    token = entry.data.get(CONF_API_TOKEN)
    coordinator = HassetteCoordinator(
        hass,
        entry,
        HassetteClient(session, url, token=token),
        HassetteClient(session, url, token=token, request_timeout=ACTION_TIMEOUT),
    )
    # The first refresh also registers the hub device, which the platforms link app devices under.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HassetteConfigEntry) -> bool:
    """Unload the platforms and withdraw the integration's repair issue.

    ``async_remove_entry`` withdraws it too, for an entry removed while it was failing to set up.
    """
    ir.async_delete_issue(hass, DOMAIN, ISSUE_UNSUPPORTED_VERSION)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: HassetteConfigEntry) -> None:
    """Withdraw the repair issue when an entry that never loaded is removed."""
    ir.async_delete_issue(hass, DOMAIN, ISSUE_UNSUPPORTED_VERSION)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: HassetteConfigEntry,
    device: DeviceEntry,
) -> bool:
    """Allow deleting an app's device only once hassette no longer has the app in its config.

    The hub device is never removable. A device with no hassette identifier isn't this integration's to keep.
    """
    apps = entry.runtime_data.data.apps
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        if identifier == SERVER_DEVICE_ID:
            return False
        app = apps.get(identifier)
        if app is not None and app.in_current_config:
            return False
    return True
