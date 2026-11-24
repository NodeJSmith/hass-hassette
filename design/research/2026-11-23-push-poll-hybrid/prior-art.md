---
topic: "Push stream plus poll in Home Assistant integrations: repairing lost messages"
date: 2026-11-23
status: Draft
---

# Prior Art: Push stream plus poll in Home Assistant integrations

## The Problem

hass-hassette (hass-hassette#73) subscribes to hassette's `/ws` for declared entity state while keeping its 30 s
REST poll. Parent ledger 155 D3 kept the poll as the repair path for a delta lost on a live stream, on the premise
that hassette's `/ws` drops messages to slow clients (#4217). That made the design a push-plus-repair-poll hybrid,
which no peer in the original survey ran. The question: is a push-plus-poll hybrid normal in HA, and should the
stream instead be lossless-or-reconnect, with resync on connect, like the push-only peers?

## How We Do It Today

- hassette 0.66.0 already closes slow `/ws` clients: `RuntimeQueryService.put_to_clients` cuts off a client whose
  queue (`WS_CLIENT_QUEUE_MAX`) is full, replaces its backlog with a `TOO_SLOW` signal, and logs and counts it
  (#4420, closing #4217, in tag v0.66.0). So a client never misses a message while its socket stays open. The
  #4217 premise behind the repair poll was stale by the time this pickup started.
- `hassette-client` `listen()` raises when the stream ends and its docstring tells the caller to fetch the
  listing in `on_connected` on every connect (spec 159 D5): resync on connect is already the contract.
- hass-hassette's `HassetteCoordinator` polls `/api/health` and `/api/apps` every 30 s. The poll also carries
  things the stream doesn't: health (`bootstrap_released`, version and schema checks), device-field updates,
  `remove_undeclared`, and the app-level fields `app_status_changed` doesn't cover.

## Patterns Found

### Pattern 1: Lossless-or-close server + reload/resync on any disconnect, no poll
- **Used by:** HA websocket_api, python-matter-server, music-assistant server (servers); matter, zwave_js, music_assistant, esphome (clients).
- **How it works:** server holds a bounded per-client queue (4096 HA, 512 matter/MA); overflow closes the client rather than drop a message. Client treats "connection ended" as the single failure signal and rebuilds from a full snapshot (entry reload in zwave_js/matter, `device_info_and_list_entities` in esphome). Liveness by WS/app ping (55 s HA/matter/zwave-js-python client, 25 s MA, 30 s zwave-js-server with outstanding-ping disconnect, esphome 4.5x keepalive).
- **Strengths:** one failure mode; no undetectable loss; no poll code or interval tuning; no poll-vs-push write race.
- **Weaknesses:** a slow client costs a full resync (zwave_js and matter reload the whole entry, which is heavy); a reconnect storm is possible if the client is persistently slow; needs bounded queue and a snapshot API on the server.
- **Example:** https://github.com/home-assistant-libs/python-matter-server (client_handler.py `_send_message`), https://github.com/home-assistant/core/blob/dev/homeassistant/components/websocket_api/http.py
- **Read:** websocket_api/http.py lines 215-240; python-matter-server `matter_server/server/client_handler.py`; `ma/.../websocket_client.py`; core `zwave_js/__init__.py` `client_listen`; core `matter/__init__.py` `_client_listen`; `esphome/manager.py` `_on_connect`; aioesphomeapi `connection.py` keepalive. zwave-js-server `src/lib/server.ts` for ping only.

### Pattern 2: Poll disabled while push is healthy, resync + resume on disconnect
- **Used by:** wled, sonos (fallback only), bond (alive check), teslemetry stream coordinators (no poll at all).
- **How it works:** WLED sets `update_interval = None` while the WS is connected and in `finally` restores it and requests a refresh. Sonos creates the fallback poll only after subscribing and cancels it on the first received event. Bond's recurring timer is a no-op when BPUP delivered a message in the last 70 s.
- **Strengths:** no steady-state poll cost; no poll write races; the poll exists only for the degraded case.
- **Weaknesses:** protects against stream death, not against silent loss on a live stream (a live WS that dropped one frame looks healthy). Sonos/Bond tie "healthy" to "any message seen recently", which a quiet device can fail.
- **Example:** https://github.com/home-assistant/core/blob/dev/homeassistant/components/wled/coordinator.py
- **Read:** core `wled/coordinator.py` lines 171-290; `sonos/speaker.py` lines 399-432 and 482-493, `sonos/const.py`; `bond/entity.py` lines 112-200 + bond-async `bpup.py`.

### Pattern 3: Resync only after a long enough gap (replay below it)
- **Used by:** hue v2 (aiohue).
- **How it works:** SSE with `last-event-id`; on reconnect, if the disconnect lasted over 60 s, `fetch_full_state()`; shorter gaps rely on the bridge replaying from the id.
- **Strengths:** avoids a full fetch on brief blips.
- **Weaknesses:** requires a server-side replay buffer with ids; it is exactly the sequence-number machinery hassette's /ws would need to add.
- **Example:** https://github.com/home-assistant-libs/aiohue/blob/main/aiohue/v2/__init__.py (`_handle_connect_event`)
- **Read:** aiohue `v2/controllers/events.py` 170-240, `v2/__init__.py` 250-275.

### Pattern 4: Push plus an always-on periodic poll as a repair/full-refresh path
- **Used by:** unifiprotect (900 s), reolink (60 s+), shelly (60 s, but only for fields the push lacks), homekit_controller (non-evented characteristics).
- **How it works:** unifiprotect runs `api.update()` every 15 minutes regardless of WS state; reolink's coordinator poll refreshes everything and renews the push subscription; shelly and homekit poll disjoint fields. In every case the poll timer is independent of the push writes.
- **Strengths:** bounded staleness whatever goes wrong; handles fields the push cannot carry.
- **Weaknesses:** the intervals chosen are long (60 s to 15 min), so as a repair of a lost delta it is slow; only shelly/homekit/unifiprotect make a stated case about what it covers, and none state "repairs missed pushes" explicitly. [no source found] for any integration that documents the poll as lost-message repair.
- **Example:** https://github.com/home-assistant/core/blob/dev/homeassistant/components/unifiprotect/data.py (`async_setup`, `_async_poll`)
- **Read:** unifiprotect/data.py 301-336, 799-806; uiprotect api.py DEVICE_UPDATE_INTERVAL = 900 (via raw GitHub grep, not a full read); reolink/coordinator.py 80-130 and host.py; shelly/coordinator.py 995-1030 + aioshelly `poll()` docstring; homekit_controller/connection.py 195-236, 375-400.

### Pattern 5: Writing pushed data without resetting the poll timer
- **Used by:** teslemetry (local-poll merge: `self.data = ...; async_update_listeners()` with comment that pushes must not postpone the timer), watts (PR #169365 fix), reolink (push goes through dispatcher, never touches coordinator), bond/sonos/homekit (entity-level writes).
- **How it works:** either keep the poll on its own timer, or write `coordinator.data` and call `async_update_listeners()`. `async_set_updated_data` is avoided when the poll must keep a fixed cadence, because it unschedules and reschedules the next refresh and cancels the debouncer.
- **Strengths:** the poll fires on schedule regardless of push rate.
- **Weaknesses:** `async_update_listeners()` does not set `last_update_success = True`, so availability needs separate handling (teslemetry comments "availability stays with the stream").
- **Example:** https://github.com/home-assistant/core/pull/169365 ; https://github.com/home-assistant/core/blob/dev/homeassistant/components/teslemetry/coordinator.py
- **Read:** update_coordinator.py 245-300 and 658-673; PR 169365 body; teslemetry/coordinator.py 255-335.

## Anti-Patterns

- **`async_set_updated_data` from a push into a coordinator that also polls on an interval.** Every push cancels the pending poll and the debouncer and re-arms a full interval; with a push rate shorter than the interval the poll never runs. Documented once: PR #169365 (Watts) for fast-poll cycles. For the 30 s repair poll in hass-hassette this applies if pushes arrive more often than every 30 s, which a busy HA instance will. Source: update_coordinator.py 658-673.
- **Treating "any message seen recently" as health** for a stream that may be quiet (Sonos, Bond do this knowingly, with fallback polls of 10 s as the safety net).

## Emerging Trends

- Newer integrations (teslemetry) split coordinators by data source and give stream-fed ones `update_interval=None`, keeping a REST read for setup and manual recovery only. [Source: teslemetry/coordinator.py docstrings, lines 220-230, 363-375.]

## Gaps

- Q3: no open or closed core issue found describing push starvation of a repair poll other than PR #169365. Tried: exa-search (3 queries), `gh search issues/prs` (returned empty for the exact phrases). The `gh search` calls returned `[]`, which may mean the search needs different terms rather than that nothing exists.
- Q4: ESPHome device-side (C++) slow-client behavior and Shelly device-side behavior not opened; zwave-js-server's `ws` library buffering not opened. The claim above is limited to what the repos' own code shows.
- deconz (pydeconz), elkm1 (elkm1_lib), plex (plexwebsocket), uiprotect (WS resync internals), aiohomekit not opened beyond HA's side. A clone of pydeconz landed in the wrong directory and was not retried.
- Q5: found no integration that explicitly documents "a missed push is healed only by the next poll". The closest implicit stance is unifiprotect's 15-minute unconditional refresh and reolink's coordinator refresh. Inferred from code, no doc statement. [no source found] for an explicit statement.
- No integration found that uses sequence numbers to detect a dropped delta on a live stream. Hue uses `last-event-id` replay across reconnects only. mqtt and ESPHome have none (mqtt relies on QoS/retained; ESPHome relies on TCP being reliable plus keepalive).

### Per-integration detail


| integration | push transport | poll? interval | what the poll covers | how push writes data | poll suspended while push healthy? |
|---|---|---|---|---|---|
| shelly (RPC, mains) | device WebSocket via aioshelly | yes: `ShellyRpcPollingCoordinator` 60 s (`RPC_SENSORS_POLLING_INTERVAL`); main `ShellyRpcCoordinator` interval is 60 s reconnect only (`_async_update_data` returns early if connected) | calls that "do not receive push updates" (aioshelly `poll()` docstring: Shelly.GetStatus + dynamic components), only for entities flagged `use_polling_coordinator`. Not a repair of lost pushes | `async_set_updated_data(None)` on STATUS on the main coordinator (its poll is a no-op, so the reset is harmless); aioshelly mutates device status itself | No. Polling coordinator runs always; it is a separate coordinator from the push one |
| shelly (sleepy) | outbound push on wake | interval = 2.2 x sleep period, used as liveness | availability | same | n/a |
| sonos | UPnP/SOAP event subscriptions (HTTP callbacks) | fallback only: `SONOS_FALLBACK_POLL` dispatcher signal every 10 s (`SCAN_INTERVAL`); battery poll 15 min | speaker state when subscriptions fail or events do not arrive | no coordinator; entities write state on dispatcher/event | Yes. `async_dispatch_event` cancels `_poll_timer` on the first event; poll timer is created in `_async_subscribe_to_services` only after subscriptions succeed ("in case subscriptions fail or callback events do not arrive"). Also activity timeout marks speaker unavailable and resubscribes |
| unifiprotect | private + public WebSockets (uiprotect) | yes: own `async_track_time_interval`, 900 s (`DEVICE_UPDATE_INTERVAL` in uiprotect `api.py`); not a DataUpdateCoordinator | full `api.update()` bootstrap refresh (also rereads fields frames do not carry); lib also does resync on WS reconnect (`subscribe_public_resync`) | `ProtectData` pushes via its own signals (`_async_update_device`, dispatcher); no coordinator | No. Timer runs unconditionally |
| hue v2 | SSE eventstream (aiohue) | no poll after setup | n/a | entity-level callbacks via `controller.subscribe` | n/a. Resync: on RECONNECTED after more than 60 s gap, `fetch_full_state()`; heartbeat via 90 s sock_read timeout + 60 s keepalive |
| bond | BPUP UDP push (bond-async) | yes, per entity: `async_call_later` 60 s when BPUP alive else 10 s (`_BPUP_ALIVE_SCAN_INTERVAL`, `_FALLBACK_SCAN_INTERVAL`) | the tick only fetches via API if `not bpup_subs.alive` or entity unavailable | `_async_bpup_callback` applies state then `async_write_ha_state()` (entity-level) | Effectively yes: the tick is a liveness check; it does nothing when BPUP is alive (alive = a message within 70 s) |
| reolink | ONVIF webhook / Baichuan TCP push / long-poll | yes: `ReolinkHostCoordinator` max(60 s, 10 s x channels) (`coordinator.py`) | refreshes all states (`update_states`), renews the ONVIF subscription; fast 5 s motion poll only when no push works | push writes via `async_dispatcher_send` to entities that call `async_write_ha_state` (`_signal_write_ha_state`); coordinator not touched by push | No. Coordinator poll always runs; fast motion poll only without push |
| plex | WebSocket (plexwebsocket) | no state poll; client scan every 10 min (`CLIENT_SCAN_INTERVAL`) | discovery of clients, not repair | `plex_websocket_callback` → `async_update_platforms()`; no coordinator | n/a. On `STATE_CONNECTED` it runs a full `async_update_platforms()` (resync on connect). On `STATE_STOPPED` with error: reload entry |
| synology_dsm | none | poll only | | | n/a (no push) |
| homewizard | none in this integration | poll only, 5 s | | | n/a |
| nut | none | poll only | | | n/a |
| wled | device WebSocket | yes: 10 s (`SCAN_INTERVAL`) | whole-device `update()` | `async_set_updated_data(device)` from WS callback | Yes. `update_interval = None` while WS connected; on WS end it is restored and `async_request_refresh()` runs |
| deconz | WebSocket (pydeconz) | no | n/a | library mutates models, entity callbacks | n/a. HA hub's `async_connection_status_callback` only flips `available`; I did not open pydeconz to confirm whether it refetches on reconnect |
| elkm1 | elkm1_lib push | no DataUpdateCoordinator found in `elkm1/*.py` | n/a | library callbacks | n/a. Library not opened |
| teslemetry (energy) | SSE stream (teslemetry_stream) | stream coordinators have no `update_interval`; REST `_async_update_data` is "cold read at setup and manual recovery only"; separate 5 s / 30 s timers poll a paired Powerwall's LAN gateway | LAN gateway keys the cloud stream does not carry | `async_set_updated_data` for stream docs; local poll uses `self.data = merged; async_update_listeners()` | Stream coordinators: no poll at all. Local-poll timer is independent so "stream pushes cannot postpone it" |
| teslemetry (vehicle) | SSE per-vehicle stream | 60 s `VEHICLE_INTERVAL` only for vehicles without command signing / marked `polling` and without key issue | vehicles that cannot stream | stream listeners write entity state | Poll vs stream is chosen per vehicle at setup, not toggled at runtime. I did not read the full vehicle handler |
| tesla_fleet | none (polling only: 10 s to 300 s intervals) | poll only | | | n/a |
| esphome | device native API TCP (aioesphomeapi) | no | n/a | entity callbacks | n/a. Keepalive ping, drop at 4.5x; on connect `_on_connect` runs `device_info_and_list_entities` and state subscription (full state sent on subscribe) |
| zwave_js | WS to zwave-js-server | no | n/a | library mutates model, entity callbacks | n/a. `client_listen` returning (disconnect) calls `async_schedule_reload`: "a new driver state will be acquired on reconnect. All model instances will be replaced" |
| matter | WS to python-matter-server | no | n/a | same | n/a. `_client_listen` end → `async_reload(entry)` |
| music_assistant | WS to MA server | no | n/a | listeners on client | n/a. Same reload-on-disconnect shape in `__init__.py` (`_client_listen`); I read only the header of that function |
| mqtt | MQTT (paho) | no | n/a | subscription callbacks | n/a. Keepalive 60 s (`DEFAULT_KEEPALIVE`); resubscribe on reconnect so retained messages replay (`client.py` ~1092). Lost QoS 0 messages are not detected; retained state is the resync |
| homekit_controller | HAP event notifications | yes: `async_track_time_interval` at `pairing.poll_interval` (aiohomekit) plus `_debounced_update` | characteristics that are not evented (`pollable_characteristics` excludes `EVENT_CHARACTERISTICS`), plus availability, plus BLE catch-up | entity callbacks via `process_new_events` | No. Runs always; but it polls a disjoint set (non-evented chars) |


## Relevance to Us

- **The stream side already matches Pattern 1.** With #4420, hassette's `/ws` is lossless-or-close, like HA's own
  `websocket_api` (closes at 4096 pending), python-matter-server and the Music Assistant server (512). Plus
  `listen()`'s `on_connected` resync, hass-hassette is exactly the push-only peers' model: the connection ending is
  the one failure signal, and a snapshot repairs it.
- **The poll is no longer a lost-message repair, and doesn't need to be.** No peer documents its poll as repair for
  a live stream. Where peers keep an always-on poll (unifiprotect 900 s, reolink 60 s+, shelly 60 s,
  homekit_controller), it covers data the push doesn't carry. That is hass-hassette's case too: health, devices,
  `remove_undeclared`, app-level fields. So keeping the 30 s poll is Pattern 4 for those reasons, not the
  unusual "poll repairs lost pushes" hybrid. Parent D3's "a lost delta heals within one poll" is now a backstop
  for a server bug, not a designed path.
- **Pattern 5 directly backs ledger 003 D1 option A.** `async_set_updated_data` on a polling coordinator postpones
  the poll on every push. Watts (core PR #169365) hit exactly this and fixed it by writing `self.data` and
  notifying listeners. Teslemetry keeps its poll on its own timer with the comment "stream pushes cannot postpone
  it". The peer row "unknown" in 003 D1 can cite these.
- **Turning the poll off while the stream is healthy (wled, sonos, bond) is an option but not a fit now.** The
  poll carries health and device sync the stream doesn't, so switching it off would need a WS message for each of
  those. That is more machinery than the 30 s poll costs.
- **Nothing here argues for sequence numbers or replay (Pattern 3, hue).** No peer detects a dropped delta on a
  live stream. They make the stream not drop.

## Recommendation

Keep the design's shape: push, resync on every connect, and the 30 s poll. But restate why the poll is there. The
stream is already lossless-or-close (Pattern 1, shipped in hassette 0.66.0), so the poll is kept for what the push
doesn't carry (Pattern 4, as unifiprotect, reolink and shelly do), not as lost-message repair. Write pushes
without resetting the poll timer (Pattern 5; Watts PR #169365, teslemetry). No new machinery is warranted: no
sequence numbers, no replay, no poll suspension.

Coverage: about 20 integrations read at code level on core `dev` (2026-11-23). Device-side slow-client policies
for ESPHome and Shelly, and several libraries (pydeconz, elkm1_lib, plexwebsocket), weren't opened.

## Sources

### Reference implementations
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/websocket_api/http.py: HA's WS server closes a client at `MAX_PENDING_MSG` (4096)
- https://github.com/home-assistant-libs/python-matter-server (`matter_server/server/client_handler.py`): closes a client on a full 512 queue
- https://github.com/music-assistant/server (`controllers/webserver/websocket_client.py`): same, 512
- https://github.com/zwave-js/zwave-js-server (`src/lib/server.ts`): ping liveness only
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/zwave_js/__init__.py and `matter/__init__.py`: reload the entry on stream end
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/esphome/manager.py: full entity list and state on every connect
- https://github.com/esphome/aioesphomeapi (`connection.py`): keepalive, drop at 4.5x
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/wled/coordinator.py: poll off while the WS is connected, refresh on WS end
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/sonos/speaker.py: fallback poll cancelled on the first event
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/bond/entity.py and https://github.com/bondhome/bond-async (`bpup.py`): poll tick no-op while BPUP is alive
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/unifiprotect/data.py: unconditional 15 min refresh
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/reolink/coordinator.py: coordinator poll alongside push
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/shelly/coordinator.py: separate polling coordinator for non-pushed fields
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/homekit_controller/connection.py: polls non-evented characteristics
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/teslemetry/coordinator.py: poll on its own timer so pushes can't postpone it
- https://github.com/home-assistant-libs/aiohue (`v2/__init__.py`, `v2/controllers/events.py`): last-event-id replay, full fetch after a 60 s gap
- https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/update_coordinator.py: `async_set_updated_data` reschedules the poll

### Blog posts and writeups
- https://github.com/home-assistant/core/pull/169365: Watts fix for pushes postponing a poll
- https://github.com/home-assistant/core/issues/178145: `async_set_updated_data` report, not about poll starvation (listed so it isn't re-chased)

### Documentation and standards
- https://github.com/home-assistant/developers.home-assistant/blob/main/docs/integration_fetching_data.md: push vs poll documented separately; silent on hybrids
- NodeJSmith/hassette #4217 / PR #4420: close slow `/ws` clients (in v0.66.0)
