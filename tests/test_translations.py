"""Every translation key the integration can raise or show exists in en.json."""

import json
from pathlib import Path

from custom_components.hassette import errors
from custom_components.hassette.sensor import STATUS

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "hassette"
STRINGS = json.loads((INTEGRATION / "translations" / "en.json").read_text())
ICONS = json.loads((INTEGRATION / "icons.json").read_text())


def keys(rows: tuple[errors.ErrorRow, ...]) -> set[str]:
    return {key for _, key in rows}


def test_exception_keys_exist() -> None:
    used = keys(errors.POLL_ERRORS) | keys(errors.ACTION_ERRORS) | keys(errors.ACTION_VALIDATION_ERRORS)
    used |= {"invalid_auth", "action_in_progress", "unknown", "unsupported_version"}
    assert used <= set(STRINGS["exceptions"])


def test_config_flow_error_keys_exist() -> None:
    used = keys(errors.FORM_ERRORS) | {"invalid_auth", "token_required", "invalid_url", "unknown"}
    assert used <= set(STRINGS["config"]["error"])


def test_status_states_are_translated_and_have_icons() -> None:
    assert STATUS.options is not None
    assert set(STATUS.options) == set(STRINGS["entity"]["sensor"]["status"]["state"])
    assert set(STATUS.options) == set(ICONS["entity"]["sensor"]["status"]["state"])
