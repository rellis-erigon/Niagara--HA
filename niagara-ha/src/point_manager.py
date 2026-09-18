"""Manage point selection — discover, persist, and filter Niagara points."""

import fnmatch
import logging
from pathlib import Path
from typing import Optional

import yaml

from obix_client import NiagaraPoint

logger = logging.getLogger(__name__)

POINTS_DIR = Path("/config/niagara-ha")
POINTS_FILE = POINTS_DIR / "points.yaml"
RULES_FILE = POINTS_DIR / "auto_enable_rules.yaml"

SKIP_SEGMENTS = {"config", "Drivers", "points", "out", ""}

PROFILES = {
    "hvac_monitoring": {
        "name": "HVAC Monitoring",
        "description": "Zone temps, supply/return air, damper positions, fan status, heating/cooling valves",
        "patterns": [
            "*Temp*", "*Temperature*", "*Setpoint*", "*Setpt*",
            "*Supply*Air*", "*Return*Air*", "*Discharge*Air*",
            "*Damper*", "*Fan*Status*", "*Fan*Speed*",
            "*Heating*Valve*", "*Cooling*Valve*", "*Valve*Pos*",
            "*Occupancy*", "*Occupied*", "*Unoccupied*",
            "*AHU*", "*FCU*", "*VAV*",
        ],
    },
    "energy_metering": {
        "name": "Energy Metering",
        "description": "Power, energy consumption, voltage, current, demand",
        "patterns": [
            "*kW*", "*kWh*", "*Power*", "*Energy*",
            "*Voltage*", "*Current*", "*Demand*",
            "*Meter*", "*Consumption*",
        ],
    },
    "alarms_only": {
        "name": "Alarms & Faults",
        "description": "Alarm and fault status points only",
        "patterns": [
            "*Alarm*", "*Fault*", "*Trip*", "*Alert*",
            "*Emergency*", "*Smoke*", "*Fire*",
        ],
    },
    "lighting": {
        "name": "Lighting",
        "description": "Lighting status, levels, and schedules",
        "patterns": [
            "*Light*", "*Lighting*", "*Lux*",
            "*Dimmer*", "*Lamp*", "*Luminaire*",
        ],
    },
    "zone_comfort": {
        "name": "Zone Comfort",
        "description": "Temperature, humidity, CO2, and air quality for occupied zones",
        "patterns": [
            "*Temp*", "*Temperature*", "*Humidity*", "*RH*",
            "*CO2*", "*Air*Quality*", "*IAQ*",
            "*Setpoint*", "*Setpt*", "*Comfort*",
        ],
    },
}


def get_group(point: NiagaraPoint) -> str:
    parts = [p for p in point.path.strip("/").split("/") if p not in SKIP_SEGMENTS]
    if len(parts) >= 2:
        return parts[0]
    return "Ungrouped"


_last_good: dict[str, dict] = {}


def load_point_selections() -> dict[str, dict]:
    global _last_good
    if not POINTS_FILE.exists():
        return {}
    try:
        with open(POINTS_FILE) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "points" not in data:
            return _last_good
        result = {p["path"]: p for p in data["points"] if "path" in p}
        _last_good = result
        return result
    except Exception as e:
        logger.warning("Failed to read %s: %s", POINTS_FILE, e)
        if _last_good:
            logger.info("Using last successfully loaded point selections (%d points)", len(_last_good))
            return _last_good
        return {}


def load_auto_enable_rules() -> list[str]:
    if not RULES_FILE.exists():
        return []
    try:
        with open(RULES_FILE) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "patterns" in data:
            return [str(p) for p in data["patterns"] if p]
        return []
    except Exception as e:
        logger.warning("Failed to read %s: %s", RULES_FILE, e)
        return []


def save_auto_enable_rules(patterns: list[str]) -> None:
    POINTS_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "_comment": "Glob patterns matched against point name and path. Matching points are auto-enabled on discovery.",
        "patterns": patterns,
    }
    with open(RULES_FILE, "w") as f:
        yaml.safe_dump(output, f, default_flow_style=False, sort_keys=False, width=10000)
    logger.info("Saved %d auto-enable rules", len(patterns))


def matches_auto_enable(name: str, path: str, patterns: list[str]) -> bool:
    name_lower = name.lower()
    path_lower = path.lower()
    for pattern in patterns:
        pat = pattern.lower()
        if fnmatch.fnmatch(name_lower, pat) or fnmatch.fnmatch(path_lower, pat):
            return True
    return False


def save_point_selections(
    discovered: list[NiagaraPoint],
    existing: dict[str, dict],
    auto_enable_patterns: list[str] | None = None,
) -> dict[str, dict]:
    POINTS_DIR.mkdir(parents=True, exist_ok=True)

    if auto_enable_patterns is None:
        auto_enable_patterns = load_auto_enable_rules()

    auto_enabled_count = 0
    merged: dict[str, dict] = {}
    for pt in discovered:
        group = get_group(pt)
        if pt.path in existing:
            entry = existing[pt.path].copy()
            entry["name"] = pt.name
            entry["group"] = group
            entry["type"] = pt.point_type
        else:
            should_enable = bool(auto_enable_patterns) and matches_auto_enable(pt.name, pt.path, auto_enable_patterns)
            entry = {
                "path": pt.path,
                "name": pt.name,
                "group": group,
                "type": pt.point_type,
                "enabled": should_enable,
            }
            if should_enable:
                auto_enabled_count += 1
        merged[pt.path] = entry

    if auto_enabled_count > 0:
        logger.info("Auto-enabled %d newly discovered points matching rules", auto_enabled_count)

    groups: dict[str, list[dict]] = {}
    for entry in merged.values():
        g = entry.get("group", "Ungrouped")
        groups.setdefault(g, []).append(entry)

    ordered = []
    for g in sorted(groups):
        ordered.extend(sorted(groups[g], key=lambda e: e["name"]))

    output = {
        "_comment": (
            "Edit this file to enable/disable points. "
            "Set enabled: true to include a point in Home Assistant. "
            "New points discovered on restart are disabled by default."
        ),
        "points": ordered,
    }

    with open(POINTS_FILE, "w") as f:
        yaml.safe_dump(output, f, default_flow_style=False, sort_keys=False, allow_unicode=True, width=10000)

    enabled = sum(1 for e in merged.values() if e.get("enabled", False))
    logger.info(
        "Point selections: %d total, %d enabled, %d disabled (file: %s)",
        len(merged), enabled, len(merged) - enabled, POINTS_FILE,
    )
    return merged


def filter_enabled(
    points: list[NiagaraPoint], selections: dict[str, dict]
) -> list[NiagaraPoint]:
    if not selections:
        return []
    enabled = []
    for pt in points:
        entry = selections.get(pt.path)
        if entry and entry.get("enabled", False):
            enabled.append(pt)
    return enabled
