# Design: Hub connectivity sensor

**Date:** 2026-10-18
**Status:** ratified
**Mode:** sketch
**Issues:** #1

## Summary

The **Hassette** hub device has no entity. When hassette is unreachable, serves an unsupported API
schema, or holds app startup, every app entity goes unavailable, and nothing on the hub says why or
gives an automation one thing to trigger on (known issue KI-002 in
`design/specs/001-hass-hassette-v0-1/known-issues.md`; v0.1's D4 reserved hub entities for v0.2).

This change adds one connectivity `binary_sensor` on the hub device, `binary_sensor.hassette`, on
while the coordinator's latest poll succeeded and off when it failed. While on, it carries
hassette's `bootstrap_released` as a state attribute; while off, a `reason` attribute holding the
failure's existing translation key (D1).

Where each piece lives:
- The entity: a new `custom_components/hassette/binary_sensor.py`, with its own small entity class
  (a `CoordinatorEntity[HassetteCoordinator]` and `BinarySensorEntity`, overriding `available` to
  return `True`), `PARALLEL_UPDATES = 0` like the other platforms, `has_entity_name`, a description with `key`/`translation_key` `connectivity`,
  `name=None` and `BinarySensorDeviceClass.CONNECTIVITY`, unique_id
  `f"{SERVER_DEVICE_ID}-connectivity"`, and `DeviceInfo` with only the hub's identifiers. It does
  not subclass `HassetteAppEntity` (`entity.py`), whose device, availability and unique_id rules are
  all per-app.
- Its state: the existing `HassetteCoordinator.last_update_success`; its attributes: the existing
  `coordinator.data.health.bootstrap_released` and `coordinator.last_exception`'s
  `translation_key`. No new coordinator state.
- Registration: `Platform.BINARY_SENSOR` added to `PLATFORMS` in `custom_components/hassette/__init__.py`.
- Names for the `bootstrap_released` and `reason` attributes, and a translated state for each
  `reason` value: `translations/en.json` under
  `entity.binary_sensor.connectivity.state_attributes`. The `reason` values are exactly the
  translation keys the coordinator can raise, whose one home is `errors.py` (`POLL_ERRORS`,
  `poll_error`'s `invalid_auth` and `unknown`) plus `unsupported_version` and `addon_refreshed`
  (`coordinator.py`); a `last_exception` without a `translation_key` gives `reason: unknown`.
- Docs: `README.md` (intro, Entities, Updates, Known limitations), and the
  `entity-disabled-by-default` comment in `custom_components/hassette/quality_scale.yaml`, which
  says each app has only three entities and should now also cover the hub sensor.

Out of scope: any other hub entity (version, uptime, websocket state), and any change to how setup
behaves when the first refresh fails.

## Posture

- **Failure handling:** none of its own; the sensor reports what the coordinator already decides,
  including the one-poll tolerance and the immediate `UpdateFailed` on an unsupported version.
- **Compatibility:** additive only. The repo is public and released through HACS and release-please,
  so existing entity ids and unique_ids stay untouched; no migration.
- **Scope:** minimal: one entity plus two state attributes, `bootstrap_released` (D3) and `reason` (D1).
- **Rollout:** ships enabled by default, no flag.
- **Hardening:** project defaults; no new logging, since the coordinator already logs outages and
  bootstrap changes (`HassetteCoordinator.note_bootstrap`).
- **Altitude:** `build`; one session builds it from this ledger.

## Decisions

### D1: What drives the sensor's state and availability?

**Deciding factor:** meet the issue's acceptance criteria (off when unreachable or too old,
availability independent of the app entities' rules, tolerance window pinned) with no new state,
and make `off` honest about its cause.

`off` covers every failed poll, including ones where hassette answered: a 401 (reauth), 403,
redirect, unreadable response, unsupported version, and the add-on follow path's deliberate
`addon_refreshed` failure. Every such failure already carries a translation key on the exception the
coordinator raises (`errors.py:75-84` `POLL_ERRORS`, `errors.py:160-168` `poll_error`,
`coordinator.py:106-112`, `coordinator.py:139-156` `follow_addon`), and HA keeps it in
`coordinator.last_exception`.

| | A: `is_on = coordinator.last_update_success`, `available` always `True` | A+reason: A, plus a `reason` attribute while off = `last_exception.translation_key` | B: own reachability tracking in the coordinator | C: off only when unreachable |
|---|---|---|---|---|
| One-poll tolerance | Inherited: a tolerated failure returns the previous data, so it stays on (`coordinator.py:113-117`) | Inherited | Must be reimplemented | Inherited |
| Unsupported version | Off at once: `UpdateFailed` is raised before the tolerance check (`coordinator.py:106-112`) | Off at once | Must be reimplemented | Stays on (breaks the issue's "too old" criterion) |
| User learns the cause from the entity | No | Yes | No | Partly |
| New state or mapping | None | None (existing translation keys) | A second success flag to keep in sync | A failure classification |
| Precedent | Core `minecraft_server/binary_sensor.py` does exactly A | `minecraft_server` plus one attribute | None found | Core `ping` |

**Recommendation:** A+reason, because it reuses the coordinator's existing decisions and keys,
matches core, and tells the user what to fix.
**Pick A instead if** the cause in the log and Repairs is enough. **Pick B instead if** the sensor
ever needs a different tolerance than the app entities. **Pick C instead if** the "too old"
criterion is dropped.
**Reversibility:** easy (one property and one attribute)
**Ratified:** Chose A+reason over A, B and C, to inherit the coordinator's tolerance and version handling with no new state while naming the cause of each outage, accepting that the sensor can't diverge from the app entities' outage timing and that `reason` stays at the outage's first failure (a failure after a failure doesn't update the entity, HA `helpers/update_coordinator.py:605-606`).

### D2: How does the sensor behave when the first refresh fails?

**Deciding factor:** keep v0.1's setup contract (D7/D16 of `design/specs/001-hass-hassette-v0-1/design.md`).

| | A: accept `unavailable`, document it | B: forward platforms even on a failed first refresh |
|---|---|---|
| State after HA restart / entry reload while hassette is down | `unavailable` (entity registry writes it for unclaimed entities) | `off` |
| State when hassette drops while loaded | `off` | `off` |
| Setup contract | Unchanged | D7/D16 rewritten; every app entity must handle no data |
| Precedent | Core `minecraft_server`, `gatus` | None found |

**Recommendation:** A, because B rewrites setup for an edge case where `unavailable` already tells
a viewer hassette isn't usable. The README states what `off` and `unavailable` mean and gives no
automation recipe: hassette users write their automations in Python, and the sensor is there to be
seen and used however they like.
**Pick B instead if** users need a strict `off` state for automations that can't treat
`unavailable` as down.
**Reversibility:** hard (B changes setup for every platform)
**Ratified:** Chose A over B, to keep the v0.1 setup contract, accepting that after a restart or reload while hassette is down the sensor reads `unavailable` rather than `off`, which the README documents as also meaning down, and that recovery then waits on HA's setup-retry backoff (`min(2**tries * 5, 600)` s, HA `config_entries.py:145,809`), so the sensor and the app entities can stay `unavailable` up to 10 minutes after hassette is back; the README's Known limitations states this bound and a manual entry reload as the workaround.

### D3: How is a held app bootstrap shown?

**Deciding factor:** smallest hub surface for a condition seen mostly at startup.

| | A: `bootstrap_released` attribute on the sensor | B: a second hub binary_sensor | C: leave it out |
|---|---|---|---|
| Hub entities | 1 | 2 | 1 |
| Automation trigger | Attribute trigger | Plain state trigger | None (app entities go unavailable) |
| Core convention | Leans against facts-as-attributes | Matches | n/a |

**Recommendation:** A. While bootstrap is held, hassette is reachable, so the sensor stays on.
**Pick B instead if** automations on held bootstrap become common. **Pick C instead if** the app
entities' unavailability is signal enough.
**Reversibility:** easy (adding B later doesn't remove A's consumers until a release says so)
**Ratified:** Chose A over B and C, to keep one hub entity, accepting that core convention prefers a separate entity for a separate fact, and that with D4 the headline entity reads Connected while held startup leaves every app unavailable; the README says Connected means the poll worked and points to `bootstrap_released` for app startup.

### D4: How is the sensor named and categorized?

**Deciding factor:** v0.1 D6's main-feature rule, and the issue's "something to glance at".

| | A: main feature (`name=None`), no category | B: main feature, `DIAGNOSTIC` | C: translated "Connectivity" |
|---|---|---|---|
| Entity id | `binary_sensor.hassette` | `binary_sensor.hassette` | `binary_sensor.hassette_connectivity` |
| On auto-generated dashboards | Yes | No | Yes |
| Precedent | Core `ping`, `gatus` | Many core connectivity sensors | Core `minecraft_server` (device has several sensors) |

**Recommendation:** A. Device class `CONNECTIVITY` (shown as Connected/Disconnected), unique_id
`-server-connectivity`.
**Pick B instead if** the hub sensor should stay out of default dashboards. **Pick C instead if**
the hub will gain a more headline-worthy entity soon.
**Reversibility:** hard (the entity id is user-visible once created)
**Ratified:** Chose A over B and C, to make the connectivity sensor the hub's visible headline entity, accepting that a later hub entity can't also take the main-feature slot, and that a user who already has a `binary_sensor.hassette` gets `binary_sensor.hassette_2`.

### D5: What does the `bootstrap_released` attribute show while the sensor is off?

**Deciding factor:** never show a bootstrap value that may have changed during an outage. Under
D1's tolerance the shown value can still be up to 45 s old (`TOLERANCE_WINDOW`, `const.py:10-13`)
while the sensor is still on, the same bound the app entities live with; the README's Updates section says the one-poll tolerance
applies to the hub sensor too (detection takes about 30-75 s).

While off, `coordinator.data` still holds the last successful poll's health
(`DataUpdateCoordinator` only replaces `data` on success), so the last-known value is available but
may be stale: hassette can restart during the outage and come back holding bootstrap.

| | A: omit the attribute while off | B: keep the last-known value | C: set it to `null` while off |
|---|---|---|---|
| Truthful during an outage | Yes | No, can be stale | Yes |
| Template/automation handling | `state_attr(...)` returns `None` | Always a bool | `None`, but the key stays listed |
| Matches existing attributes | Yes: the status sensor omits attributes that don't apply | No | No |

**Recommendation:** A, because it is truthful and matches how the status sensor already omits
inapplicable attributes (`sensor.py:55-64`).
**Pick B instead if** automations need the last-known value during an outage. **Pick C instead if**
the attribute must always be listed in the UI.
**Reversibility:** easy
**Ratified:** Chose A over B and C, to never show a bootstrap value that may have changed during an outage, accepting that templates read `None` for it during an outage and that it can be up to 45 s old during a tolerated failed poll. The two attributes never appear together: `bootstrap_released` only while on, `reason` (D1) only while off.

### D6: Is the `bootstrap_released` attribute recorded?

**Deciding factor:** whether its history is worth keeping.

The status sensor marks its attributes `_unrecorded_attributes` (`sensor.py:43`) because they hold
error text. `bootstrap_released` is a bool that changes a few times per hassette restart, so its
recorder cost is negligible, and its history shows when and how long app startup was held.

**Recommendation:** record it (no `_unrecorded_attributes`).
**Pick unrecorded instead if** you want the hub sensor consistent with the status sensor's rule
that attributes are never recorded.
**Reversibility:** easy
**Ratified:** Chose recording over unrecorded, to keep a history of held app startups, accepting a different attribute rule from the status sensor's.

### D7: Is the `reason` attribute recorded?

**Deciding factor:** whether a history of outage causes is worth keeping.

`reason` (D1) is a short translation key, set once per outage, so its recorder cost is negligible,
and its history shows what each past outage was (unreachable, rejected token, unsupported version).
It holds no free text, unlike the status sensor's unrecorded `error_message`.

**Recommendation:** record it, like `bootstrap_released` (D6).
**Pick unrecorded instead if** you want only `bootstrap_released` in history.
**Reversibility:** easy
**Ratified:** Chose recording over unrecorded, to keep a history of outage causes, accepting one more recorded attribute on the hub sensor.

## Assumed

- `integration_type` is `hub` and `single_config_entry` is `true`, so unique_id
  `-server-connectivity` can't collide across entries. Evidence: `custom_components/hassette/manifest.json`.
- No app unique_id can start with `-`, so `-server-connectivity` can't collide with
  `{app_key}-{key}`. Evidence: `const.py:21-23` comment, `entity.py:90-92`.
- The hub device `(DOMAIN, SERVER_DEVICE_ID)` exists before any platform is set up, because every
  successful poll registers it and the first refresh succeeds before platforms are forwarded. The
  sensor attaches with identifiers only and does not override the device's name, version or URL.
  Evidence: `__init__.py:28-31`, `coordinator.py:231-245`.
- A failed first refresh raises `ConfigEntryNotReady` (or `ConfigEntryAuthFailed` for a 401,
  which starts reauth rather than a setup retry) and platforms are never forwarded; HA then
  shows `unavailable` for the registered entity: at HA startup the registry writes it for every
  unclaimed entity, and on an entry unload or reload each removed entity writes it. Evidence: HA
  2026.10 `helpers/update_coordinator.py:335`, `helpers/entity_registry.py:2827`,
  `helpers/entity.py:1508`.
- `DataUpdateCoordinator` sets `data` only on success and flips `last_update_success` on failure.
  Evidence: HA 2026.10 `helpers/update_coordinator.py:456` (the only assignment in `_async_refresh`)
  and its failure branches (e.g. `:464,478,572-584`).
- The `CONNECTIVITY` device class supplies icons (`mdi:check-network-outline` /
  `mdi:close-network-outline`) and Connected/Disconnected state names, so `icons.json` needs no
  entry. Evidence: HA 2026.10 `components/binary_sensor/icons.json:33`, `strings.json:155`.
- Reauth (`ConfigEntryAuthFailed` during a poll) also sets `last_update_success` false, so the
  sensor goes off then too, and sets `last_exception`, so `reason` is `invalid_auth`. Evidence: HA
  2026.10 `helpers/update_coordinator.py:553-565`.

## Build

- [ ] Implementation and tests committed
- [ ] Docs
- [ ] Ship-time challenge

**Calls made during the build:**
