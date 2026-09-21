"""Manage point selection — discover, persist, and filter Niagara points."""

import fnmatch
import logging
from pathlib import Path
from typing import Optional

import yaml

from obix_client import NiagaraPoint

logger = logging.getLogger(__name__)


class _SafeDumper(yaml.SafeDumper):
    pass


_SafeDumper.add_representer(
    str,
    lambda dumper, data: dumper.represent_scalar("tag:yaml.org,2002:str", data, style="'" if any(c in data for c in ":{}[]|>&*!#%@`") else None),
)

POINTS_DIR = Path("/config/niagara-ha")
POINTS_FILE = POINTS_DIR / "points.yaml"
RULES_FILE = POINTS_DIR / "auto_enable_rules.yaml"
DEVICE_FOLDERS_FILE = POINTS_DIR / "device_folders.yaml"

SKIP_SEGMENTS = {"config", "Drivers", "NiagaraNetwork", "ObixNetwork", "points", "out", "exports", "obix", ""}
PATH_BOILERPLATE = SKIP_SEGMENTS

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


def _parent_path(path: str) -> str:
    parts = parse_path_segments(path)
    if len(parts) < 2:
        return ""
    return "/".join(parts[:-1])


def _match_device_folder(point_parent: str, device_folders: list[str]) -> str:
    best = ""
    for folder in device_folders:
        if point_parent == folder or point_parent.startswith(folder + "/"):
            if len(folder) > len(best):
                best = folder
    return best


def get_group(point: NiagaraPoint, device_depth: int = 0, device_folders: list[str] | None = None) -> str:
    parts = parse_path_segments(point.path)
    if len(parts) < 2:
        return "Ungrouped"
    point_parent = "/".join(parts[:-1])
    if device_folders:
        matched = _match_device_folder(point_parent, device_folders)
        if matched:
            return matched
    if device_depth > 0:
        return "/".join(parts[:device_depth])
    return point_parent


def get_group_from_path(path: str, device_depth: int = 0, device_folders: list[str] | None = None) -> str:
    parts = parse_path_segments(path)
    if len(parts) < 2:
        return "Ungrouped"
    point_parent = "/".join(parts[:-1])
    if device_folders:
        matched = _match_device_folder(point_parent, device_folders)
        if matched:
            return matched
    if device_depth > 0:
        return "/".join(parts[:device_depth])
    return point_parent


def load_device_folders() -> list[str]:
    if not DEVICE_FOLDERS_FILE.exists():
        return []
    try:
        with open(DEVICE_FOLDERS_FILE) as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and "folders" in data:
            return [str(f) for f in data["folders"] if f]
        return []
    except Exception as e:
        logger.warning("Failed to read %s: %s", DEVICE_FOLDERS_FILE, e)
        return []


def save_device_folders(folders: list[str]) -> None:
    POINTS_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "_comment": "Folders marked as HA devices. All points under a folder are grouped into one device.",
        "folders": sorted(set(folders)),
    }
    with open(DEVICE_FOLDERS_FILE, "w") as f:
        yaml.safe_dump(output, f, default_flow_style=False, sort_keys=False, width=10000)
    logger.info("Saved %d device folders", len(folders))


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


def _yaml_scalar(value) -> str:
    """Quote a value only when the fast reader would misread it bare."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = "" if value is None else str(value)
    if text == "" or text[0] in "'\"@`&*!|>%#-?{[" or text.strip() != text:
        return "'" + text.replace("'", "''") + "'"
    if ": " in text or text.endswith(":") or " #" in text:
        return "'" + text.replace("'", "''") + "'"
    return text


_ENTRY_KEY_ORDER = (
    "path", "name", "custom_name", "group", "type", "unit",
    "precision", "enabled", "writable",
)


def fast_dump_points(entries: list[dict]) -> str:
    """Serialise points.yaml by hand.

    yaml.dump on 20,000 entries takes seconds, and this file is rewritten on
    every enable/disable — which made toggling a device feel broken. The
    format is ours and the reader is _fast_load_points_yaml, so building the
    text directly is both safe and roughly two orders of magnitude faster.
    """
    out = [
        "_comment: Edit this file to enable/disable points. "
        "Set enabled: true to include a point in Home Assistant. "
        "New points discovered on restart are disabled by default.\n",
        "points:\n",
    ]
    append = out.append
    for entry in entries:
        first = True
        for key in _ENTRY_KEY_ORDER:
            if key not in entry:
                continue
            prefix = "- " if first else "  "
            append(f"{prefix}{key}: {_yaml_scalar(entry[key])}\n")
            first = False
        for key, value in entry.items():
            if key in _ENTRY_KEY_ORDER or key == "enum_range":
                continue
            prefix = "- " if first else "  "
            append(f"{prefix}{key}: {_yaml_scalar(value)}\n")
            first = False
    return "".join(out)


def write_point_selections(selections: dict[str, dict]) -> None:
    """Write points.yaml atomically, grouped and sorted as before."""
    groups: dict[str, list[dict]] = {}
    for entry in selections.values():
        groups.setdefault(entry.get("group", "Ungrouped"), []).append(entry)

    ordered: list[dict] = []
    for group in sorted(groups):
        ordered.extend(sorted(groups[group], key=lambda e: e.get("name", "")))

    POINTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = POINTS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as handle:
        handle.write(fast_dump_points(ordered))
    # Rename is atomic, so a crash mid-write cannot truncate a 5 MB file of
    # the user's point selections.
    tmp.replace(POINTS_FILE)


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
    device_depth: int = 0,
    device_folders: list[str] | None = None,
) -> dict[str, dict]:
    POINTS_DIR.mkdir(parents=True, exist_ok=True)

    if auto_enable_patterns is None:
        auto_enable_patterns = load_auto_enable_rules()

    auto_enabled_count = 0
    merged: dict[str, dict] = {}
    for pt in discovered:
        group = get_group(pt, device_depth, device_folders)
        if pt.path in existing:
            entry = existing[pt.path].copy()
            entry["name"] = pt.name
            entry["group"] = group
            entry["type"] = pt.point_type
            entry["unit"] = pt.unit or ""
            entry["writable"] = pt.writable
            if pt.precision is not None:
                entry["precision"] = pt.precision
            if pt.enum_range:
                entry["enum_range"] = pt.enum_range
        else:
            should_enable = bool(auto_enable_patterns) and matches_auto_enable(pt.name, pt.path, auto_enable_patterns)
            entry = {
                "path": pt.path,
                "name": pt.name,
                "group": group,
                "type": pt.point_type,
                "unit": pt.unit or "",
                "writable": pt.writable,
                **({"precision": pt.precision} if pt.precision is not None else {}),
                "enabled": should_enable,
            }
            if pt.enum_range:
                entry["enum_range"] = pt.enum_range
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

    POINTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = POINTS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(fast_dump_points(ordered))
    tmp.replace(POINTS_FILE)

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
