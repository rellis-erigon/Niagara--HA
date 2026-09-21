# Niagara BMS — Home Assistant Integration

## Architecture: 2-Part System

### Add-on (`niagara-ha/`)
The add-on is a Docker container running on HA with s6-overlay. It handles **everything**:
- oBIX connection to Tridium Niagara 4 station
- Point discovery and polling (watch mode or legacy polling)
- Sidebar management UI via ingress (Flask web server on port 8099)
- Device folder management (grouping points into HA devices)
- Enabling/disabling points
- Auto-enable rules and profiles
- MQTT publishing (optional/legacy)

**Key files:**
- `niagara-ha/src/main.py` — Main loop: connects to Niagara via oBIX, discovers points, polls values, writes to `values.json`
- `niagara-ha/src/web.py` — Flask web UI + REST API. Serves sidebar panel and integration API endpoints
- `niagara-ha/src/obix_client.py` — oBIX protocol client (HTTP/HTTPS, XML parsing, watch subscriptions)
- `niagara-ha/src/point_manager.py` — Point selection persistence (YAML), device folder logic, auto-enable rules
- `niagara-ha/src/mqtt_publisher.py` — Optional MQTT publishing (legacy mode)
- `niagara-ha/static/index.html` — Sidebar panel UI (vanilla JS)
- `niagara-ha/config.yaml` — HA add-on metadata, options schema
- `niagara-ha/Dockerfile` — Container build (Python 3.11, Alpine, s6-overlay)

**Integration API endpoints (served by web.py):**
- `GET /api/integration/points` — Full point list with metadata (type, unit, group, value, writable, enum_range). Only returns enabled points. Used by HACS integration for entity creation.
- `GET /api/integration/values` — Lightweight path:value pairs for all enabled points. Returns null for unpolled points. Used by HACS integration for polling.

**Data files (persisted in `/config/niagara-ha/`):**
- `points.yaml` — All discovered points with enabled/disabled state, group assignments, metadata
- `values.json` — Current point values (updated each poll cycle by main.py)
- `device_folders.yaml` — User-selected folders that become HA devices
- `auto_enable_rules.yaml` — Glob patterns for auto-enabling newly discovered points

### HACS Integration (`custom_components/niagara/`)
Lightweight client that polls the add-on's REST API and creates native HA entities. **No direct Niagara connection, no management UI.**

**Key files:**
- `custom_components/niagara/coordinator.py` — `DataUpdateCoordinator` that polls add-on API via persistent aiohttp session. Re-fetches full point list every 10th cycle.
- `custom_components/niagara/entity.py` — Base entity class. All entities disabled by default (`_attr_entity_registry_enabled_default = False`). Device grouping based on `point.group` from add-on.
- `custom_components/niagara/sensor.py` — Numeric, enum, and string sensors. Has unit mapping, device class inference from unit/name patterns, unit validation (`VALID_UNITS_FOR_CLASS`).
- `custom_components/niagara/binary_sensor.py` — Boolean points. Device class inferred from name patterns (alarm, fan, pump, etc).
- `custom_components/niagara/config_flow.py` — Single-step config: addon_url, device_name, scan_interval, area_depth
- `custom_components/niagara/const.py` — Constants. `DEFAULT_ADDON_URL = "http://a]_niagara-ha:8099"`

## Key Design Decisions

1. **All entities start disabled** — Niagara stations can have 10,000+ points. Users enable what they need via the add-on sidebar or HA entity registry.

2. **Device folders** — User selects folders in the add-on's tree view as "devices". All points under a folder get the same `group` value, creating one HA device with multiple entities.

3. **Path parsing** — Niagara paths contain boilerplate segments (`config`, `Drivers`, `NiagaraNetwork`, `ObixNetwork`, `points`, `out`, `exports`, `obix`). These are stripped by `SKIP_SEGMENTS` / `parse_path_segments()`. Both add-on and integration use the same set.

4. **Niagara name encoding** — Niagara uses `$XX` hex escapes (e.g., `$2d` = `-`, `$2e` = `.`). `decode_niagara_name()` converts these to readable characters.

5. **Unit normalization** — oBIX returns units like `obix:units/kilowatt_hours`. `_normalize_unit()` in `obix_client.py` strips the prefix and maps to HA-compatible units. `sensor.py` validates unit/device_class compatibility.

## Current Versions
- Add-on: **1.1.0** (`niagara-ha/config.yaml`)
- HACS Integration: **2.2.0** (`custom_components/niagara/manifest.json`)

## Known Issues / TODO

- **Add-on URL default** may need validation: `DEFAULT_ADDON_URL = "http://a]_niagara-ha:8099"` — the `]` looks like a typo, should likely be `http://a0d7b954-niagara-ha:8099` or whatever the actual HA add-on hostname is. Users override this in config flow.
- **MQTT is legacy** — still in config.yaml options but optional. Add-on works without it.
- **No writable entity support yet** — points marked `writable: true` are read-only in HA. Future: add `number`, `select`, `switch` platforms for writable points.
- **Integration doesn't dynamically add/remove entities** — re-fetches point list every 10th poll but doesn't create new entities or remove old ones mid-session. Requires HA reload.

## Development Notes

- Branch: `claude/loving-mayer-gxw5e7` (synced with `main`)
- Add-on runs on HA as a local add-on (repo added to HA add-on store)
- HACS integration installed via HACS custom repository
- User's Niagara station has thousands of points — performance matters
- `point_manager.py` uses a fast line-by-line YAML parser (`_fast_load_points_yaml`) instead of `yaml.safe_load` for the potentially 100K+ line points.yaml
