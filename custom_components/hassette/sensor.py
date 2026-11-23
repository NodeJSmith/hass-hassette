"""The status sensor on each hassette app's device, and the sensors app instances declare."""

import logging
from datetime import date, datetime
from typing import Any

from hassette_wire import AppInstance, AppStatus, DeclaredEntity, UnknownValue
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import ATTR_ERROR_MESSAGE, ATTR_SERVER_STATUS
from .coordinator import HassetteConfigEntry, HassetteCoordinator
from .declared import HassetteDeclaredEntity, add_declared_entities
from .entity import HassetteAppEntity, add_app_entities
from .errors import truncate_detail

_LOGGER = logging.getLogger(__name__)

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
    of Home Assistant doesn't know raises ``ValueError`` when Home Assistant adds the entity, so it isn't added
    and the add is retried, with Home Assistant logging the error, on every poll (an accepted limitation, D4).
    """

    def __init__(
        self,
        coordinator: HassetteCoordinator,
        app_key: str,
        instance: AppInstance,
        declared: DeclaredEntity,
        parent_device_id: str,
    ) -> None:
        super().__init__(coordinator, app_key, instance, declared, parent_device_id)
        # The timestamp and date values already warned about, so each is logged once.
        self.warned_values: set[str] = set()

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
        if parsed is None and raw not in self.warned_values:
            self.warned_values.add(raw)
            _LOGGER.warning(
                "%s: hassette sent %r, which isn't a valid %s; showing it as unknown",
                self.entity_id,
                raw,
                device_class,
            )
        return parsed

    @property
    def native_unit_of_measurement(self) -> str | None:
        declared = self.declared
        return None if declared is None else declared.unit

    @property
    def device_class(self) -> SensorDeviceClass | None:
        declared = self.declared
        return None if declared is None or declared.device_class is None else SensorDeviceClass(declared.device_class)

    @property
    def state_class(self) -> SensorStateClass | None:
        declared = self.declared
        return None if declared is None or declared.state_class is None else SensorStateClass(declared.state_class)

    @property
    def options(self) -> list[str] | None:
        declared = self.declared
        return None if declared is None else declared.options

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        declared = self.declared
        return None if declared is None else declared.attributes or None


def parse_date_value(raw: str, device_class: str) -> date | datetime | None:
    """Parse a timestamp or date sensor's wire value (ISO 8601), or ``None`` when it isn't one."""
    try:
        if device_class == SensorDeviceClass.TIMESTAMP:
            return dt_util.parse_datetime(raw)
        return date.fromisoformat(raw)
    except ValueError:
        return None
