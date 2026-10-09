"""Tests for the Hassette config flow."""

from unittest.mock import MagicMock

import pytest
from hassette_client import (
    AuthenticationError,
    ForbiddenError,
    GatewayError,
    HassetteClientError,
    HassetteConnectionError,
    HassetteTimeoutError,
    NotFoundError,
    RedirectError,
    ResponseValidationError,
    ServiceUnavailableError,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hassette.const import DOMAIN

from .conftest import TOKEN, URL, http_error, make_health

USER_INPUT = {CONF_URL: URL, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True}

FORM_ERRORS: list[tuple[Exception, str]] = [
    (HassetteConnectionError("refused"), "cannot_connect"),
    (HassetteTimeoutError("slow"), "timeout_connect"),
    (http_error(GatewayError, 502), "cannot_connect"),
    (http_error(ServiceUnavailableError, 503), "cannot_connect"),
    (http_error(ForbiddenError, 403), "forbidden"),
    (ResponseValidationError(model="SystemStatusResponse", endpoint="GET /api/health", problems=[]), "cannot_connect"),
    (http_error(NotFoundError, 404), "unknown"),
    (HassetteClientError("other"), "unknown"),
    (RuntimeError("bug"), "unknown"),
]


async def start_user_flow(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_creates_entry(hass: HomeAssistant, client: MagicMock) -> None:
    flow_id = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {**USER_INPUT, CONF_URL: f"{URL}/"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == URL
    assert result["data"] == USER_INPUT
    assert client.factory.call_args.kwargs["token"] == TOKEN


@pytest.mark.parametrize("token_input", [{}, {CONF_API_TOKEN: ""}])
async def test_blank_token_is_stored_as_absent(hass: HomeAssistant, client: MagicMock, token_input: dict) -> None:
    flow_id = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_URL: URL, **token_input})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: URL, CONF_API_TOKEN: None, CONF_VERIFY_SSL: True}
    assert client.factory.call_args.kwargs["token"] is None


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@hassette.local:8126",
        "http://user@hassette.local",
        "http://[::1",
        "hassette.local:8126",
        "ftp://hassette.local",
    ],
)
async def test_unusable_url_is_rejected(hass: HomeAssistant, client: MagicMock, url: str) -> None:
    flow_id = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {**USER_INPUT, CONF_URL: url})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}
    client.get_health.assert_not_called()

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_url_whitespace_and_trailing_slash_are_trimmed(hass: HomeAssistant, client: MagicMock) -> None:
    flow_id = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {**USER_INPUT, CONF_URL: f"  {URL}/ "})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == URL
    assert result["data"][CONF_URL] == URL


@pytest.mark.parametrize(("error", "key"), FORM_ERRORS)
async def test_user_flow_errors_recover(hass: HomeAssistant, client: MagicMock, error: Exception, key: str) -> None:
    flow_id = await start_user_flow(hass)
    client.get_health.side_effect = error
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

    client.get_health.side_effect = None
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(("token", "key"), [(TOKEN, "invalid_auth"), ("", "token_required")])
async def test_rejected_auth(hass: HomeAssistant, client: MagicMock, token: str, key: str) -> None:
    flow_id = await start_user_flow(hass)
    client.get_health.side_effect = http_error(AuthenticationError, 401)
    result = await hass.config_entries.flow.async_configure(flow_id, {**USER_INPUT, CONF_API_TOKEN: token})

    assert result["errors"] == {"base": key}


async def test_redirect_names_its_target_without_credentials(hass: HomeAssistant, client: MagicMock) -> None:
    flow_id = await start_user_flow(hass)
    client.get_health.side_effect = http_error(RedirectError, 302, location="https://me:pw@login.example.com/auth")
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["errors"] == {"base": "redirected"}
    assert result["description_placeholders"] == {"location": "https://login.example.com/auth"}


async def test_too_old_server_aborts(hass: HomeAssistant, client: MagicMock) -> None:
    flow_id = await start_user_flow(hass)
    client.get_health.return_value = make_health(version="0.40.0", api_schema_version=0)
    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_version"
    assert result["description_placeholders"] == {
        "server_version": "0.40.0",
        "api_schema_version": "0",
        "min_api_schema_version": "1",
    }


async def test_only_one_entry(hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reauth_replaces_token(hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry) -> None:
    result = await loaded_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"][CONF_URL] == URL

    client.get_health.side_effect = http_error(AuthenticationError, 401)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "wrong"})
    assert result["errors"] == {"base": "invalid_auth"}

    client.get_health.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_TOKEN: "new-token"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert loaded_entry.data == {**USER_INPUT, CONF_API_TOKEN: "new-token"}


async def test_reauth_without_token(hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry) -> None:
    result = await loaded_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["reason"] == "reauth_successful"
    assert loaded_entry.data[CONF_API_TOKEN] is None


async def test_reconfigure_prefills_and_updates(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry
) -> None:
    result = await loaded_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    suggested = {str(key): key.description["suggested_value"] for key in result["data_schema"].schema}
    assert suggested == USER_INPUT

    new_url = "https://hassette.example.com"
    client.get_health.side_effect = HassetteConnectionError("refused")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: f"{new_url}/", CONF_VERIFY_SSL: False}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    client.get_health.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: f"{new_url}/", CONF_VERIFY_SSL: False}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert loaded_entry.data == {CONF_URL: new_url, CONF_API_TOKEN: None, CONF_VERIFY_SSL: False}
    assert loaded_entry.title == new_url
