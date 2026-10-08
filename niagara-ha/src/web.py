"""Niagara-HA point management web UI (served via HA ingress)."""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

import areas as area_rules
import histories
import history_store
from device_templates import (
    STATE_DRAFT,
    STATE_PUBLISHED,
    TemplateError,
    export_templates,
    import_templates,
    render_card,
    bind_template,
    decode_niagara_name,
    delete_user_template,
    load_device_types,
    load_templates,
    save_device_types,
    save_user_template,
    score_candidate,
    suggest_template,
)
from faceplates import (
    VALID_DISPLAYS, apply_display, apply_faceplate, card_types_in,
    faceplates_for_card, full_entities_card, roles_in,
)
from validation import (
    BLOCKED,
    load_observations,
    validate_device,
)
import diagnostics
import rescan
from point_manager import (
    POINTS_DIR,
    POINTS_FILE,
    PROFILES,
    _parent_path,
    get_group_from_path,
    point_paths_as_folders,
    get_tree_children,
    load_auto_enable_rules,
    load_device_folders,
    load_point_selections,
    matches_auto_enable,
    parse_path_segments,
    save_auto_enable_rules,
    save_device_folders,
    write_point_selections,
)

VALUES_FILE = POINTS_DIR / "values.json"
ALARMS_FILE = POINTS_DIR / "alarms.json"
ALARM_PROBE_FILE = POINTS_DIR / "alarms_probe.json"
HISTORY_CATALOGUE_FILE = POINTS_DIR / "histories_catalogue.json"
HISTORY_PROBE_FILE = POINTS_DIR / "histories_probe.json"

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

# Offered in the unit picker. Not a restriction — a station may report
# something none of these cover, so any text is accepted.
KNOWN_UNITS = [
    "°C", "°F", "K", "%", "ppm",
    "kW", "W", "MW", "kWh", "Wh", "MWh", "kVA", "VA", "kvar",
    "V", "kV", "mV", "A", "mA", "Hz",
    "Pa", "kPa", "psi", "bar", "mbar", "inH2O",
    "L", "m³", "gal", "ft³", "CCF",
    "L/s", "L/h", "m³/h", "ft³/h", "ft³/min", "gal/min",
    "rpm", "min", "h", "s", "lx", "dB",
]

_sel_cache: dict = {"mtime": 0.0, "data": {}}
_values_cache: dict = {"mtime": 0.0, "data": {}, "seq": 0}

# How long a value request may be held open waiting for a change. Kept
# under the integration's own request timeout, and under the 60 seconds
# that proxies in front of ingress tend to cut a connection at.
MAX_LONG_POLL_SECONDS = 45.0
LONG_POLL_TICK_SECONDS = 0.25


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


def _read_values_file() -> tuple[dict[str, dict], int]:
    """The value cache and its sequence, re-read only when the file changes.

    Tolerates both the old flat {path: value} shape and the envelope that
    carries the sequence, so a downgrade — or a read landing between two
    versions — cannot take the UI down or show an empty station.
    """
    try:
        mtime = VALUES_FILE.stat().st_mtime
    except OSError:
        return {}, 0
    if mtime != _values_cache["mtime"]:
        try:
            with open(VALUES_FILE) as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        seq = 0
        if isinstance(raw.get("values"), dict):
            seq = int(raw.get("seq") or 0)
            raw = raw["values"]
        _values_cache["data"] = {
            path: (entry if isinstance(entry, dict) and "value" in entry
                   else {"value": entry, "status": "unknown", "ts": 0.0})
            for path, entry in raw.items()
        }
        _values_cache["seq"] = seq
        _values_cache["mtime"] = mtime
    return _values_cache["data"], _values_cache["seq"]


def _load_values() -> dict[str, dict]:
    """The value cache as {path: {"value", "status", "ts"}}."""
    return _read_values_file()[0]


def _current_seq() -> int:
    return _read_values_file()[1]


def _plain_value(entry: dict | None):
    return entry.get("value") if entry else None


@app.route("/api/values")
def values():
    """Flat path:value map — the sidebar UI's contract, unchanged."""
    vals = _load_values()
    paths = request.args.getlist("paths[]")
    if paths:
        return jsonify({p: _plain_value(vals.get(p)) for p in paths if p in vals})
    return jsonify({p: _plain_value(e) for p, e in vals.items()})


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


@app.route("/api/units")
def list_units():
    """Units the panel offers when overriding a point's unit."""
    return jsonify({"units": KNOWN_UNITS})


@app.route("/api/points/unit", methods=["POST"])
def set_point_unit():
    """Override a point's unit of measurement.

    Niagara does not always report one — on this station only some of a set
    of identical meters tag kWh — and it is sometimes plainly wrong. The
    discovered unit is kept, so clearing the override restores it.
    """
    data = request.get_json() or {}
    path = data.get("path")
    unit = (data.get("unit") or "").strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    if len(unit) > 24:
        return jsonify({"error": "unit is too long"}), 400

    selections = load_point_selections()
    entry = selections.get(path)
    if entry is None:
        return jsonify({"error": "point not found"}), 404

    if unit:
        entry["custom_unit"] = unit
    else:
        entry.pop("custom_unit", None)
    _write_selections(selections)

    return jsonify({
        "ok": True, "path": path,
        "custom_unit": unit, "discovered_unit": entry.get("unit", ""),
    })


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
    point_paths = point_paths_as_folders(selections)
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
            new_group = get_group_from_path(
                path, device_folders=folders, point_paths=point_paths)
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
    point_paths = point_paths_as_folders(selections)
    count = 0
    for entry in selections.values():
        path = entry.get("path", "")
        new_group = get_group_from_path(
            path, device_folders=folders, point_paths=point_paths)
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
    strict = bool(_addon_options().get("strict_publishing", False))

    # A published device tells us what each of its points means, so the
    # integration can name an entity "Fan" instead of "IndoorFanStatus" and
    # take its device class from the template rather than guessing.
    templates = load_templates()
    slot_meta: dict[str, dict] = {}
    published_paths: set[str] = set()
    typed_paths: set[str] = set()
    for group, assigned in load_device_types().items():
        template = templates.get(assigned.get("template"))
        if template is None:
            continue
        bindings = assigned.get("bindings", {})
        is_published = assigned.get("state") == STATE_PUBLISHED
        for slot in template.slots:
            path = bindings.get(slot.key)
            if not path:
                continue
            typed_paths.add(path)
            if not is_published:
                continue
            published_paths.add(path)
            slot_meta[path] = {
                "slot": slot.key,
                "slot_name": slot.name,
                "device_class": slot.device_class,
                "state_class": slot.state_class,
                "slot_units": slot.units,
                "device_group": group,
                # Which template typed this device. The integration needs it
                # to decide whether a group of points is a climate entity, a
                # fan, or a plain bundle of sensors — the slot keys alone do
                # not say, since "run_status" appears on half the templates.
                "device_type": assigned.get("template"),
            }

    # Resolved once per request, not per point: the rules are walked in
    # order and 2,800 points share a few hundred folders.
    rules = area_rules.load()
    resolved_areas: dict[str, str | None] = {}
    if rules:
        for entry in selections.values():
            group = entry.get("group", "")
            if group and group not in resolved_areas:
                resolved_areas[group] = area_rules.resolve(group, rules)

    now = time.time()
    points = []
    for entry in selections.values():
        if not entry.get("enabled", False):
            continue
        path = entry.get("path", "")
        # With the gate on, a point only reaches HA through a published
        # device; without it, enabled points behave as they always have.
        if strict and path not in published_paths:
            continue
        cached = values.get(path)
        points.append({
            **(slot_meta.get(path) or {}),
            "path": path,
            "name": entry.get("name", ""),
            "type": entry.get("type", "unknown"),
            "unit": entry.get("custom_unit") or entry.get("unit", ""),
            "unit_overridden": bool(entry.get("custom_unit")),
            "discovered_unit": entry.get("unit", ""),
            "group": entry.get("group", "Ungrouped"),
            # The area this device's folder maps to, or None when no rule
            # claims it — in which case the integration keeps its own
            # depth-based guess, which is what every station had before
            # rules existed.
            "area": resolved_areas.get(entry.get("group", "")),
            "value": _plain_value(cached),
            "status": cached.get("status", "unknown") if cached else "unknown",
            "age": round(now - cached["ts"], 1) if cached and cached.get("ts") else None,
            "writable": entry.get("writable", False),
            "precision": entry.get("precision"),
            "enum_range": entry.get("enum_range", []),
        })

    return jsonify({
        "points": points,
        "device_folders": device_folders,
        "total": len(selections),
        "enabled": len(points),
        "strict_publishing": strict,
        "published_points": len(published_paths),
    })


@app.route("/api/integration/values")
def integration_values():
    """Current values for the enabled points.

    Three modes, chosen by the query string:

    - bare: every enabled point, as a flat {path: entry} map. This is the
      shape released integrations expect, so it stays exactly as it was.
    - `?since=N`: only what has changed since sequence N, wrapped in an
      envelope carrying the current sequence. On this station that turns a
      476 KB reply into a few hundred bytes, because a dozen points move in
      a given cycle and 1,800 do not.
    - `?wait=S`: hold the request open until something changes, up to S
      seconds. Latency was previously the sum of two independent polls —
      the add-on's and Home Assistant's — so a reading could be a minute
      old before anyone saw it. Holding the request collapses that to the
      add-on's own interval, using fewer requests rather than more.
    """
    selections = _load_selections()
    since = request.args.get("since")
    wait = request.args.get("wait")

    if wait is not None:
        try:
            budget = max(0.0, min(float(wait), MAX_LONG_POLL_SECONDS))
        except ValueError:
            return jsonify({"error": "wait must be a number of seconds"}), 400
        _await_change(int(since or 0), budget)

    values, seq = _read_values_file()
    now = time.time()

    def shape(cached: dict | None) -> dict | None:
        if cached is None:
            return None
        return {
            "value": cached.get("value"),
            "status": cached.get("status", "unknown"),
            "age": round(now - cached["ts"], 1) if cached.get("ts") else None,
        }

    enabled = [
        entry.get("path", "") for entry in selections.values()
        if entry.get("enabled", False)
    ]

    if since is None:
        return jsonify({path: shape(values.get(path)) for path in enabled})

    try:
        watermark = int(since)
    except ValueError:
        return jsonify({"error": "since must be a sequence number"}), 400

    # A watermark ahead of ours means the add-on restarted and its sequence
    # went backwards. Sending a delta then would leave the caller holding
    # readings from before the restart forever, so it gets everything.
    if watermark > seq:
        watermark = 0

    changed = {
        path: shape(values.get(path))
        for path in enabled
        if watermark == 0 or (values.get(path) or {}).get("seq", 0) > watermark
    }
    return jsonify({
        "seq": seq,
        "values": changed,
        "full": watermark == 0,
        "total": len(enabled),
    })


def _await_change(since: int, budget: float) -> None:
    """Block until the value cache moves past `since`, or the budget runs out.

    Polls the file's own sequence rather than watching for an event: the
    poll loop is a separate process, so there is nothing in here to wait
    on, and a file stat every quarter second is cheaper than the full
    re-fetch it replaces.
    """
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if _current_seq() > since:
            return
        time.sleep(LONG_POLL_TICK_SECONDS)


@app.route("/api/templates")
def list_templates():
    """The device types a folder can be assigned."""
    templates = load_templates()
    return jsonify({
        "templates": [t.to_dict() for t in sorted(
            templates.values(), key=lambda t: t.name,
        )],
    })


def _points_by_group() -> dict[str, list[dict]]:
    """All points per device folder, enabled or not.

    Binding deliberately considers disabled points too. A folder selected as
    a device with nothing enabled yet is the normal starting state — it must
    still be listed and typable, and assigning a type is what tells us which
    of its points are worth enabling.
    """
    groups: dict[str, list[dict]] = {}
    for entry in _load_selections().values():
        groups.setdefault(entry.get("group", "Ungrouped"), []).append(entry)
    return groups


def _device_groups() -> dict[str, list[dict]]:
    """Folders that should appear as devices.

    A folder the user marked as a device in the tree, or any folder that
    already has points enabled. Listing only the latter hid every selected
    folder whose points had not been enabled yet.
    """
    groups = _points_by_group()
    selected = set(load_device_folders())
    return {
        group: points for group, points in groups.items()
        if group in selected or any(p.get("enabled") for p in points)
    }


@app.route("/api/devices")
def list_devices():
    """Every device folder, with its assigned type and how full it is."""
    templates = load_templates()
    assigned = load_device_types()
    groups = _device_groups()
    values = _load_values()
    observations = load_observations()
    now = time.time()

    devices = []
    for group, points in sorted(groups.items()):
        entry = assigned.get(group)
        template = templates.get(entry["template"]) if entry else None
        enabled_paths = {p.get("path") for p in points if p.get("enabled")}
        record = {
            "group": group,
            "name": decode_niagara_name(group.rstrip("/").split("/")[-1]),
            "point_count": len(points),
            "enabled_count": len(enabled_paths),
            "template": entry["template"] if entry else None,
            "template_name": template.name if template else None,
            "state": entry["state"] if entry else None,
            "bound": 0,
            "bound_disabled": 0,
            "required_total": 0,
            "required_bound": 0,
        }
        if template:
            bindings = entry.get("bindings", {})
            bound_paths = [v for v in bindings.values() if v]
            record["bound"] = len(bound_paths)
            # Bound but not enabled means the slot will produce no entity.
            record["bound_disabled"] = sum(
                1 for p in bound_paths if p not in enabled_paths
            )
            required = template.required_slots
            record["required_total"] = len(required)
            record["required_bound"] = sum(
                1 for s in required if bindings.get(s.key)
            )
            report = validate_device(
                template, bindings, {p.get("path"): p for p in points},
                values, observations, now,
            )
            record["severity"] = report["severity"]
            record["blocked"] = report["blocked"]
            record["warnings"] = report["warnings"]
            record["publishable"] = report["publishable"]
        else:
            suggested, _ = suggest_template(templates, points, group, values)
            record["suggested"] = suggested
            record["suggested_name"] = (
                templates[suggested].name if suggested else None
            )
        devices.append(record)

    return jsonify({"devices": devices, "total": len(devices)})


@app.route("/api/devices/detail")
def device_detail():
    """Slot-by-slot view of one device: what is bound and what could be."""
    group = request.args.get("group", "")
    if not group:
        return jsonify({"error": "group required"}), 400

    templates = load_templates()
    points = _points_by_group().get(group, [])
    if not points:
        return jsonify({"error": "no points in this group"}), 404

    assigned = load_device_types().get(group)
    template_id = request.args.get("template") or (
        assigned["template"] if assigned else None
    )
    if not template_id:
        template_id, _ = suggest_template(templates, points, group, _load_values())

    template = templates.get(template_id) if template_id else None
    if template is None:
        return jsonify({
            "group": group, "template": None, "slots": [],
            "points": [_point_summary(p) for p in points],
        })

    # Stored bindings win; anything unset is proposed by auto-binding.
    proposed = bind_template(template, points, _load_values())
    stored = assigned.get("bindings", {}) if assigned else {}
    by_path = {p.get("path"): p for p in points}

    slots = []
    for slot in template.slots:
        path = stored.get(slot.key) or proposed.get(slot.key)
        point = by_path.get(path)
        slots.append({
            "key": slot.key,
            "name": slot.name,
            "required": slot.required,
            "device_class": slot.device_class,
            "state_class": slot.state_class,
            "units": slot.units,
            "bound_path": path,
            "bound_name": decode_niagara_name(point.get("name", "")) if point else None,
            "bound_unit": point.get("unit") if point else None,
            "from_user": bool(stored.get(slot.key)),
            "bound_enabled": bool(point.get("enabled")) if point else False,
            # Every point in the device is offered. Pattern matching decides
            # what is suggested, never what is permitted — a slot may only be
            # fillable by a point nobody would name predictably, such as the
            # "UI 4" on an under-bench fridge.
            "suggested": [
                _point_summary(p) for p in points
                if score_candidate(slot, p) is not None
            ],
        })

    effective = {
        s["key"]: s["bound_path"] for s in slots if s["bound_path"]
    }
    report = validate_device(
        template, effective, by_path, _load_values(), load_observations(),
    )
    issues_by_slot = {s["key"]: s for s in report["slots"]}
    for slot in slots:
        detail = issues_by_slot.get(slot["key"], {})
        slot["severity"] = detail.get("severity", "ok")
        slot["issues"] = detail.get("issues", [])

    return jsonify({
        "group": group,
        "template": template.id,
        "template_name": template.name,
        "state": assigned["state"] if assigned else None,
        "display": (assigned or {}).get("display", "both"),
        "faceplate": (assigned or {}).get("faceplate", ""),
        "faceplate_options": (assigned or {}).get("faceplate_options", {}),
        "severity": report["severity"],
        "publishable": report["publishable"],
        "slots": slots,
        "points": [_point_summary(p) for p in points],
    })


def _point_summary(point: dict) -> dict:
    return {
        "path": point.get("path", ""),
        "name": decode_niagara_name(point.get("name", "")),
        "unit": point.get("custom_unit") or point.get("unit") or "",
        "unit_overridden": bool(point.get("custom_unit")),
        "type": point.get("type", "unknown"),
        "enabled": bool(point.get("enabled")),
    }


@app.route("/api/devices/assign", methods=["POST"])
def assign_device_type():
    """Assign a template to a device, auto-binding any slot not given."""
    data = request.get_json() or {}
    group = (data.get("group") or "").strip()
    template_id = (data.get("template") or "").strip()
    if not group or not template_id:
        return jsonify({"error": "group and template required"}), 400

    templates = load_templates()
    template = templates.get(template_id)
    if template is None:
        return jsonify({"error": f"unknown template {template_id}"}), 404

    points = _points_by_group().get(group, [])
    if not points:
        return jsonify({"error": "no points in this group"}), 404

    bindings = dict(bind_template(template, points, _load_values()))
    overrides = data.get("bindings") or {}
    valid_paths = {p.get("path") for p in points}
    slot_keys = {s.key for s in template.slots}
    for key, path in overrides.items():
        if key not in slot_keys:
            return jsonify({"error": f"unknown slot {key}"}), 400
        if path and path not in valid_paths:
            return jsonify({"error": f"point not in this device: {path}"}), 400
        bindings[key] = path or None

    devices = load_device_types()
    previous = devices.get(group, {})
    faceplate = data.get("faceplate")
    if faceplate is None:
        faceplate = previous.get("faceplate", "")

    # Values for the chosen face's own parameters. Only numbers: every
    # faceplate option is declared as one, and accepting anything else
    # would put a string where the drawing does arithmetic.
    raw_options = data.get("faceplate_options")
    if raw_options is None:
        faceplate_options = previous.get("faceplate_options") or {}
    elif isinstance(raw_options, dict):
        faceplate_options = {}
        for key, value in raw_options.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return jsonify({
                    "error": f"faceplate option {key} must be a number",
                }), 400
            faceplate_options[str(key)] = value
    else:
        return jsonify({"error": "faceplate_options must be an object"}), 400
    display = data.get("display") or previous.get("display") or "both"
    if display not in VALID_DISPLAYS:
        return jsonify({"error": f"unknown display {display!r}"}), 400
    devices[group] = {
        "template": template_id,
        "bindings": {k: v for k, v in bindings.items() if v},
        "state": data.get("state") or previous.get("state") or STATE_DRAFT,
        "faceplate": str(faceplate or "").strip(),
        "faceplate_options": faceplate_options,
        "display": display,
    }
    save_device_types(devices)

    missing = [s.key for s in template.required_slots if not bindings.get(s.key)]
    return jsonify({
        "ok": True,
        "group": group,
        "template": template_id,
        "bindings": devices[group]["bindings"],
        "state": devices[group]["state"],
        "missing_required": missing,
    })


def _folders_with_points() -> dict[str, int]:
    """Every folder that directly contains points, with its point count."""
    folders: dict[str, int] = {}
    for entry in _load_selections().values():
        parent = _parent_path(entry.get("path", ""))
        if parent:
            folders[parent] = folders.get(parent, 0) + 1
    return folders


@app.route("/api/device-folders/candidates")
def device_folder_candidates():
    """Branches whose children look like a set of identical devices.

    A building's guest rooms, or a carpark's exhaust fans, are all siblings
    under one branch. Marking them one at a time is unreasonable, so this
    finds branches worth adding wholesale.
    """
    folders = _folders_with_points()
    selected = set(load_device_folders())

    branches: dict[str, list[str]] = {}
    for folder in folders:
        parent = folder.rsplit("/", 1)[0] if "/" in folder else ""
        if parent:
            branches.setdefault(parent, []).append(folder)

    candidates = []
    for branch, children in branches.items():
        if len(children) < 2:
            continue
        missing = [c for c in children if c not in selected]
        candidates.append({
            "prefix": branch,
            "name": decode_niagara_name(branch.rsplit("/", 1)[-1]),
            "children": len(children),
            "already": len(children) - len(missing),
            "missing": len(missing),
            "points": sum(folders[c] for c in children),
        })

    candidates.sort(key=lambda c: (-c["missing"], c["prefix"]))
    return jsonify({"candidates": candidates})


@app.route("/api/device-folders/add-children", methods=["POST"])
def add_child_device_folders():
    """Mark every folder directly under a branch as its own device."""
    data = request.get_json() or {}
    prefix = (data.get("prefix") or "").strip().strip("/")
    if not prefix:
        return jsonify({"error": "prefix required"}), 400

    folders = _folders_with_points()
    children = [
        f for f in folders
        if f.startswith(prefix + "/") and "/" not in f[len(prefix) + 1:]
    ]
    if not children:
        return jsonify({"error": "no folders with points under this prefix"}), 404

    selected = load_device_folders()
    existing = set(selected)
    added = [c for c in sorted(children) if c not in existing]
    if added:
        save_device_folders(selected + added)

    return jsonify({
        "ok": True, "prefix": prefix,
        "added": len(added), "already": len(children) - len(added),
    })


@app.route("/api/devices/rebind", methods=["POST"])
def rebind_devices():
    """Re-run auto-binding against the current template definition.

    Editing a template does nothing to devices already typed with it: their
    bindings were resolved when the type was assigned. Without this, adding
    or changing a slot means retyping every device by hand.

    Manual overrides are replaced, which is why the caller has to ask for it
    explicitly — either for one device, or for every device of one template.
    """
    data = request.get_json() or {}
    group = (data.get("group") or "").strip()
    template_id = (data.get("template") or "").strip()
    if not group and not template_id:
        return jsonify({"error": "group or template required"}), 400

    templates = load_templates()
    devices = load_device_types()
    groups = _points_by_group()

    targets = [group] if group else [
        g for g, e in devices.items() if e.get("template") == template_id
    ]

    values = _load_values()
    rebound, changed = 0, 0
    for target in targets:
        entry = devices.get(target)
        if not entry:
            continue
        template = templates.get(entry["template"])
        if template is None:
            continue
        points = groups.get(target, [])
        if not points:
            continue
        fresh = {
            k: val for k, val in
            bind_template(template, points, values).items() if val
        }
        if fresh != entry.get("bindings"):
            changed += 1
        entry["bindings"] = fresh
        rebound += 1

    if rebound:
        save_device_types(devices)
    return jsonify({"ok": True, "rebound": rebound, "changed": changed})


@app.route("/api/devices/enable-slots", methods=["POST"])
def enable_slot_points():
    """Enable exactly the points bound to a device's slots.

    A folder marked as a device usually starts with nothing enabled. Rather
    than making the user hunt through 20,000 points, assigning a type says
    precisely which handful matter — so this enables those and nothing else.
    """
    data = request.get_json() or {}
    group = (data.get("group") or "").strip()
    if not group:
        return jsonify({"error": "group required"}), 400

    assigned = load_device_types().get(group)
    if not assigned:
        return jsonify({"error": "device has no type assigned"}), 404

    wanted = {p for p in assigned.get("bindings", {}).values() if p}
    if not wanted:
        return jsonify({"ok": True, "enabled": 0, "already": 0})

    return _enable_paths(wanted, group)


@app.route("/api/devices/enable-slots-all", methods=["POST"])
def enable_all_slot_points():
    """Enable the bound points of every typed device at once.

    Doing this device by device is impractical once a site has a couple of
    hundred of them.
    """
    wanted = {
        path
        for entry in load_device_types().values()
        for path in entry.get("bindings", {}).values()
        if path
    }
    if not wanted:
        return jsonify({"ok": True, "enabled": 0, "already": 0})
    return _enable_paths(wanted)


def _enable_paths(wanted: set[str], group: str | None = None):
    selections = load_point_selections()
    enabled = already = 0
    for path in wanted:
        entry = selections.get(path)
        if entry is None:
            continue
        if entry.get("enabled", False):
            already += 1
        else:
            entry["enabled"] = True
            enabled += 1

    if enabled:
        _write_selections(selections)

    payload = {"ok": True, "enabled": enabled, "already": already}
    if group:
        payload["group"] = group
    return jsonify(payload)


@app.route("/api/devices/set-points", methods=["POST"])
def set_device_points_enabled():
    """Enable or disable points of one device, from the device view.

    With no paths given this applies to every point in the device, which is
    how a device is switched on or off wholesale.
    """
    data = request.get_json() or {}
    group = (data.get("group") or "").strip()
    if not group:
        return jsonify({"error": "group required"}), 400
    enabled = bool(data.get("enabled", True))

    points = _points_by_group().get(group, [])
    if not points:
        return jsonify({"error": "no points in this group"}), 404

    in_device = {p.get("path") for p in points}
    requested = data.get("paths")
    if requested is None:
        targets = in_device
    else:
        targets = {p for p in requested if p in in_device}
        if not targets:
            return jsonify({"error": "no matching points in this device"}), 400

    selections = load_point_selections()
    changed = 0
    for path in targets:
        entry = selections.get(path)
        if entry is not None and bool(entry.get("enabled", False)) != enabled:
            entry["enabled"] = enabled
            changed += 1

    if changed:
        _write_selections(selections)

    return jsonify({
        "ok": True, "group": group, "enabled": enabled,
        "changed": changed, "targets": len(targets),
    })


@app.route("/api/templates/export")
def export_template_yaml():
    """Export one template, or all of them, as shareable YAML."""
    templates = load_templates()
    wanted = request.args.get("id", "")
    if wanted:
        template = templates.get(wanted)
        if template is None:
            return jsonify({"error": "unknown template"}), 404
        chosen = [template]
    else:
        chosen = sorted(templates.values(), key=lambda t: t.id)
    return jsonify({"yaml": export_templates(chosen), "count": len(chosen)})


@app.route("/api/templates/import", methods=["POST"])
def import_template_yaml():
    data = request.get_json() or {}
    text = data.get("yaml") or ""
    if not text.strip():
        return jsonify({"error": "No YAML supplied"}), 400
    try:
        saved, errors = import_templates(text)
    except TemplateError as err:
        return jsonify({"error": str(err)}), 400
    except OSError as err:
        return jsonify({"error": f"Could not write templates: {err}"}), 500
    return jsonify({"ok": True, "saved": saved, "errors": errors})


@app.route("/api/devices/card")
def device_card():
    """The card for a device, with slot placeholders resolved to point paths.

    The add-on does not know Home Assistant entity ids, only point paths.
    The integration turns these into entity ids; this endpoint supplies the
    shape and the mapping.
    """
    group = request.args.get("group", "")
    if not group:
        return jsonify({"error": "group required"}), 400

    assigned = load_device_types().get(group)
    if not assigned:
        return jsonify({"error": "device has no type assigned"}), 404

    template = load_templates().get(assigned["template"])
    if template is None:
        return jsonify({"error": "template no longer exists"}), 404
    if not template.card:
        return jsonify({"error": "this template defines no card"}), 404

    bindings = {k: v for k, v in assigned.get("bindings", {}).items() if v}
    name = decode_niagara_name(group.rstrip("/").split("/")[-1])
    card = render_card(template, {**bindings, "device_name": name})
    chosen = assigned.get("faceplate", "")
    if chosen and card:
        card = apply_faceplate(card, chosen, assigned.get("faceplate_options"))
    display = assigned.get("display", "both")
    if display == "entities":
        card = full_entities_card(template, bindings, name)
    elif card:
        card = apply_display(card, display)

    return jsonify({
        "group": group,
        "device_name": name,
        "template": template.id,
        "faceplate": chosen,
        "display": display,
        "card": card,
        "bindings": bindings,
    })


@app.route("/api/devices/cards")
def device_cards():
    """Every published device that has a card, in one call.

    A dashboard strategy builds a whole dashboard on every load. Asking
    for one device at a time would mean a request per switchboard, and
    there are dozens, so the bulk shape exists for that caller.
    """
    templates = load_templates()
    out = []
    for group, assigned in sorted(load_device_types().items()):
        if assigned.get("state") != STATE_PUBLISHED:
            continue
        template = templates.get(assigned["template"])
        if template is None or not template.card:
            continue
        bindings = {k: v for k, v in assigned.get("bindings", {}).items() if v}
        if not bindings:
            continue
        name = decode_niagara_name(group.rstrip("/").split("/")[-1])
        card = render_card(template, {**bindings, "device_name": name})
        if not card:
            continue
        chosen = assigned.get("faceplate", "")
        if chosen:
            card = apply_faceplate(card, chosen, assigned.get("faceplate_options"))
        display = assigned.get("display", "both")
        if display == "entities":
            card = full_entities_card(template, bindings, name)
        else:
            card = apply_display(card, display)
        out.append({
            "group": group,
            "device_name": name,
            "template": template.id,
            "faceplate": chosen,
            "display": display,
            "card": card,
            "bindings": bindings,
        })
    return jsonify({"devices": out, "count": len(out)})


@app.route("/api/faceplates")
def list_faceplates():
    """The faces available for a device, and which one it is wearing.

    Filtered by the custom cards the device's own template actually
    renders, so the picker never offers a meter face for a pump.
    """
    group = request.args.get("group", "")
    if not group:
        return jsonify({"error": "group required"}), 400

    assigned = load_device_types().get(group)
    if not assigned:
        return jsonify({"error": "device has no type assigned"}), 404
    template = load_templates().get(assigned["template"])
    if template is None or not template.card:
        return jsonify({"group": group, "selected": "", "faceplates": []})

    # Filtering by card type alone offers a water register for an
    # electricity meter: both are drawn by bms-meter-card, but they share
    # no roles, so the register would come up blank. Offer only faces this
    # template can actually fill, best fit first.
    choices: list[dict] = []
    for card_type in card_types_in(template.card):
        bound = roles_in(template.card, card_type)
        scored = []
        for face in faceplates_for_card(card_type):
            overlap = len(bound & set(face.get("roles") or []))
            if overlap:
                scored.append((overlap, face))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["name"]))
        choices.extend(face for _, face in scored)
    return jsonify({
        "group": group,
        "selected": assigned.get("faceplate", ""),
        "faceplates": choices,
    })


@app.route("/api/templates", methods=["POST"])
def save_template():
    try:
        payload = save_user_template(request.get_json() or {})
    except TemplateError as err:
        return jsonify({"error": str(err)}), 400
    except OSError as err:
        return jsonify({"error": f"Could not write template: {err}"}), 500
    return jsonify({"ok": True, "template": payload})


@app.route("/api/templates/<template_id>", methods=["DELETE"])
def remove_template(template_id):
    if delete_user_template(template_id):
        return jsonify({"ok": True, "deleted": template_id})
    return jsonify({"error": "no user template with that id"}), 404


def _addon_options() -> dict:
    try:
        with open("/data/options.json") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


@app.route("/api/devices/publish", methods=["POST"])
def publish_device():
    """Publish a device, or unpublish it.

    Publishing is refused while validation reports blocked: the whole point
    of the gate is that a device showing an implausible value does not reach
    Home Assistant looking like a real reading.
    """
    data = request.get_json() or {}
    group = (data.get("group") or "").strip()
    publish = bool(data.get("publish", True))
    if not group:
        return jsonify({"error": "group required"}), 400

    devices = load_device_types()
    entry = devices.get(group)
    if not entry:
        return jsonify({"error": "device has no type assigned"}), 404

    if publish:
        template = load_templates().get(entry["template"])
        if template is None:
            return jsonify({"error": "template no longer exists"}), 404
        points = _points_by_group().get(group, [])
        report = validate_device(
            template, entry.get("bindings", {}),
            {p.get("path"): p for p in points}, _load_values(),
            load_observations(),
        )
        if not report["publishable"]:
            blocking = [
                issue["message"]
                for slot in report["slots"] for issue in slot["issues"]
                if issue["severity"] == BLOCKED
            ]
            return jsonify({
                "error": "Device is blocked and cannot be published",
                "issues": blocking,
            }), 409

    entry["state"] = STATE_PUBLISHED if publish else STATE_DRAFT
    save_device_types(devices)
    return jsonify({"ok": True, "group": group, "state": entry["state"]})


@app.route("/api/devices/publish-all", methods=["POST"])
def publish_all_devices():
    """Publish every typed device that passes validation."""
    templates = load_templates()
    devices = load_device_types()
    groups = _points_by_group()
    values = _load_values()
    observations = load_observations()

    published, blocked = 0, []
    for group, entry in devices.items():
        template = templates.get(entry["template"])
        if template is None:
            continue
        report = validate_device(
            template, entry.get("bindings", {}),
            {p.get("path"): p for p in groups.get(group, [])},
            values, observations,
        )
        if report["publishable"]:
            if entry.get("state") != STATE_PUBLISHED:
                entry["state"] = STATE_PUBLISHED
                published += 1
        else:
            blocked.append(group)

    if published:
        save_device_types(devices)
    return jsonify({"ok": True, "published": published, "blocked": len(blocked)})


@app.route("/api/devices/unassign", methods=["POST"])
def unassign_device_type():
    data = request.get_json() or {}
    group = (data.get("group") or "").strip()
    devices = load_device_types()
    if group in devices:
        del devices[group]
        save_device_types(devices)
        return jsonify({"ok": True, "group": group})
    return jsonify({"error": "device has no type assigned"}), 404


@app.route("/api/devices/autotype", methods=["POST"])
def autotype_devices():
    """Assign the suggested template to every device that has none.

    Typing 42 devices by hand is a poor first run; this proposes a starting
    point the user can correct per device.
    """
    templates = load_templates()
    devices = load_device_types()
    values = _load_values()
    assigned = 0
    skipped = []

    for group, points in sorted(_device_groups().items()):
        if group in devices:
            continue
        template_id, _ = suggest_template(templates, points, group, values)
        if not template_id:
            skipped.append(group)
            continue
        devices[group] = {
            "template": template_id,
            "bindings": {
                k: v for k, v in
                bind_template(templates[template_id], points, values).items() if v
            },
            "state": STATE_DRAFT,
        }
        assigned += 1

    if assigned:
        save_device_types(devices)
    return jsonify({"ok": True, "assigned": assigned, "skipped": len(skipped)})


INTEGRATION_MANIFEST = Path("/config/custom_components/niagara/manifest.json")
ADDON_CONFIG = Path("/app/config.yaml")


def _addon_version() -> str:
    """This add-on's version, from the build arg or the manifest it shipped with."""
    from_build = os.environ.get("NIAGARA_HA_VERSION")
    if from_build:
        return from_build
    try:
        for line in ADDON_CONFIG.read_text().splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return ""


def _integration_version() -> str | None:
    """The installed integration's version, or None if it is not installed."""
    try:
        return json.loads(INTEGRATION_MANIFEST.read_text()).get("version")
    except (OSError, json.JSONDecodeError):
        return None


@app.route("/api/rescan", methods=["POST"])
def request_rescan():
    """Ask the poll loop to rediscover points from the station.

    Points added in Niagara do not appear here until something goes looking.
    Until now that meant waiting for a reconnect or restarting the add-on,
    neither of which is a reasonable answer to "I just added a meter".
    """
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "requested from the web UI").strip()[:200]
    at = rescan.request_rescan(reason)
    if not at:
        return jsonify({"error": "could not write the rescan request"}), 500
    return jsonify({
        "ok": True, "requested_at": at,
        "note": "Rediscovery starts within one poll interval.",
    })


@app.route("/api/rescan")
def rescan_status():
    at = rescan.pending()
    return jsonify({"pending": bool(at), "requested_at": at or None})


@app.route("/api/diagnostics")
def api_diagnostics():
    """Everything that is quietly wrong, in one place.

    These are the checks that previously only got run because somebody went
    looking — a meter absent from the energy picker, a total bound to a
    register that resets nightly. Nothing here is an error in the sense that
    anything stopped; that is exactly why it needs surfacing.
    """
    return jsonify(diagnostics.run_all(
        selections=_load_selections(),
        values=_load_values(),
        devices=load_device_types(),
        templates=load_templates(),
        observations=load_observations(),
        addon_version=_addon_version(),
        integration_version=_integration_version(),
        acks=diagnostics.load_acknowledgements(),
    ))


@app.route("/api/diagnostics/ack", methods=["POST"])
def acknowledge_finding():
    """Accept a finding, or withdraw that acceptance.

    Some findings are deliberate — points switched off on purpose, a meter
    nobody intends to put on the energy dashboard. An acknowledgement
    records how many items the finding had at the time, so if the problem
    grows it comes back rather than staying hidden forever.
    """
    data = request.get_json() or {}
    finding_id = (data.get("id") or "").strip()
    if not finding_id:
        return jsonify({"error": "id required"}), 400

    acks = diagnostics.load_acknowledgements()
    if data.get("acknowledge", True):
        acks[finding_id] = {
            "count": int(data.get("count", 0)),
            "note": (data.get("note") or "").strip()[:200],
            "at": time.time(),
        }
    else:
        acks.pop(finding_id, None)
    diagnostics.save_acknowledgements(acks)
    return jsonify({"ok": True, "acknowledged": finding_id in acks})


def _read_cache_file(path: Path) -> dict:
    """A cache file the poll loop writes, or {} if it has not written one."""
    try:
        with open(path) as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _alarm_payload() -> dict:
    """The alarm console, with why it is empty when it is.

    An empty list has four causes that look identical from here — the
    station exports no alarm service, the oBIX user cannot see it, the
    console is genuinely clear, or the records arrived in a shape the parser
    did not recognise. The probe is what separates them, so it travels with
    the list rather than hiding behind a second request.
    """
    cached = _read_cache_file(ALARMS_FILE)
    probe = _read_cache_file(ALARM_PROBE_FILE)
    records = cached.get("records") or []
    now = time.time()
    return {
        "records": records,
        # Stations whose alarms were left out because this bridge exports
        # no enabled points from them. Reported rather than silently
        # dropped, so a station that should be included but has nothing
        # enabled yet is visible instead of just missing.
        "other_stations": cached.get("other_stations") or {},
        "summary": cached.get("summary") or {
            "total": 0, "active": 0, "unacked": 0, "highest_priority": None,
        },
        "truncated": bool(cached.get("truncated")),
        "age": round(now - cached["ts"], 1) if cached.get("ts") else None,
        "error": cached.get("error") or probe.get("error"),
        "supported": bool(probe.get("query_op")),
        "checked_at": probe.get("checked_at"),
    }


@app.route("/api/alarms")
def list_alarms():
    """The station's alarm console, for the management UI."""
    return jsonify(_alarm_payload())


@app.route("/api/alarms/probe")
def alarm_probe():
    """What the station exposes for alarming, from the last connect.

    Refreshed when the bridge reconnects, which a rescan forces. Carries the
    raw reply when nothing parsed, because that is the only way to tell an
    empty console from an unrecognised format.
    """
    probe = _read_cache_file(ALARM_PROBE_FILE)
    if not probe:
        return jsonify({
            "error": "The bridge has not connected to the station yet.",
            "checked_at": None,
        })
    return jsonify(probe)


@app.route("/api/integration/alarms")
def integration_alarms():
    """The alarm console for the HA integration.

    Same data as /api/alarms. It has its own route so the UI's shape can
    change without breaking a released integration, which is the mistake
    /api/points made before /api/integration/points existed.
    """
    payload = _alarm_payload()
    # Only the active alarms become entity state; the integration does not
    # need the ones that have returned to normal, and a station with a
    # noisy week would otherwise send thousands of records every poll.
    payload["records"] = [r for r in payload["records"] if r.get("active")]
    return jsonify(payload)


# -- Trend logs -----------------------------------------------------------

# The poll loop holds the connection used for points, but reading a trend is
# on demand and bursty, so this process opens its own. Both run in the same
# container and read the same /data/options.json. Created lazily: a station
# with no histories selected should never cause a second login.
_station = {"client": None}


def _station_client():
    """A station connection for on-demand reads, or None if unconfigured."""
    if _station["client"] is not None:
        return _station["client"]
    options = _addon_options()
    host = options.get("niagara_host")
    if not host:
        return None
    from obix_client import ObixClient

    client = ObixClient(
        host=host,
        username=options.get("niagara_user", ""),
        password=options.get("niagara_password", ""),
        port=options.get("niagara_port", 443),
        use_https=options.get("use_https", True),
        verify_ssl=options.get("verify_ssl", False),
    )
    if not client.test_connection():
        return None
    _station["client"] = client
    return client


def _history_catalogue() -> dict:
    payload = _read_cache_file(HISTORY_CATALOGUE_FILE)
    return {
        "histories": payload.get("histories") or [],
        "links": payload.get("links") or {},
        "checked_at": payload.get("checked_at"),
        "error": payload.get("error"),
    }


def _history_screen() -> dict:
    """Everything the History screen needs in one request.

    One request rather than four because the screen is useless without all
    of it — the selection means nothing without the catalogue to pick from,
    and an empty catalogue means nothing without the reason it is empty.
    """
    state = history_store.load()
    catalogue = _history_catalogue()
    selections = _load_selections()
    links = catalogue["links"]

    chosen = []
    for name, entry in sorted(state["histories"].items()):
        point = selections.get(entry.get("point", "")) or {}
        chosen.append({
            "history": name,
            "enabled": bool(entry.get("enabled")),
            "point": entry.get("point", ""),
            "point_name": decode_niagara_name(point.get("name", "")),
            "point_enabled": bool(point.get("enabled")),
            "group": entry.get("group", ""),
            "group_name": decode_niagara_name(
                (entry.get("group") or "").rstrip("/").split("/")[-1]
            ),
            "unit": entry.get("unit", ""),
            "added": entry.get("added"),
            "last_synced": entry.get("last_synced"),
            "last_count": entry.get("last_count"),
            "last_run": entry.get("last_run"),
        })

    taken = set(state["histories"])
    available = []
    for meta in catalogue["histories"]:
        name = meta.get("name", "")
        if not name or name in taken:
            continue
        suggestion = history_store.pair(name, links, selections)
        available.append({**meta, **suggestion})

    return {
        "enabled": state["enabled"],
        "selected": chosen,
        "available": available,
        "available_total": len(available),
        "linked_points": len(links),
        "checked_at": catalogue["checked_at"],
        "error": catalogue["error"],
    }


@app.route("/api/histories")
def list_histories():
    """The History screen: the switch, the selection, and what is on offer."""
    payload = _history_screen()
    # A station with thousands of trends would send megabytes of JSON into
    # the browser on every refresh. The screen searches server-side instead.
    query = (request.args.get("q") or "").strip().lower()
    if query:
        payload["available"] = [
            h for h in payload["available"]
            if query in h.get("name", "").lower()
            or query in (h.get("group") or "").lower()
        ]
    limit = max(1, min(int(request.args.get("limit", 200)), 1000))
    payload["available_shown"] = len(payload["available"][:limit])
    payload["available"] = payload["available"][:limit]
    return jsonify(payload)


@app.route("/api/histories/enabled", methods=["POST"])
def set_histories_enabled():
    """The master switch for history syncing."""
    body = request.get_json(silent=True) or {}
    state = history_store.set_global_enabled(bool(body.get("enabled")))
    return jsonify({"enabled": state["enabled"]})


@app.route("/api/histories/add", methods=["POST"])
def add_history():
    """Add one trend log, pairing it to its device.

    One at a time on purpose. A station carries thousands of trends, and
    importing them all would write years of statistics into Home Assistant's
    database on the first run.
    """
    body = request.get_json(silent=True) or {}
    name = (body.get("history") or "").strip()
    if not name:
        return jsonify({"error": "history is required"}), 400

    catalogue = _history_catalogue()
    known = {h.get("name") for h in catalogue["histories"]}
    if known and name not in known:
        return jsonify({
            "error": f"the station does not advertise a history named {name}",
        }), 404

    selections = _load_selections()
    suggested = history_store.pair(name, catalogue["links"], selections)
    point = (body.get("point") or suggested["point"] or "").strip()
    group = (body.get("group") or "").strip()
    if not group and point:
        group = (selections.get(point) or {}).get("group", "")
    unit = (body.get("unit") or suggested["unit"] or "").strip()

    try:
        entry = history_store.add(
            name, point=point, group=group, unit=unit,
            enabled=bool(body.get("enabled", True)),
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({
        "history": name,
        "entry": entry,
        "paired_by": suggested["confidence"] if not body.get("point") else "manual",
    })


@app.route("/api/histories/toggle", methods=["POST"])
def toggle_history():
    body = request.get_json(silent=True) or {}
    name = (body.get("history") or "").strip()
    entry = history_store.set_enabled(name, bool(body.get("enabled")))
    if entry is None:
        return jsonify({"error": f"{name} is not in the selection"}), 404
    return jsonify({"history": name, "entry": entry})


@app.route("/api/histories/remove", methods=["POST"])
def remove_history():
    body = request.get_json(silent=True) or {}
    name = (body.get("history") or "").strip()
    if not history_store.remove(name):
        return jsonify({"error": f"{name} is not in the selection"}), 404
    return jsonify({"history": name, "removed": True})


@app.route("/api/histories/pair")
def pair_history():
    """What this history would be paired to, and on what evidence."""
    name = (request.args.get("history") or "").strip()
    if not name:
        return jsonify({"error": "history is required"}), 400
    catalogue = _history_catalogue()
    selections = _load_selections()
    suggestion = history_store.pair(name, catalogue["links"], selections)
    point = selections.get(suggestion["point"]) or {}
    return jsonify({
        **suggestion,
        "history": name,
        "point_name": decode_niagara_name(point.get("name", "")),
        "point_enabled": bool(point.get("enabled")),
        "point_type": point.get("type", ""),
    })


@app.route("/api/histories/preview")
def preview_history():
    """The most recent samples from one trend, to confirm it is the right one.

    Reads the station directly, bounded hard. The point of the screen is to
    let somebody see a trend before committing to syncing years of it.
    """
    name = (request.args.get("history") or "").strip()
    if not name:
        return jsonify({"error": "history is required"}), 400

    client = _station_client()
    if client is None:
        return jsonify({"error": "cannot reach the station"}), 503

    meta = client.read_history(name)
    if meta is None:
        return jsonify({"error": f"the station has no history named {name}"}), 404

    limit = max(1, min(int(request.args.get("limit", 20)), 200))
    end = histories.parse_time(meta.end) or datetime.now(timezone.utc)
    records = client.query_history(
        name, start=end - timedelta(days=2), end=end, limit=limit,
    )
    return jsonify({
        "history": name,
        "meta": meta.to_dict(),
        "records": [r.to_dict() for r in records[-limit:]],
    })


@app.route("/api/histories/probe")
def history_probe():
    """What the station exposes for trend logs, from the last connect."""
    probe = _read_cache_file(HISTORY_PROBE_FILE)
    if not probe:
        return jsonify({
            "error": "The bridge has not connected to the station yet.",
            "checked_at": None,
        })
    return jsonify(probe)


@app.route("/api/integration/histories")
def integration_histories():
    """The trends to sync, for the HA integration.

    Only the enabled ones, and only while the master switch is on, so
    turning the feature off stops the integration asking rather than relying
    on it to check.
    """
    selections = _load_selections()
    entries = []
    for name, entry in sorted(history_store.enabled_entries().items()):
        point = selections.get(entry.get("point", "")) or {}
        entries.append({
            "history": name,
            "point": entry.get("point", ""),
            "group": entry.get("group", ""),
            "unit": entry.get("unit") or point.get("custom_unit")
                    or point.get("unit", ""),
            "point_type": point.get("type", ""),
            "last_synced": entry.get("last_synced"),
        })
    return jsonify({"enabled": bool(entries), "histories": entries})


@app.route("/api/integration/histories/data")
def integration_history_data():
    """Samples from one trend, already grouped into hourly buckets.

    Grouped here rather than in the integration because the raw samples are
    the bulky part — a week of one-minute trend is ten thousand records and
    168 buckets — and Home Assistant stores hours regardless.
    """
    name = (request.args.get("history") or "").strip()
    if not name:
        return jsonify({"error": "history is required"}), 400
    if name not in history_store.enabled_entries():
        return jsonify({"error": f"{name} is not enabled for syncing"}), 403

    client = _station_client()
    if client is None:
        return jsonify({"error": "cannot reach the station"}), 503

    start = histories.parse_time(request.args.get("start") or "")
    end = histories.parse_time(request.args.get("end") or "") or datetime.now(
        timezone.utc,
    )
    records = client.query_history(name, start=start, end=end)
    buckets = histories.hourly_buckets(records)
    return jsonify({
        "history": name,
        "requested_start": start.isoformat() if start else None,
        "requested_end": end.isoformat(),
        "records": len(records),
        "buckets": buckets,
        # A full page means there is more beyond it; the caller continues
        # from the last bucket rather than assuming it reached the end.
        "complete": len(records) < histories.MAX_RECORDS,
    })


@app.route("/api/histories/synced", methods=["POST"])
def mark_history_synced():
    """The integration reporting how far it got.

    The watermark lives with the add-on because that is where the selection
    lives; keeping it in Home Assistant would lose it whenever the
    integration was removed and re-added, and the next run would re-import
    everything.
    """
    body = request.get_json(silent=True) or {}
    name = (body.get("history") or "").strip()
    through = (body.get("through") or "").strip()
    if not name or not through:
        return jsonify({"error": "history and through are required"}), 400
    history_store.record_sync(name, through, int(body.get("imported") or 0))
    return jsonify({"history": name, "through": through})


# -- Areas ----------------------------------------------------------------


def _device_group_list() -> list[str]:
    """Every folder that is or could be a device, for previewing rules."""
    return sorted(_device_groups())


@app.route("/api/areas")
def list_areas():
    """The rules, and what every device folder resolves to under them.

    The preview is the point of the screen: a rule list without it is a
    guess, and the failure it replaces — 275 of 323 devices in one area —
    was invisible precisely because nothing showed the result.
    """
    rules = area_rules.load()
    groups = _device_group_list()
    resolved = area_rules.preview(groups, rules)
    unclaimed = [g for g, a in resolved.items() if not a]
    counts: dict[str, int] = {}
    for area in resolved.values():
        if area:
            counts[area] = counts.get(area, 0) + 1
    return jsonify({
        "rules": [r.to_dict() for r in rules],
        "areas": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "resolved": {
            g: {"area": a, "name": decode_niagara_name(g.rstrip("/").split("/")[-1])}
            for g, a in resolved.items()
        },
        "unclaimed": len(unclaimed),
        "total": len(groups),
    })


@app.route("/api/areas", methods=["POST"])
def save_areas():
    """Replace the rule list. Order is the configuration, so it is kept."""
    body = request.get_json(silent=True) or {}
    raw = body.get("rules")
    if not isinstance(raw, list):
        return jsonify({"error": "rules must be a list"}), 400
    rules = []
    for item in raw:
        if not isinstance(item, dict):
            return jsonify({"error": "each rule must be an object"}), 400
        match = str(item.get("match") or "").strip()
        area = str(item.get("area") or "").strip()
        if not match or not area:
            return jsonify({
                "error": "each rule needs a match and an area",
            }), 400
        rules.append(area_rules.AreaRule(match, area,
                                        str(item.get("note") or "")))
    area_rules.save(rules)
    return jsonify({"ok": True, "rules": len(rules)})


@app.route("/api/areas/learn", methods=["POST"])
def learn_areas():
    """Propose rules from the areas already assigned in Home Assistant.

    Home Assistant holds the assignments, not the add-on, so the
    integration sends them. Those assignments are the real knowledge about
    the building — inventing area names from path segments would discard
    them and leave a second set of near-duplicates beside them.
    """
    body = request.get_json(silent=True) or {}
    assignments = body.get("assignments")
    if not isinstance(assignments, dict):
        return jsonify({"error": "assignments must be an object"}), 400

    groups = _device_group_list()
    proposed = area_rules.learn(
        {str(k): str(v or "") for k, v in assignments.items()},
        all_groups=groups,
    )
    if body.get("save"):
        area_rules.save(proposed)

    resolved = area_rules.preview(groups, proposed)
    return jsonify({
        "saved": bool(body.get("save")),
        "rules": [r.to_dict() for r in proposed],
        "would_claim": sum(1 for a in resolved.values() if a),
        "would_leave": sum(1 for a in resolved.values() if not a),
        "total": len(groups),
    })


@app.route("/api/health")
def health():
    """Liveness for the integration: is the bridge actually getting data?

    Lets Home Assistant tell "add-on down" from "add-on up, Niagara down",
    which previously looked identical from the integration's side.
    """
    selections = _load_selections()
    values = _load_values()
    now = time.time()

    enabled = [e for e in selections.values() if e.get("enabled", False)]
    ages = [
        now - v["ts"] for p, v in values.items() if v.get("ts")
    ]
    faulted = sum(1 for v in values.values() if v.get("status") == "fault")

    return jsonify({
        "ok": bool(ages) and min(ages) < 300,
        "points_total": len(selections),
        "points_enabled": len(enabled),
        "values_cached": len(values),
        "values_faulted": faulted,
        "newest_value_age": round(min(ages), 1) if ages else None,
        "oldest_value_age": round(max(ages), 1) if ages else None,
    })


def _write_selections(selections: dict[str, dict]) -> None:
    """Persist point selections, then invalidate the read cache."""
    write_point_selections(selections)
    _sel_cache["mtime"] = 0.0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    port = int(os.environ.get("INGRESS_PORT", "8099"))
    logger.info("Starting web UI on port %d", port)
    # Threaded explicitly, not by default: the value endpoint holds a
    # request open for up to 45 seconds waiting for a change, and on a
    # single-threaded server that would block the management UI for the
    # whole of it.
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
