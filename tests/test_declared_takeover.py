"""Tests for a declared entity taking over an entity id that only a leftover ``set_state`` state holds."""

import logging
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from custom_components.hassette.const import DOMAIN

from .conftest import make_declared, make_health, poll
from .test_declared import declaring, load_with, state

HALL_LUX = "sensor.hall_lux"


async def test_declared_entity_takes_over_a_leftover_set_state_id(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set("switch.hall_presence", "on")

    with caplog.at_level(logging.INFO):
        await load_with(
            hass, client, config_entry, declaring(make_declared("occupied", default_entity_id="switch.hall_presence"))
        )

    entry = er.async_get(hass).async_get("switch.hall_presence")
    assert entry is not None
    assert entry.platform == DOMAIN
    assert state(hass, "switch.hall_presence_2") is None
    assert "leftover state of switch.hall_presence" in caplog.text


async def test_takeover_waits_for_bootstrap_release(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set("switch.hall_presence", "on")
    client.get_health.return_value = make_health(bootstrap_released=False)
    await load_with(
        hass, client, config_entry, declaring(make_declared("occupied", default_entity_id="switch.hall_presence"))
    )

    assert state(hass, "switch.hall_presence") == "on"
    assert er.async_get(hass).async_get("switch.hall_presence") is None
    assert state(hass, "switch.hall_presence_2") is None

    client.get_health.return_value = make_health()
    await poll(hass, freezer)

    entry = er.async_get(hass).async_get("switch.hall_presence")
    assert entry is not None
    assert entry.platform == DOMAIN
    assert state(hass, "switch.hall_presence_2") is None


async def test_declared_entity_leaves_a_registered_id_to_its_owner(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    er.async_get(hass).async_get_or_create("switch", "other", "owner", suggested_object_id="hall_presence")
    hass.states.async_set("switch.hall_presence", "on")

    await load_with(
        hass, client, config_entry, declaring(make_declared("occupied", default_entity_id="switch.hall_presence"))
    )

    assert state(hass, "switch.hall_presence") == "on"
    entry = er.async_get(hass).async_get("switch.hall_presence_2")
    assert entry is not None
    assert entry.platform == DOMAIN


async def test_declared_entity_leaves_a_live_entity_without_unique_id_alone(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(
        hass, "template", {"template": [{"sensor": [{"name": "Hall lux", "state": "7"}]}]}
    )
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get(HALL_LUX) is None

    await load_with(
        hass,
        client,
        config_entry,
        declaring(make_declared("lux", "sensor", value=3, default_entity_id=HALL_LUX)),
    )

    assert state(hass, HALL_LUX) == "7"
    assert state(hass, f"{HALL_LUX}_2") == "3"
