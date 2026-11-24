# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: A stream that connects and then drops costs an extra listing on every poll

Status: open
Recorded: 2026-11-23 (73-ws-push)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- custom_components/hassette/stream.py

Issue:
Each restart runs the connect resync, a full health and apps fetch, on top of the poll that triggered the restart. So a stream that drops right after connecting costs about two listings per 30 s. While the resync runs, `listen()` doesn't read the socket, so a slow hassette's lossless-or-close queue can fill and cut the stream on every cycle.

Why deferred:
The cost is capped at about two listings per 30 s, and D3 already weighed backoff against using the poll as the reconnect clock. The user accepted this at the ship-time challenge.

Recommended follow-up:
If a user reports load from it, skip restarts for a few good polls after a stream that ended soon after connecting.

Acceptance criteria:
- A stream that drops within seconds of each connect doesn't restart on every poll.

## KI-002: Every push recomputes every entity, and nothing limits how often apps push

Status: open
Recorded: 2026-11-23 (73-ws-push)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- custom_components/hassette/coordinator.py

Issue:
`apply_entity_push` calls `async_update_listeners()` once per push, so every declared entity recomputes and each platform's `add_new` walk runs. A burst of pushes, such as the backlog held during the resync, is processed back to back.

Why deferred:
D1 ratified the per-push cost, the cited peers also write once per push, and no known app pushes fast enough for it to matter. The user accepted this at the ship-time challenge.

Recommended follow-up:
If an app sets values several times a second or declares hundreds of entities, apply each push at once but notify listeners once per event-loop tick.

Acceptance criteria:
- A burst of N pushes in one loop tick triggers one listener update.

## KI-003: Stream-triggered refreshes are limited only by HA's debouncer

Status: open
Recorded: 2026-11-23 (73-ws-push)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- custom_components/hassette/stream.py

Issue:
`apps_changed`, a newer status delta, and a push for an unknown target each call `async_request_refresh()`. The only limit on the resulting fetches is HA's 1 s debouncer (`ACTION_REFRESH_COOLDOWN`), and this integration doesn't test that limit.

Why deferred:
D4 accepts the debouncer's coalescing, and the critic found no refresh loop. The user accepted this at the ship-time challenge.

Recommended follow-up:
If `request_refresh` ever moves off `async_request_refresh`, add a test that a burst of triggers fetches at most once per cooldown.

Acceptance criteria:
- A test pins at most one refresh per cooldown for a burst of stream triggers.
