"""Tests for setting up, polling and unloading the Hassette integration."""

from collections.abc import Coroutine
from datetime import timedelta
from typing import Any
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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntryType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hassette import async_remove_config_entry_device
from custom_components.hassette.const import (
    ACTION_REFRESH_COOLDOWN,
    ACTION_TIMEOUT,
    DOMAIN,
    SCAN_INTERVAL,
    SERVER_DEVICE_ID,
    TOLERANCE_WINDOW,
)

from .conftest import ENTRY_ID, TOKEN, URL, http_error, make_app, make_apps, make_health

SWITCH = "switch.motion_lights"
VERSION_ISSUE = (DOMAIN, "unsupported_version")


def test_timing_constants() -> None:
    assert timedelta(seconds=30) == SCAN_INTERVAL
    assert timedelta(seconds=45) == TOLERANCE_WINDOW
    assert ACTION_REFRESH_COOLDOWN == 1.0


async def poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_registers_devices(hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry) -> None:
    assert loaded_entry.state is ConfigEntryState.LOADED
    devices = dr.async_get(hass)
    assert SERVER_DEVICE_ID == "-server"
    hub = devices.async_get_device_by_identifier((DOMAIN, "-server"), ENTRY_ID)
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


async def test_missing_hub_is_registered_again(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    devices = dr.async_get(hass)
    hub = devices.async_get_device_by_identifier((DOMAIN, SERVER_DEVICE_ID), ENTRY_ID)
    assert hub is not None
    devices.async_remove_device(hub.id)

    client.get_apps.return_value = make_apps(make_app(), make_app("garage_door"))
    await poll(hass, freezer)

    hub = devices.async_get_device_by_identifier((DOMAIN, SERVER_DEVICE_ID), ENTRY_ID)
    garage = devices.async_get_device_by_identifier((DOMAIN, "garage_door"), ENTRY_ID)
    motion = devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID)
    assert hub is not None
    assert garage is not None
    assert motion is not None
    assert garage.via_device_id == hub.id
    assert motion.via_device_id == hub.id


async def test_app_rename_updates_its_device(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    devices = dr.async_get(hass)
    client.get_apps.return_value = make_apps(make_app(display_name="Hall Lights", class_name="HallLights"))
    await poll(hass, freezer)

    app = devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID)
    assert app is not None
    assert (app.name, app.model) == ("Hall Lights", "HallLights")
    assert hass.states.get(SWITCH) is not None


async def test_app_with_key_outside_the_alphabet_is_ignored(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    client.get_apps.return_value = make_apps(make_app(), make_app("-server"), make_app("a-running"))
    await poll(hass, freezer)

    coordinator = loaded_entry.runtime_data
    assert set(coordinator.data.apps) == {"motion_lights"}
    assert coordinator.skipped_app_keys == {"-server", "a-running"}
    hub = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, SERVER_DEVICE_ID), ENTRY_ID)
    assert hub is not None
    assert hub.name == "Hassette"


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


async def test_new_app_adds_a_device(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    client.get_apps.return_value = make_apps(make_app(), make_app("garage_door", "stopped"))
    await poll(hass, freezer)

    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "garage_door"), ENTRY_ID) is not None
    assert hass.states.get("switch.garage_door").state == "off"
    assert hass.states.get("sensor.garage_door_status").state == "stopped"
    assert hass.states.get("button.garage_door_reload") is not None


@pytest.mark.parametrize(
    "apps", [make_apps(), make_apps(make_app(in_current_config=False))], ids=["missing", "not_current"]
)
async def test_departed_app_keeps_its_device(
    hass: HomeAssistant,
    client: MagicMock,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    apps: object,
) -> None:
    client.get_apps.return_value = apps
    await poll(hass, freezer)

    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.motion_lights_status").state == STATE_UNAVAILABLE
    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID) is not None

    client.get_apps.return_value = make_apps(make_app())
    await poll(hass, freezer)
    assert hass.states.get(SWITCH).state == STATE_ON


async def test_only_departed_app_devices_are_removable(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    devices = dr.async_get(hass)
    client.get_apps.return_value = make_apps(make_app(), make_app("garage_door"))
    await poll(hass, freezer)
    client.get_apps.return_value = make_apps(make_app(), make_app("garage_door", in_current_config=False))
    await poll(hass, freezer)

    def removable(identifier: str) -> Coroutine[Any, Any, bool]:
        device = devices.async_get_device_by_identifier((DOMAIN, identifier), ENTRY_ID)
        assert device is not None
        return async_remove_config_entry_device(hass, loaded_entry, device)

    assert await removable(SERVER_DEVICE_ID) is False
    assert await removable("motion_lights") is False
    assert await removable("garage_door") is True

    client.get_apps.return_value = make_apps(make_app())
    await poll(hass, freezer)
    assert await removable("garage_door") is True


async def test_nothing_is_removable_without_a_complete_app_list(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    client.get_apps.return_value = make_apps(make_app(), make_app("garage_door"))
    await poll(hass, freezer)
    client.get_apps.return_value = make_apps(make_app(), make_app("garage_door", in_current_config=False))
    await poll(hass, freezer)
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "garage_door"), ENTRY_ID)
    assert device is not None
    assert await async_remove_config_entry_device(hass, loaded_entry, device) is True

    client.get_health.return_value = make_health(bootstrap_released=False)
    await poll(hass, freezer)
    assert await async_remove_config_entry_device(hass, loaded_entry, device) is False

    client.get_health.return_value = make_health()
    client.get_apps.side_effect = http_error(ForbiddenError, 403)
    await poll(hass, freezer)
    assert await async_remove_config_entry_device(hass, loaded_entry, device) is False

    client.get_apps.side_effect = None
    await hass.config_entries.async_unload(loaded_entry.entry_id)
    assert await async_remove_config_entry_device(hass, loaded_entry, device) is False


async def test_foreign_identifiers_dont_block_removal(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=loaded_entry.entry_id, identifiers={("other_domain", "motion_lights")}
    )

    assert await async_remove_config_entry_device(hass, loaded_entry, device) is True


async def test_returning_app_after_device_deletion_gets_new_entities(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    devices = dr.async_get(hass)
    client.get_apps.return_value = make_apps(make_app(in_current_config=False))
    await poll(hass, freezer)
    device = devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID)
    assert device is not None
    devices.async_remove_device(device.id)
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH) is None

    client.get_apps.return_value = make_apps(make_app())
    await poll(hass, freezer)

    assert devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID) is not None
    assert hass.states.get(SWITCH).state == STATE_ON


async def test_deleted_device_of_departed_app_stays_deleted(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    devices = dr.async_get(hass)
    client.get_apps.return_value = make_apps(make_app(in_current_config=False))
    await poll(hass, freezer)
    device = devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID)
    assert device is not None
    devices.async_remove_device(device.id)
    await hass.async_block_till_done()

    # hassette keeps listing the app from its history.
    await poll(hass, freezer)

    assert devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID) is None
    assert hass.states.get(SWITCH) is None


async def test_disabled_entity_is_not_re_added(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    entities = er.async_get(hass)
    entities.async_update_entity(SWITCH, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    await poll(hass, freezer)

    assert hass.states.get(SWITCH) is None
    assert entities.async_get(SWITCH).disabled_by is er.RegistryEntryDisabler.USER


async def test_renamed_entity_is_not_added_twice(
    hass: HomeAssistant,
    client: MagicMock,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    entities = er.async_get(hass)
    entities.async_update_entity(SWITCH, new_entity_id="switch.renamed")
    await hass.async_block_till_done()
    await poll(hass, freezer)
    await poll(hass, freezer)

    assert hass.states.get("switch.renamed").state == STATE_ON
    # A second entity with the same unique_id is the one failure HA reports only in its log.
    assert "does not generate unique IDs" not in caplog.text


async def test_disabled_entity_of_deleted_device_returns_with_its_app(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    devices = dr.async_get(hass)
    er.async_get(hass).async_update_entity(SWITCH, disabled_by=er.RegistryEntryDisabler.USER)
    client.get_apps.return_value = make_apps(make_app(in_current_config=False))
    await poll(hass, freezer)
    device = devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID)
    assert device is not None
    devices.async_remove_device(device.id)
    await hass.async_block_till_done()

    client.get_apps.return_value = make_apps(make_app())
    await poll(hass, freezer)

    # The app's entities return; HA restores the switch's disabled setting for its unique_id.
    assert hass.states.get("sensor.motion_lights_status").state == "running"
    switch = er.async_get(hass).async_get(SWITCH)
    assert switch is not None
    assert switch.disabled_by is er.RegistryEntryDisabler.USER


async def test_history_only_app_gets_no_device(
    hass: HomeAssistant, client: MagicMock, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    client.get_apps.return_value = make_apps(make_app(), make_app("old_app", in_current_config=False))
    await poll(hass, freezer)

    assert dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "old_app"), ENTRY_ID) is None
