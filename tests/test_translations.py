"""Every translation key the integration can raise or show exists in en.json."""

import json
from pathlib import Path

import yaml

from custom_components.hassette import errors
from custom_components.hassette.sensor import STATUS

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "hassette"
STRINGS = json.loads((INTEGRATION / "translations" / "en.json").read_text())
ICONS = json.loads((INTEGRATION / "icons.json").read_text())
SERVICES = yaml.safe_load((INTEGRATION / "services.yaml").read_text())


def keys(rows: tuple[errors.ErrorRow, ...]) -> set[str]:
    return {key for _, key in rows}


def test_exception_keys_exist() -> None:
    used = keys(errors.POLL_ERRORS) | keys(errors.ACTION_ERRORS) | keys(errors.ACTION_VALIDATION_ERRORS)
    used |= {"invalid_auth", "action_in_progress", "needs_restart", "unknown", "unsupported_version", "addon_refreshed"}
    used |= {"send_unreachable", "send_disconnected"}
    assert used <= set(STRINGS["exceptions"])


def test_config_flow_error_keys_exist() -> None:
    used = keys(errors.FORM_ERRORS) | {"invalid_auth", "token_required", "invalid_url", "unknown"}
    assert used <= set(STRINGS["config"]["error"])


def test_config_flow_abort_and_step_keys_exist() -> None:
    aborts = {"not_hassette_addon", "invalid_discovery_info", "unsupported_version"}
    assert aborts <= set(STRINGS["config"]["abort"])
    assert {"user", "hassio_confirm", "reauth_confirm", "reconfigure"} <= set(STRINGS["config"]["step"])


def test_status_states_are_translated_and_have_icons() -> None:
    assert STATUS.options is not None
    assert set(STATUS.options) == set(STRINGS["entity"]["sensor"]["status"]["state"])
    assert set(STATUS.options) == set(ICONS["entity"]["sensor"]["status"]["state"])


def test_services_agree_across_services_yaml_translations_and_icons() -> None:
    assert set(SERVICES) == set(STRINGS["services"]) == set(ICONS["services"])
    for name, spec in SERVICES.items():
        assert set(spec["fields"]) == set(STRINGS["services"][name]["fields"])


def test_send_fields_required_as_the_schema_requires() -> None:
    fields = SERVICES["send"]["fields"]
    assert (fields["name"]["required"], fields["data"]["required"]) == (True, False)
