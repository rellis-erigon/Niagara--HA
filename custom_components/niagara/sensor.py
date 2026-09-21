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
    "min": "min", "h": "h", "s": "s",
    "kVA": "kVA", "VA": "VA", "kvar": "kvar",
}

# "%" is deliberately absent. In a BMS a percentage is far more often a valve
# position, damper position or VFD speed than humidity, and the unit lookup
# wins over the name patterns below — so mapping it here labelled thousands of
# fan and valve points as humidity sensors. A percentage only becomes humidity
# or battery when the point's name says so.
UNIT_DEVICE_CLASS = {
    "°F": SensorDeviceClass.TEMPERATURE, "°C": SensorDeviceClass.TEMPERATURE,
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
    "kVA": SensorDeviceClass.APPARENT_POWER, "VA": SensorDeviceClass.APPARENT_POWER,
    "min": SensorDeviceClass.DURATION, "h": SensorDeviceClass.DURATION,
    "s": SensorDeviceClass.DURATION,
}

# Name patterns only ever *refine* a class when the point already has a unit
# that supports it. They no longer carry a fallback unit: inventing "°C" for a
# point that reports no unit silently mislabels every station configured in
# Fahrenheit, and HA needs a real unit for these classes anyway.
#
# Short tokens are anchored. Without \b, "sp" matched "Speed", "rh" matched
# "Overheat" and "run" matched any word containing those letters.
NAME_PATTERNS_SENSOR = [
    (re.compile(r"temp|room.?t\b|zone.?t\b|supply.?air|return.?air|discharge|duct.?t\b|\boat\b|\bsat\b|\brat\b|\bdat\b|\bchwt\b|\bhwt\b", re.I), SensorDeviceClass.TEMPERATURE),
    (re.compile(r"humid|\brh\b|rel.?hum|dew.?point", re.I), SensorDeviceClass.HUMIDITY),
    (re.compile(r"\bco2\b|carbon.?di", re.I), SensorDeviceClass.CO2),
    (re.compile(r"press|\bpsi\b|static.?p", re.I), SensorDeviceClass.PRESSURE),
    (re.compile(r"power|\bkw\b|demand", re.I), SensorDeviceClass.POWER),
    (re.compile(r"energy|\bkwh\b|consumption", re.I), SensorDeviceClass.ENERGY),
    (re.compile(r"volt", re.I), SensorDeviceClass.VOLTAGE),
    (re.compile(r"current|\bamp", re.I), SensorDeviceClass.CURRENT),
    (re.compile(r"freq|\bhz\b", re.I), SensorDeviceClass.FREQUENCY),
    (re.compile(r"water.?meter|water.?consump|water.?total|water.?usage|\bdcw\b|\bdhw\b|chw.?flow|hydraulic", re.I), SensorDeviceClass.WATER),
    (re.compile(r"gas.?meter|gas.?consump|gas.?total|gas.?usage|natural.?gas", re.I), SensorDeviceClass.GAS),
    (re.compile(r"batter|\bsoc\b|state.?of.?charge", re.I), SensorDeviceClass.BATTERY),
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


VALID_UNITS_FOR_CLASS: dict[SensorDeviceClass, set[str]] = {
    SensorDeviceClass.TEMPERATURE: {"°C", "°F", "K"},
    SensorDeviceClass.POWER: {"kW", "W", "MW", "BTU/h"},
    SensorDeviceClass.ENERGY: {"kWh", "Wh", "MWh", "GJ", "MJ", "BTU", "therm"},
    SensorDeviceClass.VOLTAGE: {"V", "mV", "kV"},
    SensorDeviceClass.CURRENT: {"A", "mA", "μA"},
    SensorDeviceClass.FREQUENCY: {"Hz", "kHz", "MHz", "GHz"},
    SensorDeviceClass.PRESSURE: {"Pa", "kPa", "psi", "mbar", "bar", "hPa", "inHg", "mmHg", "inH2O"},
    SensorDeviceClass.HUMIDITY: {"%"},
    SensorDeviceClass.CO2: {"ppm"},
    SensorDeviceClass.BATTERY: {"%"},
    # Without these, a class with no entry accepted any unit at all — which
    # turned a tank level reported in "%" into a WATER sensor.
    SensorDeviceClass.WATER: {"L", "m³", "gal", "ft³", "CCF"},
    SensorDeviceClass.GAS: {"m³", "ft³", "CCF"},
    SensorDeviceClass.VOLUME: {"L", "m³", "gal", "ft³", "CCF"},
    SensorDeviceClass.APPARENT_POWER: {"VA", "kVA", "mVA"},
    SensorDeviceClass.DURATION: {"d", "h", "min", "s", "ms"},
}


# Templates name classes as plain strings; map them to the enums once.
_DEVICE_CLASS_BY_NAME = {cls.value: cls for cls in SensorDeviceClass}
_STATE_CLASS_BY_NAME = {cls.value: cls for cls in SensorStateClass}


def _unit_valid_for_class(unit: str, device_class: SensorDeviceClass) -> bool:
    valid = VALID_UNITS_FOR_CLASS.get(device_class)
    if valid is None:
        return True
    return unit in valid


def _infer_icon(name: str) -> str | None:
    for pattern, icon in ICON_PATTERNS:
        if pattern.search(name):
            return icon
    return None


def infer_device_class_and_unit(
    name: str, path: str, raw_unit: str | None,
) -> tuple[SensorDeviceClass | None, str | None]:
    """Decide a point's device class and HA unit.

    The point's own unit leads; a name only refines when that unit supports
    the class. A point with no unit gets no device class, because every class
    worth assigning here needs one and guessing it mislabels whole stations.
    """
    unit: str | None = None
    device_class: SensorDeviceClass | None = None

    if raw_unit:
        unit = UNIT_MAP.get(raw_unit, raw_unit)
        device_class = UNIT_DEVICE_CLASS.get(raw_unit)
        if device_class == SensorDeviceClass.VOLUME:
            device_class = _refine_volume_class(name, path)

    if device_class is None and unit is not None:
        for pattern, candidate in NAME_PATTERNS_SENSOR:
            if pattern.search(name) and _unit_valid_for_class(unit, candidate):
                device_class = candidate
                break

    if device_class is not None and unit is not None:
        if not _unit_valid_for_class(unit, device_class):
            device_class = None

    return device_class, unit


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
        device_class, unit = infer_device_class_and_unit(
            point.name, point.path, point.unit,
        )

        # A template slot states what the point is, so it outranks inference
        # — that is the whole reason templates exist. It still has to survive
        # unit validation, or we would reintroduce contradicted classes.
        if point.slot_device_class:
            declared = _DEVICE_CLASS_BY_NAME.get(point.slot_device_class.lower())
            if declared is not None and (
                unit is None or _unit_valid_for_class(unit, declared)
            ):
                device_class = declared
                # Niagara tags a unit on only some of a site's identical
                # meters. A class like energy is useless to the energy
                # dashboard without one, and the slot has already declared
                # which units it accepts — so take the first. This is the
                # template author asserting what the slot measures, not the
                # name-based guessing removed in 2.4.1.
                if unit is None and point.slot_units:
                    unit = point.slot_units[0]

        if device_class is not None:
            self._attr_device_class = device_class

        # Numeric points are always statistics-worthy, class or not, so a
        # valve position still graphs and still gets long-term statistics.
        self._attr_state_class = _infer_state_class(device_class, unit)
        if point.slot_state_class:
            declared_state = _STATE_CLASS_BY_NAME.get(
                point.slot_state_class.lower(),
            )
            if declared_state is not None:
                self._attr_state_class = declared_state

        if unit is not None:
            self._attr_native_unit_of_measurement = unit

        # Niagara knows how many decimals the point is meant to show. Without
        # it a float32 reaches HA as 22.700000762939453.
        if point.precision is not None:
            self._attr_suggested_display_precision = point.precision

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
    """Enum sensor from a Niagara BMS point.

    HA only accepts `options` alongside `device_class = ENUM`, and rejects any
    state outside that list. Previously options were declared without the class
    and unlisted values were passed through, which HA logs as an error.
    """

    def __init__(self, coordinator: NiagaraCoordinator, point: NiagaraPoint) -> None:
        super().__init__(coordinator, point)
        self._options = [str(o) for o in point.enum_range if str(o).strip()]
        self._warned_unlisted = False
        if self._options:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = self._options
        icon = _infer_icon(point.name)
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self) -> str | None:
        point = self._current_point
        if point is None or point.value is None:
            return None

        value = str(point.value)
        if not self._options:
            return value
        if value in self._options:
            return value

        # Niagara reported a state outside the declared range. Returning it
        # would make HA raise on every update, so report unknown instead.
        if not self._warned_unlisted:
            self._warned_unlisted = True
            _LOGGER.warning(
                "%s reported %r, which is not in its declared range %s",
                point.path, value, self._options,
            )
        return None


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
