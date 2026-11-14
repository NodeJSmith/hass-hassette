---
topic: "How HA integrations implement a service that fires a bus event for an external automation engine (hassette.send, #61)"
date: 2026-11-14
status: Draft
---

# Prior Art: an HA service that relays a message to an external automation engine

## The Problem

Issue #61 asks for a `hassette.send` service with a required `name` and an optional `data` field. It fires a `hassette_action` event on the HA bus, so a dashboard button or a script can message a hassette app without a helper entity. Before building it, we need to know four things: where peers register services like this, whether they refuse the call when the backend is down, how they validate the payload, and what HA's quality-scale rules require.

## How We Do It Today

We don't do it yet. The integration registers no services, and `quality_scale.yaml` marks `action-setup` and `docs-actions` as exempt for that reason. `__init__.py` has no `async_setup` and no `CONFIG_SCHEMA`. The coordinator polls every 30 s and tolerates one failed poll, and entity availability follows `last_update_success`. The integration allows a single config entry.

## Patterns Found

### Pattern 1: Register in `async_setup` and check the entry's state inside the handler (HA's recommendation)

**Used by**: HA core mqtt and knx, and the `action-setup` rule (bronze tier).

**How it works**: `async_setup` registers the service once, and it is never unregistered. The handler resolves the config entry with `homeassistant.helpers.service.async_get_config_entry(hass, DOMAIN, entry_id)`. With `entry_id=None`, the helper falls back to the domain's only entry. It raises `ServiceValidationError`, carrying HA's own translation keys, when there is no entry or the entry isn't `LOADED`. The service then stays visible to the automation editor even while the entry has failed.

**Strengths**: automations still validate, and calling the service while the entry is down gives a clear error. Nothing has to be cleaned up on unload.

**Weaknesses**: we'd have to add `async_setup` and `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)`.

**Example**: core `mqtt/__init__.py:280`, `knx/__init__.py:112`, `helpers/service.py` (`async_get_config_entry`).

**Read**: all of the above, opened at core commit c8e8c804.

### Pattern 2: Register in `async_setup_entry` and never remove (community peers)

**Used by**: pyscript and NetDaemon's integration.

**How it works**: the service is registered during entry setup. It stays registered after unload, and a reload simply overwrites it.

**Strengths**: simple.

**Weaknesses**: it violates `action-setup`. After unload the service still exists, and its handler runs against torn-down state.

**Example**: pyscript `custom_components/pyscript/__init__.py:339,378,410`, and NetDaemon `custom_components/netdaemon/__init__.py:~72-145`.

**Read**: both files opened.

### Pattern 3: Blocking the call when the backend is unreachable, which nobody does

**Used by**: none of the peers. pyscript runs inside HA. hass-node-red and NetDaemon don't check whether the backend is reachable.

**How it works**: core checks the entry's state, not live connectivity:
- mqtt `async_publish` raises `HomeAssistantError(translation_key="mqtt_not_setup_cannot_publish")` only when the entry is disabled.
- knx raises `HomeAssistantError(translation_key="integration_not_loaded")`.

A grep of core's `components/*/services.py` for `last_update_success` found nothing. The `action-exceptions` rule says a backend or network failure should raise `HomeAssistantError` and a caller mistake should raise `ServiceValidationError`.

**Strengths**: checking the entry state is cheap and can't act on a stale poll.

**Weaknesses**: when the call is just `hass.bus.async_fire`, the call succeeds even if hassette is down, and the event is lost without any error.

**Read**: grep over core `components/*/services.py`, `mqtt/client.py:150-165`, `knx/services.py:128-132`.

### Pattern 4: Payload validation and event shape

**Used by**:
- mqtt uses a strict voluptuous schema.
- hass-node-red's entity service `nodered.trigger` takes its `message` through an `object` selector and doesn't validate what's inside.
- pyscript and NetDaemon don't validate at all.

**How it works**: hass-node-red fires a single bus event named after its domain, `nodered`, with a `type` field that tells the kinds apart. It uses that event only for loaded/unloaded lifecycle notices; real commands go over its own websocket. NetDaemon fires no custom event. It registers services whose handlers do nothing and listens for HA's built-in `call_service` event instead.

**Example**: `mqtt/services.py:44-60`, and hass-node-red's `services.yaml` and `__init__.py:38,61`.

**Read**: all opened. I found no peer using `cv.match_all` or `vol.Any(dict)` for a pure relay.

## Anti-Patterns

- **NetDaemon `entity_create`:** it logs an error for invalid input and then keeps running. It should raise `ServiceValidationError`.
- **Services that outlive the unloaded entry (pyscript, NetDaemon):** `action-setup` exists to prevent this.

## Gaps

- I didn't check how entity services handle unavailable entities.
- I skipped AppDaemon's dashboard-trigger pattern. It goes the other way: HA fires the event and the app listens, with no integration in between.
- I didn't open the pages for the `exception-translations` and `config-schema` rules.
- hassfest has no validator for `action-setup`, `docs-actions` or `action-exceptions`, so whether we meet them is declared in `quality_scale.yaml`, not checked.

## Relevance to Us

- **Registration:** Pattern 1 is the rule and fits a single-entry integration well, since `async_get_config_entry(hass, DOMAIN, None)` resolves the entry with no `config_entry_id` field. It conflicts with the issue's "removed on unload" criterion, but that criterion asks for the pattern the rule exists to stop.
- **Gating on reachability:** checking whether hassette is reachable goes beyond what any peer does. It's still worth considering, because unlike mqtt's queued publish, `async_fire` gives no feedback at all. If we keep it, the docs say it should raise `HomeAssistantError`. There's no peer to copy for the signal, so `last_update_success` would be our own choice. It's consistent with entity availability, but it leaves a window of up to about 60 s after hassette dies.
- **Payload:** a light schema (a non-empty `name` string, plus `data` as a dict) is more than the peers do and less than mqtt. Neither extreme has an argument against it.

## Recommendation

**Decision (2026-11-14):** the reachability check below was not built. `last_update_success` measures the
integration's HTTP polls of hassette, while the event reaches apps over hassette's own websocket to Home
Assistant, so the check would reject sends that arrive (polls blocked, websocket up) and pass sends that are
lost. The action checks only that the entry is loaded, as peers and core do. Registration and payload
validation were built as recommended.

- Register in `async_setup` and resolve the entry with `async_get_config_entry`. This means dropping "removed on unload" from the acceptance criteria and adding `async_setup` and `CONFIG_SCHEMA`.
- Keep the issue's reachability check on `last_update_success`, raising `HomeAssistantError` with a translation key of our own. It costs one condition and gives a dashboard user an error instead of a silently lost event. That beats matching peers, which don't bother. Document that it can't catch a hassette that died within the last poll window.
- Validate `name` as a non-empty string and `data` as a dict defaulting to `{}`.

## Sources

### Reference implementations
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/mqtt/services.py: mqtt services, registered in `async_setup`, with a strict schema
- https://github.com/home-assistant/core/blob/dev/homeassistant/components/knx/services.py: `integration_not_loaded` gate
- https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/service.py: the `async_get_config_entry` helper
- https://github.com/zachowj/hass-node-red: lifecycle `nodered` event and the `nodered.trigger` entity service
- https://github.com/custom-components/pyscript: services registered in `async_setup_entry`, never removed
- https://github.com/net-daemon/integration: services whose handlers do nothing, with NetDaemon listening for `call_service`

### Documentation & standards
- https://github.com/home-assistant/developers.home-assistant/blob/master/docs/core/integration-quality-scale/rules/action-setup.md
- https://github.com/home-assistant/developers.home-assistant/blob/master/docs/core/integration-quality-scale/rules/action-exceptions.md
- https://github.com/home-assistant/developers.home-assistant/blob/master/docs/core/integration-quality-scale/rules/docs-actions.md
- https://github.com/home-assistant/core/tree/dev/script/hassfest/quality_scale_validation: there are no validators for the action rules
