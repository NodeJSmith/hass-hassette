"""The diagnostics download: the /ws stream's health and the coordinator's state, which the 30 s poll hides."""

from datetime import datetime
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_TOKEN
from homeassistant.core import HomeAssistant

from .coordinator import HassetteConfigEntry, HassetteCoordinator
from .stream import HassetteStream

TO_REDACT = {CONF_API_TOKEN}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HassetteConfigEntry) -> dict[str, Any]:
    """Report the entry's data with the token redacted, the coordinator's state and the stream's health."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "coordinator": coordinator_diagnostics(coordinator),
        "stream": stream_diagnostics(coordinator.stream),
    }


def coordinator_diagnostics(coordinator: HassetteCoordinator) -> dict[str, Any]:
    # runtime_data is set only after the first refresh, so the coordinator holds data.
    data = coordinator.data
    return {
        "last_update_success": coordinator.last_update_success,
        "last_success": isoformat(coordinator.last_success),
        "bootstrap_released": coordinator.bootstrap_released,
        "server_version": coordinator.server_version,
        "api_schema_version": data.health.api_schema_version,
        "boot_id": data.boot_id,
        "entity_revision": data.entity_revision,
        # The revision of each entity a push replaced since the listing was read, by app/instance/key/platform.
        "pushed_revisions": {"/".join(key): revision for key, revision in data.pushed_revisions.items()},
    }


def stream_diagnostics(stream: HassetteStream | None) -> dict[str, Any]:
    if stream is None:
        return {"running": False}
    health = stream.health
    return {
        "running": stream.running,
        "last_connected": isoformat(health.last_connected),
        "last_message": isoformat(health.last_message),
        "pushes_applied": health.pushes_applied,
        "pushes_ignored": dict(health.pushes_ignored),
        "last_error": health.last_error,
    }


def isoformat(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None
