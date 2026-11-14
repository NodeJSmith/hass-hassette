"""The hassette.send action: a message to hassette apps, as an event on Home Assistant's bus."""

import asyncio

import probatio
from hassette_client import HassetteClientError
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import ATTR_DATA, ATTR_NAME, DOMAIN, EVENT_ACTION, SEND_HEALTH_TIMEOUT, SERVICE_SEND
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


async def handle_send(call: ServiceCall) -> None:
    """Fire the event if hassette's websocket to Home Assistant is up right now.

    hassette receives the event over that websocket, so a send while it's down would succeed here and
    be lost. ``/api/health`` reports the websocket, and is read live rather than taken from the last
    poll, which can be 30 s old and measures hassette's HTTP API, not the websocket.
    """
    # Raises ServiceValidationError unless the domain's only entry is loaded.
    entry: HassetteConfigEntry = service.async_get_config_entry(call.hass, DOMAIN, None)
    try:
        async with asyncio.timeout(SEND_HEALTH_TIMEOUT):
            health = await entry.runtime_data.client.get_health()
    except (HassetteClientError, TimeoutError) as exc:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="send_unreachable") from exc
    if not health.websocket_connected:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="send_disconnected")
    call.hass.bus.async_fire(
        EVENT_ACTION,
        {ATTR_NAME: call.data[ATTR_NAME], ATTR_DATA: call.data[ATTR_DATA]},
        context=call.context,
    )
