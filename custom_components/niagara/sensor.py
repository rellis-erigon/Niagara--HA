"""Sensor platform for Niagara BMS."""

import logging
import re

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NiagaraCoordinator, NiagaraPoint
from .entity import NiagaraEntity

_LOGGER = logging.getLogger(__name__)

UNIT_MAP = {
    "°F": "°F", "°C": "°C", "%": "%", "psi": "psi", "kPa": "kPa", "Pa": "Pa",
    "cfm": "ft³/min", "l/s": "L/s", "gpm": "gal/min", "kW": "kW", "W": "W",
    "kWh": "kWh", "Wh": "Wh", "V": "V", "A": "A", "Hz": "Hz", "rpm": "rpm",
    "in. w.c.": "inH2O", "MWh": "MWh", "GJ": "GJ", "MJ": "MJ", "BTU": "BTU",
    "therm": "therm", "mbar": "mbar", "bar": "bar", "m³": "m³", "ft³": "ft³",
    "L": "L", "gal": "gal", "CCF": "CCF", "m³/h": "m³/h", "ft³/h": "ft³/h", "L/h": "L/h",
}

UNIT_DEVICE_CLASS = {
    "°F": SensorDeviceClass.TEMPERATURE, "°C": SensorDeviceClass.TEMPERATURE,
    "%": SensorDeviceClass.HUMIDITY,
    "kW": SensorDeviceClass.POWER, "W": SensorDeviceClass.POWER,
    "kWh": SensorDeviceClass.ENERGY, "Wh": SensorDeviceClass.ENERGY,
    "MWh": SensorDeviceClass.ENERGY, "GJ": SensorDeviceClass.ENERGY,
    "MJ": SensorDeviceClass.ENERGY,
    "V": SensorDeviceClass.VOLTAGE, "A": SensorDeviceClass.CURRENT,
    "Hz": SensorDeviceClass.FREQUENCY,
    "psi": SensorDeviceClass.PRESSURE, "kPa": SensorDeviceClass.PRESSURE,
    "Pa": SensorDeviceClass.PRESSURE, "mbar": SensorDeviceClass.PRESSURE,
    "bar": SensorDeviceClass.PRESSURE,
    "m³": SensorDeviceClass.VOLUME, "ft³": SensorDeviceClass.VOLUME,
    "L": SensorDeviceClass.VOLUME, "gal": SensorDeviceClass.VOLUME,
    "CCF": SensorDeviceClass.VOLUME,
}

NAME_PATTERNS_SENSOR = [
    (re.compile(r"temp|room.?t|zone.?t|supply.?air|return.?air|discharge|duct.?t|oat|sat|rat|dat|chwt|hwt", re.I), SensorDeviceClass.TEMPERATURE, "°C"),
    (re.compile(r"humid|rh$|rel.?hum", re.I), SensorDeviceClass.HUMIDITY, "%"),
    (re.compile(r"co2|carbon.?di", re.I), SensorDeviceClass.CO2, "ppm"),
    (re.compile(r"press|psi|static.?p", re.I), SensorDeviceClass.PRESSURE, None),
    (re.compile(r"power|kw$|demand", re.I), SensorDeviceClass.POWER, "kW"),
    (re.compile(r"energy|kwh|consumption", re.I), SensorDeviceClass.ENERGY, "kWh"),
    (re.compile(r"volt", re.I), SensorDeviceClass.VOLTAGE, "V"),
    (re.compile(r"current|amp", re.I), SensorDeviceClass.CURRENT, "A"),
    (re.compile(r"freq|hz$", re.I), SensorDeviceClass.FREQUENCY, "Hz"),
    (re.compile(r"water.?meter|water.?consump|water.?total|water.?usage|dcw|dhw|chw.?flow|hydraulic", re.I), SensorDeviceClass.WATER, "L"),
    (re.compile(r"gas.?meter|gas.?consump|gas.?total|gas.?usage|natural.?gas", re.I), SensorDeviceClass.GAS, "m³"),
    (re.compile(r"batter|soc$|state.?of.?charge", re.I), SensorDeviceClass.BATTERY, "%"),
]

ICON_PATTERNS = [
    (re.compile(r"fan|vfd", re.I), "mdi:fan"),
    (re.compile(r"pump", re.I), "mdi:pump"),
    (re.compile(r"valve|vlv", re.I), "mdi:pipe-valve"),
    (re.compile(r"damper|dpr|dmpr", re.I), "mdi:valve"),
    (re.compile(r"alarm|fault", re.I), "mdi:alarm-light"),
    (re.compile(r"setpoint|set.?pt|stpt", re.I), "mdi:thermostat"),
    (re.compile(r"mode|command|cmd", re.I), "mdi:cog"),
    (re.compile(r"status|state", re.I), "mdi:information-outline"),
    (re.compile(r"speed|vfd|freq", re.I), "mdi:speedometer"),
    (re.compile(r"flow|cfm|gpm", re.I), "mdi:waves-arrow-right"),
    (re.compile(r"light|lux|luminaire", re.I), "mdi:lightbulb"),
]

ENERGY_UNITS = frozenset({"kWh", "Wh", "MWh", "GJ", "MJ", "BTU", "therm"})
WATER_UNITS = frozenset({"m³", "ft³", "L", "gal", "CCF"})

_WATER_RE = re.compile(r"water|dcw|dhw|chw|hydraulic|irrigation|potable|sewage|drain|tank", re.I)
_GAS_RE = re.compile(r"\bgas\b|natural.?gas|lng|lpg|propane|methane", re.I)


def _refine_volume_class(name: str, path: str) -> SensorDeviceClass:
    text = f"{name} {path}"
    if _WATER_RE.search(text):
        return SensorDeviceClass.WATER
    if _GAS_RE.search(text):
        return SensorDeviceClass.GAS
    return SensorDeviceClass.WATER


def _infer_state_class(
    device_class: SensorDeviceClass | None, unit: str | None,
) -> SensorStateClass:
    if device_class in (SensorDeviceClass.ENERGY, SensorDeviceClass.GAS, SensorDeviceClass.WATER):
        return SensorStateClass.TOTAL_INCREASING
    if device_class == SensorDeviceClass.VOLUME and unit and unit in WATER_UNITS:
        return SensorStateClass.TOTAL_INCREASING
    if unit and unit in ENERGY_UNITS:
        return SensorStateClass.TOTAL_INCREASING
    return SensorStateClass.MEASUREMENT


def _infer_icon(name: str) -> str | None:
    for pattern, icon in ICON_PATTERNS:
        if pattern.search(name):
            return icon
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NiagaraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for point in coordinator.points.values():
        if point.point_type in ("numeric", "integer"):
            entities.append(NiagaraNumericSensor(coordinator, point))
        elif point.point_type == "enum":
            entities.append(NiagaraEnumSensor(coordinator, point))
        elif point.point_type == "string":
            entities.append(NiagaraStringSensor(coordinator, point))
    async_add_entities(entities)


class NiagaraNumericSensor(NiagaraEntity, SensorEntity):
    """Numeric sensor from a Niagara BMS point."""

    def __init__(self, coordinator: NiagaraCoordinator, point: NiagaraPoint) -> None:
        super().__init__(coordinator, point)
        device_class = None
        unit = None

        if point.unit:
            unit = UNIT_MAP.get(point.unit, point.unit)
            device_class = UNIT_DEVICE_CLASS.get(point.unit)
            if device_class == SensorDeviceClass.VOLUME:
                device_class = _refine_volume_class(point.name, point.path)

        if device_class is None:
            for pattern, dc, fallback_unit in NAME_PATTERNS_SENSOR:
                if pattern.search(point.name):
                    device_class = dc
                    if fallback_unit and unit is None:
                        unit = fallback_unit
                    break

        if device_class is not None:
            self._attr_device_class = device_class
            self._attr_state_class = _infer_state_class(device_class, unit)

        if unit is not None:
            self._attr_native_unit_of_measurement = unit

        if device_class is None:
            icon = _infer_icon(point.name)
            if icon:
                self._attr_icon = icon

    @property
    def native_value(self) -> float | None:
        point = self._current_point
        if point and point.value is not None:
            try:
                return float(point.value)
            except (ValueError, TypeError):
                return None
        return None


class NiagaraEnumSensor(NiagaraEntity, SensorEntity):
    """Enum sensor from a Niagara BMS point."""

    def __init__(self, coordinator: NiagaraCoordinator, point: NiagaraPoint) -> None:
        super().__init__(coordinator, point)
        if point.enum_range:
            self._attr_options = point.enum_range
        icon = _infer_icon(point.name)
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self) -> str | None:
        point = self._current_point
        return point.value if point else None


class NiagaraStringSensor(NiagaraEntity, SensorEntity):
    """String sensor from a Niagara BMS point."""

    def __init__(self, coordinator: NiagaraCoordinator, point: NiagaraPoint) -> None:
        super().__init__(coordinator, point)
        icon = _infer_icon(point.name)
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self) -> str | None:
        point = self._current_point
        return point.value if point else None
