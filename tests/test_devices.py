"""Tests for the hub and app devices: when they are added, updated, kept, removed and brought back."""

from collections.abc import Coroutine
from typing import Any
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from hassette_client import (
    ForbiddenError,
)
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hassette import async_remove_config_entry_device
from custom_components.hassette.const import (
    DOMAIN,
    SERVER_DEVICE_ID,
)

from .conftest import ENTRY_ID, SWITCH, http_error, make_app, make_apps, make_health, poll


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
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    entities = er.async_get(hass)
    entities.async_update_entity(SWITCH, disabled_by=er.RegistryEntryDisabler.USER)
    await hass.async_block_till_done()
    await poll(hass, freezer)

    assert hass.states.get(SWITCH) is None
    assert entities.async_get(SWITCH).disabled_by is er.RegistryEntryDisabler.USER


async def test_renamed_entity_is_not_added_twice(
    hass: HomeAssistant,
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
