"""Tests for the hassette.send action."""

from unittest.mock import MagicMock

import probatio
import pytest
from freezegun.api import FrozenDateTimeFactory
from hassette_client import HassetteConnectionError
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hassette.const import DOMAIN, EVENT_ACTION, SERVICE_SEND

from .conftest import SWITCH, poll


@pytest.fixture
def events(hass: HomeAssistant) -> list[Event]:
    """Every hassette_action event fired on the bus."""
    fired: list[Event] = []

    @callback
    def record(event: Event) -> None:
        fired.append(event)

    hass.bus.async_listen(EVENT_ACTION, record)
    return fired


async def test_send_fires_the_event_with_the_callers_context(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, events: list[Event]
) -> None:
    context = Context()
    await hass.services.async_call(
        DOMAIN, SERVICE_SEND, {"name": "tracker_done", "data": {"room": "kitchen"}}, blocking=True, context=context
    )
    await hass.async_block_till_done()

    assert [event.data for event in events] == [{"name": "tracker_done", "data": {"room": "kitchen"}}]
    assert events[0].context is context


async def test_send_data_defaults_to_empty(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, events: list[Event]
) -> None:
    await hass.services.async_call(DOMAIN, SERVICE_SEND, {"name": "tracker_done"}, blocking=True)
    await hass.async_block_till_done()

    assert [event.data for event in events] == [{"name": "tracker_done", "data": {}}]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="no-name"),
        pytest.param({"name": ""}, id="empty-name"),
        pytest.param({"name": "x", "data": ["not", "a", "mapping"]}, id="list-data"),
        pytest.param({"name": "x", "data": "text"}, id="string-data"),
    ],
)
async def test_send_rejects_bad_payloads(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, events: list[Event], payload: dict
) -> None:
    with pytest.raises(probatio.Invalid):
        await hass.services.async_call(DOMAIN, SERVICE_SEND, payload, blocking=True)
    assert events == []


async def test_send_stays_registered_but_refuses_after_unload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, events: list[Event]
) -> None:
    await hass.config_entries.async_unload(loaded_entry.entry_id)

    assert hass.services.has_service(DOMAIN, SERVICE_SEND)
    # The translation keys are Home Assistant's own, raised by service.async_get_config_entry.
    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(DOMAIN, SERVICE_SEND, {"name": "tracker_done"}, blocking=True)
    assert raised.value.translation_key == "service_config_entry_not_loaded"
    assert events == []


async def test_send_refuses_without_an_entry(hass: HomeAssistant, events: list[Event]) -> None:
    assert await async_setup_component(hass, DOMAIN, {})

    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(DOMAIN, SERVICE_SEND, {"name": "tracker_done"}, blocking=True)
    assert raised.value.translation_key == "service_found_no_config_entry_for_domain"
    assert events == []


async def test_send_refuses_while_hassette_is_unreachable(
    hass: HomeAssistant,
    client: MagicMock,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    events: list[Event],
) -> None:
    client.get_health.side_effect = HassetteConnectionError("refused")
    # The first failed poll is tolerated; the second makes the entities unavailable.
    await poll(hass, freezer)
    await hass.services.async_call(DOMAIN, SERVICE_SEND, {"name": "tracker_done"}, blocking=True)
    await poll(hass, freezer)
    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(DOMAIN, SERVICE_SEND, {"name": "tracker_done"}, blocking=True)
    assert raised.value.translation_key == "send_not_connected"
    await hass.async_block_till_done()
    assert len(events) == 1

    client.get_health.side_effect = None
    await poll(hass, freezer)
    await hass.services.async_call(DOMAIN, SERVICE_SEND, {"name": "tracker_done"}, blocking=True)
    await hass.async_block_till_done()
    assert len(events) == 2
