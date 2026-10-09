"""Tests for translating client exceptions into Home Assistant errors."""

import pytest
from hassette_client import (
    AuthenticationError,
    ForbiddenError,
    GatewayError,
    HassetteClientError,
    HassetteConnectionError,
    HassetteTimeoutError,
    NotFoundError,
    RedirectError,
    ResponseValidationError,
    ServiceUnavailableError,
    TelemetryUnavailableError,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.hassette.errors import poll_error, redirect_target, truncate_detail

from .conftest import http_error


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (HassetteConnectionError("refused"), "cannot_connect"),
        (HassetteTimeoutError("slow"), "timeout_connect"),
        (http_error(GatewayError, 502), "cannot_connect"),
        (http_error(ServiceUnavailableError, 503), "cannot_connect"),
        (http_error(TelemetryUnavailableError, 503, code="telemetry_unavailable"), "telemetry_unavailable"),
        (http_error(ForbiddenError, 403), "forbidden"),
        (http_error(RedirectError, 307, location="https://hassette.local/api/apps"), "redirected"),
        (ResponseValidationError(model="AppListResponse", endpoint="GET /api/apps", problems=[]), "invalid_response"),
        (http_error(NotFoundError, 404), "unknown"),
        (HassetteClientError("other"), "unknown"),
    ],
)
def test_poll_errors(error: HassetteClientError, key: str) -> None:
    mapped = poll_error(error)

    assert type(mapped) is UpdateFailed
    assert mapped.translation_key == key


def test_poll_auth_failure_starts_reauth() -> None:
    assert isinstance(poll_error(http_error(AuthenticationError, 401)), ConfigEntryAuthFailed)


@pytest.mark.parametrize(
    ("location", "shown"),
    [
        (None, ""),
        ("https://user:pw@login.example.com/auth?next=/api", "https://login.example.com/auth"),
        ("https://idp.example.com/login?state=secret#frag", "https://idp.example.com/login"),
        ("/cdn-cgi/access/login?kid=abc", "/cdn-cgi/access/login"),
        ("http://[::1", ""),
    ],
)
def test_redirect_target(location: str | None, shown: str) -> None:
    assert redirect_target(http_error(RedirectError, 302, location=location)) == shown


def test_truncate_keeps_short_text() -> None:
    assert truncate_detail("short") == "short"
