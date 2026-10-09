"""Tests for setting up, unloading and repairing the Hassette integration."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from hassette_client import (
    AuthenticationError,
    GatewayError,
    HassetteConnectionError,
    HassetteTimeoutError,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntryType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hassette.const import (
    ACTION_REFRESH_COOLDOWN,
    ACTION_TIMEOUT,
    DOMAIN,
    SCAN_INTERVAL,
    SERVER_DEVICE_ID,
    TOLERANCE_WINDOW,
)

from .conftest import ENTRY_ID, TOKEN, URL, VERSION_ISSUE, http_error, make_health


def test_timing_constants() -> None:
    assert timedelta(seconds=30) == SCAN_INTERVAL
    assert timedelta(seconds=45) == TOLERANCE_WINDOW
    assert ACTION_REFRESH_COOLDOWN == 1.0


async def test_setup_registers_devices(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    assert loaded_entry.state is ConfigEntryState.LOADED
    devices = dr.async_get(hass)
    assert SERVER_DEVICE_ID == "-server"
    hub = devices.async_get_device_by_identifier((DOMAIN, SERVER_DEVICE_ID), ENTRY_ID)
    app = devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID)
    assert hub is not None
    assert app is not None
    assert (hub.name, hub.sw_version, hub.configuration_url, hub.entry_type) == (
        "Hassette",
        "0.56.0",
        URL,
        DeviceEntryType.SERVICE,
    )
    assert (app.name, app.model, app.manufacturer, app.via_device_id, app.entry_type) == (
        "Motion Lights",
        "MotionLights",
        "Hassette",
        hub.id,
        DeviceEntryType.SERVICE,
    )
    assert app.configuration_url == f"{URL}/apps/motion_lights"


async def test_actions_get_their_own_longer_timeout(client: MagicMock, loaded_entry: MockConfigEntry) -> None:
    timeouts = [call.kwargs.get("request_timeout") for call in client.factory.call_args_list]
    assert timeouts == [None, ACTION_TIMEOUT]
    assert ACTION_TIMEOUT == 45.0
    assert all(call.kwargs["token"] == TOKEN for call in client.factory.call_args_list)


async def test_unload_withdraws_issue(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    ir.async_create_issue(
        hass, DOMAIN, "unsupported_version", is_fixable=False, severity=ir.IssueSeverity.ERROR, translation_key="x"
    )
    await hass.config_entries.async_unload(loaded_entry.entry_id)

    assert loaded_entry.state is ConfigEntryState.NOT_LOADED
    assert ir.async_get(hass).async_get_issue(*VERSION_ISSUE) is None


@pytest.mark.parametrize(
    "error",
    [HassetteConnectionError("refused"), HassetteTimeoutError("slow"), http_error(GatewayError, 502)],
)
async def test_unreachable_server_retries_setup(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, error: Exception
) -> None:
    client.get_health.side_effect = error
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    client.get_health.side_effect = http_error(AuthenticationError, 401)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_too_old_server_raises_repair_until_upgraded(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    client.get_health.return_value = make_health(version="0.40.0", api_schema_version=0)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    issue = ir.async_get(hass).async_get_issue(*VERSION_ISSUE)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert not issue.is_fixable
    assert issue.translation_placeholders == {
        "server_version": "0.40.0",
        "api_schema_version": "0",
        "min_api_schema_version": "1",
    }

    client.get_health.return_value = make_health()
    await hass.config_entries.async_reload(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.LOADED
    assert ir.async_get(hass).async_get_issue(*VERSION_ISSUE) is None


async def test_upgrade_withdraws_issue_even_if_apps_fail(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    client.get_health.return_value = make_health(version="0.40.0", api_schema_version=0)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert ir.async_get(hass).async_get_issue(*VERSION_ISSUE) is not None

    client.get_health.return_value = make_health()
    client.get_apps.side_effect = HassetteConnectionError("refused")
    await hass.config_entries.async_reload(config_entry.entry_id)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(*VERSION_ISSUE) is None


async def test_removing_unloaded_entry_withdraws_issue(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    client.get_health.return_value = make_health(api_schema_version=0)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert ir.async_get(hass).async_get_issue(*VERSION_ISSUE) is not None

    await hass.config_entries.async_remove(config_entry.entry_id)

    assert ir.async_get(hass).async_get_issue(*VERSION_ISSUE) is None
