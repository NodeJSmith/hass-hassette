# AGENTS.md

hass-hassette is the Home Assistant custom integration for hassette. Project conventions for writing
code live in `CLAUDE.md` and `.claude/rules/`; this file holds the rules for Codex code review.

## Code Review Rules

### Module review questions

`custom_components/hassette/REVIEW.md` lists invariants that past changes have broken, written as
questions. For each changed file, read every `REVIEW.md` from its directory up to the repository
root. Comment only where the diff breaks one of those invariants, and quote the question. Don't
report questions the diff satisfies.

- Safe path: a change that keeps each invariant, or that updates the `REVIEW.md` question alongside
  a deliberate change to the invariant.

### Users' entities, history and automations keep working

Users build dashboards, automations and history on what this integration creates, and none of it
is in this repository, so tests that are updated alongside a change can't catch a break. When the
PR title has no `!`, flag a change to any of:

- an entity's `unique_id` or a device's identifiers (the user's entity, its history and its area
  are orphaned, and a new entity appears in its place)
- a key the config flow stores in `entry.data`, unless the flow's `VERSION` or
  `MINOR_VERSION` is raised and `async_migrate_entry` converts existing entries
- the `hassette.send` action's fields, or the `hassette_message` event's type or payload keys
- an entity's state values or attribute names, or the `translation_key` an entity or action uses

Treat a silent `unique_id` or identifier change as P1: once it ships, users' installs have already
re-created the entities, and changing it back orphans them a second time.

- Safe path: keep the old value, migrate it (`async_migrate_entry`, or an entity or device registry
  migration that moves the old unique_id to the new one), or mark the PR title as breaking (`feat!:`,
  `fix!:`) and end the PR body with a `BREAKING CHANGE:` footer saying what users have to redo.

### Interleaving across awaits

Polls, pushes from hassette's `/ws` stream, entity actions and registry listeners all run
concurrently on Home Assistant's event loop. A check followed by an action with an `await` between
them can see the state change underneath it. Flag check-then-act sequences across an `await` on
state another task can change (`coordinator.data`, the config entry's state, the device or entity
registry), and say which task changes it. `coordinator.data` in particular has more than one writer:
a value read before an `await` and written back after it can overwrite a newer value.

- Safe path: re-read after the await, do the check and the write without an await between them, or
  route the write through the coordinator method that already orders writers.

### Everything set up for an entry is torn down with it

Users reload, reconfigure and reauthenticate the integration without restarting Home Assistant, and
each of those unloads and sets up the entry again. Flag anything started in setup that unload
doesn't stop: a task not created with `entry.async_create_background_task`, a listener, timer or
subscription whose remover isn't passed to `entry.async_on_unload`, or state stored on `hass.data`
that survives the entry. Also flag blocking I/O on the event loop (file access, `time.sleep`,
synchronous HTTP); Home Assistant logs it as a detected blocking call and it stalls every other
integration.

- Safe path: create tasks through the entry, register each remover with `entry.async_on_unload`,
  keep per-entry state on `entry.runtime_data`, and run blocking work through
  `hass.async_add_executor_job`.
