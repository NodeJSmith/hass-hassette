"""The hassette.send action: a message to hassette apps, as an event on Home Assistant's bus."""

import probatio
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import ATTR_DATA, ATTR_NAME, DOMAIN, EVENT_ACTION, SERVICE_SEND

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
    """Fire the event while the integration is loaded.

    Whether hassette is reachable isn't checked: hassette receives the event over its own websocket to
    Home Assistant, which the integration's HTTP polls of hassette don't reflect.
    """
    # Raises ServiceValidationError unless the domain's only entry is loaded; the entry itself isn't needed.
    service.async_get_config_entry(call.hass, DOMAIN, None)
    call.hass.bus.async_fire(
        EVENT_ACTION,
        {ATTR_NAME: call.data[ATTR_NAME], ATTR_DATA: call.data[ATTR_DATA]},
        context=call.context,
    )
