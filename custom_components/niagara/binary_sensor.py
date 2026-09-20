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

NAME_PATTERNS_BINARY = [
    (re.compile(r"alarm|fault|trip|alert|emergency|smoke|fire", re.I), BinarySensorDeviceClass.PROBLEM),
    (re.compile(r"fan|motor|pump|compressor|run", re.I), BinarySensorDeviceClass.RUNNING),
    (re.compile(r"door|window|damper|valve", re.I), BinarySensorDeviceClass.OPENING),
    (re.compile(r"occup", re.I), BinarySensorDeviceClass.OCCUPANCY),
    (re.compile(r"motion|pir", re.I), BinarySensorDeviceClass.MOTION),
]

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
        point = self._current_point
        if point and point.value is not None:
            return point.value.lower() in ("true", "1", "on", "active", "enabled")
        return None
