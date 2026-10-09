"""Tests for polling hassette: transient-failure tolerance, version and bootstrap changes, unreadable responses."""

from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from hassette_client import (
    AuthenticationError,
    ForbiddenError,
    GatewayError,
    HassetteConnectionError,
    HassetteTimeoutError,
    RedirectError,
    ResponseValidationError,
    ServiceUnavailableError,
    TelemetryUnavailableError,
    UnexpectedResponseError,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hassette.const import (
    DOMAIN,
    SERVER_DEVICE_ID,
)

from .conftest import ENTRY_ID, SWITCH, VERSION_ISSUE, http_error, make_health, poll


@pytest.mark.parametrize(
    "error",
    [
        HassetteConnectionError("refused"),
        HassetteTimeoutError("slow"),
        http_error(GatewayError, 504),
        http_error(ServiceUnavailableError, 503),
        http_error(TelemetryUnavailableError, 503, code="telemetry_unavailable"),
    ],
)
async def test_one_transient_failure_is_tolerated(
    hass: HomeAssistant,
    client: MagicMock,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    error: Exception,
) -> None:
    client.get_apps.side_effect = error
    await poll(hass, freezer)
    assert hass.states.get(SWITCH).state == STATE_ON

    await poll(hass, freezer)
    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE

    client.get_apps.side_effect = None
    await poll(hass, freezer)
    assert hass.states.get(SWITCH).state == STATE_ON


async def test_tolerance_resets_after_a_good_poll(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    for _ in range(3):
        client.get_health.side_effect = HassetteTimeoutError("slow")
        await poll(hass, freezer)
        assert hass.states.get(SWITCH).state == STATE_ON
        client.get_health.side_effect = None
        await poll(hass, freezer)


@pytest.mark.parametrize(
    "error",
    [
        http_error(ForbiddenError, 403),
        http_error(RedirectError, 302, location="https://login.example.com"),
        ResponseValidationError(model="AppListResponse", endpoint="GET /api/apps", problems=["apps.0.status: missing"]),
    ],
)
async def test_permanent_failures_are_not_tolerated(
    hass: HomeAssistant,
    client: MagicMock,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    error: Exception,
) -> None:
    client.get_apps.side_effect = error
    await poll(hass, freezer)

    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE


async def test_rejected_token_while_running_starts_reauth(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    client.get_health.side_effect = http_error(AuthenticationError, 401)
    await poll(hass, freezer)

    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_downgrade_while_running_raises_repair(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    client.get_health.return_value = make_health(version="0.40.0", api_schema_version=0)
    await poll(hass, freezer)

    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE
    assert ir.async_get(hass).async_get_issue(*VERSION_ISSUE) is not None

    client.get_health.return_value = make_health()
    await poll(hass, freezer)

    assert hass.states.get(SWITCH).state == STATE_ON
    assert ir.async_get(hass).async_get_issue(*VERSION_ISSUE) is None


async def test_server_upgrade_updates_hub_version(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    client.get_health.return_value = make_health(version="0.57.0", api_schema_version=2)
    await poll(hass, freezer)

    hub = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, SERVER_DEVICE_ID), ENTRY_ID)
    assert hub is not None
    assert hub.sw_version == "0.57.0"
    # A newer schema keeps working.
    assert hass.states.get(SWITCH).state == STATE_ON


async def test_held_bootstrap_makes_apps_unavailable(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    client.get_health.return_value = make_health(bootstrap_released=False)
    await poll(hass, freezer)
    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE
    assert loaded_entry.state is ConfigEntryState.LOADED

    client.get_health.return_value = make_health()
    await poll(hass, freezer)
    assert hass.states.get(SWITCH).state == STATE_ON


async def test_unreadable_responses_are_logged_once_per_outage(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    coordinator = loaded_entry.runtime_data
    for size in (100, 200):
        client.get_apps.side_effect = UnexpectedResponseError(
            model="AppListResponse",
            endpoint="GET /api/apps",
            status=200,
            content_type="text/html",
            body_size=size,
            body_excerpt="<html>",
        )
        await poll(hass, freezer)
    assert coordinator.logged_validation_errors == {("GET /api/apps", "UnexpectedResponseError")}

    client.get_apps.side_effect = None
    await poll(hass, freezer)
    assert coordinator.logged_validation_errors == set()
