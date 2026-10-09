# Design: hass-hassette v0.1

**Date:** 2026-10-08
**Status:** built
**Mode:** sketch

## Summary

Build `hass-hassette` v0.1 in this repo: a HACS custom integration (domain `hassette`) that
connects Home Assistant to one hassette server through the published `hassette-client` library and
represents every hassette app as an HA device with a running switch, a reload button, and a status
sensor. This is unit C of hassette's HACS epic (NodeJSmith/hassette#45, milestone *HACS v0.1*). The
epic brief (`design/specs/113-hacs-companion-integration/brief.md` in the hassette repo, "Owned by C")
holds decisions ratified in the 2026-09-24 define interview. They are inherited here under
**Assumed**, translated to the client API that shipped in hassette (pin: D3). This ledger only
decides what that brief left open or what has changed since.

**Governing constraint:** match HA/HACS conventions. A divergence needs a stated, strong reason.
The reference implementation is HA core's `portainer` integration (`homeassistant/components/portainer/` at core tag 2026.10.0,
Platinum): containers as devices behind a URL + token config flow, start/stop switches, restart
button, coordinator polling.

**Files this change creates** (none exist yet; the repo holds only `README.md` and `LICENSE`):

- `custom_components/hassette/`: `__init__.py`, `config_flow.py`, `const.py`, `coordinator.py`,
  `entity.py`, `switch.py`, `button.py`, `sensor.py`, `manifest.json`, `translations/en.json`
  (custom integrations ship only the built translation file; there is no `strings.json` source step), `icons.json`, `quality_scale.yaml`, `brand/icon.png`
- `hacs.json`, `README.md` (rewritten)
- `tests/`: pinning the behavior each decision and assumption below specifies
- Repo tooling and CI per D11 and D12

**In scope:** everything in the brief's v0.1 row for hass-hassette: single-entry config flow with
reauth, coordinator, per-app devices and entities, dynamic add and user-initiated removal of non-current devices (D16), conventional
errors, tests, hassfest/HACS CI, a HACS release, plus one repair issue for a too-old server (D7).
A reconfigure step is also in scope (D17), because a URL change would otherwise mean deleting the entry
and losing every device's registry customizations. One hassette-repo change is in scope as well: the
demo-stack override that mounts a local hass-hassette checkout (D15). hassette#2610 (409
`action_in_progress`) is a separate hassette issue in the same milestone; D8 and D9 account for
servers without it.
**Out of scope:** diagnostics, any other repair issues, zeroconf/Supervisor discovery, per-instance entities, app-declared entities,
services, the HACS default store, and per-client tokens (brief). Unit D (hassette#2506: pinned
install in hassette's system tests, E2E test, hassette docs page) lives in the hassette repo.

## Decisions

### D1: Which `integration_type` does the manifest declare?

**Deciding factor:** what HA's integration-type definitions say about one server fronting many devices.

| | A: `hub` | B: `service` |
|---|---|---|
| HA definition | "A gateway to multiple devices or services" — one hassette server, many app devices | "A single service" — fits a cloud/API with no device fan-out |
| Core precedent | zwave_js, matter | portainer (containers as devices) chose `service` |
| UI effect | Shown under Devices & Services as a hub; devices listed under it | Same listing; HA treats it as a service when grouping |
| Reversibility | Changing later is a manifest edit, no registry migration | Same |

**Recommendation:** A, because hassette is a server that fronts many app devices, which is HA's
definition of a hub; Portainer's `service` reads like a historical choice, not a rule.
**Pick B instead if** you see hassette as "a software service" the way Portainer's author did and
want to match the closest analog exactly.
**Reversibility:** easy (manifest field only)
**Ratified:** Chose `hub` over `service`, to match HA's definition of a server fronting many devices, accepting a different choice from the closest core analog (portainer).

### D2: What is the minimum HA version, and how is it kept honest?

**Deciding factor:** the declared floor must be a version CI actually tests, without carrying
two validation libraries.

HA replaced voluptuous with `probatio` as its validation engine in 2026.9.0 (core `cbde7ecf465`,
#175128) and removed voluptuous from its requirements in the same change. Every core config flow
now uses `probatio` (844 files, 0 voluptuous). `probatio` is a drop-in for voluptuous's API.
`pytest-homeassistant-custom-component` (phcc) is pinned per HA release: 0.13.371 ↔ 2026.10.0,
0.13.363 ↔ 2026.9.0.

| | A: floor 2026.9.0, `probatio` only, CI tests floor + latest | B: floor 2026.10.0, `probatio` only, CI tests latest | C: floor below 2026.9, conditional import (`probatio` else `voluptuous`), CI tests both sides |
|---|---|---|---|
| Users reached | Anyone on 2026.9+ | Anyone on 2026.10+ (one month narrower) | Older HA too |
| Floor tested | Yes: a phcc job pinned to 0.13.363 (`homeassistant==2026.9.0`) | Only until the first phcc bump; then the floor goes untested | Needs a pre-2026.9 phcc job plus a current one |
| Code paths | One | One | Two validation imports; `probatio.Secret` and anything probatio-only needs a fallback |
| Convention | Matches current core | Matches current core | Diverges from core, for users who haven't updated in 2+ months |
| CI cost | Two test jobs | One test job | Two jobs, with a stale HA that drifts further every month |

**Recommendation:** A, because it covers every HA that ships `probatio` with a single code path,
and the floor job tests the version `hacs.json` claims, which addresses the prior art's
min-version-drift failure mode. The floor rises only when the code starts using a newer HA API, and
that same PR bumps the floor job's phcc pin and `hacs.json` together.
**Pick B instead if** a second CI job isn't worth one month of extra reach.
**Pick C instead if** you expect users on HA from before September 2026. For a new integration
whose first users are you and early adopters, that's unlikely.
**Reversibility:** easy (raise or lower the floor in a later release)
**Ratified:** Chose a 2026.9.0 floor with `import probatio` and a CI job pinned to the floor's phcc over a 2026.10 floor or a conditional import, to cover every probatio-era HA with one code path and a tested floor, accepting a second test job and no support for pre-2026.9 HA.

### D3: How does `manifest.json` pin `hassette-client`?

**Deciding factor:** a published zip can't be changed afterward, so its requirement must already be
safe for users who never update; follow core's convention for an integration's own library.

Facts: `hassette-client` releases in lockstep with hassette (release-please,
`bump-minor-pre-major`), so its Python API may break on any minor. Every published hass-hassette zip
keeps its requirement forever. HA reinstalls requirements on fresh installs and core-container
updates, with pip `--constraint` (core `homeassistant/util/package.py:174-175`), so pip backtracks
around dependency-floor conflicts, but not around an API break. Server compatibility is gated by
`api_schema_version` (D7), not by the client's version. Core integrations exact-pin their own
library, and hassfest enforces `==` for core (`script/hassfest/requirements.py:421-424`):
`pyportainer==1.0.47`, `music-assistant-client==1.5.1`, `zwave-js-server-python==0.73.1`. Hassfest's
"no exact pin" rule for custom integrations (`requirements.py:579-593`) covers only packages HA
itself depends on, which `hassette-client` is not. HACS integrations that own their library pin it
exactly too. Renovate has a native `homeassistant-manifest` manager.

| | A: exact pin (`hassette-client==0.56.0` at first release), Renovate bumps it | B: cap at the next minor (`>=0.56.0,<0.57`) | C: `>=0.56.0,<1` + daily CI against the newest client |
|---|---|---|---|
| Already-shipped zips stay installable | Yes | Yes, unless a client patch breaks the API | No: a later minor that breaks the API breaks every older zip |
| Matches core convention | Yes | Close | No |
| Client patch fixes reach users | Via an integration release | Automatically | Automatically |
| Release churn | One per hassette release, all automated (Renovate PR → release-please) | One per hassette minor | Only on breaks |
| Allowed by hassfest for a custom integration | Yes | Yes | Yes |

**Recommendation:** A, because it's what core does for the closest analogs, it's fully
reproducible, and its churn is a mechanical merge. A user on any hassette server version is still
served by the schema gate.
**Pick B instead if** you want client patch releases to reach users without an integration release.
**Pick C instead if** never: a range open to future minors leaves shipped zips unprotected.
**Reversibility:** easy (next release changes the pin)
**Ratified:** Chose an exact `hassette-client==0.56.0` pin bumped by Renovate's `homeassistant-manifest` manager over a minor cap or `<1`, to match core's convention for an integration's own library and keep every shipped zip installable, accepting one automated integration release per hassette release.

### D4: Is there a device for the hassette server itself, with app devices linked under it?

**Deciding factor:** HA's device model for a hub that fronts other devices.

| | A: Server device + apps `via_device` | B: App devices only |
|---|---|---|
| HA convention | Hubs register the hub device and link children with `via_device` (portainer endpoint → containers, zwave_js controller → nodes) | Acceptable, but the device page shows no hierarchy |
| Where server facts live | `sw_version` = server version, `configuration_url` = hassette URL, `entry_type: service` | Nowhere in the device registry |
| Room for v0.2 server-level entities | Natural home (e.g. connected/version sensors later) | Would need adding a device then |
| Cost | One more device; registered once at setup before platforms load (portainer's pattern) | None |

**Recommendation:** A, because it's the standard hub shape and gives the server version and a link
to hassette's UI a home at no real cost. The server device's identifier is fixed by D5 and must
not collide with any app device's identifier. Every device is registered with `entry_type: service`
(software, not hardware; `entry_type` is a device-registry field). Each app device's `configuration_url` points at hassette's app page,
`{url}/apps/{app_key}`, so "Visit" opens that app in hassette.
**Pick B instead if** you want the smallest possible v0.1 and are fine adding the hub device when
v0.2 needs it. That later addition is easy, but it re-parents existing devices.
**Reversibility:** easy now, more awkward after release (re-parenting devices)
**Ratified:** Chose a server hub device (identifier per D5) with app devices linked `via_device` over app devices only, to follow HA's hub device model and give server version and UI links a home, accepting one extra device.

### D5: What are the unique_id and device-identifier formats?

**Deciding factor:** this is a one-way door: stable across restarts, reinstalls and v0.2's
per-instance entities, and following HA's unique-ID rules.

HA's rules: a unique_id must be unique per platform within the domain; never use a URL, IP, or
user-changeable name. Config `entry_id` is a last resort, used only when no stable source ID exists.
Portainer started without an entry_id prefix and later migrated to one (`__init__.py` migration
v4) because it allows multiple entries. We have `single_config_entry`.

app_keys match `^[a-zA-Z_][a-zA-Z0-9_.]{0,127}$` (hassette `src/hassette/web/routes/apps.py:37`):
letters, digits, `_` and `.`, never `-` or `:`. So `_` is ambiguous as a separator: v0.2's
`{app_key}_{instance_name}_{key}` can't tell app `a_b` + instance `c` from app `a` + instance `b_c`.
An app may also be named `server`, which would collide with D4's hub identifier.

| | A: `{app_key}_{key}` (brief) | A′: `{app_key}-{key}`, `-` outside the app_key alphabet | B: `{entry_id}_{app_key}_{key}` | C: `{server identity}_{app_key}_{key}` |
|---|---|---|---|---|
| Unique within domain | Yes in v0.1; v0.2's instance segment makes it ambiguous | Yes, unambiguous for every future segment | Yes in v0.1 (same v0.2 ambiguity) | Same as B |
| Survives delete + re-add of the integration | Same IDs | Same IDs | New IDs (history and customizations are tied to the old registry entry) | Same, if hassette had a stable identity |
| Exists today | Yes | Yes | Yes | No: hassette has no server ID (the brief dropped it) |
| If `single_config_entry` is ever lifted | Needs a migration (portainer's v4 path) | Needs a migration | Already safe | Already safe |
| Matches brief | Yes | Separator differs | No | No |

Keys are `running` (switch), `reload` (button), `status` (sensor). Under A′, the app device
identifier is `(DOMAIN, app_key)` and the hub device's is `(DOMAIN, "-server")`, which no app_key can
equal. v0.2 extends to `{app_key}-{instance_name}-{key}`; that spec must confirm `-` is outside the
instance_name alphabet, or encode the segment. Renaming an app's `app_key` in hassette's config makes
it a different app: the old device goes unavailable and the user can delete it (D16), and a new
one appears.

**Recommendation:** A′, because a separator no app_key can contain makes the format unambiguous
now and for v0.2's extra segment, and it also keeps the hub identifier collision-free. It meets
HA's unique-ID rules without the last-resort entry_id.
**Pick A instead if** you'd rather match the brief's format exactly and settle v0.2's ambiguity
when that spec arrives (it would mean a registry migration then).
**Pick B instead if** you think multiple hassette servers per HA is plausible within a year. The
brief says it has no expected users.
**Pick C instead if** hassette gains a stable server ID first; that's server work outside v0.1.
**Reversibility:** hard (changing it later needs an entity-registry migration)
**Ratified:** Chose `{app_key}-{key}` unique_ids, app device identifier `(DOMAIN, app_key)` and hub identifier `(DOMAIN, "-server")` over the brief's `_` separator or an entry_id prefix, to stay unambiguous through v0.2 with no hub collision, accepting a migration if multiple config entries are ever allowed.

### D6: How are devices and entities named and categorized?

**Deciding factor:** HA's `has-entity-name` / `entity-translations` conventions, including the
"main feature" rule.

| | A: Switch is the main feature (name `None`); button and sensor translated; reload button `CONFIG` | B: Every entity has a translated name; no categories |
|---|---|---|
| Resulting entity_ids | `switch.motion_lights`, `button.motion_lights_reload`, `sensor.motion_lights_status` | `switch.motion_lights_running`, … |
| HA convention | Main feature takes the device name (`_attr_name = None`); actions that change config/state get `EntityCategory.CONFIG` (portainer's restart button) | Valid, but ignores the main-feature rule |
| Dashboard default | The app's on/off toggle is the headline; reload is tucked in the device's Configuration section | All three equal |

Shared details for both: app device `name` = `AppSummary.display_name`, `model` = `class_name`,
`manufacturer` = "Hassette". The status sensor is `SensorDeviceClass.ENUM` with the status options
(Assumed "Statuses"), translated state names, and per-state icons via `icons.json` (`icon-translations`). The
switch is `SwitchDeviceClass.SWITCH`. The server device is named "Hassette".

Status sensor attributes (both in `_unrecorded_attributes`, so the recorder never stores them):
- `server_status`: the raw status string, present only while the state is `unknown` (a status newer
  than the client, `hassette_wire.UnknownValue`). The `unknown` option name follows core precedent
  (core `proxmoxve/sensor.py:157`, `iometer/sensor.py:68`), even though templates can't tell it from
  HA's own "no value".
- `error_message`: `AppSummary.error_message`, truncated to 300 characters like D9's
  `action_failed` detail, while the app is `failed` or `degraded`, otherwise absent. Never
  `error_traceback`.

**Recommendation:** A, because the running switch is what you control the app with, and HA's rule
is that the main feature takes the device's name.
**Pick B instead if** you want entity_ids to say what each entity does, even for the switch.
**Reversibility:** easy for names and categories (no registry identity involved)
**Ratified:** Chose the running switch as the main feature (name `None`) with a `CONFIG` reload button, plus unrecorded `server_status` (when unknown) and `error_message` (when failed or degraded) sensor attributes, over naming every entity, to follow HA's main-feature rule and make failures diagnosable in HA, accepting app exception text in a non-recorded attribute.

### D7: How does the integration react to the server's API schema version?

**Deciding factor:** a server too old fails clearly and heals itself after upgrade; a server newer
than the client keeps working.

`hassette_client.check_server_version(health)` raises `UnsupportedServerVersionError` when the
server's `api_schema_version` is below the client's `MIN_API_SCHEMA_VERSION`. The client never calls
it itself. hassette's spec 114 brief (`design/specs/114-hassette-client/brief.md`, "Version skew") says the integration should also log a warning when the server is *newer* than
its client. The client exposes no helper for that, but `hassette_wire.API_SCHEMA_VERSION` gives the
client side's value.

HA retries a `ConfigEntryNotReady` setup after `min(5 × 2^tries, 600)` seconds and logs each retry
at INFO (core `homeassistant/config_entries.py:809-820` at 2026.10.0). After about 11 minutes that's one
`GET /api/health` every 10 minutes, invisible at HA's default log level. That's the same path an
unreachable hassette already takes. A NotReady or Error reason is shown only on the integration's
card under Settings → Devices & services. A repair issue (Settings → Repairs, with a sidebar badge) is
HA's way to tell a user they must act outside HA.

| | A: NotReady | A+R: NotReady + repair issue | B: `ConfigEntryError` (no retry) | C: Flow only; setup doesn't re-check |
|---|---|---|---|---|
| Load while the user hasn't upgraded | One health GET per 10 min once retries reach the cap (~11 min) | Same | None | None (no re-check) |
| After upgrading hassette | Heals within ≤10 min | Heals; the repair clears itself | Stays failed until the entry is reloaded | Nothing to heal; failures stop |
| User notices without going looking | No: the reason is only on the integration card | Yes: Repairs plus a sidebar badge | No | Entities fail with confusing errors |
| Scope | As the brief | Adds one repair issue (the brief had listed repair issues as out) | As the brief | Less |

**Recommendation:** A+R, because the user must act outside HA, and a repair issue is how HA says so,
while the retry heals the entry on its own after the upgrade.

Behavior under A+R:
- Config flow: a too-old server aborts the flow with `unsupported_version`, giving the server's version
  and schema and the minimum schema.
- Setup: `check_server_version` runs on the first refresh. A failure creates the repair issue
  `unsupported_version` (`is_fixable=False`, severity ERROR, translated with `server_version`,
  `api_schema_version`, `min_api_schema_version`, and a link to hassette's upgrade docs) and raises
  `ConfigEntryNotReady` with the same translation placeholders.
- While running: every poll reads `get_health()` (D16) and runs `check_server_version` on it (a
  local comparison on the payload already fetched). A failure creates the same repair issue and
  fails that poll with `UpdateFailed(unsupported_version)`, so entities go unavailable. Only the
  newer-server warning keys on a change of `version`; the hub device is re-registered every poll
  (Build calls).
- The repair issue is deleted on the first poll that passes the check, and when the entry is
  unloaded or removed.
- Newer server: when `api_schema_version` is above `hassette_wire.API_SCHEMA_VERSION`, one WARNING
  is logged per distinct server version, and the integration continues (lenient parsing).

**Pick A instead if** a message on the integration card is enough.
**Pick B instead if** you'd rather HA never retry against an incompatible server.
**Pick C instead if** you consider a hassette downgrade not worth handling.
**Reversibility:** easy
**Ratified:** Chose NotReady plus a self-clearing `unsupported_version` repair issue, re-checked on every poll, over NotReady alone or `ConfigEntryError`, so the user is told to upgrade hassette whenever the server changes under a running HA and the entry heals afterward, accepting a per-poll health read.

### D8: What timeout do app actions get?

**Deciding factor:** don't report a still-running action as failed, and don't leave a dashboard
toggle hanging.

The server answers an action only after it finishes, including the app's `on_initialize()`. The
client's default `request_timeout` is 10 s, and its docs say to use a second client with a longer
timeout on the same session for actions. A timeout raises `HassetteTimeoutError`, meaning the
outcome is unknown.

| | A: 45 s action client | A₀: 30 s | B: 10 s (default) | C: 60 s+ |
|---|---|---|---|---|
| Slow app start (initializing 10–30 s) | Succeeds | Succeeds | Reported as unknown outcome while it actually works | Succeeds |
| hassette's worst-case reload (10 s shutdown + 20 s startup timeout, `config/models.py:341,344`) | Finishes with 15 s margin | Races the deadline | Times out | Finishes |
| Hung app | UI waits 45 s, then "outcome unknown" | 30 s | 10 s | A minute of spinner |
| Retry safety after timeout | start/stop converge; reload restarts again (Assumed) | Same | Same, but happens more often | Same |

The polling client keeps the 10 s default. The timeout covers all three actions: start waits for
`on_initialize()`, stop for shutdown, and reload for both. Measured on the maintainer's hassette
(production apps, full concurrent boot under a 0.5-CPU limit, 2026-10-08): most apps initialize in under
1 s, and the slowest (`otf`, a network login) takes ~5.3 s.

The timeout covers only this action's own run: a concurrent action on the same app is rejected
at once with 409 `action_in_progress` (hassette#2610, D9), so a request never waits behind another
action's lock. Against a hassette without #2610, a same-app action can still queue and time out.

**Recommendation:** A, because 45 s clears hassette's own worst-case reload with margin and covers
heavier `on_initialize()` work in other users' apps (this ships with a framework), while staying
within what a person will wait for an action that is genuinely stuck.
**Pick A₀ instead if** you'd rather cap the wait at 30 s and accept that a worst-case reload can
report "outcome unknown". **Pick B instead if** your apps initialize fast and you'd rather fail fast.
**Pick C instead if** some apps do heavy startup work (large history fetches) and you've seen them
take longer than 45 s.
**Reversibility:** easy (a constant)
**Ratified:** Chose a 45 s action timeout on a second client (polling stays at 10 s) over 30 s, 10 s or 60 s, to clear hassette's worst-case reload with margin now that concurrent same-app actions fail fast (hassette#2610), accepting a 45 s wait when an app genuinely hangs.

### D9: How does each client exception map to an HA error?

**Deciding factor:** HA's `action-exceptions` and `exception-translations` rules: caller faults are
`ServiceValidationError`, failures are `HomeAssistantError`, and every one has a `translation_key`.
This translates the brief's status-code mapping onto the client's exception classes.

| | A: one row per client exception the routes can raise, split by surface (below) | B: name only the brief's cases; everything else falls to `unknown` |
|---|---|---|
| Proxied restart (502/504) on an action | "Outcome unknown", the same as a timeout | `unknown` |
| Auth-proxy redirect, WAF 403 | Specific keys that name the cause | `unknown` |
| Translation keys | Roughly twice as many | Fewer |
| Follows the client's documented outcome semantics | Yes | Partly |

**Recommendation:** A, this table:

| Client exception | Config flow | Setup / coordinator poll | Entity action |
|---|---|---|---|
| `AuthenticationError` (401) | `invalid_auth`; without a token, the error text asks for one | `ConfigEntryAuthFailed` → reauth | `HomeAssistantError(invalid_auth)`; the next poll starts reauth |
| `HassetteConnectionError` | `cannot_connect` | `UpdateFailed(cannot_connect)` (`ConfigEntryNotReady` on first refresh) | `HomeAssistantError(cannot_connect)` |
| `HassetteTimeoutError` | `timeout_connect` | `UpdateFailed(timeout_connect)` | `HomeAssistantError(action_timeout)` — "outcome unknown" |
| `UnsupportedServerVersionError` | abort `unsupported_version` | repair issue + `ConfigEntryNotReady` on first refresh, `UpdateFailed(unsupported_version)` after (D7) | n/a |
| `TelemetryUnavailableError` (503) | n/a | `UpdateFailed(telemetry_unavailable)` after D16's one-poll tolerance; never treated as "apps removed" | n/a |
| `RedirectError` (any 3xx: an auth proxy's login page, an http→https upgrade, a moved path; the client never follows redirects) | `redirected`, with placeholder `{location}` = the exception's `location` with userinfo, query and fragment stripped. The text reads: hassette's API redirected to {location}; if that is a login page, add a proxy bypass (README); if it is the https form of your URL, use that. No core integration has a redirect key, because core libraries follow redirects silently; this follows core's form for a known failure mode (a specific key plus placeholders) | `UpdateFailed(redirected)` with the same placeholder | `HomeAssistantError(redirected)` with the same placeholder |
| `GatewayError` (502/504) or `ServiceUnavailableError` without a hassette problem code (plain 503), typically a reverse proxy while hassette restarts | `cannot_connect` | `UpdateFailed(cannot_connect)` | `HomeAssistantError(action_timeout)`: the outcome is unknown, same as a timeout |
| `ForbiddenError` (403, e.g. a WAF or proxy rule; `PathTraversalError` can't occur on these routes) | `forbidden`: the error text names proxy/WAF rules in front of hassette | `UpdateFailed(forbidden)` | `HomeAssistantError(forbidden)` |
| `ResponseValidationError` (incl. `UnexpectedResponseError`) | `cannot_connect` | `UpdateFailed(invalid_response)`; the client's validation message (endpoint, model, field locations, no values) is logged once at WARNING per (endpoint, error type) until the next good poll (Addendum). The client parses a list as a whole, so one malformed app fails the poll (per-element parsing is hassette#2611, not in v0.1) | On a 2xx JSON action response the server already acted: the action counts as done (no error raised) and one WARNING is logged that the response was unreadable. An `UnexpectedResponseError` (a body that isn't JSON, at any status) most likely came from a proxy in front of hassette, so its outcome is unknown: `HomeAssistantError(unexpected_response)`. Otherwise `HomeAssistantError(invalid_response)` |
| `AppNotFoundError` / `InvalidAppKeyError` | n/a | n/a | `ServiceValidationError(not_found)` |
| `AppBlockedError` | n/a | n/a | `ServiceValidationError(blocked_by_filter)` |
| `ConflictError` with code `action_in_progress` (hassette#2610; until the client adds `ActionInProgressError`, an unrecognized 409 code arrives as plain `ConflictError`) | n/a | n/a | `HomeAssistantError(action_in_progress)`: another action on this app is still running |
| `BootstrapNotReleasedError` | n/a | n/a | `HomeAssistantError(not_bootstrapped)` |
| `ActionFailedError` | n/a | n/a | `HomeAssistantError(action_failed)`, with the server's error detail as a placeholder, truncated to 300 characters (it is the failed instance's `error_message`, i.e. exception text from app code) |
| Any other `HassetteClientError` | `unknown` (logged with traceback) | `UpdateFailed(unknown)` | `HomeAssistantError(unknown)` |

Every action requests a coordinator refresh whatever its outcome (success, any error, timeout),
so HA reconciles with the server after cases where the outcome is unknown. A failed start leaves the
app `failed`, and the UI shows that.
**Pick B instead if** you want the smallest key set and accept `unknown` for proxy and gateway failures. The main per-row alternative is starting reauth
directly on an action's 401 (`entry.async_start_reauth`) instead of waiting ≤30 s for the next poll,
which Portainer doesn't do.
**Reversibility:** easy (no persisted state)
**Ratified:** Chose the mapping table as written (now with `action_in_progress`, gateway/503 split by surface, `forbidden`, `redirected` with its target, a refresh after every action, and 2xx-unparseable treated as done), over collapsing unknown failures to `unknown`, to give an accurate message per failure following the client's documented semantics, accepting more translation keys.

### D10: What `PARALLEL_UPDATES` does each platform set? (collapsed)

**Deciding factor:** the Silver `parallel-updates` rule requires an explicit value; hassette owns
same-app concurrency (it rejects a second concurrent action on an app with 409
`action_in_progress`, hassette#2610).

**Recommendation:** `0` (unlimited) for all three platforms. The sensor reads from the coordinator.
Actions on different apps are independent, and hassette rejects a concurrent same-app action
rather than queuing it, so a client limit would only slow a "stop everything" script. HA keeps no
per-app in-flight state. Against a server without hassette#2610 (not yet shipped), same-app actions
queue server-side; a client limit wouldn't prevent that either, so `0` still holds.
**Pick 1 for switch/button instead if** you want Portainer's conservative default, so concurrent
actions queue in HA.
**Reversibility:** easy
**Ratified:** Chose `PARALLEL_UPDATES = 0` on all three platforms over 1 for switch/button, to keep actions on different apps concurrent while hassette owns same-app concurrency (409 `action_in_progress`), accepting no client-side throttle.

### D11: What repo tooling and CI does hass-hassette use?

**Deciding factor:** the HACS-standard layout and HA's own checks, with the same dev workflow you
already use in hassette.

| | A: HACS layout + hassette's toolchain (mise, uv, prek, ruff, pyright) | B: `ludeeus/integration_blueprint` template (devcontainer, scripts/, ruff) |
|---|---|---|
| Familiarity | Same commands as hassette (`mise`, `uv run`, prek hooks) | New workflow |
| HA checks | `home-assistant/actions/hassfest` + `hacs/action` (category integration) in CI either way | Same, preconfigured |
| Tests | phcc + pytest-cov at the Assumed coverage threshold, jobs per D2 | Blueprint ships no meaningful tests |
| Dev HA | Run tests; dogfood on the maintainer's HA via HACS custom repo | Devcontainer runs a local HA |

Shape under A: a non-package `pyproject.toml` (`[tool.uv] package = false`) holding the dev
dependency groups and ruff/pyright/pytest config; `mise.toml` pinning Python 3.14 (floor in Assumed "Platform facts") and prek; `prek.toml` with the hassette subset that applies (whitespace/EOF,
ruff check/format, typos, gitleaks, pyright on pre-push, zizmor for workflows, no-future-annotations);
Renovate via the maintainer's self-hosted instance (register this repo there),
bumping the latest-HA job's phcc pin (the floor job's pin and `hacs.json` move only with a code-driven
floor change, D2), and bumping the `hassette-client` pin via its `homeassistant-manifest` manager per D3.

**Recommendation:** A, because hassfest and the HACS action carry the HA-specific conventions in
either case. What's left is dev workflow, and your hassette workflow is already tuned.
**Pick B instead if** you want a local dev HA instance from the template's devcontainer.
**Reversibility:** easy
**Ratified:** Chose the HACS layout with hassette's toolchain (mise, uv non-package pyproject, prek, ruff, pyright) plus hassfest, HACS action and phcc CI over the integration_blueprint template, to keep one familiar workflow while HA's own checks enforce conventions, accepting no bundled local dev HA.

### D12: How are releases cut?

**Deciding factor:** HACS needs real GitHub Releases with a zip asset (`zip_release: true`,
`filename: hassette.zip` per the brief), and `manifest.json` `version` must match the tag.

| | A: release-please (like hassette) + a workflow that zips `custom_components/hassette` onto the release | B: Manual tag + a release workflow on tag push |
|---|---|---|
| Version bump | release-please PR updates `manifest.json` (json extra-file, `$.version`) and CHANGELOG from conventional commits | Edit manifest by hand, then tag; easy to forget |
| Familiarity | Same as hassette | Different |
| Pre-releases / betas | Supported via release-please config later | Manual |

The zip is built and uploaded in the same workflow run, right after the release-please step creates
the release, which leaves a window of seconds where HACS could see a release without its asset. At
custom-repository volume that is negligible. A draft-then-publish flow would close it, but would
depend on release-please's draft and tag-creation behavior.

**Recommendation:** A, because it keeps the manifest version, tag, and changelog in sync
automatically. hassette already releases this way, and HACS reads the tag.
**Pick B instead if** you'd rather control release timing by hand. (release-please also waits for
you to merge its PR.)
**Reversibility:** easy
**Ratified:** Chose release-please (bumping `manifest.json` `$.version` and the changelog) with the zip built and attached in the same workflow run over manual tagging or draft releases, to keep manifest, tag and changelog in sync, accepting a seconds-long window before the asset exists.

### D13: Where do the integration's user docs live?

**Deciding factor:** HACS shows the repo README on the install page; hassette's docs site is where
hassette users look.

| | A: README here is the full integration doc; hassette's docs page (#2506) covers the hassette-side setup and links here | B: Short README; all docs on hassette's docs site | C: Both full |
|---|---|---|---|
| What HACS shows | The real docs | A pointer | The real docs |
| Single source | Yes, split by side | Yes | No: two copies drift |
| Quality-scale docs rules (installation, configuration parameters, removal, known limitations, troubleshooting) | Met here | Met via link | Met twice |

README content: installing from a HACS custom repository, the config fields, the token versus
`trusted_proxies` trade-off, entities and what each state means, a stop not surviving a hassette
restart, troubleshooting (reachability, forward-auth bypass), and removal.

**Recommendation:** A, because the page HACS shows the user is the one that should answer their
questions, and hassette's page owns the hassette-side setup (token creation, `trusted_proxies`).
**Pick B instead if** you want every doc in one mkdocs site with hassette's review tooling
(`doc-persona-review`).
**Pick C instead if** never.
**Reversibility:** easy
**Ratified:** Chose a full README in this repo as the integration's docs, with hassette's #2506 docs page covering the hassette side and linking here, over docs only on hassette's site, so the page HACS shows answers the user's questions, accepting docs split across two repos by side.

### D14: Can the config flow carry credentials for an auth proxy in front of hassette? (collapsed)

**Deciding factor:** HA convention for integrations whose server sits behind a forward-auth proxy.

**Recommendation:** No. v0.1 sends only hassette's own bearer token, and the README documents a proxy
bypass for `/api/*` (the brief's reachability plan; D9 surfaces a redirect as `redirected`, showing its target).
Core integrations that front self-hosted servers (portainer, proxmoxve) take a URL and the server's
own credentials, never proxy headers. Bypassing the proxy for token-authenticated API paths is the
standard setup.
**Pick "optional custom headers" instead if** you'd rather keep Cloudflare Access in front of the API
and give HA an Access service token. It's one more config-flow field and secret, and
`HassetteClient` would need to accept extra headers (a hassette-client change).
**Reversibility:** easy (adding the field later is backward-compatible)
**Ratified:** Chose sending only hassette's bearer token and documenting an /api bypass over optional proxy headers, to match core integrations for self-hosted servers, accepting that a proxied hassette needs a bypass rule before HA can reach it.

### D15: Where is the integration exercised against a real HA and hassette before release?

**Deciding factor:** catch what phcc's mocked HA can't (real HACS-style install, real hassette
responses, the device and entity UI) without depending on the maintainer's production network.

hassette's repo already runs HA and hassette together for its demo and system tests
(`scripts/demo_stack.py`: HA on 18123, hassette on 18126, a fixture HA config with a baked token).
Unit D (hassette#2506) plans to install a *pinned release* of this integration into that HA. That
can't happen before v0.1 is released.

| | A: hassette's demo stack, with an env var that mounts a local hass-hassette checkout | B: a dev compose file in this repo (HA + hassette image + sample apps) | C: no shared env; tests plus the maintainer's own HA |
|---|---|---|---|
| Reuses existing setup | Yes: HA fixture config, token, sample apps, teardown | No: duplicates HA config, token and apps | n/a |
| Cross-repo | A small hassette change (a precursor to #2506, which adds the pinned-release default to the same mount) | Self-contained | Self-contained |
| Network | Same compose network, no proxy | Same | Needs the auth-proxy bypass first |
| Pre-release smoke for v0.1 | Yes | Yes | Only on production |

**Recommendation:** A, because the HA + hassette pairing, fixtures and lifecycle already exist and
#2506 will touch the same mount. A local-checkout override is the dev-mode version of its pinned
release. Before tagging v0.1, the build runs a manual smoke against it: add the entry, see a device
per app, toggle a switch, press reload.
**Pick B instead if** you want this repo to stand alone with no hassette checkout needed.
**Pick C instead if** phcc tests plus dogfooding on your own HA after the bypass is enough.
**Reversibility:** easy
**Ratified:** Chose hassette's demo stack with a local-checkout mount override (a small hassette PR preceding #2506) over a dev compose here or production-only dogfooding, to reuse the existing HA + hassette fixtures for a pre-release smoke test, accepting a cross-repo change before v0.1 is tagged.

### D16: What does each poll read, and how does HA learn hassette's phase and app removals?

**Deciding factor:** HA acts only on server state it is told, never on state it infers from a list
endpoint that wasn't built to report it.

`GET /api/apps` builds its list from the telemetry DB's `app_manifests` table, overlaid with the
in-memory registry (hassette `src/hassette/web/routes/apps.py:286-302`). It can be partial on a
fresh or wiped DB until bootstrap writes the rows (`core/app_lifecycle_service.py:478`), and a saved
config edit flips an app to `in_current_config=false` on the next poll
(`core/app_registry.py:421-447`). `GET /api/health` is built from in-memory state only
(`routes/health.py`, `core/runtime_query_service.py` `get_system_status`) and carries
`bootstrap_released`, `version` and `api_schema_version`. Before `bootstrap_released`, every app
reads `stopped` (`core/app_registry.py:299-316`). Portainer never removes devices automatically:
it offers only user-initiated removal (core `homeassistant/components/portainer/__init__.py:247-270`,
`quality_scale.yaml` `stale-devices: done`).

| | A: `get_apps` only; auto-remove on first miss | B: health + apps each poll; bootstrap gate; per-poll version check; user-initiated removal; one-poll tolerance for transient failures | C: B, plus auto-remove after N misses while bootstrapped |
|---|---|---|---|
| Registry loss on a partial or mid-edit list | Immediate: devices, areas and customizations deleted | None | Only if the partial view lasts N polls |
| Switch flap across a hassette restart | Every switch goes off → on | Entities unavailable while booting; a short window remains while `start_apps` runs after release | Same as B |
| Server version change while HA runs | Unnoticed until a poll fails generically | Checked every poll (D7); hub `sw_version` updated | Same as B |
| One stalled poll (transient error) | Every entity flips unavailable | Previous data kept; unavailable only after 2 in a row | Same as B |
| Requests per 30 s | 1 | 2 (health is in-memory) | 2 |
| New state in HA | None | Last-seen version; consecutive-failure counter | B plus a per-app miss counter |
| Match to the reference (portainer) | Diverges | Matches | Diverges |

**Recommendation:** B, because each server fact HA needs comes from the endpoint that reports it,
and HA never deletes registry state on a guess.

Behavior under B:
- Each refresh calls `get_health()`, then `get_apps()`. Either failing fails the refresh (D9's
  table), with one tolerance: a failed refresh caused by `HassetteConnectionError`,
  `HassetteTimeoutError`, `GatewayError`, `ServiceUnavailableError` or `TelemetryUnavailableError`
  within `TOLERANCE_WINDOW` (45 s) of the last good poll keeps the previous data and availability
  (logged at DEBUG), so one stalled scheduled poll is absorbed and two in a row raise `UpdateFailed`
  as D9 maps it (time-based, per the Build calls). Auth failures, `unsupported_version`,
  redirects, 403 and validation errors are never tolerated, and neither is the first refresh. This
  suppresses dashboard and automation flaps from one stalled poll, at the cost of
  a per-entry last-success time.
- While `bootstrap_released` is false, every app entity is unavailable. The hub device and its
  data stay, and the transition is logged once at INFO.
- Version handling is D7's "While running" rule.
- An app with `in_current_config` false, or missing from the list, keeps its device. Its entities
  are unavailable. `async_remove_config_entry_device` lets the user delete a device only when its
  app is not current, judged from a good app list taken after bootstrap was released on a loaded
  entry (otherwise it refuses); the hub device is never removable. An app that comes back reuses its device
  and entities.
- A telemetry-DB failure (`TelemetryUnavailableError`) fails the refresh after the one-poll
  tolerance (D9). The maintainer
  intends to make hassette's DB a required service whose failure stops hassette, after which this
  shows up as a connection failure.

**Pick A instead if** never: it deletes registry state on an inference HA can't verify.
**Pick C instead if** you remove apps often enough that deleting devices by hand becomes a chore.
**Reversibility:** easy for polling; user-initiated removal is the safe default to relax later
**Ratified:** Chose health + apps on every poll, a bootstrap gate, per-poll version checks, user-initiated removal of non-current devices and a one-poll tolerance for transient failures over the apps-only poll with automatic removal, so HA acts only on server state it is told and never deletes registry state on a guess, accepting two GETs per 30 s and a consecutive-failure counter.

### D17: How does the config flow handle URL input, and what does reconfigure look like?

**Deciding factor:** never store or send something the user didn't mean, and follow core's
reconfigure shape.

The client strips a trailing `/` and redacts userinfo only inside its own transport
(`client/src/hassette_client/transport.py:94-95`); the integration builds the entry title and every
`configuration_url` (`{url}/apps/{app_key}`, D4) from the stored URL. aiohttp turns URL userinfo
into a Basic `Authorization` header, which would collide with the bearer token. The URL is the
connection setting most likely to change, and without a reconfigure step a change means deleting
the entry, which removes every device's area and customizations. Portainer's reconfigure step
(core `portainer/config_flow.py:148-191`) pre-fills every field and reuses the user-step validation.

| | A: strip trailing `/`; reject userinfo with `invalid_url`; reconfigure pre-fills all fields | B: strip trailing `/`; silently strip userinfo; same reconfigure | C: no normalization; no reconfigure step |
|---|---|---|---|
| `user:pass@host` URL | Clear error (no proxy credentials, D14) | Accepted, credentials dropped without telling the user | Stored as typed; a Basic header collides with the bearer |
| `http://host/` | Normalized; links stay clean | Same | `//apps/...` links |
| Changing hassette's URL later | Reconfigure, registry kept | Same | Delete and re-add; registry customizations lost |
| Convention | Portainer's reconfigure | Same | Diverges (portainer has `reconfiguration-flow: done`) |

**Recommendation:** A, because a rejected URL tells the user what's wrong where silent stripping
hides a credential problem, and reconfigure avoids a destructive workaround at the cost of one step
that reuses existing validation.

Behavior under A:
- The user and reconfigure steps strip one trailing `/` before validating and storing; a URL with
  userinfo fails with `invalid_url`. The entry title and every `configuration_url` use the stored URL.
- `reconfigure` shows URL, token and `verify_ssl` pre-filled with the stored values; a token cleared
  there becomes `None` (Assumed "Config flow fields"). It runs the same validation and errors, then
  updates and reloads the entry. hassette has no server identity, so no unique-id mismatch check
  applies.

**Pick B instead if** you'd rather accept pasted URLs leniently.
**Pick C instead if** never: it stores URLs the integration can't use cleanly.
**Reversibility:** easy
**Ratified:** Chose trailing-slash stripping, `invalid_url` for userinfo, and a portainer-style pre-filled reconfigure step over silent stripping or no normalization, so the integration never stores or sends credentials the user didn't mean and a URL change keeps the registry, accepting one more config-flow step in v0.1.

## Assumed

Inherited decisions come from the hassette repo's `design/specs/113-hacs-companion-integration/brief.md`
("Decided in the v0.1 define interview", "Owned by C"), translated to the shipped client API.

- **Transport and library:** the integration uses `hassette_client.HassetteClient`, given
  `homeassistant.helpers.aiohttp_client.async_get_clientsession(hass, verify_ssl=...)` (`inject-websession`).
  The client never retries. Evidence: hassette `client/src/hassette_client/client.py:39-80`.
- **Config flow fields:** URL (TextSelector URL), token (optional, password selector,
  `probatio.Secret`), `verify_ssl` (default true). No token means no `Authorization` header, which
  a hassette listing HA's address in `web_api.trusted_proxies` admits. Validation is one
  `get_health()` (authenticated; `/api/health` is not in hassette's `EXEMPT_ROUTES`) plus
  `check_server_version`. In every step, a blank token is stored as absent (`None`), never `""`,
  because `hassette-client` sends any string, empty included, as a bearer token
  (`client/src/hassette_client/client.py:58-62`), which hassette rejects with 401. The token field
  has a `data_description` saying that leaving it blank relies on hassette's
  `web_api.trusted_proxies` and gives everything at HA's address full access to hassette's API.
  The reauth step asks only for the token. URL handling and reconfigure are D17. Evidence: brief
  "Owned by C"; hassette `src/hassette/web/middleware.py:58-64`; portainer `config_flow.py`.
- **One entry:** `"single_config_entry": true` in `manifest.json`. No config-entry unique_id is needed.
  Evidence: brief; ADR-0006.
- **Polling interval:** one `DataUpdateCoordinator` refreshes every 30 s, fixed, not configurable.
  State lives in `entry.runtime_data`. What each refresh reads is D16. Evidence: brief (which named
  `/api/apps/manifests`, renamed by hassette spec 125); `client.py` `get_apps`, `get_health`.
- **Statuses:** `AppSummary.status` is `AppStatus` (disabled, blocked, degraded, running, failed,
  stopped) or `hassette_wire.UnknownValue` for a newer value. The sensor has seven options, those
  six plus `unknown`. Switch on = running or degraded; off = stopped or failed. For disabled,
  blocked, and unknown, the switch and button are unavailable and the sensor stays available. An
  unknown status does not raise `UpdateFailed`. Evidence: brief; hassette `wire/src/hassette_wire/enums.py:93-115`.
- **New apps:** an app seen for the first time adds its device and entities on that poll
  (`dynamic-devices`). Removal is D16. Evidence: brief; portainer `coordinator.py` new-entity callbacks.
- **Actions:** switch on → `action(app_key, "start")`, off → `"stop"`, button → `"reload"`. All are
  app-level (no instance index). No optimistic state; every action requests a refresh. start and
  stop converge and are safe to retry; a repeated reload restarts the app again. A stop lasts until
  hassette restarts (documented, not fixed). Evidence: brief; `client.py` `action` docstring.
- **Availability on poll failure:** all entities become unavailable when the coordinator reports a
  failed refresh (D16 says when a transient failure is tolerated). The coordinator logs once when
  unavailable and once when it recovers (built into `DataUpdateCoordinator`, core
  `helpers/update_coordinator.py:461-472`). The README's troubleshooting section tells the user to
  turn on the integration's "Enable debug logging", which covers `hassette_client` through the
  manifest's `loggers`, for the per-failure detail. Evidence: brief; HA `log-when-unavailable`.
- **Quality target:** Silver, plus the Gold rules the design meets (devices, dynamic-devices,
  stale-devices, entity-translations, exception-translations, icon-translations, entity-category,
  entity-device-class, repair-issues via D7's issue, and reconfiguration-flow). `quality_scale.yaml` records each rule as done, exempt with a reason, or
  todo. Evidence: brief; portainer `quality_scale.yaml` for the format.
- **Security:** the token lives only in the config entry and is never logged. Evidence: brief.
- **Coverage:** tests use `pytest-homeassistant-custom-component`; CI enforces at least 95% line
  coverage (`--cov-fail-under=95`) and full coverage of config-flow paths
  (`config-flow-test-coverage`). Evidence: brief.
- **Platform facts:** HA 2026.9 and later require Python ≥3.14.2; 2026.10 pins `aiohttp==3.14.4` and
  `pydantic==2.13.5`. `hassette-client` (pinned per D3) needs `aiohttp>=3.10` and `pydantic>=2.7`,
  and is on PyPI. The phcc ↔ HA pairings are in D2. The maintainer runs HA 2026.10.0 (HAOS,
  Python 3.14.6, HACS 2.0.5). Evidence: core `pyproject.toml:24` at 2026.10.0,
  `homeassistant/package_constraints.txt`; PyPI; HA system health 2026-10-08.
- **HACS packaging:** `hacs.json` has `name`, `homeassistant` (D2), `zip_release: true`,
  `filename: "hassette.zip"`. The brand icon is `custom_components/hassette/brand/icon.png`, which
  HACS validation has accepted since Feb 2026. The repo is public with topics `hacs`,
  `hacs-integration`, `home-assistant`, `hassette`. Evidence: hassette
  `design/research/2026-09-24-hacs-integration-prior-art/research.md` §6; `gh repo view`.
- **Manifest basics:** `domain: hassette`, `name: Hassette`, `config_flow: true`, `iot_class:
  local_polling`, `codeowners: ["@NodeJSmith"]`, `loggers: ["hassette_client"]`, `documentation`
  and `issue_tracker` pointing at this repo. Evidence: HA manifest docs; portainer `manifest.json`.
- **Validation facts (D2 owns the import choice):** HA 2026.9+ aliases `voluptuous` to probatio via
  `probatio.compat.install_as_voluptuous()` in `homeassistant/__init__.py`. HA 2026.9.0 ships probatio
  0.11.4 and 2026.10.0 ships 0.13.0; both export `Secret`. Code uses only probatio API present in
  0.11.4, which D2's floor job enforces. Evidence: `git show 2026.9.0:homeassistant/__init__.py`;
  probatio wheels' `__init__.py`.
- **Auth proxies redirect the API.** A hassette published behind a forward-auth proxy such as
  Cloudflare Access answers `/api/*` with a 302 to the proxy's login page, even when the request
  carries a valid bearer token (probed against the maintainer's deployment, 2026-10-08). Dogfooding
  therefore needs either a proxy bypass for `/api/*` (hassette's token is the gate there) or a
  network path from HA to hassette that skips the proxy. That's infrastructure setup outside this
  repo, and the README documents it (D13). Whether the integration can send proxy credentials is D14.
  Evidence: `curl` probe.
- **hassette app page URL** is `/apps/{app_key}` in hassette's frontend (used by D4). Evidence:
  hassette `frontend/src/app.tsx` routes under `/apps/:key`.

## Build

- [x] Implementation and tests committed
- [x] Docs (README is the user documentation, per D13)
- [x] Ship-time challenge

**Calls made during the build:**

- App devices link to the hub with `via_device_id` (the hub's registry id, registered before the platforms load), not `via_device`: HA 2026.9 and 2026.10 both deprecate `DeviceInfo.via_device` (removal 2027.8) and make device identifiers unique only within a config entry.
- A 401 in the config flow with no token uses its own error key, `token_required`, instead of `invalid_auth`: a form error has one fixed string per key, and D9 wants different text when no token was sent.
- A URL that doesn't parse, isn't `http`/`https` with a host, or has userinfo fails with `invalid_url`, after surrounding whitespace is trimmed: D17 covers userinfo only, and the others are the same user mistake (a scheme-less `host:8126` would otherwise parse and fail later as `cannot_connect`).
- The reload button has `ButtonDeviceClass.RESTART`: the `entity-device-class` rule, following Portainer's restart button; its translated name stays "Reload".
- Each platform adds new apps from a coordinator listener, not Portainer's per-coordinator callback lists, which would put platform state on the coordinator. Which apps get entities is decided against the entity registry: an app gets one when it is in hassette's config or already registered. hassette keeps listing removed apps from its history, so a history-only app HA never had gets no device. A departed app whose device the user deleted comes back only once it is current again. Renamed and disabled entities keep their registry entries, so they're never added twice.
- Every action is followed by `coordinator.async_request_refresh()` on a debouncer with a 1 s cooldown that fires immediately (`ACTION_REFRESH_COOLDOWN`). HA's default 10 s cooldown left a switch turned off showing on for about 10 s (seen in the D15 smoke test); a full `async_refresh()` per action instead serialized one poll per action on the coordinator's lock, so a script acting on N apps against a down server returned after about 45 + N×10 s. The 1 s cooldown coalesces a burst into a few refreshes and bounds the stale window to the cooldown.
- D16's "one failed poll tolerated, two in a row not" is measured in time, not counted: a transient failure keeps the previous data while the last good poll is under `TOLERANCE_WINDOW` (1.5 × the 30 s interval) old. Scheduled polls behave exactly as D16 states, and an action's refresh failing inside the window neither flips entities nor uses up the next poll's tolerance.
- Every successful poll also brings each existing app device's name and model in line with hassette's `display_name` and `class_name` (a user's own rename is kept as `name_by_user`); entity ids keep the names they were created with.
- The repair issue is withdrawn only after the platforms unload successfully, so a failed unload doesn't leave a partly loaded entry without it.
- Every successful poll registers the hub device (idempotent; an unchanged device writes nothing), so setup needs no separate registration step, entities resolve the hub's id from the registry, and a hub that goes missing is back by the next poll, with existing app devices linked to it again.
- The D9 mappings live in `errors.py` as ordered (exception class → translation key) tables, one per surface, first match wins. A poll's `unknown` is logged at DEBUG with its traceback, since the coordinator already logs the failure once.
- Pyright checks `custom_components/` only, with `reportIncompatibleVariableOverride` and `reportPrivateImportUsage` off: HA overrides its own `cached_property` attributes with properties and re-exports without `__all__`, which core's mypy accepts. hassette also leaves tests out of pyright.
- The integration uses relative imports (HA convention), unlike hassette's ruff `TID252`. From hassette's toolchain it also leaves out ruff's `D`, `TCH` and `DTZ` families (the code has no `datetime`, and HA core doesn't enforce docstring style), `house-lint` (hassette-specific rules), and the flake8-async hook (ruff's `ASYNC` covers it). It ports the whole-tree gitleaks scan, so CI scans more than an empty index.
- Tests are grouped by surface (`test_config_flow`, `test_init` for setup and polling, `test_entities` for all three platforms, `test_errors`) rather than one file per platform: the three platforms share one base entity and one action path.
- The floor job installs phcc 0.13.363 and the manifest's client pin with `uv pip`, outside `uv.lock`, and fails if the HA it installed differs from `hacs.json`'s `homeassistant`. Its pin lives in `tests.yml`, where Renovate doesn't touch it.
- `hassette-client` is pinned in `manifest.json` and in the `test` dependency group. Renovate groups both into one `fix(deps)` PR, so release-please releases it, and `tests/test_manifest.py` fails if the two differ.
- release-please uses the `simple` release type with `manifest.json` `$.version` as a JSON extra-file, starting from `0.0.0` so the first `feat` release is 0.1.0. Like hassette, it runs with a GitHub App token so CI runs on its release PRs. That needs the `RELEASE_PLEASE_APP_ID` / `RELEASE_PLEASE_APP_PRIVATE_KEY` secrets on this repo, which the maintainer adds.

## Addendum

- 2026-10-08, ship-time challenge: D9's entity-action cell for `ResponseValidationError` was amended so `UnexpectedResponseError` (non-JSON body) is "outcome unknown" (`unexpected_response`) instead of "done". hassette-client documents that this error most likely didn't come from hassette and that a write's outcome is unknown; counting it as done let a proxy that answers POSTs with a 2xx HTML page turn every action into a silent no-op.
- 2026-10-08, ship-time challenge: D9's `{location}` placeholder also drops the query and fragment, not only userinfo: a forward-auth login URL carries state and key ids there, and scheme, host and path are what identify the proxy.
- 2026-10-08, ship-time challenge: D9's "logged once at WARNING per distinct message" became once per (endpoint, error type) since the last good poll. A non-JSON body's message embeds its size, so a proxy page that varies per request logged every poll, and a second outage after recovery logged nothing.
- 2026-10-08, ship-time challenge: D5's "`-` is outside the app_key alphabet" is enforced at runtime: an app whose key doesn't match hassette's pattern is ignored and logged once.
