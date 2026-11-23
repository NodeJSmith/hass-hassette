"""Tests for hassette's /ws pushes: entity values, status changes, the connect resync, and restarting the stream."""

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from hassette_client import HassetteConnectionError, HassetteTimeoutError
from hassette_wire import (
    AppListResponse,
    AppsChangedData,
    AppsChangedWsMessage,
    AppStatusChangedData,
    AppStatusChangedWsMessage,
    AppSummary,
    DeclaredEntity,
    EntityStateChangedData,
    EntityStateChangedWsMessage,
)
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.hassette.const import SCAN_INTERVAL
from custom_components.hassette.coordinator import PUSH_SKIP_REASONS, PushOutcome
from custom_components.hassette.stream import MESSAGE_TYPES

from .conftest import FakeStream, make_app, make_declared, make_instance, poll, settle, stream_running

LUX = "sensor.motion_lights_living_room_lux"
OCCUPIED = "switch.motion_lights_living_room_occupied"
SWITCH = "switch.motion_lights"


def lux(value: Any, **overrides: Any) -> DeclaredEntity:
    return make_declared("lux", "sensor", value=value, **overrides)


def declaring(*entities: DeclaredEntity, status: str = "running", **app_overrides: Any) -> AppSummary:
    instance = make_instance(entities=list(entities), status=status)
    return make_app(status=status, instances=[instance.model_dump()], **app_overrides)


def listing(*apps: AppSummary, revision: int = 1, boot: str = "boot-1") -> AppListResponse:
    return AppListResponse(total=len(apps), apps=list(apps), boot_id=boot, entity_revision=revision)


def entity_push(
    entity: DeclaredEntity,
    revision: int,
    *,
    boot: str = "boot-1",
    app_key: str = "motion_lights",
    instance_name: str = "living_room",
) -> EntityStateChangedWsMessage:
    data = EntityStateChangedData(
        boot_id=boot, entity_revision=revision, app_key=app_key, instance_name=instance_name, entity=entity
    )
    return EntityStateChangedWsMessage(type="entity_state_changed", data=data, timestamp=0.0)


def status_push(status: str, revision: int, *, app_key: str = "motion_lights") -> AppStatusChangedWsMessage:
    data = AppStatusChangedData(
        app_key=app_key,
        instance_name="living_room",
        status=status,
        status_revision=revision,
        boot_id="boot-1",
        app_status=status,
    )
    return AppStatusChangedWsMessage(type="app_status_changed", data=data, timestamp=0.0)


def apps_changed() -> AppsChangedWsMessage:
    return AppsChangedWsMessage(type="apps_changed", data=AppsChangedData(), timestamp=0.0)


async def load(hass: HomeAssistant, client: MagicMock, entry: MockConfigEntry, apps: AppListResponse) -> FakeStream:
    client.get_apps.return_value = apps
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    return client.stream


def current(hass: HomeAssistant, entity_id: str) -> State:
    found = hass.states.get(entity_id)
    assert found is not None
    return found


def state(hass: HomeAssistant, entity_id: str) -> str:
    return current(hass, entity_id).state


async def test_connect_subscribes_then_fetches_a_listing(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))

    assert stream.connects == 1
    assert client.listen.await_args.kwargs["types"] == MESSAGE_TYPES
    # The first refresh, then the resync on connect.
    assert client.get_apps.await_count == 2


async def test_pushed_value_reaches_the_entity(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    polls = client.get_apps.await_count

    await stream.send(hass, entity_push(lux(7), revision=2))

    assert state(hass, LUX) == "7"
    assert client.get_apps.await_count == polls


async def test_pushes_do_not_postpone_the_poll(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    polls = client.get_apps.await_count

    # A push every 10 s for longer than the poll interval: the scheduled poll still runs.
    for revision in range(2, 7):
        freezer.tick(10)
        async_fire_time_changed(hass)
        await stream.send(hass, entity_push(lux(revision), revision=revision))

    assert client.get_apps.await_count > polls


async def test_push_does_not_make_entities_available_after_failed_polls(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    client.get_apps.side_effect = HassetteTimeoutError("slow")
    await poll(hass, freezer)
    await poll(hass, freezer)
    assert state(hass, LUX) == STATE_UNAVAILABLE

    await stream.send(hass, entity_push(lux(7), revision=2))

    assert state(hass, LUX) == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("message", "outcome"),
    [
        (entity_push(lux(7), revision=1), PushOutcome.STALE),
        (entity_push(lux(7), revision=5, boot="boot-0"), PushOutcome.OTHER_BOOT),
    ],
)
async def test_stale_and_other_boot_pushes_are_ignored(
    hass: HomeAssistant,
    client: MagicMock,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    message: EntityStateChangedWsMessage,
    outcome: PushOutcome,
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3)), revision=1))
    polls = client.get_apps.await_count
    caplog.set_level(logging.DEBUG, "custom_components.hassette.stream")

    await stream.send(hass, message)

    assert state(hass, LUX) == "3"
    assert client.get_apps.await_count == polls
    assert any(PUSH_SKIP_REASONS[outcome] in record.getMessage() for record in caplog.records)


async def test_push_for_an_entity_the_listing_lacks_fetches_a_listing(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    client.get_apps.return_value = listing(declaring(lux(3), make_declared("occupied", value=True)), revision=2)

    await stream.send(hass, entity_push(make_declared("occupied", value=True), revision=2))

    assert state(hass, OCCUPIED) == STATE_ON


@pytest.mark.parametrize(
    "message",
    [
        entity_push(make_declared("mode", "select"), revision=2),
        entity_push(lux(1), revision=2, app_key="bad-key"),
    ],
)
async def test_push_that_never_reaches_the_listing_fetches_nothing(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, message: EntityStateChangedWsMessage
) -> None:
    """An undeclared platform or an unusable app key is never in the held data, so refetching would loop."""
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    polls = client.get_apps.await_count

    await stream.send(hass, message)

    assert client.get_apps.await_count == polls


async def test_newer_status_fetches_a_listing(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    client.get_apps.return_value = listing(declaring(lux(3), status="stopped", status_revision=2))

    await stream.send(hass, status_push("stopped", revision=2))

    assert state(hass, LUX) == STATE_UNAVAILABLE


async def test_status_no_newer_than_the_listing_fetches_nothing(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3), status_revision=2)))
    polls = client.get_apps.await_count

    await stream.send(hass, status_push("stopped", revision=2))

    assert client.get_apps.await_count == polls
    assert state(hass, LUX) == "3"


@pytest.mark.parametrize(("app_key", "fetches"), [("new_app", 1), ("bad-key", 0)])
async def test_status_of_an_app_the_listing_lacks(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, app_key: str, fetches: int
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    polls = client.get_apps.await_count

    await stream.send(hass, status_push("running", revision=1, app_key=app_key))

    assert client.get_apps.await_count == polls + fetches


async def test_apps_changed_fetches_a_listing(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    client.get_apps.return_value = listing(declaring(lux(3)), make_app("doorbell"))

    await stream.send(hass, apps_changed())

    assert hass.states.get("switch.doorbell") is not None


async def test_older_listing_keeps_a_newer_pushed_value(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """A poll read before a push was applied must not roll its value back; status still follows the listing."""
    stream = await load(hass, client, config_entry, listing(declaring(lux(3)), revision=1))
    await stream.send(hass, entity_push(lux(7, attributes={"room": "den"}), revision=5))

    # Read at revision 3, before the push at 5.
    client.get_apps.return_value = listing(declaring(lux(4), make_declared("ping", "button")), revision=3)
    await poll(hass, freezer)

    assert current(hass, LUX).state == "7"
    assert current(hass, LUX).attributes["room"] == "den"
    # The entity set follows the listing.
    assert hass.states.get("button.motion_lights_living_room_ping") is not None

    # A listing as new as the push wins.
    client.get_apps.return_value = listing(declaring(lux(9)), revision=5)
    await poll(hass, freezer)
    assert state(hass, LUX) == "9"


async def test_push_during_a_poll_survives_the_poll(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """The merge reads the held data after the poll's HTTP await, so a push applied during it is kept."""
    stream = await load(hass, client, config_entry, listing(declaring(lux(3)), revision=1))
    release = asyncio.Event()

    async def slow_listing() -> AppListResponse:
        await release.wait()
        return listing(declaring(lux(4)), revision=2)

    client.get_apps.side_effect = slow_listing
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await asyncio.sleep(0)
    assert client.get_apps.await_count == 3

    await stream.queue_item(entity_push(lux(7), revision=5))
    assert state(hass, LUX) == "7"
    release.set()
    await settle(hass)

    assert state(hass, LUX) == "7"


async def test_listing_status_applies_alongside_a_kept_pushed_value(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3)), revision=1))
    await stream.send(hass, entity_push(lux(7), revision=5))

    client.get_apps.return_value = listing(declaring(lux(4), status="stopped", status_revision=2), revision=3)
    await poll(hass, freezer)

    assert state(hass, LUX) == STATE_UNAVAILABLE
    assert state(hass, SWITCH) == "off"


async def test_new_boot_takes_the_listing_whole_and_resets_marks(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3)), revision=1))
    await stream.send(hass, entity_push(lux(7), revision=50))

    # hassette restarted: its revisions start over, below the old boot's mark.
    client.get_apps.return_value = listing(declaring(lux(4)), revision=2, boot="boot-2")
    await poll(hass, freezer)
    assert state(hass, LUX) == "4"

    await stream.send(hass, entity_push(lux(5), revision=3, boot="boot-2"))
    assert state(hass, LUX) == "5"


async def test_instance_listed_without_entities_keeps_the_held_values(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3)), revision=1))
    await stream.send(hass, entity_push(lux(7), revision=2))

    unlisted = make_instance(entities=None)
    client.get_apps.return_value = listing(make_app(instances=[unlisted.model_dump()]), revision=4)
    await poll(hass, freezer)
    assert state(hass, LUX) == "7"

    # The listing carried no values for the instance, so its mark wasn't raised past the push.
    await stream.send(hass, entity_push(lux(8), revision=3))
    assert state(hass, LUX) == "8"


async def test_failed_resync_ends_the_stream_before_any_push_applies(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    await stream.send(hass, HassetteConnectionError("closed"))

    # The poll that restarts the stream succeeds, but the resync on connect gets a tolerated failure.
    calls = 0

    async def second_call_fails(*_args: Any) -> AppListResponse:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HassetteTimeoutError("slow")
        return listing(declaring(lux(3)))

    client.get_apps.side_effect = second_call_fails
    queued = stream.queue_item(entity_push(lux(7), revision=2))
    await poll(hass, freezer)
    await settle(hass)

    assert stream.connects == 2
    assert not stream_running(hass)
    assert not queued.done()
    assert state(hass, LUX) == "3"


async def test_poll_in_flight_at_connect_does_not_count_as_the_resync(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """A poll that started before the stream subscribed may have read an older listing, so it can't pass for it."""
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    hassette_stream = client.listen.await_args.kwargs["on_connected"].__self__
    await stream.send(hass, HassetteConnectionError("closed"))
    in_flight = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def in_flight_then_fail() -> AppListResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            in_flight.set()
            await release.wait()
            return listing(declaring(lux(3)))
        raise HassetteTimeoutError("slow")

    client.get_apps.side_effect = in_flight_then_fail
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await in_flight.wait()
    # The stream connects while that poll waits on hassette; its resync queues behind the poll.
    hassette_stream.launch()
    await asyncio.sleep(0)
    release.set()
    # The resync runs in the stream's own task, which settle() doesn't wait for. Give it a bounded number of
    # loop turns to end: a stream that wrongly survives the resync never does.
    for _ in range(50):
        if hassette_stream.task.done():
            break
        await asyncio.sleep(0)
    await settle(hass)

    assert stream.connects == 2
    assert calls == 2
    assert not stream_running(hass)


async def test_dropped_stream_restarts_only_after_a_good_poll(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    await stream.send(hass, HassetteConnectionError("closed"))
    assert stream.connects == 1

    client.get_health.side_effect = HassetteConnectionError("refused")
    await poll(hass, freezer)
    await poll(hass, freezer)
    assert stream.connects == 1

    client.get_health.side_effect = None
    await poll(hass, freezer)
    await settle(hass)
    assert stream.connects == 2

    await stream.send(hass, entity_push(lux(7), revision=2))
    assert state(hass, LUX) == "7"


async def test_repeated_drops_are_logged_once_until_the_stream_connects(
    hass: HomeAssistant,
    client: MagicMock,
    caplog: pytest.LogCaptureFixture,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    def drop_logs() -> int:
        return sum("stream ended" in record.getMessage() for record in caplog.records)

    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    # The stream drops, and each restart after a poll fails the same way: one log line.
    client.listen.side_effect = HassetteConnectionError("refused")
    await stream.send(hass, HassetteConnectionError("closed"))
    await poll(hass, freezer)
    await poll(hass, freezer)
    assert drop_logs() == 1

    # A restart that connects clears the record, so the next drop is logged again.
    client.listen.side_effect = stream.listen
    await poll(hass, freezer)
    await stream.send(hass, HassetteConnectionError("closed"))
    assert drop_logs() == 2


async def test_apply_bug_is_logged_with_its_traceback(
    hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    stream = await load(hass, client, config_entry, listing(declaring(lux(3))))
    coordinator = config_entry.runtime_data
    coordinator.apply_entity_push = MagicMock(side_effect=RuntimeError("bug"))

    await stream.send(hass, entity_push(lux(7), revision=2))

    assert not stream_running(hass)
    assert any(record.exc_info and "stream failed" in record.getMessage() for record in caplog.records)


async def test_unload_cancels_the_stream(hass: HomeAssistant, client: MagicMock, config_entry: MockConfigEntry) -> None:
    await load(hass, client, config_entry, listing(declaring(lux(3))))
    assert stream_running(hass)

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not stream_running(hass)
