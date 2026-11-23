"""Fixtures for the Hassette integration tests.

``HassetteClient`` is the boundary: tests replace it with a mock whose methods return real
``hassette_wire`` models and raise real ``hassette_client`` exceptions.
"""

from collections.abc import Generator, Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from hassette_client import HassetteHTTPError
from hassette_wire import (
    LENIENT_CONTEXT,
    ActionResponse,
    AppInstance,
    AppListResponse,
    AppSummary,
    DeclaredEntity,
    EntityCommandResponse,
    ProblemDetail,
    SystemStatusResponse,
)
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.hassette.const import DOMAIN, SCAN_INTERVAL

URL = "http://hassette.local:8126"
ENTRY_ID = "01HASSETTEENTRY"
TOKEN = "s3cret-token"  # noqa: S105 - a test fixture, not a credential

SWITCH = "switch.motion_lights"
VERSION_ISSUE = (DOMAIN, "unsupported_version")


def make_health(**overrides: Any) -> SystemStatusResponse:
    """A healthy, bootstrapped hassette server on the current API schema."""
    data = {
        "status": "ok",
        "websocket_connected": True,
        "bootstrap_released": True,
        "uptime_seconds": 100.0,
        "entity_count": 10,
        "app_count": 2,
        "version": "0.66.0",
        "api_schema_version": 7,
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
        "status": status,
        "status_revision": 1,
    }
    return AppSummary.model_validate(data | overrides, context=LENIENT_CONTEXT)


def make_instance(
    app_key: str = "motion_lights",
    instance_name: str = "living_room",
    status: str = "running",
    entities: Sequence[DeclaredEntity] | None = (),
    **overrides: Any,
) -> AppInstance:
    """An app instance as ``GET /api/apps`` lists it, declaring ``entities``; ``None`` lists it without them."""
    data = {
        "app_key": app_key,
        "instance_name": instance_name,
        "class_name": "MotionLights",
        "status": status,
        "entities": None if entities is None else [entity.model_dump() for entity in entities],
    }
    return AppInstance.model_validate(data | overrides, context=LENIENT_CONTEXT)


def make_declared(key: str = "occupied", platform: str = "switch", **overrides: Any) -> DeclaredEntity:
    """A declared entity. ``platform`` may be one newer than the client knows."""
    data = {"key": key, "platform": platform, "name": key.replace("_", " ").capitalize()}
    return DeclaredEntity.model_validate(data | overrides, context=LENIENT_CONTEXT)


def make_apps(*apps: AppSummary) -> AppListResponse:
    return AppListResponse(total=len(apps), apps=list(apps), boot_id="boot-1", entity_revision=0)


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


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Advance time by one poll interval and let the scheduled poll run."""
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def delete_device_and_poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory, device_id: str) -> None:
    """Delete a device as the user would, then let the next poll run."""
    dr.async_get(hass).async_remove_device(device_id)
    await hass.async_block_till_done()
    await poll(hass, freezer)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components/."""


@pytest.fixture
def client() -> Generator[MagicMock]:
    """The polling ``HassetteClient``, with one app running.

    The integration's action client (the one built with ``request_timeout``) is a separate mock at
    ``client.action_client``; each mock fails a call meant for the other, so a call through the wrong
    client fails the test. Declared-entity commands go through the polling client.
    """
    instance = MagicMock()
    instance.get_health = AsyncMock(return_value=make_health())
    instance.get_apps = AsyncMock(return_value=make_apps(make_app()))
    instance.action = AsyncMock(side_effect=AssertionError("actions go through the action client"))
    instance.press_button = AsyncMock(
        side_effect=lambda app_key, name, key: EntityCommandResponse(
            app_key=app_key, instance_name=name, platform="button", key=key
        )
    )
    instance.set_switch = AsyncMock(
        side_effect=lambda app_key, name, key, _on: EntityCommandResponse(
            app_key=app_key, instance_name=name, platform="switch", key=key
        )
    )
    action_instance = MagicMock()
    action_instance.get_health = AsyncMock(side_effect=AssertionError("polls go through the polling client"))
    action_instance.get_apps = AsyncMock(side_effect=AssertionError("polls go through the polling client"))
    action_instance.press_button = AsyncMock(side_effect=AssertionError("commands go through the polling client"))
    action_instance.set_switch = AsyncMock(side_effect=AssertionError("commands go through the polling client"))
    action_instance.action = AsyncMock(
        side_effect=lambda app_key, action: ActionResponse(
            app_key=app_key, action=action, instance_name=None, outcomes=[]
        )
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
