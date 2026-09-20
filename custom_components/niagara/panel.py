"""Web panel API for Niagara BMS point management."""

import fnmatch
import logging
import re
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import NiagaraCoordinator, NiagaraPoint, decode_niagara_name

_LOGGER = logging.getLogger(__name__)

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

PROFILES = {
    "hvac": {
        "name": "HVAC",
        "description": "Air handling, fans, pumps, valves, temperatures, and setpoints",
        "patterns": ["*Temp*", "*Fan*", "*Pump*", "*Valve*", "*Damper*", "*AHU*", "*Supply*", "*Return*", "*Setpoint*", "*VFD*"],
    },
    "energy": {
        "name": "Energy",
        "description": "Power meters, energy consumption, and demand",
        "patterns": ["*Power*", "*Energy*", "*kWh*", "*kW*", "*Demand*", "*Meter*", "*Watt*"],
    },
    "alarms": {
        "name": "Alarms",
        "description": "Fault conditions, alarms, trips, and emergency states",
        "patterns": ["*Alarm*", "*Fault*", "*Trip*", "*Alert*", "*Emergency*", "*Smoke*", "*Fire*"],
    },
    "lighting": {
        "name": "Lighting",
        "description": "Lighting levels, switches, and lux sensors",
        "patterns": ["*Light*", "*Lux*", "*Luminaire*", "*Lamp*", "*Dimmer*"],
    },
    "zone_comfort": {
        "name": "Zone Comfort",
        "description": "Zone temperatures, humidity, CO2, and occupancy",
        "patterns": ["*Zone*Temp*", "*Room*Temp*", "*Humid*", "*CO2*", "*Occup*", "*Zone*SP*", "*Room*SP*"],
    },
}

PAGE_SIZE = 50


def _categorize(point: NiagaraPoint) -> str:
    text = f"{point.name} {point.path}"
    for cat_name, pattern, units in CATEGORY_RULES:
        if pattern.search(text):
            return cat_name
        if units and point.unit and point.unit in units:
            return cat_name
    if point.point_type == "boolean":
        return "Status"
    return "Other"


def _parse_path_segments(path: str) -> list[str]:
    skip = {"config", "Drivers", "NiagaraNetwork", "ObixNetwork", "points", "out", "exports", "obix", ""}
    return [p for p in path.strip("/").split("/") if p not in skip]


def _point_to_dict(point: NiagaraPoint, coordinator: NiagaraCoordinator) -> dict[str, Any]:
    return {
        "path": point.path,
        "name": decode_niagara_name(point.name),
        "type": point.point_type,
        "unit": point.unit or "",
        "value": point.value,
        "group": coordinator.get_group(point),
        "category": _categorize(point),
        "writable": point.writable,
    }


def _get_tree_children(
    points: dict[str, NiagaraPoint], prefix: str,
) -> list[dict[str, Any]]:
    prefix_parts = prefix.split("/") if prefix else []
    depth = len(prefix_parts)

    folders: dict[str, dict[str, int]] = {}
    for pt in points.values():
        segs = _parse_path_segments(pt.path)
        if len(segs) <= depth:
            continue
        if prefix_parts and segs[:depth] != prefix_parts:
            continue
        folder_name = segs[depth]
        folder_path = "/".join(segs[: depth + 1])
        if folder_path not in folders:
            folders[folder_path] = {"name": folder_name, "path": folder_path, "total": 0, "has_children": False}
        folders[folder_path]["total"] += 1
        if len(segs) > depth + 2:
            folders[folder_path]["has_children"] = True

    return sorted(folders.values(), key=lambda f: f["name"])


def _matches_patterns(name: str, path: str, patterns: list[str]) -> bool:
    text_name = name.lower()
    text_path = path.lower()
    for pat in patterns:
        pat_lower = pat.lower()
        if fnmatch.fnmatch(text_name, pat_lower) or fnmatch.fnmatch(text_path, pat_lower):
            return True
    return False


def _get_coordinator(hass: HomeAssistant) -> NiagaraCoordinator | None:
    entries = hass.data.get(DOMAIN, {})
    for coordinator in entries.values():
        if isinstance(coordinator, NiagaraCoordinator):
            return coordinator
    return None


class NiagaraPanelView(HomeAssistantView):
    """Serve the panel HTML."""

    url = "/api/niagara/panel"
    name = "api:niagara:panel"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        return web.Response(
            text=PANEL_HTML,
            content_type="text/html",
        )


class NiagaraStatsView(HomeAssistantView):
    url = "/api/niagara/stats"
    name = "api:niagara:stats"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if not coordinator:
            return self.json({"total": 0, "enabled": 0, "disabled": 0, "categories": []})

        points = coordinator.points
        categories: dict[str, int] = {}
        for pt in points.values():
            cat = _categorize(pt)
            categories[cat] = categories.get(cat, 0) + 1

        total = len(points)
        return self.json({
            "total": total,
            "enabled": total,
            "disabled": 0,
            "categories": sorted(
                [{"name": k, "count": v} for k, v in categories.items()],
                key=lambda c: c["name"],
            ),
        })


class NiagaraTreeView(HomeAssistantView):
    url = "/api/niagara/tree"
    name = "api:niagara:tree"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if not coordinator:
            return self.json({"prefix": "", "children": []})

        prefix = request.query.get("prefix", "")
        children = _get_tree_children(coordinator.points, prefix)
        return self.json({"prefix": prefix, "children": children})


class NiagaraPointsView(HomeAssistantView):
    url = "/api/niagara/points"
    name = "api:niagara:points"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if not coordinator:
            return self.json({"points": [], "total": 0, "page": 1, "pages": 1})

        prefix = request.query.get("prefix", "")
        search = request.query.get("search", "").lower()
        category = request.query.get("category", "")
        page = int(request.query.get("page", "1"))

        point_list = []
        prefix_parts = prefix.split("/") if prefix else []
        for pt in coordinator.points.values():
            if prefix_parts:
                segs = _parse_path_segments(pt.path)
                if len(segs) < len(prefix_parts) or segs[: len(prefix_parts)] != prefix_parts:
                    continue
            if search:
                if search not in pt.name.lower() and search not in pt.path.lower():
                    continue
            d = _point_to_dict(pt, coordinator)
            if category and d["category"] != category:
                continue
            point_list.append(d)

        point_list.sort(key=lambda p: (p["group"], p["name"]))
        total = len(point_list)
        start = (page - 1) * PAGE_SIZE
        page_points = point_list[start : start + PAGE_SIZE]

        return self.json({
            "points": page_points,
            "total": total,
            "page": page,
            "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        })


class NiagaraValuesView(HomeAssistantView):
    url = "/api/niagara/values"
    name = "api:niagara:values"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if not coordinator:
            return self.json({})

        paths = request.query.getall("paths[]", [])
        vals: dict[str, str | None] = {}
        for p in paths:
            pt = coordinator.points.get(p)
            if pt and pt.value is not None:
                vals[p] = pt.value
        return self.json(vals)


class NiagaraProfilesView(HomeAssistantView):
    url = "/api/niagara/profiles"
    name = "api:niagara:profiles"
    requires_auth = True

    async def get(self, request: web.Request) -> web.Response:
        result = []
        for key, profile in PROFILES.items():
            result.append({
                "id": key,
                "name": profile["name"],
                "description": profile["description"],
                "pattern_count": len(profile["patterns"]),
            })
        return self.json({"profiles": result})


class NiagaraProfilePreviewView(HomeAssistantView):
    url = "/api/niagara/profiles/{profile_id}/preview"
    name = "api:niagara:profiles:preview"
    requires_auth = True

    async def get(self, request: web.Request, profile_id: str) -> web.Response:
        profile = PROFILES.get(profile_id)
        if not profile:
            return self.json({"error": "not found"}, status_code=404)

        hass = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if not coordinator:
            return self.json({"profile": profile["name"], "would_enable": 0, "sample": [], "patterns": profile["patterns"]})

        would_enable = []
        for pt in coordinator.points.values():
            name = decode_niagara_name(pt.name)
            if _matches_patterns(name, pt.path, profile["patterns"]):
                would_enable.append({"name": name, "path": pt.path, "group": coordinator.get_group(pt)})

        return self.json({
            "profile": profile["name"],
            "would_enable": len(would_enable),
            "sample": would_enable[:20],
            "patterns": profile["patterns"],
        })


async def async_setup_panel(hass: HomeAssistant) -> None:
    """Register the panel views and frontend panel."""
    hass.http.register_view(NiagaraPanelView())
    hass.http.register_view(NiagaraStatsView())
    hass.http.register_view(NiagaraTreeView())
    hass.http.register_view(NiagaraPointsView())
    hass.http.register_view(NiagaraValuesView())
    hass.http.register_view(NiagaraProfilesView())
    hass.http.register_view(NiagaraProfilePreviewView())

    hass.components.frontend.async_register_built_in_panel(
        component_name="iframe",
        sidebar_title="Niagara BMS",
        sidebar_icon="mdi:office-building",
        frontend_url_path="niagara",
        config={"url": "/api/niagara/panel"},
    )


PANEL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Niagara BMS</title>
<style>
:root {
  --bg: #f5f6fa;
  --card: #ffffff;
  --border: #e0e3eb;
  --text: #1a1d23;
  --muted: #6b7280;
  --primary: #3b82f6;
  --primary-hover: #2563eb;
  --success: #22c55e;
  --danger: #ef4444;
  --tag-bg: #e5e7eb;
  --toggle-off: #d1d5db;
  --toggle-on: #3b82f6;
  --row-hover: #f0f4ff;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #111318;
    --card: #1c1f26;
    --border: #2d3140;
    --text: #e5e7eb;
    --muted: #9ca3af;
    --tag-bg: #374151;
    --toggle-off: #4b5563;
    --row-hover: #1e2432;
  }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.5; padding: 16px;
}
h1 { font-size: 1.4rem; font-weight: 600; }
.header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }
.stats { display: flex; gap: 16px; flex-wrap: wrap; }
.stat { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 12px 20px; text-align: center; min-width: 100px; }
.stat-value { font-size: 1.6rem; font-weight: 700; }
.stat-label { font-size: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.layout { display: flex; gap: 16px; }
@media (max-width: 768px) { .layout { flex-direction: column; } }
.sidebar { min-width: 320px; width: max-content; max-width: 50%; flex-shrink: 0; background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
@media (max-width: 768px) { .sidebar { width: 100%; max-width: 100%; min-width: 0; max-height: 300px; overflow-y: auto; } }
.sidebar-header { padding: 12px 16px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 0.9rem; display: flex; justify-content: space-between; align-items: center; }
.group-list { max-height: calc(100vh - 260px); overflow-y: auto; }
.breadcrumb { display: flex; align-items: center; gap: 4px; padding: 8px 16px; font-size: 0.8rem; border-bottom: 1px solid var(--border); flex-wrap: wrap; min-height: 36px; }
.breadcrumb-seg { cursor: pointer; color: var(--primary); font-weight: 500; }
.breadcrumb-seg:hover { text-decoration: underline; }
.breadcrumb-current { color: var(--text); font-weight: 600; cursor: default; }
.breadcrumb-sep { color: var(--muted); font-size: 0.7rem; }
.tree-item { display: flex; align-items: center; justify-content: space-between; padding: 8px 16px; cursor: pointer; border-bottom: 1px solid var(--border); transition: background 0.15s; gap: 8px; }
.tree-item:hover { background: var(--row-hover); }
.tree-item-name { font-size: 0.85rem; font-weight: 500; flex: 1; min-width: 0; white-space: nowrap; }
.tree-item-right { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.tree-arrow { color: var(--muted); font-size: 0.7rem; flex-shrink: 0; }
.group-badge { padding: 2px 8px; border-radius: 10px; font-weight: 500; font-size: 0.75rem; }
.badge-total { background: var(--tag-bg); color: var(--muted); }
.main { flex: 1; min-width: 0; }
.toolbar { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; align-items: center; }
.search-box { flex: 1; min-width: 200px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); color: var(--text); font-size: 0.9rem; }
.search-box:focus { outline: 2px solid var(--primary); border-color: transparent; }
.btn { padding: 8px 16px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); color: var(--text); cursor: pointer; font-size: 0.85rem; font-weight: 500; white-space: nowrap; transition: background 0.15s; }
.btn:hover { background: var(--row-hover); }
.btn-primary { background: var(--primary); color: white; border-color: var(--primary); }
.btn-primary:hover { background: var(--primary-hover); }
.btn-sm { padding: 4px 10px; font-size: 0.8rem; }
.category-select { padding: 8px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); color: var(--text); font-size: 0.85rem; cursor: pointer; min-width: 140px; }
.category-select:focus { outline: 2px solid var(--primary); border-color: transparent; }
.table-wrap { background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; padding: 10px 16px; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); border-bottom: 1px solid var(--border); background: var(--card); position: sticky; top: 0; }
td { padding: 8px 16px; font-size: 0.85rem; border-bottom: 1px solid var(--border); }
tr:hover td { background: var(--row-hover); }
.point-path { color: var(--muted); font-size: 0.75rem; word-break: break-all; }
.point-type { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; background: var(--tag-bg); color: var(--muted); text-transform: uppercase; }
.point-value { font-family: "SF Mono", "Cascadia Code", "Consolas", monospace; font-size: 0.8rem; color: var(--text); max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.point-value-na { color: var(--muted); font-style: italic; font-family: inherit; }
.cat-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: 500; text-transform: uppercase; }
.cat-Temperature { background: #fef3c7; color: #92400e; }
.cat-Fan { background: #dbeafe; color: #1e40af; }
.cat-Pump { background: #e0e7ff; color: #3730a3; }
.cat-Valve { background: #fce7f3; color: #9d174d; }
.cat-Pressure { background: #fde68a; color: #78350f; }
.cat-Power { background: #fecaca; color: #991b1b; }
.cat-Humidity { background: #d1fae5; color: #065f46; }
.cat-Flow { background: #cffafe; color: #155e75; }
.cat-Setpoint { background: #ede9fe; color: #5b21b6; }
.cat-Status { background: #e5e7eb; color: #374151; }
.cat-CO2 { background: #f3e8ff; color: #6b21a8; }
.cat-Other { background: var(--tag-bg); color: var(--muted); }
@media (prefers-color-scheme: dark) {
  .cat-Temperature { background: #78350f; color: #fef3c7; }
  .cat-Fan { background: #1e3a5f; color: #93c5fd; }
  .cat-Pump { background: #312e81; color: #c7d2fe; }
  .cat-Valve { background: #831843; color: #fbcfe8; }
  .cat-Pressure { background: #713f12; color: #fde68a; }
  .cat-Power { background: #7f1d1d; color: #fecaca; }
  .cat-Humidity { background: #064e3b; color: #a7f3d0; }
  .cat-Flow { background: #164e63; color: #a5f3fc; }
  .cat-Setpoint { background: #4c1d95; color: #ddd6fe; }
  .cat-Status { background: #374151; color: #d1d5db; }
  .cat-CO2 { background: #581c87; color: #e9d5ff; }
}
.pagination { display: flex; align-items: center; justify-content: center; gap: 8px; padding: 12px; }
.page-info { font-size: 0.85rem; color: var(--muted); }
.empty { text-align: center; padding: 48px 16px; color: var(--muted); }
.empty-icon { font-size: 2rem; margin-bottom: 8px; }
.toast { position: fixed; bottom: 20px; right: 20px; background: #1a1d23; color: white; padding: 10px 20px; border-radius: 8px; font-size: 0.85rem; opacity: 0; transform: translateY(10px); transition: all 0.3s; z-index: 100; }
.toast.show { opacity: 1; transform: translateY(0); }
@media (prefers-color-scheme: dark) { .toast { background: #374151; } }
.loading { text-align: center; padding: 24px; color: var(--muted); }
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 200; display: flex; align-items: center; justify-content: center; }
.modal { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; max-width: 560px; width: 90%; max-height: 80vh; overflow-y: auto; }
.modal h2 { font-size: 1.1rem; margin-bottom: 16px; }
.profile-card { border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s, background 0.15s; }
.profile-card:hover { border-color: var(--primary); background: var(--row-hover); }
.profile-name { font-weight: 600; font-size: 0.9rem; }
.profile-desc { font-size: 0.8rem; color: var(--muted); margin-top: 2px; }
.profile-meta { font-size: 0.75rem; color: var(--muted); margin-top: 4px; }
.preview-list { max-height: 200px; overflow-y: auto; border: 1px solid var(--border); border-radius: 6px; margin: 8px 0; }
.preview-item { padding: 6px 12px; border-bottom: 1px solid var(--border); font-size: 0.8rem; }
.preview-item:last-child { border-bottom: none; }
.preview-item-name { font-weight: 500; }
.preview-item-path { color: var(--muted); font-size: 0.75rem; }
.rule-list { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.rule-tag { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; background: var(--tag-bg); border-radius: 12px; font-size: 0.8rem; font-family: "SF Mono", "Cascadia Code", monospace; }
</style>
</head>
<body>

<div class="header">
  <h1>Niagara BMS</h1>
  <div class="stats" id="stats"></div>
</div>

<div class="layout">
  <div class="sidebar">
    <div class="sidebar-header">
      <span>Browse</span>
      <span style="font-size:0.75rem;color:var(--muted)" id="tree-count"></span>
    </div>
    <div class="breadcrumb" id="breadcrumb"></div>
    <div class="group-list" id="tree-list">
      <div class="loading">Loading...</div>
    </div>
  </div>
  <div class="main">
    <div class="toolbar">
      <input type="text" class="search-box" id="search" placeholder="Search points by name or path...">
      <select class="category-select" id="category-filter">
        <option value="">All Categories</option>
      </select>
      <button class="btn btn-sm" id="btn-profiles" title="Point profiles">Profiles</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th style="width:120px">Value</th>
            <th style="width:100px">Category</th>
            <th style="width:80px">Type</th>
            <th>Group</th>
          </tr>
        </thead>
        <tbody id="point-table">
          <tr><td colspan="5" class="loading">Select a group or search</td></tr>
        </tbody>
      </table>
    </div>
    <div class="pagination" id="pagination"></div>
  </div>
</div>

<div class="toast" id="toast"></div>
<div id="modal-root"></div>

<script>
const $ = s => document.querySelector(s);
const BASE = '/api/niagara';
let state = { prefix: '', search: '', category: '', page: 1 };
let debounceTimer;

async function api(path) {
  const url = BASE + (path.startsWith('/') ? path : '/' + path);
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(res.statusText);
  return res.json();
}

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2000);
}

async function loadStats() {
  try {
    const data = await api('/stats');
    if (data.total === 0) {
      $('#stats').innerHTML = '';
      $('#tree-list').innerHTML = '<div class="empty" style="padding:24px"><div class="empty-icon">&#9203;</div><div>Waiting for point discovery&hellip;</div><div style="font-size:0.8rem;margin-top:8px">Configure the integration in Settings and restart.</div></div>';
      return;
    }
    $('#stats').innerHTML = '<div class="stat"><div class="stat-value">' + data.total.toLocaleString() + '</div><div class="stat-label">Total Points</div></div>';
    renderCategories(data.categories || []);
  } catch (e) {
    $('#tree-list').innerHTML = '<div class="empty" style="padding:24px"><div class="empty-icon">&#9888;</div><div>Could not load points</div><div style="font-size:0.8rem;margin-top:8px">' + esc(e.message) + '</div></div>';
  }
}

async function loadTree() {
  try {
    const data = await api('/tree?prefix=' + encodeURIComponent(state.prefix));
    renderBreadcrumb();
    renderTree(data.children);
  } catch (e) {
    $('#tree-list').innerHTML = '<div class="empty" style="padding:24px">Failed to load tree</div>';
  }
}

function renderBreadcrumb() {
  const bc = $('#breadcrumb');
  if (!state.prefix) { bc.innerHTML = '<span class="breadcrumb-current">All Devices</span>'; return; }
  const parts = state.prefix.split('/');
  let html = '<span class="breadcrumb-seg" data-nav="">All</span>';
  for (let i = 0; i < parts.length; i++) {
    html += '<span class="breadcrumb-sep">&#9656;</span>';
    const partial = parts.slice(0, i + 1).join('/');
    if (i === parts.length - 1) {
      html += '<span class="breadcrumb-current">' + esc(parts[i]) + '</span>';
    } else {
      html += '<span class="breadcrumb-seg" data-nav="' + esc(partial) + '">' + esc(parts[i]) + '</span>';
    }
  }
  bc.innerHTML = html;
  bc.querySelectorAll('.breadcrumb-seg').forEach(el => {
    el.addEventListener('click', () => navigateTo(el.dataset.nav));
  });
}

function renderTree(children) {
  const el = $('#tree-list');
  $('#tree-count').textContent = children.length + ' items';
  if (children.length === 0) { el.innerHTML = '<div class="empty" style="padding:24px">No sub-items here</div>'; return; }
  el.innerHTML = children.map(c => {
    return '<div class="tree-item" data-path="' + esc(c.path) + '">' +
      '<span class="tree-item-name" title="' + esc(c.name) + '">' + esc(c.name) + '</span>' +
      '<div class="tree-item-right">' +
        '<span class="group-badge badge-total">' + c.total.toLocaleString() + '</span>' +
        (c.has_children ? '<span class="tree-arrow">&#9656;</span>' : '') +
      '</div></div>';
  }).join('');
  el.querySelectorAll('.tree-item').forEach(item => {
    item.addEventListener('click', () => navigateTo(item.dataset.path));
  });
}

function renderCategories(categories) {
  const sel = $('#category-filter');
  sel.innerHTML = '<option value="">All Categories</option>' +
    categories.map(c => '<option value="' + esc(c.name) + '"' + (state.category === c.name ? ' selected' : '') + '>' + esc(c.name) + ' (' + c.count.toLocaleString() + ')</option>').join('');
}

function navigateTo(prefix) {
  state.prefix = prefix || '';
  state.page = 1;
  loadTree();
  loadPoints();
}

async function loadPoints() {
  const params = new URLSearchParams();
  if (state.prefix) params.set('prefix', state.prefix);
  if (state.search) params.set('search', state.search);
  if (state.category) params.set('category', state.category);
  params.set('page', state.page);

  let data;
  try { data = await api('/points?' + params); }
  catch (e) { $('#point-table').innerHTML = '<tr><td colspan="5"><div class="empty"><div class="empty-icon">&#9888;</div>Failed to load points</div></td></tr>'; return; }

  if (data.points.length === 0) {
    $('#point-table').innerHTML = '<tr><td colspan="5"><div class="empty"><div class="empty-icon">&#128269;</div>No points found' + (state.search ? ' matching "' + esc(state.search) + '"' : '') + '</div></td></tr>';
    $('#pagination').innerHTML = '';
    return;
  }

  $('#point-table').innerHTML = data.points.map(p => {
    return '<tr>' +
      '<td><div>' + esc(p.name) + '</div><div class="point-path">' + esc(p.path) + '</div></td>' +
      '<td><span class="point-value point-value-na" data-value-path="' + esc(p.path) + '">&mdash;</span></td>' +
      '<td><span class="cat-badge cat-' + (p.category || 'Other') + '">' + esc(p.category || 'Other') + '</span></td>' +
      '<td><span class="point-type">' + esc(p.type) + '</span></td>' +
      '<td>' + esc(p.group) + '</td></tr>';
  }).join('');

  loadValues(data.points.map(p => p.path));

  const { page, pages, total } = data;
  let pagHtml = '<span class="page-info">' + total.toLocaleString() + ' points &mdash; page ' + page + ' of ' + pages + '</span>';
  if (pages > 1) {
    pagHtml += '<button class="btn btn-sm" ' + (page <= 1 ? 'disabled' : '') + ' id="prev-page">&laquo; Prev</button>';
    pagHtml += '<button class="btn btn-sm" ' + (page >= pages ? 'disabled' : '') + ' id="next-page">Next &raquo;</button>';
  }
  $('#pagination').innerHTML = pagHtml;
  if ($('#prev-page')) $('#prev-page').onclick = () => { state.page--; loadPoints(); };
  if ($('#next-page')) $('#next-page').onclick = () => { state.page++; loadPoints(); };
}

let valueRefreshTimer;
async function loadValues(paths) {
  try {
    const params = new URLSearchParams();
    paths.forEach(p => params.append('paths[]', p));
    const vals = await api('/values?' + params);
    document.querySelectorAll('[data-value-path]').forEach(el => {
      const v = vals[el.dataset.valuePath];
      if (v !== undefined) { el.textContent = v; el.classList.remove('point-value-na'); }
    });
  } catch (e) {}
  clearTimeout(valueRefreshTimer);
  valueRefreshTimer = setTimeout(() => {
    const visible = document.querySelectorAll('[data-value-path]');
    if (visible.length > 0) loadValues(Array.from(visible).map(el => el.dataset.valuePath));
  }, 10000);
}

function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

function closeModal() { $('#modal-root').innerHTML = ''; }

$('#search').addEventListener('input', e => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => { state.search = e.target.value; state.page = 1; loadPoints(); }, 300);
});

$('#category-filter').addEventListener('change', e => {
  state.category = e.target.value; state.page = 1; loadPoints();
});

$('#btn-profiles').addEventListener('click', async () => {
  const data = await api('/profiles');
  let html = '<div class="modal-backdrop" onclick="if(event.target===this)closeModal()"><div class="modal"><h2>Point Profiles</h2><p style="font-size:0.85rem;color:var(--muted);margin-bottom:16px">Browse pre-built profiles to see which points match common BMS categories.</p>';
  for (const p of data.profiles) {
    html += '<div class="profile-card" data-profile="' + esc(p.id) + '"><div class="profile-name">' + esc(p.name) + '</div><div class="profile-desc">' + esc(p.description) + '</div><div class="profile-meta">' + p.pattern_count + ' patterns</div></div>';
  }
  html += '</div></div>';
  $('#modal-root').innerHTML = html;
  document.querySelectorAll('.profile-card').forEach(card => {
    card.addEventListener('click', () => showProfilePreview(card.dataset.profile));
  });
});

async function showProfilePreview(profileId) {
  const data = await api('/profiles/' + profileId + '/preview');
  let html = '<div class="modal-backdrop" onclick="if(event.target===this)closeModal()"><div class="modal"><h2>' + esc(data.profile) + '</h2><p style="font-size:0.85rem;margin-bottom:8px"><strong>' + data.would_enable + '</strong> points match this profile.</p>';
  if (data.sample.length > 0) {
    html += '<div class="preview-list">';
    for (const s of data.sample) {
      html += '<div class="preview-item"><div class="preview-item-name">' + esc(s.name) + '</div><div class="preview-item-path">' + esc(s.path) + '</div></div>';
    }
    if (data.would_enable > data.sample.length) html += '<div class="preview-item" style="text-align:center;color:var(--muted)">&hellip; and ' + (data.would_enable - data.sample.length) + ' more</div>';
    html += '</div>';
  }
  html += '<h3 style="font-size:0.95rem;margin:16px 0 8px">Patterns</h3><div class="rule-list">';
  for (const pat of data.patterns) html += '<span class="rule-tag">' + esc(pat) + '</span>';
  html += '</div><div style="display:flex;gap:8px;margin-top:16px"><button class="btn" onclick="closeModal()">Close</button></div></div></div>';
  $('#modal-root').innerHTML = html;
}

loadStats();
loadTree();
loadPoints();
</script>
</body>
</html>"""
