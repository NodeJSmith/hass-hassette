"""Receive hassette's ``/ws`` pushes, so declared entity values and app status reach Home Assistant as they change.

The poll stays the source of truth for everything the stream doesn't carry (health, devices, removing
undeclared entities), and stands in for a reconnect loop: a stream that ends is started again after the next
good poll, so it never retries against a hassette the poll can't reach or a token the poll found rejected.
"""

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from hassette_client import HassetteClientError, ResponseValidationError
from hassette_wire import (
    AppsChangedWsMessage,
    AppStatusChangedData,
    AppStatusChangedWsMessage,
    EntityStateChangedData,
    EntityStateChangedWsMessage,
)
from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .const import SCAN_INTERVAL
from .coordinator import PUSH_SKIP_REASONS, HassetteConfigEntry, HassetteCoordinator, PushOutcome

_LOGGER = logging.getLogger(__name__)

type StreamMessage = EntityStateChangedWsMessage | AppStatusChangedWsMessage | AppsChangedWsMessage

MESSAGE_TYPES: tuple[type[StreamMessage], ...] = (
    EntityStateChangedWsMessage,
    AppStatusChangedWsMessage,
    AppsChangedWsMessage,
)

# The stream task's name, followed by the config entry's id.
STREAM_TASK_NAME = "hassette /ws stream"


@dataclass(slots=True)
class StreamHealth:
    """What the stream has done since setup, for the diagnostics download: the poll hides every stream failure."""

    last_connected: datetime | None = None
    last_message: datetime | None = None
    pushes_applied: int = 0
    # Entity pushes not applied, by the PushOutcome name that says why.
    pushes_ignored: Counter[str] = field(default_factory=Counter)
    last_error: str | None = None


class ResyncFailedError(Exception):
    """The listing fetched on connect wasn't applied, so pushes would apply against stale data."""


class HassetteStream:
    """One config entry's ``/ws`` subscription: at most one ``listen()`` at a time, restarted after a good poll."""

    def __init__(self, entry: HassetteConfigEntry, coordinator: HassetteCoordinator) -> None:
        self.entry = entry
        self.coordinator = coordinator
        self.task: asyncio.Task[None] | None = None
        # The coordinator's last_applied_fetch when the stream last looked. A restart waits for a later one.
        self.seen_fetch = coordinator.last_applied_fetch
        # Whether a connect resync has succeeded since setup: until then the stream has never worked, and an
        # ending is a warning.
        self.has_resynced = False
        # The exception types a stream ended with since a resync last succeeded, so each one is logged once.
        self.logged_failures: set[str] = set()
        # A message this integration can't read is reported once per setup: reconnecting doesn't fix it.
        self.reported_unreadable = False
        self.health = StreamHealth()

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    @callback
    def start(self) -> None:
        """Connect now, and again after each good poll whenever the stream isn't running, until the entry unloads."""
        self.entry.async_on_unload(self.coordinator.async_add_listener(self.restart_after_good_poll))
        self.launch()

    @callback
    def launch(self) -> None:
        # Eager, which is safe because listen() suspends connecting before it awaits on_connected: the resync's
        # coordinator listeners then find this task held, so restart_after_good_poll doesn't start a second one.
        self.task = self.entry.async_create_background_task(
            self.coordinator.hass, self.run(), name=f"{STREAM_TASK_NAME} {self.entry.entry_id}"
        )

    @callback
    def restart_after_good_poll(self) -> None:
        """A coordinator listener: restart a stream that ended once a listing was applied after it last looked.

        Listeners also fire on tolerated failures, failed polls and pushes, none of which apply a listing, so
        only a listing actually fetched restarts the stream.
        """
        current = self.coordinator.last_applied_fetch
        if self.running:
            self.seen_fetch = current
            return
        if current == self.seen_fetch:
            return
        self.seen_fetch = current
        self.launch()

    async def run(self) -> None:
        """Listen until the stream ends, then log and record why; the next good refresh starts it again."""
        try:
            await self.coordinator.client.listen(self.on_message, types=MESSAGE_TYPES, on_connected=self.on_connected)
        except ResponseValidationError as exc:
            self.record_error(exc)
            self.log_unreadable_once(exc)
        except (HassetteClientError, ResyncFailedError) as exc:
            self.record_error(exc)
            self.log_failure_once(exc)
        except Exception as exc:
            self.record_error(exc)
            # A bug applying a message. Logged on every cycle: the same message would end the next stream too.
            _LOGGER.exception("hassette's /ws stream failed; it restarts after the next good refresh")

    def record_error(self, exc: Exception) -> None:
        self.health.last_error = f"{type(exc).__name__}: {exc}"

    async def on_connected(self) -> None:
        """Fetch a listing before any push applies, and end the stream unless that fetch's listing was applied.

        ``listen()`` holds messages that arrive meanwhile, so nothing changed between subscribing and the
        listing is lost. ``async_refresh`` waits for any poll in flight, and that poll may have read its listing
        before the subscription, so only a fetch started after this point counts.
        """
        _LOGGER.debug("Connected to hassette's /ws stream; fetching a listing")
        self.health.last_connected = dt_util.utcnow()
        before = self.coordinator.fetches_started
        await self.coordinator.async_refresh()
        if self.coordinator.last_applied_fetch <= before:
            raise ResyncFailedError("the listing fetched on connect wasn't applied")
        # The stream works: say so the first time and after a logged failure, and re-arm the failure log.
        if not self.has_resynced or self.logged_failures:
            _LOGGER.info("Receiving hassette's live updates")
        self.has_resynced = True
        self.logged_failures.clear()

    async def on_message(self, message: StreamMessage) -> None:
        """Apply an entity value, or ask for a listing when the message says the held one is out of date.

        Nothing here awaits a refresh: an awaited request runs the whole refresh inline and would stall the
        stream reader for a round trip.
        """
        self.health.last_message = dt_util.utcnow()
        match message:
            case EntityStateChangedWsMessage(data=data):
                self.on_entity_push(data)
            case AppStatusChangedWsMessage(data=data):
                self.on_status_push(data)
            case AppsChangedWsMessage():
                self.request_refresh("hassette's apps changed")

    def on_entity_push(self, push: EntityStateChangedData) -> None:
        """Apply a pushed value; for one the held listing lacks, fetch a listing that may have it.

        A newly started instance's first values can arrive before any listing that has it. An app key outside
        the alphabet or a platform this integration doesn't have never reaches the held data, so fetching for
        one would repeat on every such push without ever applying it.
        """
        outcome = self.coordinator.apply_entity_push(push)
        if outcome is PushOutcome.APPLIED:
            self.health.pushes_applied += 1
            return
        self.health.pushes_ignored[outcome.name.lower()] += 1
        _LOGGER.debug(
            "Not applying %s on %s/%s: %s",
            push.entity.key,
            push.app_key,
            push.instance_name,
            PUSH_SKIP_REASONS[outcome],
        )
        if outcome is PushOutcome.UNKNOWN_TARGET and self.coordinator.usable_app_key(push.app_key):
            self.request_refresh(f"{push.app_key}/{push.instance_name} pushed an entity the listing lacks")

    def on_status_push(self, push: AppStatusChangedData) -> None:
        """Fetch a listing when an instance's status changed after the held listing was read.

        The fetched listing carries the new status, and a newly running instance's declarations with it.
        ``status_revision`` is the app's: it rises with any change to the app's or its instances' status, so
        the push's is compared with the held app's.
        """
        if not self.coordinator.usable_app_key(push.app_key):
            return
        app = self.coordinator.data.apps.get(push.app_key)
        if app is None:
            self.request_refresh(f"{push.app_key} changed status but isn't in the held listing")
        elif push.status_revision > app.status_revision:
            self.request_refresh(f"{push.app_key}/{push.instance_name} is now {push.status}")
        else:
            _LOGGER.debug("Not applying %s/%s's status: the held listing is as new", push.app_key, push.instance_name)

    @callback
    def request_refresh(self, reason: str) -> None:
        """Ask for a debounced refresh without waiting for it.

        The debouncer can fold the request into a poll already in flight, whose listing may predate the push.
        The next scheduled poll then brings the change, up to one poll interval later.
        """
        _LOGGER.debug("Refreshing from hassette: %s", reason)
        self.entry.async_create_background_task(
            self.coordinator.hass,
            self.coordinator.async_request_refresh(),
            name=f"hassette refresh after a /ws push {self.entry.entry_id}",
        )

    def log_unreadable_once(self, exc: ResponseValidationError) -> None:
        """Warn about a message this integration can't read, once per setup: reconnecting doesn't fix it.

        Unlike an unreadable poll response (``log_poll_error_once``), this never re-arms: the stream keeps
        reconnecting into the same message, while the poll's error clears when a response reads again.
        """
        if self.reported_unreadable:
            return
        self.reported_unreadable = True
        _LOGGER.warning(
            "hassette sent a live update this integration can't read (%s); values still update with each poll. "
            "Update the integration if this repeats",
            exc,
        )

    def log_failure_once(self, exc: Exception) -> None:
        """Log why a stream ended, once per exception type until a connect resync next succeeds.

        Before the stream has ever worked this session, an ending is a warning, since it is usually something no
        reconnect fixes, such as a proxy that doesn't pass WebSockets. A drop of a stream that worked is info. A
        later ending of an already logged type, even with a different message, isn't logged.
        """
        kind = type(exc).__name__
        if kind in self.logged_failures:
            return
        self.logged_failures.add(kind)
        if self.has_resynced:
            _LOGGER.info(
                "hassette's /ws stream ended (%s); values update with each refresh, and it reconnects after the next "
                "good one",
                exc,
            )
        else:
            _LOGGER.warning(
                "Can't receive hassette's live updates (%s); values update with each %d s poll instead. If this "
                "repeats and hassette is behind a proxy, the proxy has to pass WebSocket connections through",
                exc,
                SCAN_INTERVAL.total_seconds(),
            )
