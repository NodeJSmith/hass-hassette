"""The status sensor on each hassette app's device."""

from typing import Any

from hassette_wire import AppStatus, UnknownValue
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import ATTR_ERROR_MESSAGE, ATTR_SERVER_STATUS
from .coordinator import HassetteConfigEntry
from .entity import HassetteAppEntity, add_app_entities
from .errors import truncate_detail

# Reads only coordinator data.
PARALLEL_UPDATES = 0

# The sensor state for a status newer than this integration's hassette-client; the raw value goes in an attribute.
UNKNOWN_STATUS = "unknown"
STATUSES_WITH_ERROR = frozenset({AppStatus.FAILED, AppStatus.DEGRADED})

STATUS = SensorEntityDescription(
    key="status",
    translation_key="status",
    device_class=SensorDeviceClass.ENUM,
    options=[status.value for status in AppStatus] + [UNKNOWN_STATUS],
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HassetteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a status sensor for every hassette app."""
    add_app_entities(entry, async_add_entities, Platform.SENSOR, STATUS, HassetteStatusSensor)


class HassetteStatusSensor(HassetteAppEntity, SensorEntity):
    """The app's lifecycle status, with the server's error text while it is failed or degraded."""

    _unrecorded_attributes = frozenset({ATTR_SERVER_STATUS, ATTR_ERROR_MESSAGE})

    @property
    def native_value(self) -> str | None:
        app = self.app
        if app is None:
            return None
        if isinstance(app.status, UnknownValue):
            return UNKNOWN_STATUS
        return app.status.value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        app = self.app
        if app is None:
            return None
        attributes: dict[str, Any] = {}
        if isinstance(app.status, UnknownValue):
            attributes[ATTR_SERVER_STATUS] = app.status.value
        elif app.status in STATUSES_WITH_ERROR and app.error_message:
            attributes[ATTR_ERROR_MESSAGE] = truncate_detail(app.error_message)
        return attributes or None
