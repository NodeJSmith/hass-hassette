"""Tests for the buttons, switches and sensors app instances declare, and their instance devices."""

import logging
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from hassette_client import (
    EntityNotDeclaredError,
    HassetteConnectionError,
    InstanceNotFoundError,
    InstanceNotRunningError,
)
from hassette_wire import AppSummary, DeclaredEntity
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_UNIT_OF_MEASUREMENT,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from custom_components.hassette import async_remove_config_entry_device
from custom_components.hassette.const import DOMAIN

from .conftest import (
    ENTRY_ID,
    delete_device_and_poll,
    http_error,
    make_app,
    make_apps,
    make_declared,
    make_health,
    make_instance,
    poll,
)

OCCUPIED = "switch.motion_lights_living_room_occupied"
PING = "button.motion_lights_living_room_ping"
LUX = "sensor.motion_lights_living_room_lux"
HALL_LUX = "sensor.hall_lux"
INSTANCE_ID = (DOMAIN, "motion_lights-living_room")
THREE = (
    make_declared("occupied", "switch", value=True),
    make_declared("ping", "button"),
    make_declared("lux", "sensor", value=3, unit="lx", state_class="measurement"),
)


def app_with(*instances: Any, **overrides: Any) -> AppSummary:
    return make_app(instances=[instance.model_dump() for instance in instances], **overrides)


def declaring(*entities: DeclaredEntity, **overrides: Any) -> AppSummary:
    """The motion_lights app with one instance, living_room, declaring ``entities``."""
    return app_with(make_instance(entities=list(entities), **overrides))


async def load_with(hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry, *apps: AppSummary) -> None:
    client.get_apps.return_value = make_apps(*apps)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def relist(hass: HomeAssistant, client: MagicMock, freezer: FrozenDateTimeFactory, *apps: AppSummary) -> None:
    client.get_apps.return_value = make_apps(*apps)
    await poll(hass, freezer)


def instance_device(hass: HomeAssistant) -> dr.ChildDeviceEntry | None:
    return dr.async_get(hass).async_get_child_device_by_identifier(INSTANCE_ID, ENTRY_ID)


def state(hass: HomeAssistant, entity_id: str) -> str | None:
    current = hass.states.get(entity_id)
    return None if current is None else current.state


async def test_declared_entities_sit_on_a_child_device_of_the_app_device(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))

    devices = dr.async_get(hass)
    app = devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID)
    instance = instance_device(hass)
    assert app is not None
    assert instance is not None
    assert (instance.name, instance.parent_device_id) == ("Living room", app.id)
    registry = er.async_get(hass)
    for entity_id, unique_id in (
        (OCCUPIED, "motion_lights-living_room-occupied"),
        (PING, "motion_lights-living_room-ping"),
        (LUX, "motion_lights-living_room-lux"),
    ):
        entry = registry.async_get(entity_id)
        assert entry is not None
        assert (entry.unique_id, entry.device_id) == (unique_id, instance.id)


async def test_app_device_of_a_declaring_app_has_the_v01_fields(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await load_with(hass, client, config_entry, app_with(make_instance(entities=list(THREE)), description="Lights"))

    devices = dr.async_get(hass)
    hub = devices.async_get_device_by_identifier((DOMAIN, "-server"), ENTRY_ID)
    app = devices.async_get_device_by_identifier((DOMAIN, "motion_lights"), ENTRY_ID)
    assert hub is not None
    assert app is not None
    assert (app.name, app.model, app.model_id, app.manufacturer, app.via_device_id) == (
        "Motion Lights",
        "Lights",
        "MotionLights",
        "Hassette",
        hub.id,
    )
    assert app.configuration_url == "http://hassette.local:8126/apps/motion_lights"


async def test_an_instance_declaring_nothing_gets_no_device(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await load_with(hass, client, config_entry, declaring())

    assert instance_device(hass) is None


async def test_instance_name_reads_in_sentence_case(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await load_with(hass, client, config_entry, app_with(make_instance(instance_name="main", entities=[THREE[0]])))

    device = dr.async_get(hass).async_get_child_device_by_identifier((DOMAIN, "motion_lights-main"), ENTRY_ID)
    assert device is not None
    assert device.name == "Main"


async def test_suggested_entity_id_applies_only_on_first_creation(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(
        hass, client, config_entry, declaring(make_declared("occupied", default_entity_id="switch.hall_presence"))
    )
    assert state(hass, "switch.hall_presence") is not None

    await relist(hass, client, freezer, declaring(make_declared("occupied", default_entity_id="switch.other")))
    assert state(hass, "switch.hall_presence") is not None
    assert state(hass, "switch.other") is None


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


async def test_renamed_declaration_renames_the_entity(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(make_declared("occupied", name="Occupied")))

    await relist(hass, client, freezer, declaring(make_declared("occupied", name="Someone home")))

    current = hass.states.get(OCCUPIED)
    assert current is not None
    assert current.attributes["friendly_name"].endswith("Someone home")


async def test_unknown_platform_is_skipped(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.hassette")
    await load_with(hass, client, config_entry, declaring(make_declared("dial", "number"), THREE[0]))

    assert state(hass, OCCUPIED) == STATE_ON
    assert not [entry for entry in er.async_get(hass).entities.values() if entry.unique_id.endswith("-dial")]
    assert "no number platform" in caplog.text


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("running", STATE_ON),
        ("stopped", STATE_UNAVAILABLE),
        ("failed", STATE_UNAVAILABLE),
        ("rebooting", STATE_UNAVAILABLE),
    ],
)
async def test_available_only_while_the_instance_runs(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, status: str, expected: str
) -> None:
    """A stopped instance's last declarations still add its entities; they show unavailable."""
    await load_with(hass, client, config_entry, declaring(THREE[0], status=status))

    assert state(hass, OCCUPIED) == expected


async def test_running_instance_outside_config_stays_available(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(THREE[0]))

    await relist(hass, client, freezer, declaring(THREE[0], in_current_config=False))

    assert state(hass, OCCUPIED) == STATE_ON


async def test_unavailable_until_bootstrap_released(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    client.get_health.return_value = make_health(bootstrap_released=False)
    await load_with(hass, client, config_entry, declaring(THREE[0]))
    assert state(hass, OCCUPIED) == STATE_UNAVAILABLE

    client.get_health.return_value = make_health()
    await poll(hass, freezer)
    assert state(hass, OCCUPIED) == STATE_ON


async def test_instance_listed_without_entities_is_left_alone(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """``entities`` of ``None`` means not included, so nothing is added or removed for that instance."""
    await load_with(hass, client, config_entry, app_with(make_instance(entities=None)))
    assert instance_device(hass) is None

    await relist(hass, client, freezer, declaring(THREE[0]))
    await relist(hass, client, freezer, app_with(make_instance(entities=None)))

    assert er.async_get(hass).async_get(OCCUPIED) is not None
    assert instance_device(hass) is not None


@pytest.mark.parametrize(("value", "expected"), [(True, STATE_ON), (False, STATE_OFF), (None, STATE_UNKNOWN)])
async def test_switch_shows_the_declared_value(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, value: bool | None, expected: str
) -> None:
    await load_with(hass, client, config_entry, declaring(make_declared("occupied", value=value)))

    assert state(hass, OCCUPIED) == expected


async def test_switch_shows_the_attributes_the_app_set(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await load_with(
        hass, client, config_entry, declaring(make_declared("occupied", value=True, attributes={"who": "Ann"}))
    )

    current = hass.states.get(OCCUPIED)
    assert current is not None
    assert current.attributes["who"] == "Ann"


@pytest.mark.parametrize(("service", "on"), [(SERVICE_TURN_ON, True), (SERVICE_TURN_OFF, False)])
async def test_toggle_sends_set_switch_then_refreshes(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, service: str, on: bool
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))
    polls = client.get_apps.await_count

    await hass.services.async_call(SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: OCCUPIED}, blocking=True)
    await hass.async_block_till_done()

    client.set_switch.assert_awaited_once_with("motion_lights", "living_room", "occupied", on)
    assert client.get_apps.await_count == polls + 1


async def test_press_sends_press_button_then_refreshes(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))
    polls = client.get_apps.await_count

    await hass.services.async_call(BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: PING}, blocking=True)
    await hass.async_block_till_done()

    client.press_button.assert_awaited_once_with("motion_lights", "living_room", "ping")
    assert client.get_apps.await_count == polls + 1


@pytest.mark.parametrize(
    ("error", "raised", "key"),
    [
        (
            http_error(InstanceNotRunningError, 409, code="instance_not_running"),
            HomeAssistantError,
            "instance_not_running",
        ),
        (
            http_error(EntityNotDeclaredError, 409, code="entity_not_declared"),
            HomeAssistantError,
            "entity_not_declared",
        ),
        (
            http_error(InstanceNotFoundError, 404, code="instance_not_found"),
            ServiceValidationError,
            "instance_not_found",
        ),
    ],
)
async def test_command_errors(
    hass: HomeAssistant,
    client: MagicMock,
    config_entry: MockConfigEntry,
    error: Exception,
    raised: type[HomeAssistantError],
    key: str,
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))
    client.press_button.side_effect = error

    with pytest.raises(raised) as caught:
        await hass.services.async_call(BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: PING}, blocking=True)

    assert type(caught.value) is raised
    assert caught.value.translation_key == key


async def test_sensor_shows_the_declared_fields(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    lux = make_declared(
        "lux",
        "sensor",
        value=3.5,
        unit="lx",
        device_class="illuminance",
        state_class="measurement",
        attributes={"raw": 7},
    )
    await load_with(hass, client, config_entry, declaring(lux))

    current = hass.states.get(LUX)
    assert current is not None
    assert current.state == "3.5"
    assert current.attributes[ATTR_UNIT_OF_MEASUREMENT] == "lx"
    assert (current.attributes["device_class"], current.attributes["state_class"]) == ("illuminance", "measurement")
    assert current.attributes["raw"] == 7


async def test_enum_sensor_passes_its_options(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    mode = make_declared("mode", "sensor", value="away", device_class="enum", options=["home", "away"])
    await load_with(hass, client, config_entry, declaring(mode))

    current = hass.states.get("sensor.motion_lights_living_room_mode")
    assert current is not None
    assert (current.state, current.attributes["options"]) == ("away", ["home", "away"])


@pytest.mark.parametrize("state_class", ["measurement", None])
async def test_attributes_home_assistant_owns_are_left_out(
    state_class: str | None,
    hass: HomeAssistant,
    client: MagicMock,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An app attribute can't stand in for the sensor's own state class, which the recorder reads from the state."""
    attributes = {"state_class": "total_increasing", "last_reset": "2024-01-01T00:00:00Z", "latitude": 1.5, "raw": 7}
    lux = make_declared("lux", "sensor", value=1, state_class=state_class, attributes=attributes)
    await load_with(hass, client, config_entry, declaring(lux))

    assert (current := hass.states.get(LUX)) is not None
    assert current.attributes.get("state_class") == state_class
    assert "last_reset" not in current.attributes
    assert (current.attributes["latitude"], current.attributes["raw"]) == (1.5, 7)
    assert caplog.text.count("attribute 'state_class'") == caplog.text.count("attribute 'last_reset'") == 1


async def test_switch_keeps_sensor_attribute_names_but_not_home_assistants(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    attributes = {"device_class": "outlet", "options": ["a", "b"]}
    await load_with(hass, client, config_entry, declaring(make_declared("occupied", value=True, attributes=attributes)))

    current = hass.states.get(OCCUPIED)
    assert current is not None
    assert "device_class" not in current.attributes
    assert current.attributes["options"] == ["a", "b"]


@pytest.mark.parametrize(
    ("device_class", "value", "expected"),
    [
        ("timestamp", "2024-01-01T12:30:00Z", datetime(2024, 1, 1, 12, 30, tzinfo=UTC).isoformat()),
        ("timestamp", "2024-01-01T13:30:00+01:00", datetime(2024, 1, 1, 12, 30, tzinfo=UTC).isoformat()),
        ("date", "2024-01-01", date(2024, 1, 1).isoformat()),
    ],
)
async def test_timestamp_and_date_values_are_parsed(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, device_class: str, value: str, expected: str
) -> None:
    await load_with(
        hass, client, config_entry, declaring(make_declared("when", "sensor", value=value, device_class=device_class))
    )

    assert state(hass, "sensor.motion_lights_living_room_when") == expected


@pytest.mark.parametrize(
    ("device_class", "value"),
    [("timestamp", "soon"), ("timestamp", "2024-13-45T00:00:00Z"), ("date", "soon"), ("date", "2024-13-45")],
)
async def test_unparsable_timestamp_or_date_is_unknown_with_one_warning(
    hass: HomeAssistant,
    client: MagicMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    device_class: str,
    value: str,
) -> None:
    when = make_declared("when", "sensor", value=value, device_class=device_class)
    await load_with(hass, client, config_entry, declaring(when))
    await relist(hass, client, freezer, declaring(when.model_copy(update={"attributes": {"n": 1}})))

    assert state(hass, "sensor.motion_lights_living_room_when") == STATE_UNKNOWN
    assert caplog.text.count(f"{value!r}, which isn't a valid") == 1


async def test_stopped_declaration_is_removed_from_a_running_instance(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))

    await relist(hass, client, freezer, declaring(THREE[0]))

    registry = er.async_get(hass)
    assert registry.async_get(OCCUPIED) is not None
    assert registry.async_get(PING) is None
    assert registry.async_get(LUX) is None
    assert state(hass, PING) is None


async def test_key_moved_to_another_platform_is_a_new_entity(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(make_declared("occupied", "switch")))

    await relist(hass, client, freezer, declaring(make_declared("occupied", "sensor", value="yes")))

    registry = er.async_get(hass)
    assert registry.async_get(OCCUPIED) is None
    assert state(hass, "sensor.motion_lights_living_room_occupied") == "yes"


async def test_redeclared_key_comes_back_with_its_entity_id(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))
    er.async_get(hass).async_update_entity(PING, new_entity_id="button.doorbell")

    await relist(hass, client, freezer, declaring(THREE[0]))
    await relist(hass, client, freezer, declaring(*THREE))

    assert state(hass, "button.doorbell") is not None


async def test_running_instance_declaring_nothing_loses_its_device(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))

    await relist(hass, client, freezer, declaring())

    assert instance_device(hass) is None
    assert er.async_get(hass).async_get(OCCUPIED) is None


@pytest.mark.parametrize("status", ["stopped", "failed"])
async def test_non_running_instance_is_never_diffed(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory, status: str
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))

    await relist(hass, client, freezer, declaring(status=status))

    assert instance_device(hass) is not None
    assert er.async_get(hass).async_get(PING) is not None


async def test_listing_before_bootstrap_release_is_not_diffed(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))

    client.get_health.return_value = make_health(bootstrap_released=False)
    await relist(hass, client, freezer, declaring())

    assert instance_device(hass) is not None


async def test_departed_instance_leaves_its_entities_to_home_assistant(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))

    await relist(hass, client, freezer, app_with())

    assert er.async_get(hass).async_get(OCCUPIED) is not None
    assert state(hass, OCCUPIED) == STATE_UNAVAILABLE


async def test_unregistered_instance_outside_config_gets_nothing(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE, in_current_config=False))

    assert instance_device(hass) is None
    assert state(hass, OCCUPIED) is None


async def test_registered_instance_outside_config_is_restored(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    """After a restart, an instance hassette still runs outside its config keeps its entities."""
    config_entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "switch", DOMAIN, "motion_lights-living_room-occupied", config_entry=config_entry, suggested_object_id="x"
    )
    await load_with(hass, client, config_entry, declaring(*THREE, in_current_config=False))

    assert state(hass, "switch.x") == STATE_ON
    assert state(hass, PING) is None


async def test_deleted_device_of_instance_outside_config_stays_deleted(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))
    await relist(hass, client, freezer, declaring(*THREE, in_current_config=False))
    device = instance_device(hass)
    assert device is not None

    await delete_device_and_poll(hass, freezer, device.id)

    assert instance_device(hass) is None
    assert state(hass, OCCUPIED) is None


async def test_deleted_device_of_instance_in_config_comes_back(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))
    device = instance_device(hass)
    assert device is not None

    await delete_device_and_poll(hass, freezer, device.id)

    assert instance_device(hass) is not None
    assert state(hass, OCCUPIED) == STATE_ON


@pytest.mark.parametrize(
    ("listed", "removable"),
    [
        (declaring(*THREE), False),
        (declaring(*THREE, in_current_config=False), True),
        (app_with(), True),
        (make_app("garage_door"), True),
    ],
    ids=["in-config", "outside-config", "instance-gone", "app-gone"],
)
async def test_instance_device_removable_once_out_of_config(
    hass: HomeAssistant,
    client: MagicMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    listed: AppSummary,
    removable: bool,
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))
    device = instance_device(hass)
    assert device is not None

    await relist(hass, client, freezer, listed)

    assert await async_remove_config_entry_device(hass, config_entry, device) is removable


async def test_listed_app_without_a_device_doesnt_block_declared_entities(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """hassette lists a departed app from its history; with no device, it must not stop other apps' entities."""
    departed = make_app("old_app", status="stopped", in_current_config=False)
    await load_with(hass, client, config_entry, departed, declaring(*THREE))
    assert state(hass, OCCUPIED) == STATE_ON

    old = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "old_app"), ENTRY_ID)
    assert old is None
    await relist(hass, client, freezer, departed, declaring(*THREE, make_declared("ring", "button")))

    assert state(hass, "button.motion_lights_living_room_ring") is not None


async def test_deleting_a_departed_apps_device_doesnt_block_declared_entities(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, make_app("old_app"), declaring(*THREE))
    await relist(hass, client, freezer, make_app("old_app", in_current_config=False), declaring(*THREE))
    old = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "old_app"), ENTRY_ID)
    assert old is not None

    client.get_apps.return_value = make_apps(
        make_app("old_app", in_current_config=False), declaring(*THREE, make_declared("ring", "button"))
    )
    await delete_device_and_poll(hass, freezer, old.id)

    assert state(hass, "button.motion_lights_living_room_ring") is not None


async def test_tolerated_failure_removes_nothing(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """A poll that keeps the previous listing after a transient failure has no new listing to diff."""
    await load_with(hass, client, config_entry, declaring(*THREE))

    client.get_apps.side_effect = HassetteConnectionError("refused")
    with patch.object(config_entry.runtime_data, "remove_undeclared") as remove:
        await poll(hass, freezer)

    remove.assert_not_called()

    registry = er.async_get(hass)
    assert all(registry.async_get(entity_id) is not None for entity_id in (OCCUPIED, PING, LUX))
    assert state(hass, PING) is not None


async def test_redeclared_key_keeps_the_users_name_area_and_disabled_state(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """One-poll removal is safe because Home Assistant restores these when the unique_id returns."""
    await load_with(hass, client, config_entry, declaring(*THREE))
    area = ar.async_get(hass).async_create("Hall")
    er.async_get(hass).async_update_entity(
        PING, name="Doorbell", area_id=area.id, disabled_by=er.RegistryEntryDisabler.USER
    )

    await relist(hass, client, freezer, declaring(THREE[0]))
    await relist(hass, client, freezer, declaring(*THREE))

    entry = er.async_get(hass).async_get(PING)
    assert entry is not None
    assert (entry.name, entry.area_id, entry.disabled_by) == ("Doorbell", area.id, er.RegistryEntryDisabler.USER)


async def test_users_instance_device_name_survives_polls(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))
    device = instance_device(hass)
    assert device is not None
    dr.async_get(hass).async_update_child_device(device.id, name_by_user="Lounge")

    await relist(hass, client, freezer, declaring(*THREE, make_declared("ring", "button")))

    device = instance_device(hass)
    assert device is not None
    assert (device.name, device.name_by_user) == ("Living room", "Lounge")


async def test_instance_device_not_removable_before_bootstrap_release(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))
    device = instance_device(hass)
    assert device is not None

    client.get_health.return_value = make_health(bootstrap_released=False)
    await relist(hass, client, freezer, declaring(*THREE, in_current_config=False))

    assert await async_remove_config_entry_device(hass, config_entry, device) is False


async def test_instance_declaring_only_unknown_platforms_loses_its_device(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await load_with(hass, client, config_entry, declaring(*THREE))

    await relist(hass, client, freezer, declaring(make_declared("dial", "number")))

    assert instance_device(hass) is None
    assert state(hass, "switch.motion_lights") == STATE_ON


@pytest.mark.parametrize(("field", "attribute"), [("device_class", "device_class"), ("state_class", "state_class")])
async def test_unknown_sensor_class_is_left_out_with_one_warning(
    hass: HomeAssistant,
    client: MagicMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
    field: str,
    attribute: str,
) -> None:
    """Like ESPHome, a class this Home Assistant lacks is dropped; the sensor is added and keeps updating."""
    await load_with(hass, client, config_entry, declaring(make_declared("lux", "sensor", value=1)))

    await relist(hass, client, freezer, declaring(make_declared("lux", "sensor", value=2, **{field: "plasma"})))
    await relist(hass, client, freezer, declaring(make_declared("lux", "sensor", value=3, **{field: "plasma"})))

    current = hass.states.get(LUX)
    assert current is not None
    assert current.state == "3"
    assert attribute not in current.attributes
    assert caplog.text.count("class 'plasma'") == 1


async def test_new_sensor_with_unknown_class_is_still_added(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    """Its options go too, since Home Assistant accepts options only on an enum sensor."""
    lux = make_declared("lux", "sensor", value=1, device_class="plasma", options=["1"])
    await load_with(hass, client, config_entry, declaring(lux))

    assert state(hass, LUX) == "1"
    assert "motion_lights-living_room-lux: this Home Assistant has no device class 'plasma'" in caplog.text
