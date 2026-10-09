"""Constants for the Hassette integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "hassette"
MANUFACTURER: Final = "Hassette"
SERVER_DEVICE_NAME: Final = "Hassette"

SCAN_INTERVAL: Final = timedelta(seconds=30)
# A transient failure keeps the previous data while the last good poll is younger than this: one
# missed 30 s poll is tolerated, two in a row are not, whatever refreshes happened in between.
TOLERANCE_WINDOW: Final = SCAN_INTERVAL * 1.5
# Refreshes after actions run at once, then at most once per cooldown, so a burst of actions
# coalesces into about two polls and a toggled switch shows its new state within a second.
ACTION_REFRESH_COOLDOWN: Final = 1.0

# A start or reload answers only after the app's on_initialize() finishes. 45 s clears hassette's
# worst-case reload (10 s shutdown + 20 s startup timeout) with margin; polling keeps the client default.
ACTION_TIMEOUT: Final = 45.0

# No app_key can contain "-", so neither this identifier nor any "{app_key}-{key}" unique_id collides.
SERVER_DEVICE_ID: Final = "-server"

# The Repairs issue id, and its key under "issues" in translations/en.json.
ISSUE_UNSUPPORTED_VERSION: Final = "unsupported_version"
UPGRADE_DOCS_URL: Final = "https://hassette.readthedocs.io/en/stable/pages/operating/upgrading/"

# Exception text from app code is shown in toasts and attributes; keep it to a readable length.
MAX_DETAIL_LENGTH: Final = 300

ATTR_SERVER_STATUS: Final = "server_status"
ATTR_ERROR_MESSAGE: Final = "error_message"
