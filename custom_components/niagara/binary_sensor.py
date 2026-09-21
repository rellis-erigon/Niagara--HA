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

from .const import DOMAIN
from .coordinator import NiagaraCoordinator, NiagaraPoint
from .entity import NiagaraEntity

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
    async_add_entities(entities)


class NiagaraBinarySensor(NiagaraEntity, BinarySensorEntity):
    """Binary sensor from a Niagara BMS point."""

    def __init__(self, coordinator: NiagaraCoordinator, point: NiagaraPoint) -> None:
        super().__init__(coordinator, point)
        self._warned_unparseable = False
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
