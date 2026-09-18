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

SKIP_SEGMENTS = {"config", "Drivers", "points", "out", "exports", ""}
PATH_BOILERPLATE = {"config", "Drivers", "NiagaraNetwork", "ObixNetwork", "points", "out", "exports", ""}

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


def parse_path_segments(path: str) -> list[str]:
    raw = path.strip("/").split("/")
    return [s for s in raw if s and s not in PATH_BOILERPLATE]


def build_point_tree(points: list[dict]) -> dict:
    tree: dict = {}
    for p in points:
        segs = parse_path_segments(p.get("path", ""))
        if len(segs) < 2:
            continue
        node = tree
        for seg in segs[:-1]:
            if seg not in node:
                node[seg] = {"_total": 0, "_enabled": 0}
            node[seg]["_total"] += 1
            if p.get("enabled", False):
                node[seg]["_enabled"] += 1
            node = node[seg]

    def to_list(node: dict, prefix: list[str]) -> list[dict]:
        result = []
        for key in sorted(node):
            if key.startswith("_"):
                continue
            child = node[key]
            full = prefix + [key]
            entry = {
                "name": key,
                "path": "/".join(full),
                "total": child.get("_total", 0),
                "enabled": child.get("_enabled", 0),
                "has_children": any(not k.startswith("_") for k in child),
            }
            result.append(entry)
        return result

    return {"_tree": tree, "_to_list": to_list}


def get_tree_children(points: list[dict], prefix: str = "") -> list[dict]:
    tree_data = build_point_tree(points)
    tree = tree_data["_tree"]
    to_list = tree_data["_to_list"]

    if not prefix:
        return to_list(tree, [])

    parts = prefix.split("/")
    node = tree
    for part in parts:
        if part not in node:
            return []
        node = node[part]
    return to_list(node, parts)


_last_good: dict[str, dict] = {}


def _fast_load_points_yaml(filepath: Path) -> dict[str, dict]:
    """Parse points.yaml without full YAML parsing.

    The file is written by us in a predictable format: a flat list of
    dicts under "points:" with keys path, name, group, type, enabled.
    Reading it line-by-line avoids the massive memory overhead of
    yaml.safe_load on 100K+ line files.
    """
    result: dict[str, dict] = {}
    current: dict | None = None
    in_points = False

    with open(filepath) as f:
        for line in f:
            stripped = line.strip()
            if not in_points:
                if stripped == "points:":
                    in_points = True
                continue

            if stripped.startswith("- "):
                if current and "path" in current:
                    result[current["path"]] = current
                rest = stripped[2:]
                current = {}
                if rest:
                    _parse_kv(rest, current)
            elif stripped and current is not None:
                _parse_kv(stripped, current)
            elif not stripped:
                continue
            elif not stripped.startswith(" ") and not stripped.startswith("-"):
                break

    if current and "path" in current:
        result[current["path"]] = current
    return result


def _parse_kv(text: str, target: dict) -> None:
    colon = text.find(":")
    if colon < 1:
        return
    key = text[:colon].strip()
    val = text[colon + 1:].strip()
    if val.startswith("'") and val.endswith("'"):
        val = val[1:-1]
    elif val.startswith('"') and val.endswith('"'):
        val = val[1:-1]
    if key == "enabled":
        target[key] = val.lower() == "true"
    else:
        target[key] = val


def load_point_selections() -> dict[str, dict]:
    global _last_good
    if not POINTS_FILE.exists():
        return {}
    try:
        result = _fast_load_points_yaml(POINTS_FILE)
        if not result:
            return _last_good
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
