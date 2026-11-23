# Design: App-declared entities from hassette's app listing

**Date:** 2026-11-23
**Status:** built
**Mode:** sketch
**Issues:** #72
**Parent:** NodeJSmith/hassette `design/specs/155-hacs-app-entities/design.md` (Breakdown issue 5)
**Prior art:** NodeJSmith/hassette `design/research/2026-11-16-app-declared-ha-entities/prior-art.md`

## Summary

hassette 0.66 lets an app instance declare Home Assistant buttons, switches and sensors through
`self.integration` (hassette spec 157), and serves them on `GET /api/apps` as `AppInstance.entities`
(`hassette_wire.DeclaredEntity`), with `press_button` / `set_switch` commands on `hassette-client` (hassette
spec 158). This change makes hass-hassette create those entities: one device per instance that declares any,
linked under the app's v0.1 device; a button, switch or sensor per declared entity, read from the polled
listing; presses and toggles sent through the command route; entities removed when a running instance stops
declaring them; and the instance device removable by the user once the instance leaves hassette's config.

Out of scope: `/ws` push, and ordering availability by `status_revision` (both #73); the end-to-end test
against a real HA (hassette#4221); any change to v0.1's app device fields, identifiers or removal rule,
framework entities or config flow. (The coordinator now also creates the app device of a declaring app before
its platforms run; the device it creates is the one v0.1 would.)

Where each new piece lives:

- **The declared unique_id and the add rule's session-free condition:** module-level functions in
  `coordinator.py` (beside `app_device_fields`, which `entity.py` already imports from there):
  `declared_unique_id(app_key, instance_name, key)` and one predicate saying whether a declared entity may be
  added, "its instance is in current config, or its unique_id is already registered". The coordinator's
  app-device registration and the add listener both call that predicate, so the rule has one home.
- **Declared entity base, instance device info, the per-platform add listener, the command
  helper:** a new module `custom_components/hassette/declared.py`, beside `entity.py`'s app-entity base. It
  is a separate base because a declared entity belongs to an instance, not an app (different device,
  availability and unique_id), so it shares nothing with `HassetteAppEntity` beyond `CoordinatorEntity`.
- **Declared button, switch and sensor classes:** `button.py`, `switch.py`, `sensor.py`, each next to its
  v0.1 class and added from the same `async_setup_entry`. Sensor value conversion (D4) lives in `sensor.py`.
- **Per-poll registry diff (removed declarations, instance devices of instances declaring nothing):**
  `HassetteCoordinator`, beside `update_app_devices`. It runs in `_async_update_data` after the version check and
  app-key filtering, takes the fresh listing as its argument, and is gated on this poll's own
  `health.bootstrap_released`; it never calls `app_list_trusted`, which there still describes the previous poll
  (and raises on the first refresh, when `self.data` is `None`). A poll that keeps the previous data after a
  tolerated failure runs no diff, since it carries no new listing.
- **App device registration for declaring apps:** `HassetteCoordinator`, in the same poll pass as
  `register_server_device` and before `self.data` is set, so every platform's listener finds it. Each app with
  a declared entity the add-rule predicate admits gets its app device get-or-created with v0.1's full
  fields (`app_device_fields`, manufacturer, hub link, configuration URL, entry type), because an HA child
  device's parent must already be registered and the three platforms set up concurrently. A declared entity
  looks the app device's id up by identifier for `parent_device_id`, as v0.1 entities look up the hub.
- **Instance device removal rule:** `async_remove_config_entry_device` in `__init__.py`.
- **Error mapping for the new conflict codes:** `errors.py` tables and `translations/en.json`.
- **Client pin and HA minimum:** `manifest.json` and `pyproject.toml` to `hassette-client==0.66.0`;
  `hacs.json`'s `homeassistant` minimum to 2026.11.0, and the `pytest-homeassistant-custom-component` pin to a
  2026.11 build. The device-info code is written against that build's `ChildDeviceInfo` (a dataclass from
  2026.11; the pinned 2026.10.4 has a `TypedDict`), so the pin moves first.
- **Docs:** a README section on app-declared entities (instance devices, availability, removal, that
  hassette ≥ 0.66 is required).

Behavior the build must hold, settled by the parent or the posture:

- unique_id `{app_key}-{instance_name}-{key}`; instance device identifier `(hassette, "{app_key}-{instance_name}")`
  (parent D11). A declared entity's registry entry is on its platform's domain, so a key moved between
  platforms is a new entity (parent D6).
- An instance device is created by the platforms with its first added entity and removed when its running
  instance declares none (parent D1); an instance whose entities the add rule declines gets none. It is created under an app device the coordinator has already registered; its kind and
  name are D1 and D2.
- `_attr_has_entity_name = True` with the declaration's `name` as the entity name, so the friendly name is
  `<device name> <entity name>`; the name is read from the latest listing, so a renamed declaration follows.
- A declared entity is available only while `app_list_trusted` holds, its instance is in the listing, and
  the instance's status is `running` (parent D9), whether or not the instance is in hassette's current config:
  hassette's command route admits commands for a running instance it still tracks. Every other status,
  including one newer than the client knows, is unavailable. This differs on purpose from v0.1's app entities,
  which also require the app's `in_current_config`.
- Entities are added from the listing whatever the instance's status, because hassette serves a down
  instance's last declarations (parent D9). The add rule mirrors v0.1's `wanted()` per declared unique_id: a
  declared entity not yet added this session is added when the coordinator's add-rule predicate admits it; one
  added this session is re-added only when its registry entry is gone and its instance is in current config
  (the listener's per-platform "added this session" set is the only state, and it narrows the predicate, so
  every entity it adds has an app device the coordinator registered). So a user's deletion of an out-of-config instance's device sticks, and a key
  the diff removed comes back when the instance declares it again.
- On a trusted poll, a declared entity whose instance is `running` but no longer declares its
  `(key, platform)` is removed from the entity registry, and a running instance that declares nothing loses
  its instance device. A non-running instance is never diffed (parent D9). One poll is enough: under parent D8 a
  running instance's set changes only when it reinitializes, and an unintended removal (a conditional
  declaration that changed on reload) is recoverable because HA keeps a removed entity's record and restores
  its entity_id, name, area, labels and disabled state when the same unique_id returns, as ESPHome's one-list
  diff relies on too.
- An entity on a platform this integration doesn't know is skipped with a debug log (posture).
- `default_entity_id`, when set, becomes the entity's `entity_id` at construction; the entity registry keeps
  an existing entity_id for a registered unique_id, so it applies only on first creation (parent D12).
- Switch `is_on` is the declared `value`, and a `value` of `None` reports the switch as unknown; sensor native value is the declared `value` (converted per D4),
  with `unit`, `device_class`, `state_class` and `options` mapped to HA's sensor fields, and `attributes` as
  the extra state attributes. An `enum` sensor passes `options` straight through.
- A press calls `client.press_button`, a toggle `client.set_switch` (parent D4), then requests a coordinator
  refresh, as v0.1's `run_action` does. Commands use the poll client (`coordinator.client`, the client's
  default timeout), not `action_client`: the route returns once the command is dispatched (parent D5), and
  `ACTION_TIMEOUT`'s 45 s exists only because start and reload wait for `on_initialize`.
- `InstanceNotRunningError` and `EntityNotDeclaredError` map to new `ACTION_ERRORS` rows with their own
  translations; `InstanceNotFoundError` maps to the `not_found` validation error (parent D5).
- `async_remove_config_entry_device` allows removing an instance device once the trusted list has no such
  instance, or has it with `in_current_config` false (parent D13). Today an instance identifier matches no
  app and falls through to `return True`, so this is a fix as well as a feature.

## Posture

- **Failure handling:** loud failure, as the parent sets it. Commands are not retried or queued. An entity
  on a platform this integration doesn't know is skipped with a debug log.
- **Compatibility:** v0.1 unique_ids, device fields and identifiers, and config flow are untouched. The change raises the hassette
  floor to 0.66 (API schema 7) and the Home Assistant minimum to 2026.11 (composed naming and dataclass device info, D1), shipped as
  `feat!:` with a `BREAKING CHANGE:` footer, following #77.
- **Scope:** minimal and poll-driven only. `/ws` push and `status_revision`-ordered availability are #73.
- **Rollout:** the PR pins `hassette-client==0.66.0` and can't merge until hassette 0.66.0 is on PyPI
  (release PR hassette#4263). Development may run against hassette's `client/` and `wire/` as editable
  installs first.
- **Hardening:** project defaults; coverage stays at the `pyproject.toml` floor (95%).
- **Altitude:** build; one session builds it in hass-hassette.

## Decisions

### D1: Is an instance device a child device of the app device, or a separate device linked by `via_device`?

**Situation:** parent D1 settled that each instance declaring entities gets its own device under the app's
v0.1 device, without naming the kind of link; this decision settles it. HA Core 2026.9 added
first-class child devices (`ChildDeviceInfo` with `parent_device_id`), and 2026.11 rolled them out across core
integrations alongside its composed-naming release: a child device is a logical part of its parent
that inherits the parent's area unless given its own, inherits `model`, firmware and `configuration_url`,
is removed with its parent, and whose parent's name HA adds to entity display names and new entity_ids
(the `PARENT_DEVICE` name part). HA's own guidance: split multi-part devices into child devices named for
the part ("Outlet 3"), not prefixed with the parent. An app instance is a logical part of an app, not
separate hardware. hass-hassette's HA minimum is 2026.9.0 (`hacs.json`), so the child-device API itself
needs no floor change (the pinned 2026.10.4 already has the `PARENT_DEVICE` name part); the floor rests on
2026.11 being the composed-naming release D2 relies on ("Name it once, Home Assistant
adds the context", 2026.11 release) and the dataclass `ChildDeviceInfo` (`DeviceInfo is now a dataclass`,
HA developer blog 2026-10-13).

**Deciding factor:** follow HA's current device model (the project's HA-conventions rule) so instance
entities name and group correctly with no prefixing.

| | A: Child device of the app device | B: Separate device, `via_device` the app device |
|---|---|---|
| Fits what an instance is | Yes: a logical part of the app | Says the instance is reached through the app, like hardware behind a bridge |
| Entity names and ids | HA composes app + instance + entity itself | The device name must carry the app name to stay unambiguous (Reolink's pattern, which HA asks integrations to drop) |
| Area per instance | Inherits the app's area; each can be given its own | Own area |
| Device fields | Name only; model and URL come from the app device | Name, model, URL, entry type set and kept in step by the coordinator |
| Removing the app device | Removes its instance devices too | Instance devices stay, unlinked |
| HA minimum | 2026.11, for the composed naming and dataclass device-info API (the child-device API dates to 2026.9) | 2026.9 (unchanged) |
| Peers | Shelly (PR #189783), ESPHome `relation: child`, Z-Wave, MQTT, as of 2026.11 | Shelly and Reolink before 2026.11; ESPHome `relation: via` for separate hardware |

**Recommendation:** A, because it is the model HA now prescribes for exactly this shape, it gives correct
names without prefixing, and it needs less upkeep than B.
**Pick B instead if** you need to keep supporting HA older than 2026.11 and don't rely on its composed naming.
**Reversibility:** hard (switching later changes device identity and entity_id generation for users).
**Ratified:** Chose a child device of the app device over a separate `via_device`-linked device, to follow HA's current device model so HA composes app, instance and entity names itself with no prefixing, accepting a Home Assistant minimum of 2026.11: the child-device API dates to 2026.9, but 2026.11 is the composed-naming release D2 relies on and brings the dataclass `ChildDeviceInfo` (challenge Finding 5 corrected the stated reason; the floor stands). This refines parent D1, which settled "on demand, under the app device" without naming the link kind.

### D2: What is an instance device named?

**Situation:** the parent left the instance device's name to this pickup. `instance_name` is required in
hassette config, with no default, and is lowercase letters, digits and `_`: multi-instance apps use names
like `bedroom` and `living_room`, single-instance apps usually something like `main`. Entities use
`has_entity_name`. Under D1's child device, HA adds the app device's name as context to display names and
new entity_ids, so the instance device's own name should be the instance alone (HA's convention: name
each thing for what it is, no name repeats another). HA's own names are sentence case ("Living room").

**Deciding factor:** the name follows HA's naming convention and reads naturally on its own (area pages,
voice) and with HA's added context.

| | A: Instance humanized, sentence case ("Living room") | B: Raw instance name ("living_room") | C: App + instance ("Cameras Cast Living room") |
|---|---|---|---|
| Shown with HA's context | "Cameras Cast › Living room › Show cameras" | "Cameras Cast › living_room › Show cameras" | App name appears twice |
| Shown alone (area page, voice) | "Living room" | "living_room" | "Cameras Cast Living room" |
| Follows HA's naming convention | Yes | Reads as an identifier, not a name | No: repeats the parent's name, which HA asks integrations to drop |
| Single-instance app (`main`) | "Main", in context "Monarch › Main" | "main" | "Monarch Main" |
| Users can rename | Yes: `name_by_user` is never overwritten | Same | Same |
| Peers | Shelly's default "Output 0", ESPHome user-named sub-devices | — | Reolink, Shelly before 2026.11 |

**Recommendation:** A, because it is the convention HA and its child-device peers follow, and HA supplies
the app context itself.
**Pick B instead if** you want the device to show exactly the config value. **Pick C instead if** D1 went
to `via_device`.
**Reversibility:** medium (the name seeds the default entity_id of each new entity; changing it later
renames devices but leaves existing entity_ids alone).
**Ratified:** Chose the instance name humanized in sentence case (`living_room` → "Living room") over the raw config value or an app-prefixed name, to follow HA's name-each-thing-for-itself convention while HA adds the app as parent context, accepting that a generic name like `main` reads as "Main" alone.

### D3: Does this change order listings by revision, or does that wait for #73?

**Situation:** the parent's Breakdown gives this issue "polled entity state applied when its revision is
equal to or newer than the last applied" (parent D3). The listing carries one `boot_id` and one hassette-wide
`entity_revision`, and a listing may be applied to any instance whose last-applied revision is equal or
older. With polling only, every update comes from a full listing, and HA serializes every coordinator
refresh: the scheduled refresh, `async_refresh` and the debounced `async_request_refresh` all run
`_async_refresh` under the debouncer's lock (HA `helpers/update_coordinator.py`, `helpers/debounce.py`). So
listings are fetched and applied one after another and no older listing can overwrite a newer one. The
ordering exists to merge pushed per-instance deltas with listings, which arrives with #73.

**Deciding factor:** carry machinery only when this change has a failure it prevents.

| | A: Defer to #73 | B: Track per-instance marks now |
|---|---|---|
| Failure prevented in #72 | None exists: refreshes are serialized | None exists |
| New state | None | `boot_id` plus a per-instance last-applied revision on the coordinator, reset on a new `boot_id` |
| Stale listing handling | Applied like any poll, as v0.1's app status is | Per-instance merge: keep the previous instance data where the listing is older |
| Testable in #72 | n/a | No: nothing in #72 can produce an older listing |
| #73 | Adds marks together with the push path that needs them | Reuses them |
| Parent | Supersedes Breakdown issue 5's "D3 (revision)" share; parent D3's rule still governs #73 | As planned |

**Recommendation:** A, because under polling no listing can arrive out of order, and #73 can build and test
the marks against the push path that actually needs them.
**Pick B instead if** you want #72 to ship the full parent plan and #73 to only add the push side.
**Reversibility:** easy.
**Ratified:** Chose deferring revision ordering to #73 over tracking per-instance marks now, to carry no machinery that prevents no failure under polling (HA serializes coordinator refreshes, so no older listing can be applied after a newer one), accepting that #73 adds the marks with the push path. This moves parent Breakdown issue 5's "D3 (revision)" share to #73; parent D3's rule is unchanged.

### D4: How do timestamp and date sensor values reach HA?

**Situation:** parent D12 says the integration passes values through and leaves rejections to HA's log.
On the wire a timestamp sensor's value is an ISO 8601 string with offset (`2024-01-01T12:30:00Z`, hassette
spec 158), a date sensor's an ISO 8601 date. HA's `SensorEntity` requires a `datetime` with a timezone for
the `timestamp` device class and a `date` for `date`, and raises `ValueError` on every state write when
given a string. So passing through makes every timestamp and date sensor fail.

**Deciding factor:** declared timestamp and date sensors work at all.

**Recommendation:** parse in `sensor.py`: a `timestamp` value through `homeassistant.util.dt.parse_datetime`,
a `date` value through `date.fromisoformat`. A value that doesn't parse shows the sensor as unknown and logs
a warning once per entity and value, since hassette's `set()` already enforces the type (parent D12) and a
bad value means a hassette bug. Every other value still passes through. This supersedes parent D12's
pass-through for these two device classes only.
**Pick pass-through instead if** you'd rather hassette send values HA accepts as-is, which JSON can't do for
these two classes.
**Reversibility:** easy.
**Ratified:** Chose parsing `timestamp` and `date` sensor values in `sensor.py` (an unparseable value shows unknown with one warning per entity and value) over passing strings through, so those sensors work at all, accepting a small conversion step that supersedes parent D12's pass-through for these two device classes only. Accepted limitation (challenge Finding 7): a value HA rejects despite this parsing (a timestamp without an offset, or a device or state class this HA version doesn't know) leaves that sensor stuck with a logged traceback each poll until hassette is fixed, since each trigger needs hassette to break its own validation.

### D5: What happens to the entities of an instance that is no longer in the listing?

**Situation:** hassette lists a stopped or failed instance with its last declarations, so those entities stay
and show unavailable. An instance drops out of the listing only after hassette's startup prune of an
instance removed from config (parent D13), or when its app leaves the listing. Its registry entries remain.
v0.1's `add_app_entities` re-creates entities from registry entries of departed apps, so they show as
unavailable entities of this integration rather than as HA's "no longer provided" orphans. For declared
entities the registry entry still has the platform and unique_id but not the declaration (name, sensor
fields).

**Deciding factor:** the user can see and remove a departed instance with the least new code.

| | A: Leave them to HA | B: Restore them from the registry, as v0.1 does for apps |
|---|---|---|
| How they show | HA's "This entity is no longer being provided" (unavailable, restored) | Unavailable entities with no current declaration |
| Device removable | Yes, through D13's rule (absent instance) | Same |
| New code | None | Registry scan per platform and a declaration-less entity variant |
| Matches v0.1 | No | Yes |
| Peers | ESPHome: entities no longer provided stay as orphans until removed | — |

**Recommendation:** A, because the departed instance's device is already removable through D13's rule, and
HA's own orphan state says exactly what happened with no extra code.
**Pick B instead if** you want declared entities to look the same as v0.1's departed-app entities.
**Reversibility:** easy.
**Ratified:** Chose leaving entities of an instance absent from the listing to HA's "no longer provided" state over restoring them from the registry, so a departed instance is visible and removable through parent D13's rule with no new code, accepting that these entities look different from v0.1's departed-app entities.

## Assumed

- Parent D1 (an instance device on demand under the app device; D1 here settles its kind), D4, D5, D6
  (state keyed by platform, so a moved key is a new entity), D8 (declarations only during `on_initialize`), D9, D11,
  D12 (suggested entity id) and D13 hold as ratified, and this change implements
  their integration side. Evidence: hassette `design/specs/155-hacs-app-entities/design.md`.
- Parent D9's accepted limitation "pushed `app_status_changed` carries no revision" no longer holds:
  `AppSummary.status_revision` and `AppStatusChangedWsMessage.status_revision` exist, and a pushed status
  applies only when strictly greater. That is for #73 to use; polling here takes each listing's statuses as-is.
  Evidence: hassette `wire/src/hassette_wire/apps.py` (`AppSummary.status_revision`), `wire/src/hassette_wire/ws.py`.
- `AppListResponse` carries `boot_id` and a listing-wide `entity_revision`, not a per-instance revision.
  Evidence: hassette `wire/src/hassette_wire/apps.py` (`AppListResponse`).
- `AppInstance.entities` is `[]` for an instance that declares nothing and is filled on `GET /api/apps`; a
  stopped instance keeps its last declared entities. `None` means "not included", not "none", so an instance
  whose `entities` is `None` is skipped by both the add path and the diff (left as it was); a test pins it.
  Evidence: same file, `AppInstance.entities`.
- `DeclaredEntity` is one flat shape for all platforms, with `platform` an open literal, so an unknown
  platform parses and can be skipped. Evidence: same file, `DeclaredEntity`; `OpenIntegrationPlatformName`.
- hassette validates `default_entity_id` as `<platform>.<object_id>`, so its domain always matches the
  entity's platform. Evidence: hassette `src/hassette/integration/declarations.py` (`validate_default_entity_id`).
- `press_button` and `set_switch` raise `InstanceNotFoundError`, `InstanceNotRunningError` and
  `EntityNotDeclaredError`, and return once the command is published. Evidence: hassette
  `client/src/hassette_client/client.py`, `client/src/hassette_client/errors.py`.
- `instance_name` and entity keys contain no `-`, and v0.1's framework keys contain none, so three-segment
  unique_ids and the `{app_key}-{instance_name}` identifier can't collide with v0.1's and can be split on
  the first `-`. Evidence: hassette `resolve_instance_name` / `instance_name_reason`; `APP_KEY_PATTERN` in
  `coordinator.py`; parent D11.
- HA keeps a registered unique_id's existing entity_id over an `entity_id` the entity sets before being
  added. Evidence: HA entity registry `async_get_or_create`; MQTT's `default_entity_id` relies on it.
- HA child devices (available since Core 2026.9; `ChildDeviceInfo`: identifiers, name, `parent_device_id`, suggested area, translation
  fields only) require the parent to be registered by the same config entry, inherit the parent's area unless
  given their own, are removed with their parent, and contribute the parent's name through the
  `PARENT_DEVICE` entity name part. Evidence: HA `helpers/device_registry.py` (`ChildDeviceInfo`,
  `async_remove_device`), `helpers/entity_registry.py` (entity name parts); home-assistant/architecture
  discussion #1414; HA blog "Naming things in Home Assistant" (2026-11-03).
- HA's sensor platform rejects a string for `timestamp`/`date` device classes. Evidence: HA
  `homeassistant/components/sensor/__init__.py` (`SensorEntity.state`).
- A user's device rename is stored as `name_by_user` and survives `async_update_device(name=...)`.
  Evidence: `coordinator.py` `update_app_devices` docstring and HA device registry.
- After a toggle, the switch shows its new state only once the handler returns and a poll reads it (parent
  D3's "up to 30 s" under polling, D7); push in #73 removes the lag. The action refresh can land before a
  slow handler finishes. No user-facing note: #72 and #73 ship in the same hass-hassette release, so no release carries the lag.

## Build

- [x] Implementation and tests committed
- [x] Docs
- [x] Ship-time challenge

**Calls made during the build:**
- The `pytest-homeassistant-custom-component` pin stays at 0.13.375 (HA 2026.10.4), and CI's `FLOOR_PHCC` stays at 0.13.363: no build for HA 2026.11 exists yet. The device-info code builds `ChildDeviceInfo` with keyword arguments, which give the same result from 2026.10.4's `TypedDict` and 2026.11's dataclass. `hacs.json` already says 2026.11.0, so the floor job fails until both pins move to a 2026.11 build, as does `uv.lock` until hassette-client 0.66.0 is on PyPI. Both are merge blockers; the user approved this plan at the start of the build.
- Instance devices are HA child devices, which the device registry indexes apart from main devices: the diff and the tests find them with `async_get_child_device_by_identifier`, and `async_remove_config_entry_device` takes a `ChildDeviceEntry` too.
- The add listener skips an app with no device (`async_get_device_by_identifier`, not `async_get_device_id_by_identifier`, which raises). hassette lists departed apps that may have no device; such an app has nothing the add rule admits.
- A declared entity is also unavailable once its declaration is gone from the listing: its value, name and sensor fields come from the declaration.
- `InstanceNotFoundError` maps to its own `instance_not_found` validation message rather than reusing `not_found`'s app wording; it is still a `ServiceValidationError`, as parent D5 says.
- hassette-client 0.66 adds `starting` and `stopping` app statuses. The v0.1 status sensor already lists every status, so they get translations, icons and README rows. `CONTROLLABLE_STATUSES` is unchanged, so the v0.1 switch and reload button stay unavailable during them, as for any status they don't list.
- The app device's full fields moved into one `app_device_spec` in `coordinator.py`, used by the v0.1 entity and by the coordinator's registration, and v0.1's `run_action` and the declared commands share `run_command` in `entity.py`.
- Unknown-platform declarations are logged at debug on each poll by the coordinator, not by the three platform listeners.
- Declared switches (and buttons) show the declaration's `attributes` too, not only sensors: hassette's `IntegrationSwitch.set(attributes=)` publishes them (ship-time challenge Finding 4, the user's choice).
- A sensor device or state class this HA doesn't know is left out with one warning, on add and on update (`try_parse_enum`, as ESPHome does), instead of raising. This narrows D4's accepted limitation to values HA still rejects after parsing, such as a timestamp without an offset (ship-time challenge Finding 5, the user's choice after a peer check).
- Removing a declared entity or an instance device logs at INFO, since the user sees it disappear.
