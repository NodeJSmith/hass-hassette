"""Fixtures for the Hassette integration tests.

``HassetteClient`` is the boundary: tests replace it with a mock whose methods return real
``hassette_wire`` models and raise real ``hassette_client`` exceptions.
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hassette_client import HassetteHTTPError
from hassette_wire import (
    LENIENT_CONTEXT,
    ActionResponse,
    AppListResponse,
    AppSummary,
    ProblemDetail,
    SystemStatusResponse,
)
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hassette.const import DOMAIN

URL = "http://hassette.local:8126"
ENTRY_ID = "01HASSETTEENTRY"
TOKEN = "s3cret-token"  # noqa: S105 - a test fixture, not a credential


def make_health(**overrides: Any) -> SystemStatusResponse:
    """A healthy, bootstrapped hassette server on the current API schema."""
    data = {
        "status": "ok",
        "websocket_connected": True,
        "bootstrap_released": True,
        "uptime_seconds": 100.0,
        "entity_count": 10,
        "app_count": 2,
        "version": "0.56.0",
        "api_schema_version": 1,
    }
    return SystemStatusResponse.model_validate(data | overrides, context=LENIENT_CONTEXT)


def make_app(app_key: str = "motion_lights", status: str = "running", **overrides: Any) -> AppSummary:
    """An app as ``GET /api/apps`` lists it. ``status`` may be a value newer than the client knows."""
    data = {
        "app_key": app_key,
        "class_name": "MotionLights",
        "display_name": app_key.replace("_", " ").title(),
        "filename": f"{app_key}.py",
        "enabled": True,
        "auto_loaded": False,
        "status": status,
    }
    return AppSummary.model_validate(data | overrides, context=LENIENT_CONTEXT)


def make_apps(*apps: AppSummary) -> AppListResponse:
    return AppListResponse(total=len(apps), apps=list(apps))


def http_error[E: HassetteHTTPError](
    cls: type[E], status: int, *, code: str | None = None, detail: str = "", location: str | None = None
) -> E:
    """A client HTTP error as the client raises it, with a hassette problem body when ``code`` is given."""
    problem = None
    if code is not None:
        problem = ProblemDetail.model_validate(
            {"title": "Error", "status": status, "detail": detail, "code": code}, context=LENIENT_CONTEXT
        )
    return cls(status=status, endpoint="GET /api/health", problem=problem, location=location)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components/."""


@pytest.fixture
def client() -> Generator[MagicMock]:
    """The polling ``HassetteClient``, with one app running.

    The integration's action client (the one built with ``request_timeout``) is a separate mock at
    ``client.action_client``; each mock fails a call meant for the other, so a call through the wrong
    client fails the test.
    """
    instance = MagicMock()
    instance.get_health = AsyncMock(return_value=make_health())
    instance.get_apps = AsyncMock(return_value=make_apps(make_app()))
    instance.action = AsyncMock(side_effect=AssertionError("actions go through the action client"))
    action_instance = MagicMock()
    action_instance.get_health = AsyncMock(side_effect=AssertionError("polls go through the polling client"))
    action_instance.get_apps = AsyncMock(side_effect=AssertionError("polls go through the polling client"))
    action_instance.action = AsyncMock(
        side_effect=lambda app_key, action: ActionResponse(app_key=app_key, action=action, instance_index=None)
    )

    def build(*_args: Any, request_timeout: float | None = None, **_kwargs: Any) -> MagicMock:
        return instance if request_timeout is None else action_instance

    with (
        patch("custom_components.hassette.HassetteClient", side_effect=build) as factory,
        patch("custom_components.hassette.config_flow.HassetteClient", return_value=instance),
    ):
        instance.factory = factory
        instance.action_client = action_instance
        yield instance


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title=URL,
        data={CONF_URL: URL, CONF_API_TOKEN: TOKEN, CONF_VERIFY_SSL: True},
    )


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry) -> MockConfigEntry:
    """The config entry, set up against ``client``."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
