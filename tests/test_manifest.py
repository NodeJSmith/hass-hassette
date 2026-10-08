"""The packaging metadata HACS and Home Assistant read agrees with what CI tests."""

import json
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).parent.parent
MANIFEST = json.loads((ROOT / "custom_components" / "hassette" / "manifest.json").read_text())
HACS = json.loads((ROOT / "hacs.json").read_text())


def test_tested_client_is_the_one_users_install() -> None:
    assert MANIFEST["requirements"] == [f"hassette-client=={version('hassette-client')}"]


def test_hacs_zip_matches_domain() -> None:
    assert HACS["filename"] == f"{MANIFEST['domain']}.zip"
    assert HACS["zip_release"] is True
