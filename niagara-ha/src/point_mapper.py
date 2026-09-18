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
}


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _stable_id(path: str) -> str:
    return hashlib.md5(path.encode()).hexdigest()[:12]


def _friendly_name(point: NiagaraPoint) -> str:
    parts = point.path.strip("/").split("/")
    relevant = [p for p in parts if p not in ("config", "Drivers", "NiagaraNetwork", "ObixNetwork", "points", "out", "exports")]
    if not relevant:
        return point.name
    return " / ".join(relevant[-3:])


def _get_group(point: NiagaraPoint) -> str:
    skip = {"config", "Drivers", "NiagaraNetwork", "ObixNetwork", "points", "out", "exports", ""}
    parts = [p for p in point.path.strip("/").split("/") if p not in skip]
    if len(parts) >= 2:
        return parts[0]
    return "Ungrouped"


def map_point(
    point: NiagaraPoint, topic_prefix: str, device_name: str = "Niagara BMS",
    group: str = "",
) -> Optional[dict[str, Any]]:
    """Convert a NiagaraPoint into an MQTT discovery payload dict.

    Returns None for point types we don't map yet.
    """
    uid = _stable_id(point.path)
    object_id = _slugify(point.name) + "_" + uid
    friendly = _friendly_name(point)

    base_topic = f"{topic_prefix}/{object_id}"
    state_topic = f"{base_topic}/state"
    availability_topic = f"{topic_prefix}/bridge/availability"

    group_name = group or _get_group(point)
    device_id = f"niagara_{_stable_id(topic_prefix + '/' + group_name)}"
    display_name = f"{device_name} — {group_name}"

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
    payload: dict[str, Any] = {
        "component": "binary_sensor",
        "discovery_topic": f"homeassistant/binary_sensor/{object_id}/config",
        "config_payload": {
            "name": friendly,
            "unique_id": f"niagara_{object_id}",
            "state_topic": state_topic,
            "payload_on": "true",
            "payload_off": "false",
            "availability_topic": availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info,
        },
        "state_topic": state_topic,
    }
    if point.status and point.status != "ok":
        payload["config_payload"]["json_attributes_topic"] = f"{state_topic}/attributes"
    return payload


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
            config["state_class"] = "measurement"

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
    return {
        "component": "sensor",
        "discovery_topic": f"homeassistant/sensor/{object_id}/config",
        "config_payload": {
            "name": friendly,
            "unique_id": f"niagara_{object_id}",
            "state_topic": state_topic,
            "availability_topic": availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device_info,
        },
        "state_topic": state_topic,
    }
