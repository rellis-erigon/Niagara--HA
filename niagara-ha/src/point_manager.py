"""Manage point selection — discover, persist, and filter Niagara points."""

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

SKIP_SEGMENTS = {"config", "Drivers", "points", "out", ""}


def get_group(point: NiagaraPoint) -> str:
    parts = [p for p in point.path.strip("/").split("/") if p not in SKIP_SEGMENTS]
    if len(parts) >= 2:
        return parts[0]
    return "Ungrouped"


def load_point_selections() -> dict[str, dict]:
    if not POINTS_FILE.exists():
        return {}
    try:
        with open(POINTS_FILE) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or "points" not in data:
            return {}
        return {p["path"]: p for p in data["points"] if "path" in p}
    except Exception as e:
        logger.warning("Failed to read %s: %s", POINTS_FILE, e)
        return {}


def save_point_selections(
    discovered: list[NiagaraPoint], existing: dict[str, dict]
) -> dict[str, dict]:
    POINTS_DIR.mkdir(parents=True, exist_ok=True)

    merged: dict[str, dict] = {}
    for pt in discovered:
        group = get_group(pt)
        if pt.path in existing:
            entry = existing[pt.path].copy()
            entry["name"] = pt.name
            entry["group"] = group
            entry["type"] = pt.point_type
        else:
            entry = {
                "path": pt.path,
                "name": pt.name,
                "group": group,
                "type": pt.point_type,
                "enabled": False,
            }
        merged[pt.path] = entry

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
        yaml.dump(output, f, Dumper=_SafeDumper, default_flow_style=False, sort_keys=False, allow_unicode=True)

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
