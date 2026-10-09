# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: Use hassette-client's `ActionInProgressError` once it is released

Status: open
Recorded: 2026-10-08 (feat/v0.1-integration)
Source: other
Reason not fixed now: blocked
Affected files:
- custom_components/hassette/errors.py
- tests/test_entities.py

Issue:
`action_error` recognizes a concurrent-action 409 by matching `ConflictError.code == "action_in_progress"`,
because hassette-client 0.56.0 has no class for it. hassette#2612 (merged to hassette main on
2026-10-08) adds `ActionInProgressError(ConflictError)` to the client.

Why deferred:
The class isn't in a released hassette-client, and the integration pins the released 0.56.0. The code
match already maps the server's new 409 correctly.

Recommended follow-up:
When the Renovate PR bumps hassette-client to a release with `ActionInProgressError`, replace the
`ACTION_IN_PROGRESS_CODE` branch with an `(ActionInProgressError, "action_in_progress")` row in
`ACTION_ERRORS` and drop the constant.

Acceptance criteria:
- `errors.py` has no `ACTION_IN_PROGRESS_CODE`, and the `action_in_progress` case in `test_action_errors`
  raises `ActionInProgressError`.

## KI-002: No hub-level entity for hassette's own availability

Status: open
Recorded: 2026-10-08 (feat/v0.1-integration)
Source: ship-challenge
Reason not fixed now: out-of-scope
Affected files:
- custom_components/hassette/coordinator.py

Issue:
During an outage, a held bootstrap or an unsupported server version, every app entity goes
unavailable, but nothing on the hub device says why. Users can't automate on "hassette is down".

Why deferred:
D4 reserves hub entities for v0.2; the ship-time challenge kept that scope.

Recommended follow-up:
In v0.2, add a hub connectivity `binary_sensor` (and possibly bootstrap state) under the `-server`
unique_id scheme.

Acceptance criteria:
- One entity on the hub device turns off when hassette is unreachable or too old.

## KI-003: The unsupported-version repair survives disabling a retrying entry

Status: open
Recorded: 2026-10-08 (feat/v0.1-integration)
Source: ship-challenge
Reason not fixed now: out-of-scope
Affected files:
- custom_components/hassette/__init__.py

Issue:
For an entry in SETUP_RETRY (hassette too old), disabling it makes HA cancel the retry without
calling `async_unload_entry`, so the repair issue stays until the entry is re-enabled or removed.
An `async_on_unload` callback can't fix it: HA runs those after every failed setup, which would delete
the issue right after each retry raised it.

Why deferred:
The trigger is rare (too-old server plus disabling rather than deleting), and it clears on
re-enable or removal; the fix needs extra lifecycle machinery.

Recommended follow-up:
If users report it, add an `async_setup` that deletes the issue at startup when the entry is disabled.

Acceptance criteria:
- After disabling a retrying entry and restarting HA, no `unsupported_version` repair remains.
