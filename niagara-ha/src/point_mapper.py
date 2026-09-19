"""Map Niagara oBIX points to Home Assistant entity types and MQTT discovery payloads."""

import hashlib
import logging
import re
from typing import Any, Optional

from obix_client import NiagaraPoint

logger = logging.getLogger(__name__)

UNIT_MAP = {
    "°F": "°F",
    "°C": "°C",
    "%": "%",
    "psi": "psi",
    "kPa": "kPa",
    "Pa": "Pa",
    "cfm": "ft³/min",
    "l/s": "L/s",
    "gpm": "gal/min",
    "kW": "kW",
    "W": "W",
    "kWh": "kWh",
    "Wh": "Wh",
    "V": "V",
    "A": "A",
    "Hz": "Hz",
    "rpm": "rpm",
    "in. w.c.": "inH2O",
    "MWh": "MWh",
    "GJ": "GJ",
    "MJ": "MJ",
    "BTU": "BTU",
    "therm": "therm",
    "mbar": "mbar",
    "bar": "bar",
}

DEVICE_CLASS_MAP = {
    "°F": "temperature",
    "°C": "temperature",
    "%": "humidity",
    "kW": "power",
    "W": "power",
    "kWh": "energy",
    "Wh": "energy",
    "V": "voltage",
    "A": "current",
    "Hz": "frequency",
    "psi": "pressure",
    "kPa": "pressure",
    "Pa": "pressure",
    "mbar": "pressure",
    "bar": "pressure",
    "MWh": "energy",
    "GJ": "energy",
    "MJ": "energy",
}

NAME_DEVICE_CLASS_SENSOR = [
    (re.compile(r"temp|room.?t|zone.?t|supply.?air|return.?air|discharge|duct.?t|oat|sat|rat|dat|chwt|hwt", re.I), "temperature", "°C"),
    (re.compile(r"humid|rh$|rel.?hum", re.I), "humidity", "%"),
    (re.compile(r"co2|carbon.?di", re.I), "carbon_dioxide", "ppm"),
    (re.compile(r"press|psi|static.?p", re.I), "pressure", None),
    (re.compile(r"power|kw$|demand", re.I), "power", "kW"),
    (re.compile(r"energy|kwh|consumption", re.I), "energy", "kWh"),
    (re.compile(r"volt", re.I), "voltage", "V"),
    (re.compile(r"current|amp", re.I), "current", "A"),
    (re.compile(r"freq|hz$", re.I), "frequency", "Hz"),
]

_TOTAL_INCREASING_RE = re.compile(
    r"total|cumul|consump|daily.?energy|monthly|annual|lifetime|accum|meter.?read|import|export.?energy",
    re.I,
)

ENERGY_UNITS = frozenset({"kWh", "Wh", "MWh", "GJ", "MJ", "BTU", "therm"})

NAME_DEVICE_CLASS_BINARY = [
    (re.compile(r"alarm|fault|trip|alert|emergency|smoke|fire", re.I), "problem"),
    (re.compile(r"fan|motor|pump|compressor|run", re.I), "running"),
    (re.compile(r"door|window|damper|valve", re.I), "opening"),
    (re.compile(r"occup", re.I), "occupancy"),
    (re.compile(r"motion|pir", re.I), "motion"),
]

NAME_ICON_MAP = [
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


def _infer_state_class(device_class: str, name: str, unit: str | None) -> str:
    if device_class == "energy" or (unit and unit in ENERGY_UNITS):
        if _TOTAL_INCREASING_RE.search(name):
            return "total_increasing"
        return "total_increasing"
    return "measurement"


def _infer_device_class_sensor(name: str) -> tuple[str | None, str | None]:
    for pattern, dc, unit in NAME_DEVICE_CLASS_SENSOR:
        if pattern.search(name):
            return dc, unit
    return None, None


def _infer_device_class_binary(name: str) -> str | None:
    for pattern, dc in NAME_DEVICE_CLASS_BINARY:
        if pattern.search(name):
            return dc
    return None


def _infer_icon(name: str) -> str | None:
    for pattern, icon in NAME_ICON_MAP:
        if pattern.search(name):
            return icon
    return None


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _stable_id(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()[:12]


def _friendly_name(point: NiagaraPoint) -> str:
    return point.name


def _get_group(point: NiagaraPoint, device_depth: int = 0, device_folders: list[str] | None = None) -> str:
    skip = {"config", "Drivers", "NiagaraNetwork", "ObixNetwork", "points", "out", "exports", ""}
    parts = [p for p in point.path.strip("/").split("/") if p not in skip]
    if len(parts) < 2:
        return "Ungrouped"
    point_path = "/".join(parts[:-1])
    if device_folders:
        best = ""
        for folder in device_folders:
            if point_path == folder or point_path.startswith(folder + "/"):
                if len(folder) > len(best):
                    best = folder
        if best:
            return best
    if device_depth > 0:
        return "/".join(parts[:device_depth])
    return point_path


def map_point(
    point: NiagaraPoint, topic_prefix: str, device_name: str = "Niagara BMS",
    group: str = "", custom_name: str = "", device_depth: int = 0,
    device_folders: list[str] | None = None,
) -> Optional[dict[str, Any]]:
    """Convert a NiagaraPoint into an MQTT discovery payload dict.

    Returns None for point types we don't map yet.
    """
    uid = _stable_id(point.path)
    object_id = _slugify(point.name) + "_" + uid
    friendly = custom_name or _friendly_name(point)

    base_topic = f"{topic_prefix}/{object_id}"
    state_topic = f"{base_topic}/state"
    availability_topic = f"{topic_prefix}/bridge/availability"

    group_name = group or _get_group(point, device_depth, device_folders)
    device_id = f"niagara_{_stable_id(topic_prefix + '/' + group_name)}"
    group_parts = group_name.split("/")
    short_group = " / ".join(group_parts[-3:]) if len(group_parts) > 3 else " / ".join(group_parts)
    display_name = f"{device_name} — {short_group}"

    device_info = {
        "identifiers": [device_id],
        "name": display_name,
        "manufacturer": "Tridium",
        "model": "Niagara 4",
        "sw_version": "oBIX",
        "via_device": f"niagara_{_stable_id(topic_prefix)}",
    }

    if point.point_type == "boolean":
        return _map_binary_sensor(point, object_id, friendly, state_topic, availability_topic, device_info)
    elif point.point_type in ("numeric", "integer"):
        return _map_sensor_numeric(point, object_id, friendly, state_topic, availability_topic, device_info)
    elif point.point_type == "enum":
        return _map_sensor_enum(point, object_id, friendly, state_topic, availability_topic, device_info)
    elif point.point_type == "string":
        return _map_sensor_string(point, object_id, friendly, state_topic, availability_topic, device_info)
    else:
        return None


def _map_binary_sensor(
    point: NiagaraPoint,
    object_id: str,
    friendly: str,
    state_topic: str,
    availability_topic: str,
    device_info: dict,
) -> dict:
    config: dict[str, Any] = {
        "name": friendly,
        "unique_id": f"niagara_{object_id}",
        "state_topic": state_topic,
        "payload_on": "true",
        "payload_off": "false",
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device_info,
    }

    dc = _infer_device_class_binary(point.name)
    if dc:
        config["device_class"] = dc

    icon = _infer_icon(point.name)
    if icon and not dc:
        config["icon"] = icon

    if point.status and point.status != "ok":
        config["json_attributes_topic"] = f"{state_topic}/attributes"

    return {
        "component": "binary_sensor",
        "discovery_topic": f"homeassistant/binary_sensor/{object_id}/config",
        "config_payload": config,
        "state_topic": state_topic,
    }


def _map_sensor_numeric(
    point: NiagaraPoint,
    object_id: str,
    friendly: str,
    state_topic: str,
    availability_topic: str,
    device_info: dict,
) -> dict:
    config: dict[str, Any] = {
        "name": friendly,
        "unique_id": f"niagara_{object_id}",
        "state_topic": state_topic,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device_info,
    }

    if point.unit:
        ha_unit = UNIT_MAP.get(point.unit, point.unit)
        config["unit_of_measurement"] = ha_unit
        device_class = DEVICE_CLASS_MAP.get(point.unit)
        if device_class:
            config["device_class"] = device_class
            config["state_class"] = _infer_state_class(device_class, point.name, point.unit)

    if "device_class" not in config:
        inferred_dc, inferred_unit = _infer_device_class_sensor(point.name)
        if inferred_dc:
            config["device_class"] = inferred_dc
            config["state_class"] = _infer_state_class(inferred_dc, point.name, inferred_unit)
            if inferred_unit and "unit_of_measurement" not in config:
                config["unit_of_measurement"] = inferred_unit

    icon = _infer_icon(point.name)
    if icon and "device_class" not in config:
        config["icon"] = icon

    return {
        "component": "sensor",
        "discovery_topic": f"homeassistant/sensor/{object_id}/config",
        "config_payload": config,
        "state_topic": state_topic,
    }


def _map_sensor_enum(
    point: NiagaraPoint,
    object_id: str,
    friendly: str,
    state_topic: str,
    availability_topic: str,
    device_info: dict,
) -> dict:
    config: dict[str, Any] = {
        "name": friendly,
        "unique_id": f"niagara_{object_id}",
        "state_topic": state_topic,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device_info,
    }
    if point.enum_range:
        config["options"] = point.enum_range

    icon = _infer_icon(point.name)
    if icon:
        config["icon"] = icon

    return {
        "component": "sensor",
        "discovery_topic": f"homeassistant/sensor/{object_id}/config",
        "config_payload": config,
        "state_topic": state_topic,
    }


def _map_sensor_string(
    point: NiagaraPoint,
    object_id: str,
    friendly: str,
    state_topic: str,
    availability_topic: str,
    device_info: dict,
) -> dict:
    config: dict[str, Any] = {
        "name": friendly,
        "unique_id": f"niagara_{object_id}",
        "state_topic": state_topic,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": device_info,
    }

    icon = _infer_icon(point.name)
    if icon:
        config["icon"] = icon

    return {
        "component": "sensor",
        "discovery_topic": f"homeassistant/sensor/{object_id}/config",
        "config_payload": config,
        "state_topic": state_topic,
    }
