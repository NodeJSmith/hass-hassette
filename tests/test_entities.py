"""Tests for the switch, button and sensor on each hassette app's device."""

from unittest.mock import MagicMock

import pytest
from hassette_client import (
    ActionFailedError,
    AppBlockedError,
    AppNotFoundError,
    AuthenticationError,
    BootstrapNotReleasedError,
    ConflictError,
    ForbiddenError,
    GatewayError,
    HassetteClientError,
    HassetteConnectionError,
    HassetteTimeoutError,
    InvalidAppKeyError,
    RedirectError,
    ResponseValidationError,
    ServiceUnavailableError,
    UnexpectedResponseError,
)
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hassette.const import MAX_DETAIL_LENGTH

from .conftest import http_error, make_app, make_apps

SWITCH = "switch.motion_lights"
BUTTON = "button.motion_lights_reload"
SENSOR = "sensor.motion_lights_status"


async def load_with(hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry, *apps: object) -> None:
    client.get_apps.return_value = make_apps(*apps)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_entity_identity(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    entries = {e.entity_id: e for e in er.async_entries_for_config_entry(registry, loaded_entry.entry_id)}

    assert {e.unique_id for e in entries.values()} == {
        "motion_lights-running",
        "motion_lights-reload",
        "motion_lights-status",
    }
    assert set(entries) == {SWITCH, BUTTON, SENSOR}
    assert entries[BUTTON].entity_category is EntityCategory.CONFIG
    assert entries[SWITCH].entity_category is None
    assert hass.states.get(SWITCH).attributes["friendly_name"] == "Motion Lights"


@pytest.mark.parametrize(
    ("status", "switch_state"),
    [("running", STATE_ON), ("degraded", STATE_ON), ("stopped", STATE_OFF), ("failed", STATE_OFF)],
)
async def test_controllable_statuses(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, status: str, switch_state: str
) -> None:
    await load_with(hass, client, config_entry, make_app(status=status))

    assert hass.states.get(SWITCH).state == switch_state
    assert hass.states.get(BUTTON).state != STATE_UNAVAILABLE
    assert hass.states.get(SENSOR).state == status


@pytest.mark.parametrize(
    ("status", "sensor_state"), [("disabled", "disabled"), ("blocked", "blocked"), ("paused", "unknown")]
)
async def test_uncontrollable_statuses(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, status: str, sensor_state: str
) -> None:
    await load_with(hass, client, config_entry, make_app(status=status))

    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE
    assert hass.states.get(BUTTON).state == STATE_UNAVAILABLE
    assert hass.states.get(SENSOR).state == sensor_state


async def test_status_newer_than_client_shows_raw_value(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await load_with(hass, client, config_entry, make_app(status="paused"))

    assert hass.states.get(SENSOR).attributes["server_status"] == "paused"
    assert "error_message" not in hass.states.get(SENSOR).attributes


@pytest.mark.parametrize("status", ["failed", "degraded"])
async def test_error_message_shown_while_failing(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, status: str
) -> None:
    await load_with(hass, client, config_entry, make_app(status=status, error_message="x" * 1000))

    message = hass.states.get(SENSOR).attributes["error_message"]
    assert len(message) == MAX_DETAIL_LENGTH
    assert message.endswith("…")


async def test_error_message_hidden_otherwise(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await load_with(hass, client, config_entry, make_app(status="running", error_message="old failure"))

    assert "error_message" not in hass.states.get(SENSOR).attributes


async def test_status_attributes_are_not_recorded(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    sensor = hass.data["entity_components"]["sensor"].get_entity(SENSOR)
    assert sensor._unrecorded_attributes >= {"server_status", "error_message"}


@pytest.mark.parametrize(
    ("domain", "service", "entity_id", "action"),
    [
        (SWITCH_DOMAIN, SERVICE_TURN_ON, SWITCH, "start"),
        (SWITCH_DOMAIN, SERVICE_TURN_OFF, SWITCH, "stop"),
        (BUTTON_DOMAIN, SERVICE_PRESS, BUTTON, "reload"),
    ],
)
async def test_actions_then_refresh(
    hass: HomeAssistant,
    client: MagicMock,
    loaded_entry: MockConfigEntry,
    domain: str,
    service: str,
    entity_id: str,
    action: str,
) -> None:
    polls = client.get_apps.await_count
    await hass.services.async_call(domain, service, {ATTR_ENTITY_ID: entity_id}, blocking=True)

    client.action.assert_awaited_once_with("motion_lights", action)
    assert client.get_apps.await_count == polls + 1


ACTION_ERRORS: list[tuple[Exception, type[HomeAssistantError], str]] = [
    (http_error(AuthenticationError, 401), HomeAssistantError, "invalid_auth"),
    (HassetteConnectionError("refused"), HomeAssistantError, "cannot_connect"),
    (HassetteTimeoutError("slow"), HomeAssistantError, "action_timeout"),
    (http_error(GatewayError, 502), HomeAssistantError, "action_timeout"),
    (http_error(ServiceUnavailableError, 503), HomeAssistantError, "action_timeout"),
    (http_error(ForbiddenError, 403), HomeAssistantError, "forbidden"),
    (http_error(RedirectError, 302, location="https://login.example.com"), HomeAssistantError, "redirected"),
    (http_error(AppNotFoundError, 404, code="app_not_found"), ServiceValidationError, "not_found"),
    (http_error(InvalidAppKeyError, 400, code="invalid_app_key"), ServiceValidationError, "not_found"),
    (http_error(AppBlockedError, 409, code="app_blocked"), ServiceValidationError, "blocked_by_filter"),
    (http_error(ConflictError, 409, code="action_in_progress"), HomeAssistantError, "action_in_progress"),
    (http_error(ConflictError, 409), HomeAssistantError, "unknown"),
    (http_error(BootstrapNotReleasedError, 409, code="bootstrap_not_released"), HomeAssistantError, "not_bootstrapped"),
    (
        ResponseValidationError(model="ActionResponse", endpoint="POST /api/apps/x/start", problems=[], status=502),
        HomeAssistantError,
        "invalid_response",
    ),
    (HassetteClientError("other"), HomeAssistantError, "unknown"),
]


@pytest.mark.parametrize(("error", "raised", "key"), ACTION_ERRORS)
async def test_action_errors(
    hass: HomeAssistant,
    client: MagicMock,
    loaded_entry: MockConfigEntry,
    error: Exception,
    raised: type[HomeAssistantError],
    key: str,
) -> None:
    client.action.side_effect = error
    polls = client.get_apps.await_count

    with pytest.raises(raised) as caught:
        await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True)

    assert type(caught.value) is raised
    assert caught.value.translation_key == key
    assert client.get_apps.await_count == polls + 1


async def test_failed_action_shows_truncated_detail(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry
) -> None:
    client.action.side_effect = http_error(ActionFailedError, 500, code="action_failed", detail="boom " * 200)

    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: BUTTON}, blocking=True)

    assert caught.value.translation_key == "action_failed"
    assert len(caught.value.translation_placeholders["detail"]) == MAX_DETAIL_LENGTH


async def test_redirect_on_action_names_target(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry
) -> None:
    client.action.side_effect = http_error(RedirectError, 302, location="https://u:p@login.example.com/x")

    with pytest.raises(HomeAssistantError) as caught:
        await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SWITCH}, blocking=True)

    assert caught.value.translation_placeholders == {"location": "https://login.example.com/x"}


@pytest.mark.parametrize(
    "error",
    [
        ResponseValidationError(model="ActionResponse", endpoint="POST /api/apps/x/start", problems=[], status=200),
        UnexpectedResponseError(
            model="ActionResponse",
            endpoint="POST /api/apps/x/start",
            status=202,
            content_type="text/html",
            body_size=10,
            body_excerpt="<html>",
        ),
    ],
)
async def test_unreadable_success_counts_as_done(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, error: Exception
) -> None:
    client.action.side_effect = error
    polls = client.get_apps.await_count

    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True)

    assert client.get_apps.await_count == polls + 1


async def test_back_to_back_actions_show_each_outcome(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry
) -> None:
    client.get_apps.return_value = make_apps(make_app(status="stopped"))
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True)
    assert hass.states.get(SWITCH).state == STATE_OFF

    client.get_apps.return_value = make_apps(make_app(status="running"))
    await hass.services.async_call(SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True)
    assert hass.states.get(SWITCH).state == STATE_ON
