"""The hassette.send action: a message to hassette apps, as an event on Home Assistant's bus."""

import probatio
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import ATTR_DATA, ATTR_NAME, DOMAIN, EVENT_ACTION, SERVICE_SEND
from .coordinator import HassetteConfigEntry

SEND_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_NAME): probatio.All(cv.string, probatio.Length(min=1)),
        probatio.Optional(ATTR_DATA, default=dict): dict,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions. They stay registered for Home Assistant's lifetime."""
    hass.services.async_register(DOMAIN, SERVICE_SEND, handle_send, schema=SEND_SCHEMA)


@callback
def handle_send(call: ServiceCall) -> None:
    """Fire the event while the integration is loaded and its last poll of hassette succeeded.

    A good poll doesn't prove hassette's own websocket to Home Assistant is up, which is how hassette
    receives the event, but a failed one means hassette is down or restarting and the event would be lost.
    """
    # Raises ServiceValidationError unless the domain's only entry is loaded.
    entry: HassetteConfigEntry = service.async_get_config_entry(call.hass, DOMAIN, None)
    if not entry.runtime_data.last_update_success:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="send_not_connected")
    call.hass.bus.async_fire(
        EVENT_ACTION,
        {ATTR_NAME: call.data[ATTR_NAME], ATTR_DATA: call.data[ATTR_DATA]},
        context=call.context,
    )
