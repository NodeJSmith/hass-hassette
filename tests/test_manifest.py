"""The packaging metadata HACS and Home Assistant read agrees with what CI tests."""

import json
from importlib.metadata import version
from pathlib import Path

from hassette_client import MIN_API_SCHEMA_VERSION

ROOT = Path(__file__).parent.parent
MANIFEST = json.loads((ROOT / "custom_components" / "hassette" / "manifest.json").read_text())
HACS = json.loads((ROOT / "hacs.json").read_text())


def test_tested_client_is_the_one_users_install() -> None:
    assert MANIFEST["requirements"] == [f"hassette-client=={version('hassette-client')}"]


def test_hacs_zip_matches_domain() -> None:
    assert HACS["filename"] == f"{MANIFEST['domain']}.zip"
    assert HACS["zip_release"] is True


def test_server_floor_is_unchanged() -> None:
    """A client bump that raises the oldest supported hassette must not ship as a routine dependency fix.

    When this fails, release the bump deliberately: a ``feat!`` commit and the README's hassette
    requirement updated to the release that introduced the new schema, then update this number.
    """
    assert MIN_API_SCHEMA_VERSION == 1
