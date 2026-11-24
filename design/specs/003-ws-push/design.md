# Design: Push declared entity state from hassette's /ws

**Date:** 2026-11-23
**Status:** built
**Mode:** sketch
**Issues:** #73
**Parent:** NodeJSmith/hassette `design/specs/155-hacs-app-entities/design.md` (Breakdown issue 6)
**Prior art:** NodeJSmith/hassette `design/research/2026-11-23-push-poll-hybrid/prior-art.md`, NodeJSmith/hassette `design/research/2026-11-23-push-stream-failure-reporting/prior-art.md`, NodeJSmith/hassette `design/research/2026-11-22-python-ws-client-subscriptions/prior-art.md`, NodeJSmith/hassette `design/research/2026-11-16-app-declared-ha-entities/prior-art.md`

## Summary

Today hass-hassette learns declared entity values and app status only from its 30 s poll of `GET /api/health`
and `GET /api/apps` (`HassetteCoordinator._async_update_data` in `custom_components/hassette/coordinator.py`).
A sensor an app `set()`s can therefore reach HA up to 30 s late, and so can a toggled switch whose handler
outlasts the action refresh. This change keeps the poll and adds a subscription to hassette's `/ws` through
`hassette-client` `HassetteClient.listen()`. Pushed `entity_state_changed` values are applied to the
coordinator's data within a second, a newer `app_status_changed` or an `apps_changed` requests a refresh (D2, D4),
and every (re)connect resyncs from the listing. A dropped stream falls back to polling.

In scope: the stream task and its reconnect (D3), applying entity pushes, the per-entity revision
ordering spec 002 D3 deferred here, status pushes (D2), the `apps_changed` refetch, and the README's "Updates"
section. Out of scope: any hassette-side change (the wire and `listen()` shipped in hassette 0.66.0), the
end-to-end test and the README's declared-entities walkthrough (hassette#4221), a different poll interval, and
any change to unique_ids, devices or the config flow.

Where each new piece lives:

- **The stream task and its reconnect:** a new module `custom_components/hassette/stream.py`. It owns:
  - running `listen()` on the coordinator's polling client (`coordinator.client`, default request timeout) as a
    config-entry background task (`entry.async_create_background_task`, so unload cancels it), holding the task
    handle so "not running" is `task.done()` and no second `listen()` can start;
  - the `on_connected` resync and the restart rule (both D4), through a coordinator listener registered with
    `entry.async_on_unload`, plus the last `coordinator.last_success` it has seen;
  - routing: `entity_state_changed` to the coordinator's entity-push method; a newer `app_status_changed`
    (compared with the held app's `status_revision`, D2), an `apps_changed`, and a same-boot push for a target
    the held data lacks (D4, with its skip filter) to a refresh request scheduled with
    `entry.async_create_background_task`, so unload cancels any still running;
  - the logging of connects, drops and pushes not applied (Posture).
  `async_setup_entry` in `__init__.py` starts it after `async_forward_entry_setups`, so the platforms' listeners
  exist before the first push.
- **The revision marks:** three new fields on `HassetteData` in `coordinator.py`: the listing's `boot_id`, the
  listing's `entity_revision`, and the revision of each entity a push replaced since that listing (keyed by
  app, instance, key and platform; ship-time challenge, D5). They describe the data they
  sit beside, so the tolerated-failure path that returns `self.data` keeps them consistent with no extra step,
  and a push replaces data and marks together.
- **The apply rules:** two methods on `HassetteCoordinator`: one that merges a fresh listing with the held data
  (D5, called from `_async_update_data`), and one that applies an entity push (D1) and tells the caller when its
  app, instance or `(key, platform)` isn't in the held data, so `stream.py` can request a refresh. Both build a
  new `HassetteData` (pydantic `model_copy` on the touched `AppSummary` and `AppInstance`) rather than mutating
  the held one. Status deltas and `apps_changed` change no data directly (D2, D4).
- **Entities:** unchanged. `HassetteDeclaredEntity` (`declared.py`) and the v0.1 app entities (`entity.py`)
  already read everything from `coordinator.data`, so a value or status that lands there reaches them.

## Posture

- **Failure handling:** loud failure, as in the parent. A dropped or failing stream degrades to polling and
  never makes an entity unavailable on its own; availability still follows the listing's health and the
  instance status (parent D9).
- **Compatibility:** nothing new to protect. unique_ids, devices and the config flow are unchanged, and the
  pinned `hassette-client==0.66.0` already has `listen()`.
- **Scope:** minimal: entity pushes, status pushes, the `apps_changed` refetch and the resync on every
  (re)connect. No UI and no new options.
- **Rollout:** ships outright, in the same hass-hassette release as #72 (release PR #78 is held for it).
- **Hardening:** project defaults. The stream counts as working once a connect resync succeeds. A stream
  ending before it has worked this session is a warning, once per exception type; a drop of a working stream
  is info, once per exception type; the record re-arms when a resync succeeds, not on connect. A message the integration can't
  read is a warning once per setup, never re-armed (unlike an unreadable poll response, which re-arms on a good
  poll), since the stream reconnects into the same message. (Revised at the ship-time challenge from "info, once until the stream next
  connects", which hid a stream that never works, such as behind a proxy without WebSocket support, and
  repeated every 30 s for one that connects and then fails; prior art: hassette
  `design/research/2026-11-23-push-stream-failure-reporting/prior-art.md`.)
  Each push that isn't applied is logged at debug with its reason (another boot, unknown target, stale
  revision). A diagnostics download with stream health is #81, not this change.
- **Altitude:** build; one session builds it in hass-hassette.

## Decisions

### D1: How does a pushed value reach the coordinator's data?

**Situation:** HA's documented push pattern is `coordinator.async_set_updated_data(new_data)`. On a polling
coordinator that call also cancels the scheduled poll and reschedules it a full `update_interval` later,
cancels any pending debounced refresh, and sets `last_update_success` to true (HA
`helpers/update_coordinator.py`, `async_set_updated_data`). An app that `set()`s a sensor more often than every
30 s would keep pushing the poll back, so the poll would never run. Parent D3 relies on that poll to repair a
lost delta and to re-read health, and `remove_undeclared` and the device updates run only in the poll. The
pending refresh it cancels is the one `run_command` requests after a press or toggle.

**Deciding factor:** the 30 s repair poll keeps running however often apps push.

| | A: Replace `coordinator.data` and call `async_update_listeners()` | B: `async_set_updated_data` | C: A separate overlay that entities read on top of `data` |
|---|---|---|---|
| Poll cadence under frequent pushes | Unchanged: 30 s | Never runs while pushes come faster than 30 s | Unchanged |
| Lost delta repaired (parent D3) | Within one poll | Not while the poll is starved | Within one poll, if the overlay is cleared on each listing |
| After-action refresh | Kept | Cancelled by any push in the cooldown | Kept |
| `last_update_success` | Left to the poll | Set true by a push even if the last poll failed | Left to the poll |
| New state | None beyond the marks | None | A second store, and every entity property reads two places |
| Peers | Teslemetry keeps its poll on its own timer "so stream pushes cannot postpone it" and writes `data` + `async_update_listeners()`; Watts fixed this exact starvation the same way (core PR #169365) | HA docs' push pattern, written for coordinators with no poll | Spec 160 D8's frontend store (per-delta map) |

**Recommendation:** A, because it is the only option that keeps parent D3's repair poll on its cadence
without a second store. The apply method lives on the coordinator, so the write happens in one place.
**Pick B instead if** you are fine with pushes postponing the poll indefinitely. **Pick C instead if** you want
pushed values kept apart from polled data, for example so a test can tell them apart.
**Reversibility:** easy.
**Ratified:** Chose replacing `coordinator.data` and calling `async_update_listeners()` from a coordinator method over `async_set_updated_data` or a separate overlay, to keep the 30 s poll on its cadence however often apps push, as Teslemetry and the Watts fix do, accepting that `last_update_success` and availability stay with the poll, that every push recomputes every entity and repeats each platform's `add_new` walk (as the cited peers' writes do), and that a push hassette fails to build after bumping the revision is lost until the next poll (no known trigger; queue overflow closes the stream instead).

### D2: What does a pushed `app_status_changed` do?

**Situation:** parent D9 makes a declared entity available only while its instance is `running`, and Breakdown
issue 6 says to apply `app_status_changed` to availability. Each message carries the instance's new `status`,
the app's rollup `app_status`, a `status_revision`, and the failure's `exception` fields. `AppSummary` carries
the `status_revision` it was read at, which covers `status` and every instance's `status`, `error_message` and
`needs_restart` (hassette `wire/src/hassette_wire/apps.py`). It does not cover the app-level `error_message`,
which the v0.1 status sensor shows. Spec 160 D3: apply a delta only when its `status_revision` is strictly
greater than the summary's. Spec 159 D4: a declaration commit sends nothing on `/ws`, so a newly running
instance's new declarations and `on_initialize` values reach HA only through a listing, "unless hass-hassette
(#73) refetches on the `running` status". hassette commits declarations before it sets `running`
(`AppLifecycleInstances`, start path).

**Deciding factor:** status changes reach every entity in about a second, with the least merge code.

| | A: Fold the delta into the held `AppSummary` | B: A newer delta requests a refresh |
|---|---|---|
| What happens | Copy the app with the instance's `status`, `needs_restart` and error fields, `app_status` and `status_revision` from the delta. A later listing whose `status_revision` is older keeps those fields and takes the rest | When `status_revision` is greater than the held app's, call `async_request_refresh()` (the debounced, serialized refresh actions already use); the listing it reads carries the change |
| Latency | Immediate | One health and apps round trip (milliseconds on a LAN) |
| New merge code | Per-app status merge on every listing, and a mapping from `exception` to `error_message` | No status merge code: the listing taken after the delta already contains it (entity values still merge, D5) |
| v0.1 app entities (status sensor, control switch) | Follow the delta, except the app-level `error_message`, which waits for the next poll | Follow, including `error_message` |
| Newly running instance's declarations and `on_initialize` values | Wait up to 30 s, unless a separate refetch on `running` is added | Arrive with the refresh, since the commit precedes `running` |
| hassette shutting down | Entities go unavailable as each `stopping` delta arrives | Same, if the refresh gets an answer before hassette's web API stops. Otherwise poll tolerance keeps the last data for up to 45 s, as today |
| Traffic | None | One listing per status change, coalesced by the 1 s cooldown; status changes are rare (starts, stops, reloads) |
| Peers | Spec 160 D8's frontend, which avoids refetching per message across many browser tabs | aiounifi's HA consumer refetches on recovery; spec 160 already makes `apps_changed` a refetch signal |

**Recommendation:** B, because a status change is rare enough that refetching costs nothing, it needs no merge
code, it keeps every entity consistent with the listing (the app-level error included), and it delivers a newly
running instance's declarations in the same step. It departs from the frontend's fold-in approach, which exists
to avoid refetch traffic from every open tab. HA is one client.
**Pick A instead if** you want availability to flip without waiting on an HTTP answer, especially while hassette
is shutting down, and accept the merge code plus a separate refetch on `running`.
**Reversibility:** easy.
**Ratified:** Chose having a delta whose `status_revision` is greater than the held app's request a debounced refresh over folding it into the held `AppSummary`, to get every entity (v0.1 ones and the app-level error included) consistent with the listing with no status merge code, and to deliver a newly running instance's declarations and `on_initialize` values in the same step (settling spec 159 D4's open refetch), accepting one listing round trip per status change and, if hassette's web API stops before answering during shutdown, today's tolerance window before entities go unavailable.

### D3: How does the stream reconnect?

**Situation:** `listen()` never reconnects. When the stream ends it raises, and the caller decides (spec 159
D5). The integration already polls every 30 s, and that poll tells it whether hassette is reachable and the
token works (an auth failure starts reauth through the poll). Peers: zwave_js, music_assistant and matter
reload the whole config entry when their stream ends. HA's unifi integration runs its own retry loop (15, 30,
60 s with ±20% jitter). aioesphomeapi ships an opt-in backoff helper. Each of those peers has no poll, so the
stream is their only source of truth.

**Deciding factor:** the stream comes back without new timing machinery, and never fights the poll.

| | A: Restart on the next successful poll | B: Own backoff loop | C: Reload the config entry |
|---|---|---|---|
| Push gap after a drop | Up to 30 s, during which the poll keeps data current | Seconds to a minute | The reload's duration, with entities torn down and set up again |
| Attempts while hassette is down | None: polls fail, so nothing restarts it | One per backoff step | Repeated setup failures and retries |
| `/ws` broken but REST fine (a proxy without WebSocket support) | One failed attempt per poll, logged once | One per backoff step, logged once | A reload loop |
| Bad token | The poll's reauth handles it; no stream attempts until a poll succeeds | Retries a rejected token until the loop is told to stop | Reauth through setup |
| New code | A listener on the coordinator that starts the stream after a good poll (what counts as good is D4) | Delays, jitter, a reset-after-stable rule, auth handling | A reload call |
| Peers | None exactly; the poll stands in for aiounifi's recovery callback | unifi, aioesphomeapi, aiohue | zwave_js, music_assistant, matter |

**Recommendation:** A, because the poll already answers "is hassette reachable with this token", so starting
the stream from a good poll needs no delays or auth handling, and the 30 s gap is the same degradation the
posture accepts. It departs from the peers because each of them has no poll to lean on.
**Pick B instead if** you want pushes back within seconds after a brief network blip. **Pick C instead if**
you'd rather rebuild everything from scratch on any drop.
**Reversibility:** easy.
**Ratified:** Chose restarting the stream after the next successful poll whenever it isn't running over an own backoff loop or reloading the config entry, to bring the stream back with no delays, jitter or auth handling because the poll already proves hassette is reachable with a valid token, accepting a push gap of up to 30 s after a drop while the poll keeps data current, and that a pushed message tripping an apply bug ends the stream on every reconnect, logging each 30 s cycle, until the bug is fixed.

### D4: When does the stream fetch a listing, and how does it know the fetch landed?

**Situation:** the stream fetches a listing on connect (`on_connected`), on a newer status delta (D2), on
`apps_changed`, and, so a newly started instance's first values aren't lost, on a same-boot push for an app,
instance or `(key, platform)` the held listing lacks. It restarts after a good poll (D3). HA's refresh
machinery was built for "refresh soon, any refresh will do", and its signals mean less than they look like:
- `Debouncer.async_call` runs the first request inline in the caller's task (`helpers/debounce.py`), so an
  awaited `async_request_refresh()` in `on_message` stalls the stream reader for a whole health and apps round
  trip.
- A request made while a poll holds the lock can be coalesced into that poll, or dropped when its timer fires
  while the lock is still held.
- Listeners fire on tolerated failures, on failure flips, on action refreshes and on every push (D1).
- A tolerated transient failure returns `self.data` with `last_update_success` still true
  (`coordinator.py` `_async_update_data`).
The one signal that proves a fresh listing was fetched and applied is `coordinator.last_success` advancing,
which `_async_update_data` sets only on its real-success path.

**Deciding factor:** the fetch whose silent failure would break correctness is guaranteed, and nothing else
gains state.

| | A: Guaranteed connect resync, best-effort other triggers | B: Guarantee every trigger |
|---|---|---|
| Connect resync | `on_connected` awaits `coordinator.async_refresh()` (locked, so it reads after any in-flight poll), then raises unless `last_success` advanced, ending the stream so no push applies against stale data | Same |
| Other triggers (newer status delta, `apps_changed`, unknown same-boot target) | Schedule `async_request_refresh()` as a background task; may coalesce or be dropped behind a slow poll, healing at the next poll | Record a pending generation and re-request after each refresh until a listing covers it |
| Lag in the rare slow-poll case | Up to 30 s | About one round trip |
| Restart (D3) | Only once `last_success` advanced since the stream last looked | Same |
| Stream reader blocking | Never | Never |
| New state | The task handle and the last `last_success` seen | Those, plus the pending generation and the re-request loop |
| Peers | WLED requests a coordinator refresh when its WS ends; peers accept the debouncer's coalescing | No peer found |

Unknown-target refreshes skip app keys `usable_app_key` rejects (for status deltas and entity pushes) and
platforms outside `DECLARED_PLATFORMS` (for entity pushes), which never reach the held data, so they can't loop.
A status delta for a usable app the held data lacks is an unknown target too. A boot change needs no trigger of its own: every message on
one socket comes from one hassette process, and the connect resync fetches after subscribing, so the held
listing is from the socket's boot or newer before any message applies.

**Recommendation:** A, because only the connect resync breaks correctness if it silently fails; every other
trigger only runs late, and the poll heals it.
**Pick B instead if** your hassette host is slow enough that polls regularly take over a second.
**Reversibility:** easy.
**Ratified:** Chose a guaranteed connect resync (locked `async_refresh()`, then raise unless `last_success` advanced) with best-effort background refresh requests for a newer status delta, `apps_changed` and same-boot pushes for unknown targets (excluding unusable app keys and undeclared platforms), and restarting the stream only once `last_success` advanced, over guaranteeing every trigger with a pending generation, so the one fetch whose silent failure would apply pushes against stale data can't fail silently and the stream reader never blocks, accepting that a trigger coalesced into or dropped behind a slow poll waits up to 30 s for the next poll.

### D5: How does a fresh listing merge with the held data?

**Situation:** the marks order declared entity values only. A listing also carries each
instance's status, `needs_restart`, error fields, `in_current_config`, the app-level fields and the
declarations, which no mark orders. When a poll's HTTP await overlaps a push, the listing can be older than an
entity value the push applied. Taking the listing's instance whole loses that value; keeping the held instance
whole loses the fresher status the listing carries, which D2 relies on. HA assigns
`self.data = await self._async_update_data()` (`helpers/update_coordinator.py`), so a merge that reads
`self.data` before an await could merge against data a push replaced meanwhile.

**Deciding factor:** every field comes from the newest source that orders it, with the least merge code.

| | A: Per-field merge | B: Per-instance whole swap |
|---|---|---|
| Status under a racing push | Always from the newest listing | The racing listing's fresher status dropped until the next refresh (up to 30 s) |
| Entity values | Ordered by marks (parent D3) | Ordered by marks |
| Code | Overlay held entity values onto the listing's instance | Pick the held or listed instance |
| Mark update rule | An applied listing drops each mark at or below its revision; a higher held mark stays | Same rule needed |
| Matches D2 ("status comes from the listing") | Yes | Only when no push raced |
| Peers | Teslemetry merges local-poll fields into `data` (`self.data = merged`); no peer found that orders pushes against listings by revision | Same |

**Recommendation:** A, because it is the only reading under which every field comes from the newest source
that orders it, which D2's premise needs.
**Pick B instead if** you want a whole-instance choice and accept a racing listing's status waiting up to 30 s.
**Reversibility:** easy.
**Ratified:** Chose a per-field merge (each instance takes everything from the listing except a declared entity a push replaced at a revision newer than the listing's `entity_revision`, which is taken whole (declaration, value and attributes) from the held data, matched by `(key, platform)`, with its mark; the entity set itself follows the listing; a push applies only when its revision exceeds both the held listing's and that entity's own mark; a new `boot_id` takes the listing whole and drops the marks; the merge reads `self.data` and builds the result after the last await, in one synchronous step) over swapping whole instances, so every field comes from the newest source that orders it as D2 assumes, accepting a per-entity overlay as the integration's one piece of merge code. Amended at the ship-time challenge: the marks were per instance, which let one pushed entity hold back a racing listing's newer declarations and values for the instance's other entities (declaration commits raise the revision but send nothing on `/ws`), and an `entities` `None` clause was dropped because `GET /api/apps` always fills `entities`.

## Assumed

- The parent's decisions this issue implements hold as ratified: D3 (subscribe to `/ws`; the 30 s poll keeps
  applying entity state after push is live; a pushed value applies only when its revision is newer than the last
  applied for that instance; a listing applies when its revision is equal or newer; subscribe before fetching on
  every (re)connect; marks are dropped when the boot identifier changes) and D9 (a declared entity is available
  only while its instance is `running` and the trusted-list gate holds). Evidence: hassette
  `design/specs/155-hacs-app-entities/design.md`.
- Spec 002 D3 moved parent Breakdown issue 5's "D3 (revision)" share to this issue: #72 shipped no revision
  ordering, and `_async_update_data` replaces the data wholesale. Evidence: `design/specs/002-app-declared-entities/design.md` D3;
  `coordinator.py` `_async_update_data`.
- Parent D9's accepted limitation ("pushed `app_status_changed` carries no revision") no longer holds: spec 160
  added `status_revision` to the message and to `AppSummary`. Parent D3's "revision" is `entity_revision` on the
  wire (spec 160 D5). The parent isn't edited; this ledger records both. Evidence: hassette
  `wire/src/hassette_wire/ws.py` (`AppStatusChangedData`), `wire/src/hassette_wire/apps.py` (`AppSummary`,
  `AppListResponse`); hassette `design/specs/160-app-status-revision/design.md` D5.
- `AppListResponse` carries one `boot_id` and one hassette-wide `entity_revision`, which reflects every
  declaration and value change up to it. `entity_revision` values compare only within one `boot_id`, and a
  listing with a new `boot_id` discards every mark held. Evidence: `AppListResponse` field docs.
- An `entity_state_changed` push carries `boot_id`, `entity_revision`, `app_key`, `instance_name` and the
  entity's full `DeclaredEntity` (name, value, attributes). It applies only when its `boot_id` equals the held
  listing's and its `entity_revision` is greater than both the held listing's and that entity's own mark (D5). A push from another boot is ignored,
  not treated as a restart, because a restart during the `on_connected` fetch can leave old-boot messages queued
  on the socket. Evidence: `HassetteClient.listen` docstring; `EntityStateChangedData`.
- Only the listing creates or removes entities. A push for an app, instance or `(key, platform)` the held
  listing doesn't have isn't applied (D4 says when it requests a refresh). Declaration commits send nothing on `/ws`. Evidence: hassette spec 159 D4;
  `EntityStateChangedData` docs.
- hassette's `/ws` is lossless-or-close: a client whose queue fills is cut off (its backlog replaced by a
  too-slow signal) instead of having messages dropped, so a live stream never silently misses a delta and a drop
  surfaces as the stream ending. This is the push-only peers' model (HA `websocket_api`, python-matter-server,
  Music Assistant), so the resync on every connect is the repair and the 30 s poll is kept for what the stream
  doesn't carry (health, device fields, `remove_undeclared`, app-level fields), as unifiprotect, reolink and shelly
  keep theirs. Parent D3's "a lost delta heals within one poll" remains true but is now a backstop, not a designed
  path. Evidence: hassette `RuntimeQueryService.put_to_clients` (#4420, closing #4217, in v0.66.0);
  `design/research/2026-11-23-push-poll-hybrid/prior-art.md` (Patterns 1 and 4).
- hassette sends entity messages to each `/ws` client in `entity_revision` order, after the commit. So pushes
  never arrive out of order among themselves, and only a listing read before an applied push can be older than
  it. Evidence: hassette spec 159 D2.
- `status_revision` strictly increases within a run and, being seeded from the server clock, across restarts.
  A delta applies only when it is strictly greater than the summary's, so ties go to the summary.
  `apps_changed` means "refetch": blocking, config changes and registration send no `app_status_changed`.
  Evidence: `AppSummary.status_revision` docs; hassette spec 160 D1, D3.
- `listen()` awaits `on_connected` before passing on any message, and holds messages that arrive during it, so
  fetching the listing there is subscribe-before-fetch. It takes a required `types=` of `hassette_wire` message
  classes and skips every other type unparsed. It raises when the stream ends (`HassetteConnectionError`,
  `AuthenticationError`, `ResponseValidationError` and others), including when `on_message` raises. Cancelling
  its task closes the socket. Evidence: `HassetteClient.listen` docstring; hassette spec 159 D5, D8, D9.
- hassette commits an instance's declarations, with its `on_initialize` values, before it sets the instance to
  `running`. Evidence: hassette `src/hassette/core/app_lifecycle_instances.py` (start path: `_commit_declarations`, then
  `set_app_status(..., RUNNING)`).
- Every coordinator refresh is serialized under the debouncer's lock: the scheduled poll, `async_refresh` and
  the debounced `async_request_refresh`. A refresh that starts before a push was applied can still finish after
  it, because `_async_update_data` awaits HTTP. Evidence: HA `helpers/update_coordinator.py` (`async_refresh`,
  `_async_refresh`), `helpers/debounce.py`; spec 002 D3.
- HA pins aiohttp 3.14.5, which resets the WebSocket heartbeat on any received frame, so `listen()`'s warning
  about callbacks that block for about 45 s doesn't apply. Callbacks stay non-blocking anyway: each one only
  updates data and listeners, or schedules a refresh as a background task (D4), since an awaited
  `async_request_refresh()` would run the whole refresh inline. Evidence: `homeassistant/package_constraints.txt`
  (`aiohttp==3.14.5`); hassette spec 159 D6's accepted limitation, which records that the installed 3.14 resets the
  heartbeat on received frames; `listen` docstring.
- The pinned `hassette-client==0.66.0` includes `listen()`, and its server-version floor keeps the integration
  off a hassette too old to serve these messages. Evidence: `manifest.json`, `pyproject.toml`; hassette tag
  v0.66.0.
- The README's "Updates" section says the integration polls every 30 seconds and after every action. Evidence:
  `README.md`, "Updates".

## Build

- [x] Implementation and tests committed
- [x] Docs (README "Updates" and declared-entities bullet, CLAUDE.md layout)
- [x] Ship-time challenge

**Calls made during the build:**
- Fetch numbers stand in for "`last_success` advanced" (D4): each refresh numbers its fetch when it starts (`fetches_started`), and a refresh whose listing was applied records its number (`last_applied_fetch`), set with `last_success` after the data is built. The connect resync passes only when a fetch started after subscribing was applied, so a poll already in flight (which may have read its listing before the subscription) can't pass for it, and two good polls in one clock tick still count as two. The restart rule (D3) compares `last_applied_fetch` the same way.
- A failed connect resync raises `ResyncFailedError` and is logged with the client errors that end a stream (per the Hardening stance); any other exception out of `listen()` is an apply bug and is logged with its traceback on every cycle, as D3 accepts.
- A status delta for an app key `usable_app_key` rejects is dropped before its revision check, matching D4's skip filter for unknown targets.
