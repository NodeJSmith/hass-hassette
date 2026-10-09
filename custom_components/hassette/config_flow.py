"""Config flow for Hassette: connect, reauthenticate, reconfigure."""

import logging
from collections.abc import Mapping
from typing import Any

import probatio
from hassette_client import (
    AuthenticationError,
    HassetteClient,
    HassetteClientError,
    RedirectError,
    UnsupportedServerVersionError,
    check_server_version,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import BooleanSelector, TextSelector, TextSelectorConfig, TextSelectorType
from yarl import URL

from .const import DOMAIN
from .errors import FORM_ERRORS, lookup, redirect_target, version_placeholders

_LOGGER = logging.getLogger(__name__)

URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

CONNECTION_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_URL): URL_SELECTOR,
        probatio.Optional(probatio.Secret(CONF_API_TOKEN)): TOKEN_SELECTOR,
        probatio.Optional(CONF_VERIFY_SSL, default=True): BooleanSelector(),
    }
)
REAUTH_SCHEMA = probatio.Schema({probatio.Optional(probatio.Secret(CONF_API_TOKEN)): TOKEN_SELECTOR})


class InvalidUrlError(Exception):
    """The URL can't be used: it doesn't parse, isn't http(s) with a host, or embeds credentials."""


def normalize_url(raw: str) -> str:
    """Trim whitespace and one trailing ``/``; reject a URL with credentials, which collide with the bearer token."""
    raw = raw.strip()
    try:
        parsed = URL(raw)
    except ValueError as err:
        raise InvalidUrlError from err
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise InvalidUrlError
    if parsed.user is not None or parsed.password is not None:
        raise InvalidUrlError
    return raw.removesuffix("/")


def normalize_token(raw: str | None) -> str | None:
    """Store a blank token as absent: hassette-client sends any string, empty included, as a bearer token."""
    return raw or None


class HassetteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one hassette server."""

    VERSION = 1

    def __init__(self) -> None:
        self.placeholders: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for hassette's URL and, optionally, its web API token."""
        errors: dict[str, str] = {}
        if user_input is not None and (data := await self.validate(user_input, errors)) is not None:
            return self.async_create_entry(title=data[CONF_URL], data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(CONNECTION_SCHEMA, user_input),
            errors=errors,
            description_placeholders=self.placeholders,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after hassette rejected the stored token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new token only."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = await self.validate({**entry.data, CONF_API_TOKEN: user_input.get(CONF_API_TOKEN)}, errors)
            if data is not None:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_API_TOKEN: data[CONF_API_TOKEN]})
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={**self.placeholders, CONF_URL: entry.data[CONF_URL]},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change the URL, token or TLS check, keeping every device and its customizations."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None and (data := await self.validate(user_input, errors)) is not None:
            return self.async_update_reload_and_abort(entry, title=data[CONF_URL], data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(CONNECTION_SCHEMA, user_input or entry.data),
            errors=errors,
            description_placeholders=self.placeholders,
        )

    async def validate(self, user_input: Mapping[str, Any], errors: dict[str, str]) -> dict[str, Any] | None:
        """Check the input against the server and return the entry data to store.

        Returns ``None`` with ``errors`` filled in when the input can't be used, and aborts the flow for a
        server too old for this integration, since no other input would fix that.
        """
        self.placeholders = {}
        token = normalize_token(user_input.get(CONF_API_TOKEN))
        verify_ssl: bool = user_input.get(CONF_VERIFY_SSL, True)
        try:
            url = normalize_url(user_input[CONF_URL])
        except InvalidUrlError:
            errors[CONF_URL] = "invalid_url"
            return None

        client = HassetteClient(async_get_clientsession(self.hass, verify_ssl=verify_ssl), url, token=token)
        try:
            check_server_version(await client.get_health())
        except UnsupportedServerVersionError as err:
            raise AbortFlow("unsupported_version", version_placeholders(err)) from err
        except AuthenticationError:
            # With no token, the 401 means hassette doesn't trust Home Assistant's address as a proxy.
            errors["base"] = "invalid_auth" if token else "token_required"
        except HassetteClientError as err:
            if isinstance(err, RedirectError):
                self.placeholders = {"location": redirect_target(err)}
            if (key := lookup(FORM_ERRORS, err)) is None:
                _LOGGER.exception("hassette-client raised an error this integration doesn't map")
                key = "unknown"
            errors["base"] = key
        except Exception:
            _LOGGER.exception("Unexpected error connecting to hassette")
            errors["base"] = "unknown"
        else:
            return {CONF_URL: url, CONF_API_TOKEN: token, CONF_VERIFY_SSL: verify_ssl}
        return None
