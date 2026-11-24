---
topic: "How push-stream integrations report a stream that never comes up or keeps failing"
date: 2026-11-23
status: Draft
---

# Prior Art: Push-Stream Failure Reporting

## The Problem

hass-hassette subscribes to hassette's `/ws` alongside its 30 s poll. A dropped stream falls back to polling and entities stay available, so nothing the user sees changes when the stream fails. Some failures never heal on reconnect: a reverse proxy that doesn't pass WebSockets, a stream-only auth rejection, or a newer hassette whose messages the pinned wire package can't parse. The question is what log level, dedupe key, re-arm point and user-visible signal comparable integrations use for these.

## How We Do It Today

`HassetteStream.log_failure_once` (hass-hassette `custom_components/hassette/stream.py`, spec 003) logs each stream ending at INFO, once per exception type, and `on_connected` clears the record on every connect. So a stream that never works is hidden at HA's default log level, and a stream that connects and then fails logs again every 30 s.

## Patterns Found

### P1. Boolean "streak" latch, log first, re-arm on success (coordinator style)
**Used by:** `DataUpdateCoordinator`, unifiprotect (first-vs-repeat), HA quality-scale rule.
**How it works:** one flag records the last result. On a transition ok to fail, log once. While failing, log nothing (or DEBUG). On fail to ok, log a "recovered" line and re-arm. The coordinator uses ERROR for the failure and INFO for recovery. The quality-scale rule says INFO for both. unifiprotect uses ERROR on first loss, DEBUG on repeats, and WARNING on restored.
**Strengths:** a trivial mechanism, no timers or keys, and the recovered line brackets the outage in the log.
**Weaknesses:** "ok" is whatever flips the flag. For a connect-then-fail loop, the flag flips to ok on every connect and each cycle logs again. unifiprotect would log ERROR on every cycle of such a loop.
**Example:** [update_coordinator.py](https://github.com/home-assistant/core/blob/01ddf2a6da3cdafacf02878d87bd8e97b85b28f2/homeassistant/helpers/update_coordinator.py) L475-560 and L612-615; [unifiprotect/data.py](https://github.com/home-assistant/core/blob/01ddf2a6da3cdafacf02878d87bd8e97b85b28f2/homeassistant/components/unifiprotect/data.py) L610-632.
**Read:** update_coordinator.py L470-640; unifiprotect/data.py L526-640; quality-scale doc (full rule text).

### P2. Dedupe key = exception type, re-arm on session restored
**Used by:** unifi (`UnifiConnectionManager._log_unexpected_error`).
**How it works:** `_logged_error` holds the type of the last logged exception. The same type again goes to DEBUG, a new type goes to ERROR. It is cleared in `_set_available(True)`. Lost/restored lines are INFO, one per transition.
**Strengths:** the same shape as hass-hassette's current "once per exception type" scheme, in core.
**Weaknesses:** I read the code. It re-arms when the session is marked available again, and the recovery callback runs *after* that. A recovery that fails right after goes unavailable and clears the latch on the next cycle, so a connect-then-fail loop re-logs. `CONNECTION_STABLE_PERIOD` (60 s) only resets the retry backoff (`_attempt = 0`), not the latch.
**Example:** [unifi/hub/connection.py](https://github.com/home-assistant/core/blob/01ddf2a6da3cdafacf02878d87bd8e97b85b28f2/homeassistant/components/unifi/hub/connection.py) L380-400 and L187-190.
**Read:** unifi/hub/connection.py L60-135, L280-345, L376-405.

### P3. Cycle-scoped level: first attempt INFO, later attempts DEBUG, reset after successful handshake
**Used by:** esphome / aioesphomeapi `ReconnectLogic`.
**How it works:** a `_tries` counter picks the level: INFO when `_tries == 0`, DEBUG after. Unhandled exceptions are always ERROR with a traceback. `_tries` resets only after `finish_connection` succeeds, not on socket open. The code comment says connection warnings are INFO because "the unavailable logic will still trigger so the user knows."
**Strengths:** re-arms on the handshake. That is close to "re-arm only when the stream works", and it survives a connect-then-fail loop.
**Weaknesses:** INFO is hidden by default, which is what the brief's critics object to. esphome accepts that because entities go unavailable, which hass-hassette's do not.
**Example:** [reconnect_logic.py](https://github.com/esphome/aioesphomeapi/blob/d6cdaaa5bd967295b377d1f5d28bed622363aa76/aioesphomeapi/reconnect_logic.py) L466-495, L528-593.
**Read:** reconnect_logic.py L408-432, L462-495, L528-593; esphome/manager.py `on_connect_error` (L963-1008), which starts reauth for auth errors only.

### P4. State-machine dedupe: error only while not already RETRYING
**Used by:** deconz / pydeconz websocket.
**How it works:** a connect failure or unexpected exception logs ERROR only if `_state != RETRYING`. After a drop, the code retries once immediately without signalling. Only if that retry fails does it log an INFO and signal state (entities go unavailable). Opening the socket sets RUNNING, which re-arms.
**Strengths:** repeat failures are silent. The "never worked" case logs ERROR once.
**Weaknesses:** the re-arm is socket-open, not first message. A server that accepts the upgrade and then closes logs a WARNING "Connection closed" on every cycle. A bad JSON frame hits the broad `except Exception`, logs ERROR, and forces a reconnect.
**Example:** [pydeconz websocket.py](https://github.com/Kane610/deconz/blob/bd255dcb7044217fb22eef724557b67564b0cebe/pydeconz/websocket.py) L96-165.
**Read:** pydeconz/websocket.py L90-175 (cloned file).

### P5. Repairs issue for a push channel that never works, cleared on first received push
**Used by:** reolink (only one found).
**How it works:** after a timeout (`FIRST_ONVIF_TIMEOUT`, then a long-poll check), if no push or long-poll event has arrived, the integration logs at DEBUG and raises WARNING-severity, non-fixable Repairs issues (`webhook_url`, plus `https_webhook` and `ssl` when they apply). Each says what to check and that the integration is using fast polling until the first push. `_async_delete_webhook_issues` runs on the first received webhook or long-poll event (L864-865, L976-977) and on the checks passing. Separately, `_lost_subscription` logs ERROR once when a *previously working* renew fails and resets on the next successful renew.
**Strengths:** user-visible, actionable text, healthy means "a real event arrived", and the issue clears itself. This is the closest analogue to a never-working stream.
**Weaknesses:** the issue text is Reolink-specific, because the failure has a known cause (HA URL unreachable). A generic "WS failed" cause is harder to make actionable. Per the repair-issues rule, a Repairs issue needs something the user can do.
**Example:** [reolink/host.py](https://github.com/home-assistant/core/blob/01ddf2a6da3cdafacf02878d87bd8e97b85b28f2/homeassistant/components/reolink/host.py) L175-210, L437-520, L735-750.
**Read:** reolink/host.py L170-245, L437-535, L715-760, plus grep of the delete call sites; reolink/strings.json `issues.webhook_url`.

### P6. They don't bother: debug-log the stream, rely on polling or entity availability
**Used by:** unifi websocket (`Websocket setup failed` / `Websocket disconnected` at DEBUG), plex (connect/disconnect/retry at DEBUG), teslemetry (stream drop at DEBUG), zwave_js and matter and music_assistant (disconnect at DEBUG, then reload).
**How it works:** transient push-channel problems go to DEBUG. The user-visible signal is entities going unavailable, either after a grace timer (teslemetry) or at once (unifi, unifiprotect). Terminal failure is ERROR once (plex "failed, aborting", zwave_js "Cannot recover"). Auth failure goes to the reauth flow (teslemetry `TeslemetryStreamAuthenticationError`, unifiprotect `AUTH_FAILED`, esphome auth errors). None of these raise a Repairs issue for a stream that never connects, and none has a diagnostic "connected" entity for the stream. teslemetry's one connection binary sensor is for BLE, not the stream.
**Strengths:** zero machinery, and no log noise.
**Weaknesses:** these peers can do it only because a dead stream becomes visible as unavailable entities. The hass-hassette design deliberately keeps entities available while polling, so a never-working stream has no other signal. That is the critics' point, and no peer's choice transfers directly.
**Example:** [unifi/hub/websocket.py](https://github.com/home-assistant/core/blob/01ddf2a6da3cdafacf02878d87bd8e97b85b28f2/homeassistant/components/unifi/hub/websocket.py) L58-70; [plex/__init__.py](https://github.com/home-assistant/core/blob/01ddf2a6da3cdafacf02878d87bd8e97b85b28f2/homeassistant/components/plex/__init__.py) L181-197.
**Read:** unifi/hub/websocket.py L53-72; plex/__init__.py L178-232; teslemetry/__init__.py L1035-1140; zwave_js/__init__.py L1229-1264; matter/__init__.py L356-378; music_assistant/__init__.py L313-335.

### P7. Delegate retry noise to HA's ConfigEntryNotReady
**Used by:** zwave_js, matter, music_assistant (reload on listen end); HA core config entries.
**How it works:** raising `ConfigEntryNotReady` makes core log at INFO each retry ("not ready yet; Retrying in N seconds") with `2**tries * 5` backoff capped, and shows "Retrying setup" in the UI. Not deduped.
**Strengths:** free, and visible in the integration card.
**Weaknesses:** only applies if the stream failure is allowed to fail the entry. hass-hassette keeps the entry loaded, so it does not apply.
**Example:** [config_entries.py](https://github.com/home-assistant/core/blob/dev/homeassistant/config_entries.py) retry branch (searched for "Retrying in"; line numbers are from the `dev` branch at fetch time).
**Read:** config_entries.py L825-870 in my fetched copy; zwave_js/matter/music_assistant listen functions as above.

## Anti-Patterns

- **Re-arming on socket open or connect.** pydeconz and unifi's `_set_available(True)` both re-arm before the stream has proven useful. A server that accepts then drops produces one log line per cycle. This is the critics' second complaint, and it matches peer behavior (found in the code, not in an issue).
- **ERROR for transient repeats.** unifiprotect logs every first loss at ERROR and restoration at WARNING. In a flapping link, each cycle gets one ERROR plus one WARNING.
- **Broad `except Exception` on message parse that forces a reconnect.** pydeconz does this: a JSON error logs ERROR "Unexpected error" and triggers a retry. Nobody I read logs a distinct, deduped message for "server sent something the client can't parse" (`[no source found]` for a peer that does).

## Emerging Trends

- Newer core integrations (unifi's connection manager, teslemetry's grace timer) separate "session available" from "transport detail", and gate user-visible state on a debounce (grace timer, stable period) instead of the raw connect/disconnect event. Only the backoff reset is debounced in unifi, not the log latch.

## Gaps

- Not read: `watts`, `hass-node-red` (as far as I know it is the reverse direction, a client of HA's own WS API, so it is not comparable), `shelly`'s websocket library, `plexwebsocket` internals, `aiounifi` websocket internals. Not read means no claim made.
- No peer found that logs at WARNING for "never connected this session" or that raises a Repairs issue for a generic WS failure. The only Repairs-issue precedent (reolink) fires on "no push event received within a timeout" and carries a specific diagnosis.
- No peer found that logs a distinct deduped line for unparsable stream messages. Searches: grepped the components above for the parse-error paths. `[no source found]`.
- No Home Assistant issue-tracker or forum discussion of a push channel falling back silently was opened. The one exa query returned only hassette's own docs.

## Relevance to Us

- Most peers "don't bother" (P6) because a dead stream makes their entities unavailable, which is the user's signal. hass-hassette deliberately keeps entities available while polling, so it can't borrow that signal. The closest peer in shape is reolink: push alongside a polling fallback, entities stay available. It is also the only peer that tells the user about a push channel that never worked, with a Repairs issue cleared on the first push received (P5).
- Re-arming on connect, as hass-hassette does today, is common (deconz, unifi), and peers that do it show the same log-every-cycle behavior the critics described. Re-arming after the handshake succeeds (esphome, P3) or on a real event (reolink, P5) avoids it.
- The fix the critics proposed is a warning once for a stream that never worked, info for drops of one that did, and re-arming once the stream works. It is no heavier than the peers' latches (P1, P3, P4): one session flag and a moved re-arm point. A Repairs issue (P5) is more machinery than the peers that keep entities available otherwise carry, and HA's `repair-issues` rule wants a user-fixable cause. hass-hassette can't tell a proxy problem from other connection errors without matching message strings.

## Recommendation

Adopt a coordinator/esphome-style latch, not a Repairs issue:
- Treat the stream as working once its connect resync succeeds (esphome's "handshake succeeded").
- Warn once for a failure before the stream has ever worked in this session. Log drops of a working stream at info, once per type.
- Re-arm the dedupe when a resync succeeds, not on connect.
- Log a message the pinned wire can't parse at warning, once per session. It is a version mismatch that no reconnect heals.
- Leave a user-visible stream-health surface to the diagnostics issue (#81). Reolink's Repairs issue is the precedent if log lines turn out not to be enough.

Coverage gaps: watts, hass-node-red, shelly's websocket library, plexwebsocket and aiounifi internals weren't read.

## Sources


1. `homeassistant/helpers/update_coordinator.py` ([link](https://github.com/home-assistant/core/blob/01ddf2a6da3cdafacf02878d87bd8e97b85b28f2/homeassistant/helpers/update_coordinator.py)), code, **Opened: yes**. Logs the first failure of a streak at ERROR (or INFO for `UpdateFailedUnreachable`), nothing for repeats, and "recovered" at INFO on the next success. The key is the `last_update_success` boolean. Relevance: the polling half of hass-hassette already gets this for free if it uses a coordinator.
2. `unifi/hub/connection.py` + `unifi/hub/websocket.py` (core, same SHA), code, **Opened: yes**. WS setup/handshake failure is logged at DEBUG. A connection manager logs "lost"/"restored" at INFO and unexpected errors at ERROR once per exception type. Relevance: nearest match to the "once per exception type" scheme in the brief.
3. `unifiprotect/data.py` L526-640 (core), code, **Opened: yes**. First "Connection lost" is ERROR, repeats are DEBUG, and "restored" is WARNING. Relevance: the only peer I found with a two-level first-vs-repeat scheme, keyed on the websocket state.
4. `reolink/host.py` (core), code, **Opened: yes**, plus `reolink/strings.json`. Push subscription failure is logged at DEBUG and a Repairs issue is raised. Subscription loss is logged once at ERROR. Relevance: the only peer that surfaces a never-working push channel as a Repairs issue.
5. `esphome` `manager.py` + aioesphomeapi `reconnect_logic.py` ([link](https://github.com/esphome/aioesphomeapi/blob/d6cdaaa5bd967295b377d1f5d28bed622363aa76/aioesphomeapi/reconnect_logic.py)), code, **Opened: yes**. First attempt of a reconnect cycle logs at INFO, later attempts at DEBUG. The counter resets after a successful handshake and the on-connect callback.
6. `deconz` + pydeconz `websocket.py` ([link](https://github.com/Kane610/deconz/blob/bd255dcb7044217fb22eef724557b67564b0cebe/pydeconz/websocket.py)), code, **Opened: yes**. Connect failures log at ERROR only while the state is not RETRYING. The state machine re-arms when the socket opens.
7. `plex/__init__.py` L178-232 (core), code, **Opened: yes**. Connect, disconnect and retry are all DEBUG. ERROR only when the library gives up, and then the entry is reloaded.
8. `teslemetry/__init__.py` L1035-1140 (core), code, **Opened: yes**. A stream drop logs at DEBUG and starts a grace timer. Only if the stream stays down past the grace are the coordinators failed, which makes entities unavailable. A rejected token starts reauth.
9. `zwave_js/__init__.py` L1229-1264, `matter/__init__.py` L356-378, `music_assistant/__init__.py` L313-335 (core), code, **Opened: yes**. Listen failure is logged at ERROR, then the entry is reloaded, so retry and backoff are HA's `ConfigEntryNotReady` machinery.
10. `homeassistant/config_entries.py` retry branch (L825-870 in my copy; fetched from the `dev` branch, not pinned), code, **Opened: yes**. Each setup retry logs at INFO, with no dedupe and an exponential backoff capped at a maximum.
11. Quality Scale rule `log-when-unavailable` ([link](https://github.com/home-assistant/developers.home-assistant/blob/master/docs/core/integration-quality-scale/rules/log-when-unavailable.md)), doc, **Opened: yes**. Says: log once when unavailable and once when back, at INFO, tracked with an instance flag. Docs show intent, not peer behavior.
12. Quality Scale rule `repair-issues` ([link](https://github.com/home-assistant/developers.home-assistant/blob/master/docs/core/integration-quality-scale/rules/repair-issues.md)), doc, **Opened: partly** (first 30 lines). Says repairs are for problems the user can fix, not for "something is wrong" notices.
13. `shelly/coordinator.py` (core), code, **Opened: partly** (grep plus excerpts around L140-155 and L800-822). The RPC websocket's connected/disconnected callbacks set `self.connected`. While the socket is down, a poll raises `UpdateFailedUnreachable` and the coordinator logs "unavailable" at INFO once. I did not read the Shelly websocket library.
