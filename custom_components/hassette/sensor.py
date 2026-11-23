"""The status sensor on each hassette app's device, and the sensors app instances declare."""

from datetime import date, datetime
from typing import Any

from hassette_wire import AppStatus, UnknownValue
from homeassistant.components.sensor import (
    ATTR_LAST_RESET,
    ATTR_OPTIONS,
    ATTR_STATE_CLASS,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.util.enum import try_parse_enum

from .const import ATTR_ERROR_MESSAGE, ATTR_SERVER_STATUS
from .coordinator import HassetteConfigEntry
from .declared import HA_OWNED_ATTRIBUTES, HassetteDeclaredEntity, add_declared_entities
from .entity import HassetteAppEntity, add_app_entities
from .errors import truncate_detail

# Reads only coordinator data.
PARALLEL_UPDATES = 0

# The sensor state for a status newer than this integration's hassette-client; the raw value goes in an attribute.
UNKNOWN_STATUS = "unknown"
STATUSES_WITH_ERROR = frozenset({AppStatus.FAILED, AppStatus.DEGRADED})
# Declared sensor device classes whose ISO 8601 wire value is parsed, since Home Assistant rejects a string for them.
PARSED_DEVICE_CLASSES = frozenset({SensorDeviceClass.TIMESTAMP, SensorDeviceClass.DATE})

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
    """Add a status sensor for every hassette app, and each sensor an app instance declares."""
    add_app_entities(entry, async_add_entities, Platform.SENSOR, STATUS, HassetteStatusSensor)
    add_declared_entities(entry, async_add_entities, Platform.SENSOR, HassetteDeclaredSensor)


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


class HassetteDeclaredSensor(HassetteDeclaredEntity, SensorEntity):
    """A sensor an app instance declares, showing the value and attributes the app last set.

    The unit, device class, state class and options are the declared ones. A device or state class this version
    of Home Assistant doesn't know is left out with one warning, as ESPHome does, so the sensor still works.
    """

    # The recorder reads the state class and last reset from the state, so an app attribute can't stand in for them.
    owned_attributes = HA_OWNED_ATTRIBUTES | {ATTR_STATE_CLASS, ATTR_OPTIONS, ATTR_LAST_RESET}

    @property
    def native_value(self) -> str | int | float | date | datetime | None:
        declared = self.declared
        if declared is None or declared.value is None:
            return None
        device_class = declared.device_class
        if device_class is None or device_class not in PARSED_DEVICE_CLASSES:
            return declared.value
        raw = str(declared.value)
        parsed = parse_date_value(raw, device_class)
        if parsed is None:
            self.warn_once(raw, f"hassette sent {raw!r}, which isn't a valid {device_class}; showing it as unknown")
        return parsed

    @property
    def native_unit_of_measurement(self) -> str | None:
        declared = self.declared
        return None if declared is None else declared.unit

    @property
    def device_class(self) -> SensorDeviceClass | None:
        declared = self.declared
        if declared is None or declared.device_class is None:
            return None
        if (parsed := try_parse_enum(SensorDeviceClass, declared.device_class)) is None:
            self.warn_once(f"device_class:{declared.device_class}", unknown_class("device", declared.device_class))
        return parsed

    @property
    def state_class(self) -> SensorStateClass | None:
        declared = self.declared
        if declared is None or declared.state_class is None:
            return None
        if (parsed := try_parse_enum(SensorStateClass, declared.state_class)) is None:
            self.warn_once(f"state_class:{declared.state_class}", unknown_class("state", declared.state_class))
        return parsed

    @property
    def options(self) -> list[str] | None:
        # HA accepts options only on an enum sensor, so they go with an enum class this HA knows.
        declared = self.declared
        return None if declared is None or self.device_class is not SensorDeviceClass.ENUM else declared.options


def parse_date_value(raw: str, device_class: str) -> date | datetime | None:
    """Parse a timestamp or date sensor's wire value (ISO 8601), or ``None`` when it isn't one."""
    try:
        if device_class == SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(raw)
        return date.fromisoformat(raw)
    except ValueError:
        return None


def unknown_class(kind: str, value: str) -> str:
    """The warning for a declared device or state class this version of Home Assistant doesn't have."""
    return f"this Home Assistant has no {kind} class {value!r}; leaving it out"
