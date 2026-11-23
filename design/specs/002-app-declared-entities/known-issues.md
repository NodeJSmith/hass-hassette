# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: A registry error in the declared-entity poll steps fails the whole poll

Status: open
Recorded: 2026-11-23 (72-app-declared-entities)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- custom_components/hassette/coordinator.py

Issue:
`register_declaring_app_devices` and `remove_undeclared` run inside `_async_update_data` with nothing catching
their errors. If either raised, every poll would fail and all of the integration's entities, v0.1's included,
would go unavailable.

Why deferred:
No trigger exists on HA 2026.10.4 or 2026.11.2 (the identifier spaces can't overlap), HA's coordinator already
logs a failed refresh with its traceback, and a broad except would hide a real diff bug.

Recommended follow-up:
If a registry change in a future HA makes either step raise, fix the cause; contain the step only if the cause
is outside this integration.

Acceptance criteria:
- A failing declared-entity step no longer takes the v0.1 entities down, or the cause is removed.

## KI-002: The add rule's registry lookup isn't scoped to this config entry

Status: open
Recorded: 2026-11-23 (72-app-declared-entities)
Source: ship-challenge
Reason not fixed now: accepted-limitation
Affected files:
- custom_components/hassette/coordinator.py
- custom_components/hassette/entity.py

Issue:
`may_add_declared` (and v0.1's `add_app_entities`) asks the entity registry for a unique_id across the whole
integration domain. With two hassette servers in one HA sharing an app and instance name, an out-of-config
instance's entity can be admitted because of the other entry's registration, then skipped with a logged
unique_id clash.

Why deferred:
Nobody is known to run two hassette servers against one HA, the outcome is a logged, skipped entity, and v0.1
has the same pattern.

Recommended follow-up:
If multiple hassette entries become a real setup, check the registry entry's `config_entry_id` in both add rules.

Acceptance criteria:
- Both add rules only count registrations owned by their own config entry.
