"""Translate hassette-client exceptions into Home Assistant errors.

Each surface (config flow, coordinator poll, entity action) maps the client's exception classes
through an ordered table: the first row whose class matches wins, so a subclass row must come before
its base (``TelemetryUnavailableError`` before ``ServiceUnavailableError``). A case that needs a
different Home Assistant exception type, or a check beyond the class, is a branch in the mapping
function instead of a row. Every key is in ``translations/en.json``, which ``tests/test_translations.py`` checks.
"""

import logging

from hassette_client import (
    ActionFailedError,
    AppBlockedError,
    AppNotFoundError,
    AuthenticationError,
    BootstrapNotReleasedError,
    ConflictError,
    ForbiddenError,
    GatewayError,
    HassetteClientError,
    HassetteConnectionError,
    HassetteTimeoutError,
    InvalidAppKeyError,
    RedirectError,
    ResponseValidationError,
    ServiceUnavailableError,
    TelemetryUnavailableError,
    UnsupportedServerVersionError,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import UpdateFailed
from yarl import URL

from .const import DOMAIN, MAX_DETAIL_LENGTH

_LOGGER = logging.getLogger(__name__)

# The 409 code hassette sends while another action on the same app is running (hassette#2610). Until
# hassette-client gains a class for it, it arrives as a plain ConflictError; switch to that class then.
ACTION_IN_PROGRESS_CODE = "action_in_progress"

# Failures a single stalled poll can cause; the coordinator tolerates one of these in a row.
TRANSIENT_POLL_ERRORS: tuple[type[HassetteClientError], ...] = (
    HassetteConnectionError,
    HassetteTimeoutError,
    GatewayError,
    ServiceUnavailableError,
)

type ErrorRow = tuple[type[HassetteClientError], str]


def truncate_detail(text: str) -> str:
    """Shorten exception text from app code to ``MAX_DETAIL_LENGTH`` characters."""
    if len(text) <= MAX_DETAIL_LENGTH:
        return text
    return text[: MAX_DETAIL_LENGTH - 1] + "…"


def redirect_target(err: RedirectError) -> str:
    """Return where a redirect pointed, without any credentials embedded in it.

    Empty when the redirect carried no ``Location`` or one that doesn't parse as a URL.
    """
    if not err.location:
        return ""
    try:
        target = URL(err.location)
    except ValueError:
        return ""
    if target.user is None and target.password is None:
        return str(target)
    return str(target.with_user(None))


def placeholders_for(err: HassetteClientError) -> dict[str, str] | None:
    """Return the translation placeholders an error's message needs, if any (``{location}``, ``{detail}``)."""
    if isinstance(err, RedirectError):
        return {"location": redirect_target(err)}
    if isinstance(err, ActionFailedError):
        return {"detail": truncate_detail(err.detail or "")}
    return None


def version_placeholders(err: UnsupportedServerVersionError) -> dict[str, str]:
    """Return the placeholders the unsupported-version messages show."""
    return {
        "server_version": err.server_version or "unknown",
        "api_schema_version": str(err.api_schema_version),
        "min_api_schema_version": str(err.min_api_schema_version),
    }


# Config flow form errors. A 401 is a branch, since its key depends on whether a token was sent.
FORM_ERRORS: tuple[ErrorRow, ...] = (
    (RedirectError, "redirected"),
    (ForbiddenError, "forbidden"),
    (HassetteTimeoutError, "timeout_connect"),
    (HassetteConnectionError, "cannot_connect"),
    (GatewayError, "cannot_connect"),
    (ServiceUnavailableError, "cannot_connect"),
    (ResponseValidationError, "cannot_connect"),
)


# Coordinator poll errors. A 401 is a branch, since it raises ConfigEntryAuthFailed to start reauth.
POLL_ERRORS: tuple[ErrorRow, ...] = (
    (RedirectError, "redirected"),
    (TelemetryUnavailableError, "telemetry_unavailable"),
    (GatewayError, "cannot_connect"),
    (ServiceUnavailableError, "cannot_connect"),
    (HassetteConnectionError, "cannot_connect"),
    (HassetteTimeoutError, "timeout_connect"),
    (ForbiddenError, "forbidden"),
    (ResponseValidationError, "invalid_response"),
)

# Entity action errors. action_in_progress is a branch, since it's a ConflictError told apart by its code.
ACTION_ERRORS: tuple[ErrorRow, ...] = (
    (RedirectError, "redirected"),
    (AuthenticationError, "invalid_auth"),
    (BootstrapNotReleasedError, "not_bootstrapped"),
    (ActionFailedError, "action_failed"),
    # A proxy's 502/503/504 or a timeout: the action may still have run.
    (GatewayError, "action_timeout"),
    (ServiceUnavailableError, "action_timeout"),
    (HassetteTimeoutError, "action_timeout"),
    (HassetteConnectionError, "cannot_connect"),
    (ForbiddenError, "forbidden"),
    (ResponseValidationError, "invalid_response"),
)

# Caller faults: the app doesn't exist, or the server is configured to exclude it.
ACTION_VALIDATION_ERRORS: tuple[ErrorRow, ...] = (
    (AppNotFoundError, "not_found"),
    (InvalidAppKeyError, "not_found"),
    (AppBlockedError, "blocked_by_filter"),
)


def lookup(rows: tuple[ErrorRow, ...], err: HassetteClientError) -> str | None:
    """Return the translation key of the first row ``err`` is an instance of."""
    return next((key for cls, key in rows if isinstance(err, cls)), None)


def poll_error(err: HassetteClientError) -> HomeAssistantError:
    """Map a failed coordinator poll to the error the coordinator should raise."""
    if isinstance(err, AuthenticationError):
        return ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth")
    key = lookup(POLL_ERRORS, err)
    if key is None:
        _LOGGER.debug("Unexpected error polling hassette", exc_info=err)
        key = "unknown"
    return UpdateFailed(translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders_for(err))


def action_error(err: HassetteClientError) -> HomeAssistantError:
    """Map a failed app action to the error the entity action should raise."""
    if (key := lookup(ACTION_VALIDATION_ERRORS, err)) is not None:
        return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)
    if isinstance(err, ConflictError) and err.code == ACTION_IN_PROGRESS_CODE:
        key = "action_in_progress"
    elif (key := lookup(ACTION_ERRORS, err)) is None:
        _LOGGER.error("Unexpected error from a hassette app action", exc_info=err)
        key = "unknown"
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders_for(err)
    )


def is_completed_action(err: HassetteClientError) -> bool:
    """Whether hassette answered 2xx, so the action ran and only its response failed to parse."""
    return isinstance(err, ResponseValidationError) and err.status is not None and 200 <= err.status < 300
