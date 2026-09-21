"""Niagara-HA point management web UI (served via HA ingress)."""

import json
import logging
import os
import time
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

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
from validation import (
    BLOCKED,
    load_observations,
    validate_device,
)
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
    write_point_selections,
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


def _load_values() -> dict[str, dict]:
    """Return the value cache as {path: {"value", "status", "ts"}}.

    Tolerates the old flat {path: value} format so a downgrade or a
    half-written file cannot take the UI down.
    """
    try:
        mtime = VALUES_FILE.stat().st_mtime
    except OSError:
        return {}
    if mtime != _values_cache["mtime"]:
        try:
            with open(VALUES_FILE) as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        _values_cache["data"] = {
            path: (entry if isinstance(entry, dict) and "value" in entry
                   else {"value": entry, "status": "unknown", "ts": 0.0})
            for path, entry in raw.items()
        }
        _values_cache["mtime"] = mtime
    return _values_cache["data"]


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
                "device_group": group,
            }

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
            "unit": entry.get("unit", ""),
            "group": entry.get("group", "Ungrouped"),
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
    """Return current values for all enabled points.

    Lightweight endpoint for polling — just path:value pairs.
    Returns null for points that haven't been polled yet so the
    integration knows they exist.
    """
    selections = _load_selections()
    values = _load_values()
    now = time.time()

    result = {}
    for entry in selections.values():
        if not entry.get("enabled", False):
            continue
        path = entry.get("path", "")
        cached = values.get(path)
        if cached is None:
            result[path] = None
            continue
        result[path] = {
            "value": cached.get("value"),
            "status": cached.get("status", "unknown"),
            "age": round(now - cached["ts"], 1) if cached.get("ts") else None,
        }

    return jsonify(result)


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
            suggested, _ = suggest_template(templates, points, group)
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
        template_id, _ = suggest_template(templates, points, group)

    template = templates.get(template_id) if template_id else None
    if template is None:
        return jsonify({
            "group": group, "template": None, "slots": [],
            "points": [_point_summary(p) for p in points],
        })

    # Stored bindings win; anything unset is proposed by auto-binding.
    proposed = bind_template(template, points)
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
        "severity": report["severity"],
        "publishable": report["publishable"],
        "slots": slots,
        "points": [_point_summary(p) for p in points],
    })


def _point_summary(point: dict) -> dict:
    return {
        "path": point.get("path", ""),
        "name": decode_niagara_name(point.get("name", "")),
        "unit": point.get("unit") or "",
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

    bindings = dict(bind_template(template, points))
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
    devices[group] = {
        "template": template_id,
        "bindings": {k: v for k, v in bindings.items() if v},
        "state": data.get("state") or previous.get("state") or STATE_DRAFT,
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

    A hotel's 192 guest rooms, or a carpark's exhaust fans, are all siblings
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
            bind_template(template, points).items() if val
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

    return jsonify({
        "group": group,
        "device_name": name,
        "template": template.id,
        "card": card,
        "bindings": bindings,
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
    assigned = 0
    skipped = []

    for group, points in sorted(_device_groups().items()):
        if group in devices:
            continue
        template_id, _ = suggest_template(templates, points, group)
        if not template_id:
            skipped.append(group)
            continue
        devices[group] = {
            "template": template_id,
            "bindings": {
                k: v for k, v in
                bind_template(templates[template_id], points).items() if v
            },
            "state": STATE_DRAFT,
        }
        assigned += 1

    if assigned:
        save_device_types(devices)
    return jsonify({"ok": True, "assigned": assigned, "skipped": len(skipped)})


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
    app.run(host="0.0.0.0", port=port, debug=False)
