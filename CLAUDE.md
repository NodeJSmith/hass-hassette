# CLAUDE.md

hass-hassette is the Home Assistant custom integration for [hassette](https://github.com/NodeJSmith/hassette). It connects Home Assistant to one hassette server and gives each hassette app a device with a switch, a reload button and a status sensor. `README.md` describes it from a user's side.

## Layout

- `custom_components/hassette/`: the integration. `config_flow.py` holds the config and options flow, `coordinator.py` polls the hassette server, and `switch.py`, `button.py` and `sensor.py` hold the entities.
- `tests/`: pytest with `pytest-homeassistant-custom-component`. Coverage must stay at 95% or above (`pyproject.toml`).
- `tools/`: the repo's own lint and gate scripts.
- `design/specs/`: design records for past changes.

## Commands

- Tests: `uv run pytest`
- Lint and format: `prek -a`
- Type checks: `prek run pyright -a --stage pre-push`
- New-code gates: `mise run gates`. They're the same tasks CI's `file-sizes` and `duplicate-code` jobs and the pre-push hooks run. A change must not grow a file past 800 lines or add a 3rd+ copy of a code block.

## Conventions

- The integration's public surface is what Home Assistant users depend on: entity unique IDs (changing one orphans the user's entity and its history), the config and options flow (its steps, fields and stored data), and any services it registers. Don't change any of them unless the issue asks for it.
- Commits follow Conventional Commits. release-please builds `CHANGELOG.md` from their subjects, so use `chore:` for internal work (see `.claude/rules/changelog-quality.md`).
- `.github/autofix.toml` is the autofix pipeline's recipe for this repo. An automated PR that edits it is parked for a human to merge or close.
