"""Tests for the diagnostics download: the token redacted, and the stream's and coordinator's state reported."""

from unittest.mock import MagicMock

from hassette_client import HassetteConnectionError
from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant.typing import ClientSessionGenerator

from .conftest import URL, settle
from .test_stream import declaring, entity_push, listing, load, lux


async def test_diagnostics_redact_the_token_and_report_the_stream(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3)), revision=1))
    await stream.send(hass, entity_push(lux(7), revision=2))
    await stream.send(hass, entity_push(lux(8), revision=2))
    await stream.send(hass, entity_push(lux(9), revision=5, boot="boot-0"))

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert diagnostics["entry"][CONF_API_TOKEN] == REDACTED
    assert diagnostics["entry"][CONF_URL] == URL
    assert diagnostics["stream"]["running"] is True
    assert diagnostics["stream"]["last_connected"] is not None
    assert diagnostics["stream"]["last_message"] is not None
    assert diagnostics["stream"]["pushes_applied"] == 1
    assert diagnostics["stream"]["pushes_ignored"] == {"stale": 1, "other_boot": 1}
    assert diagnostics["stream"]["last_error"] is None
    assert diagnostics["coordinator"]["last_success"] is not None
    assert diagnostics["coordinator"]["bootstrap_released"] is True
    assert diagnostics["coordinator"]["server_version"] == "0.66.0"
    assert diagnostics["coordinator"]["api_schema_version"] == 7
    assert diagnostics["coordinator"]["boot_id"] == "boot-1"
    assert diagnostics["coordinator"]["entity_revision"] == 1
    assert diagnostics["coordinator"]["pushed_revisions"] == {"motion_lights/living_room/lux/sensor": 2}


async def test_diagnostics_report_why_the_stream_ended(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    stream.queue_item(HassetteConnectionError("dropped"))
    await settle(hass)

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert diagnostics["stream"]["running"] is False
    assert diagnostics["stream"]["last_error"] == "HassetteConnectionError: dropped"
