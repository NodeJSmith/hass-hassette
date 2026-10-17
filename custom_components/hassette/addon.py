"""Read the connection the hassette add-on announces through Supervisor discovery."""

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.components.hassio import AddonError, AddonManager
from homeassistant.const import CONF_API_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.hassio import is_hassio

from .const import ADDON_NAME

_LOGGER = logging.getLogger(__name__)


def announced_connection(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map an add-on discovery payload (``host``, ``port``, ``auth_token``) to entry data, or ``None`` if unusable."""
    host = config.get("host")
    port = config.get("port")
    if not host or not port:
        return None
    # An add-on that serves without a token announces an empty one; the entry stores that as absent.
    return {CONF_URL: f"http://{host}:{port}", CONF_API_TOKEN: config.get("auth_token") or None}


async def async_read_announced_connection(hass: HomeAssistant, slug: str) -> dict[str, Any] | None:
    """Return the URL and token the add-on ``slug`` announces now, or ``None`` if there is none to use.

    Logged at debug only: the caller falls through to the poll's own error, which the user sees.
    """
    if not is_hassio(hass):
        _LOGGER.debug("Supervisor isn't loaded, so the hassette add-on %s can't be read", slug)
        return None
    try:
        config = await AddonManager(hass, _LOGGER, ADDON_NAME, slug).async_get_addon_discovery_info()
    except AddonError as err:
        _LOGGER.debug("Couldn't read the discovery info of the hassette add-on %s: %s", slug, err)
        return None
    if (announced := announced_connection(config)) is None:
        _LOGGER.debug("The hassette add-on %s announced a payload without host or port", slug)
    return announced
