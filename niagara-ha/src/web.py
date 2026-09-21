"""Niagara-HA point management web UI (served via HA ingress)."""

import json
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from point_manager import (
    POINTS_DIR,
    POINTS_FILE,
    PROFILES,
    _parent_path,
    get_group_from_path,
    get_tree_children,
    load_auto_enable_rules,
    load_device_folders,
    load_point_selections,
    matches_auto_enable,
    parse_path_segments,
    save_auto_enable_rules,
    save_device_folders,
)

VALUES_FILE = POINTS_DIR / "values.json"

logger = logging.getLogger("niagara-ha.web")

import re

CATEGORY_RULES = [
    ("Temperature", re.compile(r"temp|tmp|zone.?t|supply.?air|return.?air|discharge|duct.?t|room.?t|outside.?air|oat|sat|rat|dat|chwt|hwt|clg.?t|htg.?t", re.I), {"°F", "°C"}),
    ("Fan", re.compile(r"fan|sf|rf|ef|supply.?fan|return.?fan|exhaust.?fan|vfd", re.I), set()),
    ("Pump", re.compile(r"pump|chw.?p|hw.?p|cw.?p|cdw.?p", re.I), set()),
    ("Valve", re.compile(r"valve|vlv|damper|dpr|dmpr", re.I), set()),
    ("Pressure", re.compile(r"press|psi|static", re.I), {"psi", "kPa", "Pa", "in. w.c."}),
    ("Power", re.compile(r"power|energy|kwh|kw|watt|elec|demand", re.I), {"kW", "W", "kWh", "Wh"}),
    ("Humidity", re.compile(r"humid|rh|dew.?point", re.I), {"%"}),
    ("Flow", re.compile(r"flow|cfm|gpm|velocity|air.?vol", re.I), {"cfm", "l/s", "gpm"}),
    ("Setpoint", re.compile(r"setpoint|set.?pt|sp|stpt|spt|limit", re.I), set()),
    ("Status", re.compile(r"status|state|alarm|fault|enable|disable|occup|mode|cmd|command|run|stop", re.I), set()),
    ("CO2", re.compile(r"co2|carbon", re.I), {"ppm"}),
]


def _categorize(point: dict) -> str:
    name = point.get("name", "")
    path = point.get("path", "")
    ptype = point.get("type", "")
    unit = point.get("unit", "")
    text = f"{name} {path}"
    for cat_name, pattern, units in CATEGORY_RULES:
        if pattern.search(text):
            return cat_name
        if units and unit in units:
            return cat_name
    if ptype == "boolean":
        return "Status"
    return "Other"

app = Flask(__name__, static_folder="/app/static")

PAGE_SIZE = 50

_sel_cache: dict = {"mtime": 0.0, "data": {}}
_values_cache: dict = {"mtime": 0.0, "data": {}}


def _load_selections() -> dict[str, dict]:
    try:
        mtime = POINTS_FILE.stat().st_mtime
    except OSError:
        return {}
    if mtime != _sel_cache["mtime"]:
        _sel_cache["data"] = load_point_selections()
        _sel_cache["mtime"] = mtime
    return _sel_cache["data"]


def _load_points() -> list[dict]:
    return list(_load_selections().values())


@app.route("/")
def index():
    return send_from_directory("/app/static", "index.html")


@app.route("/api/stats")
def stats():
    points = _load_points()
    groups: dict[str, dict] = {}
    categories: dict[str, int] = {}
    for p in points:
        g = p.get("group", "Ungrouped")
        if g not in groups:
            groups[g] = {"name": g, "total": 0, "enabled": 0}
        groups[g]["total"] += 1
        if p.get("enabled", False):
            groups[g]["enabled"] += 1

        cat = _categorize(p)
        categories[cat] = categories.get(cat, 0) + 1

    total = len(points)
    enabled = sum(1 for p in points if p.get("enabled", False))
    return jsonify({
        "total": total,
        "enabled": enabled,
        "disabled": total - enabled,
        "groups": sorted(groups.values(), key=lambda g: g["name"]),
        "categories": sorted(
            [{"name": k, "count": v} for k, v in categories.items()],
            key=lambda c: c["name"],
        ),
    })


@app.route("/api/tree")
def tree():
    points = _load_points()
    prefix = request.args.get("prefix", "")
    children = get_tree_children(points, prefix)
    return jsonify({"prefix": prefix, "children": children})


def _points_match_prefix(path: str, prefix_parts: list[str]) -> bool:
    segs = parse_path_segments(path)
    if len(segs) < len(prefix_parts):
        return False
    return segs[:len(prefix_parts)] == prefix_parts


def _load_values() -> dict[str, str]:
    try:
        mtime = VALUES_FILE.stat().st_mtime
    except OSError:
        return {}
    if mtime != _values_cache["mtime"]:
        try:
            with open(VALUES_FILE) as f:
                _values_cache["data"] = json.load(f)
        except (json.JSONDecodeError, OSError):
            _values_cache["data"] = {}
        _values_cache["mtime"] = mtime
    return _values_cache["data"]


@app.route("/api/values")
def values():
    vals = _load_values()
    paths = request.args.getlist("paths[]")
    if paths:
        vals = {p: vals[p] for p in paths if p in vals}
    return jsonify(vals)


@app.route("/api/points")
def list_points():
    points = _load_points()

    group = request.args.get("group", "")
    prefix = request.args.get("prefix", "")
    search = request.args.get("search", "").lower()
    status = request.args.get("status", "")
    category = request.args.get("category", "")
    page = int(request.args.get("page", "1"))

    if group:
        points = [p for p in points if p.get("group") == group]
    if prefix:
        prefix_parts = prefix.split("/")
        points = [p for p in points if _points_match_prefix(p.get("path", ""), prefix_parts)]
    if search:
        points = [p for p in points if search in p.get("name", "").lower() or search in p.get("path", "").lower()]
    if status == "enabled":
        points = [p for p in points if p.get("enabled", False)]
    elif status == "disabled":
        points = [p for p in points if not p.get("enabled", False)]

    for p in points:
        p["category"] = _categorize(p)

    if category:
        points = [p for p in points if p["category"] == category]

    points.sort(key=lambda p: (p.get("group", ""), p.get("name", "")))
    total = len(points)
    start = (page - 1) * PAGE_SIZE
    page_points = points[start:start + PAGE_SIZE]

    return jsonify({
        "points": page_points,
        "total": total,
        "page": page,
        "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
    })


@app.route("/api/points/toggle", methods=["POST"])
def toggle_point():
    data = request.get_json()
    path = data.get("path")
    enabled = data.get("enabled")
    if not path:
        return jsonify({"error": "path required"}), 400

    selections = load_point_selections()
    if path in selections:
        selections[path]["enabled"] = bool(enabled)
        _write_selections(selections)
        return jsonify({"ok": True, "path": path, "enabled": selections[path]["enabled"]})
    return jsonify({"error": "point not found"}), 404


@app.route("/api/points/rename", methods=["POST"])
def rename_point():
    data = request.get_json()
    path = data.get("path")
    custom_name = data.get("custom_name", "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400

    selections = load_point_selections()
    if path in selections:
        if custom_name:
            selections[path]["custom_name"] = custom_name
        else:
            selections[path].pop("custom_name", None)
        _write_selections(selections)
        return jsonify({"ok": True, "path": path, "custom_name": custom_name})
    return jsonify({"error": "point not found"}), 404


@app.route("/api/device-folders")
def get_device_folders():
    folders = load_device_folders()
    return jsonify({"folders": folders})


@app.route("/api/device-folders", methods=["POST"])
def set_device_folders():
    data = request.get_json()
    folders = data.get("folders", [])
    folders = [str(f).strip() for f in folders if str(f).strip()]
    save_device_folders(folders)
    return jsonify({"ok": True, "folders": folders})


@app.route("/api/device-folders/toggle", methods=["POST"])
def toggle_device_folder():
    data = request.get_json()
    folder = data.get("folder", "").strip()
    if not folder:
        return jsonify({"error": "folder required"}), 400
    folders = load_device_folders()
    if folder in folders:
        folders.remove(folder)
        action = "removed"
    else:
        folders.append(folder)
        action = "added"
    save_device_folders(folders)

    selections = load_point_selections()
    enabled_count = 0
    regrouped = 0
    for entry in selections.values():
        path = entry.get("path", "")
        point_parent = _parent_path(path)
        under_folder = point_parent == folder or point_parent.startswith(folder + "/")
        if under_folder:
            if action == "added" and not entry.get("enabled", False):
                entry["enabled"] = True
                enabled_count += 1
            new_group = get_group_from_path(path, device_folders=folders)
            if entry.get("group") != new_group:
                entry["group"] = new_group
                regrouped += 1
    if enabled_count > 0 or regrouped > 0:
        _write_selections(selections)

    return jsonify({
        "ok": True, "folder": folder, "action": action,
        "folders": folders, "enabled": enabled_count, "regrouped": regrouped,
    })


@app.route("/api/device-folders/apply", methods=["POST"])
def apply_device_folders():
    folders = load_device_folders()
    selections = load_point_selections()
    count = 0
    for entry in selections.values():
        path = entry.get("path", "")
        new_group = get_group_from_path(path, device_folders=folders)
        if entry.get("group") != new_group:
            entry["group"] = new_group
            count += 1
    _write_selections(selections)
    return jsonify({"ok": True, "updated": count, "folders": folders})


@app.route("/api/groups/toggle", methods=["POST"])
def toggle_group():
    data = request.get_json()
    group = data.get("group")
    enabled = data.get("enabled")
    if not group:
        return jsonify({"error": "group required"}), 400

    selections = load_point_selections()
    count = 0
    for entry in selections.values():
        if entry.get("group") == group:
            entry["enabled"] = bool(enabled)
            count += 1

    _write_selections(selections)
    return jsonify({"ok": True, "group": group, "enabled": bool(enabled), "count": count})


@app.route("/api/bulk", methods=["POST"])
def bulk_toggle():
    data = request.get_json()
    paths = data.get("paths", [])
    enabled = data.get("enabled", False)

    selections = load_point_selections()
    count = 0
    for path in paths:
        if path in selections:
            selections[path]["enabled"] = bool(enabled)
            count += 1

    _write_selections(selections)
    return jsonify({"ok": True, "count": count, "enabled": bool(enabled)})


@app.route("/api/all/toggle", methods=["POST"])
def toggle_all():
    data = request.get_json()
    enabled = data.get("enabled", False)

    selections = load_point_selections()
    for entry in selections.values():
        entry["enabled"] = bool(enabled)

    _write_selections(selections)
    return jsonify({"ok": True, "enabled": bool(enabled), "count": len(selections)})


@app.route("/api/filtered/toggle", methods=["POST"])
def toggle_filtered():
    data = request.get_json()
    search = data.get("search", "").lower()
    group = data.get("group", "")
    prefix = data.get("prefix", "")
    status_filter = data.get("status", "")
    enabled = data.get("enabled", False)

    prefix_parts = prefix.split("/") if prefix else []

    selections = load_point_selections()
    count = 0
    for entry in selections.values():
        if group and entry.get("group") != group:
            continue
        if prefix_parts and not _points_match_prefix(entry.get("path", ""), prefix_parts):
            continue
        if search:
            if search not in entry.get("name", "").lower() and search not in entry.get("path", "").lower():
                continue
        if status_filter == "enabled" and not entry.get("enabled", False):
            continue
        elif status_filter == "disabled" and entry.get("enabled", False):
            continue
        entry["enabled"] = bool(enabled)
        count += 1

    _write_selections(selections)
    return jsonify({"ok": True, "count": count, "enabled": bool(enabled)})


@app.route("/api/rules")
def get_rules():
    rules = load_auto_enable_rules()
    return jsonify({"patterns": rules})


@app.route("/api/rules", methods=["POST"])
def set_rules():
    data = request.get_json()
    patterns = data.get("patterns", [])
    patterns = [str(p).strip() for p in patterns if str(p).strip()]
    save_auto_enable_rules(patterns)
    return jsonify({"ok": True, "patterns": patterns})


@app.route("/api/rules/apply", methods=["POST"])
def apply_rules():
    data = request.get_json()
    patterns = data.get("patterns")
    if patterns is None:
        patterns = load_auto_enable_rules()
    else:
        patterns = [str(p).strip() for p in patterns if str(p).strip()]

    selections = load_point_selections()
    count = 0
    for entry in selections.values():
        if not entry.get("enabled", False):
            if matches_auto_enable(entry.get("name", ""), entry.get("path", ""), patterns):
                entry["enabled"] = True
                count += 1

    if count > 0:
        _write_selections(selections)
    return jsonify({"ok": True, "count": count})


@app.route("/api/profiles")
def list_profiles():
    result = []
    for key, profile in PROFILES.items():
        result.append({
            "id": key,
            "name": profile["name"],
            "description": profile["description"],
            "pattern_count": len(profile["patterns"]),
        })
    return jsonify({"profiles": result})


@app.route("/api/profiles/<profile_id>/preview")
def preview_profile(profile_id):
    profile = PROFILES.get(profile_id)
    if not profile:
        return jsonify({"error": "profile not found"}), 404

    selections = load_point_selections()
    would_enable = []
    for entry in selections.values():
        if not entry.get("enabled", False):
            if matches_auto_enable(entry.get("name", ""), entry.get("path", ""), profile["patterns"]):
                would_enable.append({
                    "name": entry.get("name", ""),
                    "path": entry.get("path", ""),
                    "group": entry.get("group", ""),
                })

    return jsonify({
        "profile": profile["name"],
        "would_enable": len(would_enable),
        "sample": would_enable[:20],
        "patterns": profile["patterns"],
    })


@app.route("/api/profiles/<profile_id>/apply", methods=["POST"])
def apply_profile(profile_id):
    profile = PROFILES.get(profile_id)
    if not profile:
        return jsonify({"error": "profile not found"}), 404

    data = request.get_json() or {}
    save_rules = data.get("save_as_rules", False)

    selections = load_point_selections()
    count = 0
    for entry in selections.values():
        if not entry.get("enabled", False):
            if matches_auto_enable(entry.get("name", ""), entry.get("path", ""), profile["patterns"]):
                entry["enabled"] = True
                count += 1

    if count > 0:
        _write_selections(selections)

    if save_rules:
        existing_rules = load_auto_enable_rules()
        merged = list(dict.fromkeys(existing_rules + profile["patterns"]))
        save_auto_enable_rules(merged)

    return jsonify({"ok": True, "count": count, "profile": profile["name"], "rules_saved": save_rules})


@app.route("/api/integration/points")
def integration_points():
    """Return enabled points with current values for the HA integration.

    The HACS integration polls this endpoint to create native HA entities.
    Returns only enabled points with their type, unit, group, and live value.
    """
    selections = _load_selections()
    values = _load_values()
    device_folders = load_device_folders()

    points = []
    for entry in selections.values():
        if not entry.get("enabled", False):
            continue
        path = entry.get("path", "")
        points.append({
            "path": path,
            "name": entry.get("name", ""),
            "type": entry.get("type", "unknown"),
            "unit": entry.get("unit", ""),
            "group": entry.get("group", "Ungrouped"),
            "value": values.get(path),
            "writable": entry.get("writable", False),
            "enum_range": entry.get("enum_range", []),
        })

    return jsonify({
        "points": points,
        "device_folders": device_folders,
        "total": len(selections),
        "enabled": len(points),
    })


@app.route("/api/integration/values")
def integration_values():
    """Return current values for all enabled points.

    Lightweight endpoint for polling — just path:value pairs.
    Returns null for points that haven't been polled yet so the
    integration knows they exist.
    """
    selections = _load_selections()
    values = _load_values()

    result = {}
    for entry in selections.values():
        if entry.get("enabled", False):
            path = entry.get("path", "")
            result[path] = values.get(path)

    return jsonify(result)


def _write_selections(selections: dict[str, dict]) -> None:
    import yaml
    from point_manager import _SafeDumper

    POINTS_DIR.mkdir(parents=True, exist_ok=True)

    groups: dict[str, list[dict]] = {}
    for entry in selections.values():
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
        yaml.dump(output, f, Dumper=_SafeDumper, default_flow_style=False, sort_keys=False, allow_unicode=True, width=10000)
    _sel_cache["mtime"] = 0.0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    port = int(os.environ.get("INGRESS_PORT", "8099"))
    logger.info("Starting web UI on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
