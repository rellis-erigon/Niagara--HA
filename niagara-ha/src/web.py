"""Niagara-HA point management web UI (served via HA ingress)."""

import json
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from point_manager import POINTS_DIR, POINTS_FILE, load_point_selections

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

_cache: dict = {"mtime": 0.0, "data": []}
_values_cache: dict = {"mtime": 0.0, "data": {}}


def _load_points() -> list[dict]:
    try:
        mtime = POINTS_FILE.stat().st_mtime
    except OSError:
        return []
    if mtime != _cache["mtime"]:
        sel = load_point_selections()
        _cache["data"] = list(sel.values())
        _cache["mtime"] = mtime
    return _cache["data"]


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
    search = request.args.get("search", "").lower()
    status = request.args.get("status", "")
    category = request.args.get("category", "")
    page = int(request.args.get("page", "1"))

    if group:
        points = [p for p in points if p.get("group") == group]
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
        yaml.dump(output, f, Dumper=_SafeDumper, default_flow_style=False, sort_keys=False, allow_unicode=True)
    _cache["mtime"] = 0.0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    port = int(os.environ.get("INGRESS_PORT", "8099"))
    logger.info("Starting web UI on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
