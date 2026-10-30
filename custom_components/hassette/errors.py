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
    ActionInProgressError,
    AppBlockedError,
    AppNotFoundError,
    AuthenticationError,
    BootstrapNotReleasedError,
    ForbiddenError,
    GatewayError,
    HassetteClientError,
    HassetteConnectionError,
    HassetteTimeoutError,
    InvalidAppKeyError,
    NeedsRestartError,
    RedirectError,
    ResponseValidationError,
    ServiceUnavailableError,
    TelemetryUnavailableError,
    UnexpectedResponseError,
    UnsupportedServerVersionError,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import UpdateFailed
from yarl import URL

from .const import DOMAIN, MAX_DETAIL_LENGTH

_LOGGER = logging.getLogger(__name__)

# Failures a single stalled poll can cause; the coordinator tolerates them within TOLERANCE_WINDOW of a good poll.
TRANSIENT_POLL_ERRORS: tuple[type[HassetteClientError], ...] = (
    HassetteConnectionError,
    HassetteTimeoutError,
    GatewayError,
    ServiceUnavailableError,
)

# Failures after which a discovered entry re-reads its add-on's announcement: a rejected token, or no
# answer at the stored address (refused, or a host that no longer answers at all).
ADDON_FOLLOW_ERRORS: tuple[type[HassetteClientError], ...] = (
    AuthenticationError,
    HassetteConnectionError,
    HassetteTimeoutError,
)

type ErrorRow = tuple[type[HassetteClientError], str]

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

# Entity action errors.
ACTION_ERRORS: tuple[ErrorRow, ...] = (
    (RedirectError, "redirected"),
    (AuthenticationError, "invalid_auth"),
    (BootstrapNotReleasedError, "not_bootstrapped"),
    (ActionInProgressError, "action_in_progress"),
    (NeedsRestartError, "needs_restart"),
    (ActionFailedError, "action_failed"),
    # A proxy's 502/503/504 or a timeout: the action may still have run.
    (GatewayError, "action_timeout"),
    (ServiceUnavailableError, "action_timeout"),
    (HassetteTimeoutError, "action_timeout"),
    (HassetteConnectionError, "cannot_connect"),
    (ForbiddenError, "forbidden"),
    # A non-JSON answer (a proxy's login page, say) most likely never reached hassette. A subclass of
    # ResponseValidationError, so it must come first.
    (UnexpectedResponseError, "unexpected_response"),
    (ResponseValidationError, "invalid_response"),
)

# Caller faults: the app doesn't exist, or the server is configured to exclude it.
ACTION_VALIDATION_ERRORS: tuple[ErrorRow, ...] = (
    (AppNotFoundError, "not_found"),
    (InvalidAppKeyError, "not_found"),
    (AppBlockedError, "blocked_by_filter"),
)


def truncate_detail(text: str) -> str:
    """Shorten exception text from app code to ``MAX_DETAIL_LENGTH`` characters."""
    if len(text) <= MAX_DETAIL_LENGTH:
        return text
    return text[: MAX_DETAIL_LENGTH - 1] + "…"


def redirect_target(exc: RedirectError) -> str:
    """Return where a redirect pointed: scheme, host and path only.

    Userinfo, query and fragment are dropped, since a login page's query carries state and tokens.
    Empty when the redirect carried no ``Location`` or one that doesn't parse as a URL.
    """
    if not exc.location:
        return ""
    try:
        target = URL(exc.location)
    except ValueError:
        return ""
    target = target.with_query(None).with_fragment(None)
    if target.user is None and target.password is None:
        return str(target)
    return str(target.with_user(None))


def placeholders_for(exc: HassetteClientError) -> dict[str, str] | None:
    """Return the translation placeholders an error's message needs, if any (``{location}``, ``{detail}``)."""
    if isinstance(exc, RedirectError):
        return {"location": redirect_target(exc)}
    if isinstance(exc, ActionFailedError | NeedsRestartError):
        return {"detail": truncate_detail(exc.detail or "")}
    return None


def version_placeholders(exc: UnsupportedServerVersionError) -> dict[str, str]:
    """Return the placeholders the unsupported-version messages show."""
    return {
        "server_version": exc.server_version or "unknown",
        "api_schema_version": str(exc.api_schema_version),
        "min_api_schema_version": str(exc.min_api_schema_version),
    }


def lookup(rows: tuple[ErrorRow, ...], exc: HassetteClientError) -> str | None:
    """Return the translation key of the first row ``exc`` is an instance of."""
    return next((key for cls, key in rows if isinstance(exc, cls)), None)


def poll_error(exc: HassetteClientError) -> HomeAssistantError:
    """Map a failed coordinator poll to the error the coordinator should raise."""
    if isinstance(exc, AuthenticationError):
        return ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth")
    key = lookup(POLL_ERRORS, exc)
    if key is None:
        _LOGGER.debug("Unexpected error polling hassette", exc_info=exc)
        key = "unknown"
    return UpdateFailed(translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders_for(exc))


def action_error(exc: HassetteClientError) -> HomeAssistantError:
    """Map a failed app action to the error the entity action should raise."""
    if (key := lookup(ACTION_VALIDATION_ERRORS, exc)) is not None:
        return ServiceValidationError(translation_domain=DOMAIN, translation_key=key)
    if (key := lookup(ACTION_ERRORS, exc)) is None:
        _LOGGER.error("Unexpected error from a hassette app action", exc_info=exc)
        key = "unknown"
    return HomeAssistantError(
        translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders_for(exc)
    )


def is_completed_action(exc: HassetteClientError) -> bool:
    """Whether hassette answered 2xx with JSON, so the action ran and only its response failed to parse.

    A 2xx that isn't JSON (``UnexpectedResponseError``) most likely came from something in front of
    hassette, so whether the action ran is unknown.
    """
    return (
        isinstance(exc, ResponseValidationError)
        and not isinstance(exc, UnexpectedResponseError)
        and exc.status is not None
        and 200 <= exc.status < 300
    )
