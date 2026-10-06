"""Binary sensor platform for Niagara BMS."""

import logging
import re

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alarm_state import alarm_attributes
from .const import DOMAIN
from .coordinator import NiagaraCoordinator, NiagaraPoint
from .entity import NiagaraDeviceEntity, NiagaraEntity

_LOGGER = logging.getLogger(__name__)

# Short tokens are anchored: "run" previously matched any word containing
# those letters, so unrelated points were classed as running equipment.
NAME_PATTERNS_BINARY = [
    (re.compile(r"alarm|fault|trip|alert|emergency|smoke|\bfire\b", re.I), BinarySensorDeviceClass.PROBLEM),
    (re.compile(r"\bfan\b|motor|pump|compressor|\brun", re.I), BinarySensorDeviceClass.RUNNING),
    (re.compile(r"door|window|damper|valve", re.I), BinarySensorDeviceClass.OPENING),
    (re.compile(r"occup", re.I), BinarySensorDeviceClass.OCCUPANCY),
    (re.compile(r"motion|\bpir\b", re.I), BinarySensorDeviceClass.MOTION),
]

# Templates name classes as plain strings; map them to the enums once.
_DEVICE_CLASS_BY_NAME = {cls.value: cls for cls in BinarySensorDeviceClass}

# Niagara reports booleans in several shapes depending on the driver.
TRUE_VALUES = frozenset({"true", "1", "on", "active", "enabled", "yes", "open"})
FALSE_VALUES = frozenset({"false", "0", "off", "inactive", "disabled", "no", "closed"})

ICON_PATTERNS = [
    (re.compile(r"fan|vfd", re.I), "mdi:fan"),
    (re.compile(r"pump", re.I), "mdi:pump"),
    (re.compile(r"valve|vlv", re.I), "mdi:pipe-valve"),
    (re.compile(r"damper|dpr|dmpr", re.I), "mdi:valve"),
    (re.compile(r"alarm|fault", re.I), "mdi:alarm-light"),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NiagaraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for point in coordinator.points.values():
        if point.point_type == "boolean":
            entities.append(NiagaraBinarySensor(coordinator, point))

    if coordinator.alarms_supported:
        for device in coordinator.devices().values():
            entities.append(NiagaraDeviceAlarm(coordinator, device))

    async_add_entities(entities)


class NiagaraDeviceAlarm(NiagaraDeviceEntity, BinarySensorEntity):
    """Whether the station's alarm console holds an alarm for this device.

    Distinct from the per-point `problem` sensors, which come from a point
    whose *name* matched `*alarm*`. This one comes from the alarm console:
    it knows the alarm's priority, when it started and whether anyone has
    acknowledged it, and it finds alarms on points nobody thought to enable.
    """

    DOMAIN_KEY = "device_alarm"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Station alarm"
    _attr_entity_registry_enabled_default = True

    @property
    def _records(self) -> list[dict]:
        return self.coordinator.alarms_by_group().get(self._group, [])

    @property
    def is_on(self) -> bool:
        return bool(self._records)

    @property
    def available(self) -> bool:
        """Tracks the console, not the device's own points.

        A device whose points have all faulted may well be exactly the one
        the station is alarming about, so this must not go unavailable with
        them — that would hide the alarm at the moment it matters.
        """
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {"niagara_group": self._group}
        attrs.update(alarm_attributes(self._records))
        return attrs


class NiagaraBinarySensor(NiagaraEntity, BinarySensorEntity):
    """Binary sensor from a Niagara BMS point."""

    def __init__(self, coordinator: NiagaraCoordinator, point: NiagaraPoint) -> None:
        super().__init__(coordinator, point)
        self._warned_unparseable = False

        if point.slot_device_class:
            declared = _DEVICE_CLASS_BY_NAME.get(point.slot_device_class.lower())
            if declared is not None:
                self._attr_device_class = declared
                return

        for pattern, dc in NAME_PATTERNS_BINARY:
            if pattern.search(point.name):
                self._attr_device_class = dc
                break

        if not hasattr(self, "_attr_device_class") or self._attr_device_class is None:
            for pattern, icon in ICON_PATTERNS:
                if pattern.search(point.name):
                    self._attr_icon = icon
                    break

    @property
    def is_on(self) -> bool | None:
        """Return None rather than False for a value we cannot interpret.

        Previously anything outside the true-list read as False, so an
        unrecognised state reported a confidently wrong "off" — bad for an
        alarm or a running status.
        """
        point = self._current_point
        if point is None or point.value is None:
            return None

        value = str(point.value).strip().lower()
        if value in TRUE_VALUES:
            return True
        if value in FALSE_VALUES:
            return False

        if not self._warned_unparseable:
            self._warned_unparseable = True
            _LOGGER.warning(
                "%s reported %r, which is not a recognised boolean",
                point.path, point.value,
            )
        return None
