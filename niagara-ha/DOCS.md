# Niagara BMS Bridge (MQTT Add-on)

Connect your Tridium Niagara 4 Building Management System to Home Assistant via oBIX and MQTT Discovery.

This add-on reads points from a Niagara station via the oBIX REST interface and publishes them as Home Assistant entities through MQTT Discovery. It includes a web UI for managing which points are published.

> **New to Niagara BMS for Home Assistant?** Consider the [Native Integration (v2.0)](https://github.com/rellis-erigon/Niagara--HA/blob/main/docs/integration-guide.md) instead — it connects directly to your station with no MQTT broker required.

---

## Prerequisites

### On the Niagara Station

Complete these steps in Niagara Workbench. No paid modules or licenses are required. See the full [Niagara Setup Guide](https://github.com/rellis-erigon/Niagara--HA/blob/main/docs/niagara-setup.md) for detailed instructions.

**Step 1 — Enable HTTPS**

1. Open **Station → Services → WebService**
2. Open the **AX Property Sheet** view
3. Turn on **HTTPS** and set the port (default `443`)

**Step 2 — Install the oBIX Network Driver**

1. Navigate to **Station → Config → Drivers**
2. Click **New** → set Type to `Obix Network`

**Step 3 — Add HTTPBasicScheme Authentication**

1. Navigate to **Station → Services → AuthenticationService → AuthenticationSchemes**
2. From the Palette, add **baja → WebServicesSchemes → HTTPBasicScheme**

**Step 4 — Create an oBIX User Account**

1. Navigate to **Station → Services → UserService**
2. Duplicate the Admin user, rename to `obixUser`
3. Set a password and change **AuthenticationSchemeName** to `HTTPBasicScheme`

### On Home Assistant

1. **Install Mosquitto MQTT broker** — Settings → Add-ons → Add-on Store → Mosquitto broker
2. **Enable MQTT integration** — Settings → Devices & Services → Add Integration → MQTT

---

## Configuration

| Option | Default | Description |
|---|---|---|
| `niagara_host` | *(required)* | IP or hostname of the Niagara station |
| `niagara_port` | `443` | HTTPS port |
| `niagara_user` | *(required)* | oBIX user account |
| `niagara_password` | *(required)* | Password |
| `use_https` | `true` | Use HTTPS connection |
| `verify_ssl` | `false` | Verify SSL cert (set `false` for self-signed) |
| `poll_interval_seconds` | `30` | Polling interval in seconds (5–3600) |
| `poll_workers` | `5` | Concurrent polling workers (1–20) |
| `point_filter` | *(empty)* | Path prefix filter (e.g., `/config/AHU/`) |
| `device_name` | `Niagara BMS` | Prefix for HA device names |
| `device_depth` | `0` | Folder depth for device grouping (0 = immediate parent) |
| `mqtt_host` | *(auto)* | MQTT broker host (auto-detected from Mosquitto) |
| `mqtt_port` | `1883` | MQTT broker port |
| `mqtt_user` | *(auto)* | MQTT username (auto-detected) |
| `mqtt_password` | *(auto)* | MQTT password (auto-detected) |
| `mqtt_topic_prefix` | `niagara` | MQTT topic prefix |
| `auto_enable_patterns` | `[]` | Glob patterns to auto-enable points |
| `log_level` | `info` | Logging level (debug, info, warning, error) |

---

## How It Works

1. The add-on connects to the Niagara station over HTTPS using Basic authentication
2. It walks the oBIX point tree starting from `/config/` (or your filter path)
3. Discovered points are saved to `points.yaml` — all disabled by default
4. Use the web UI to enable the points you want published to Home Assistant
5. Enabled points are published via MQTT Discovery so entities appear automatically in HA
6. On each poll cycle, the add-on reads updated values and publishes changes via MQTT

### oBIX Watch Mode

The add-on automatically uses oBIX Watch when available:
- **Watch mode:** A single HTTP request per poll cycle retrieves only changed values — much faster for large sites
- **Legacy mode:** Falls back to individual point reads with concurrent workers if Watch is not available

### Data Files

Stored under `/config/niagara-ha/`:

| File | Purpose |
|---|---|
| `points.yaml` | Discovered points with enabled/disabled status and custom names |
| `auto_enable_rules.yaml` | Glob patterns for auto-enabling points |
| `device_folders.yaml` | Custom device folder selections |
| `values.json` | Cached values for instant restore on restart |

---

## Web UI

Access via **Settings → Add-ons → Niagara BMS Bridge → Open Web UI** or the sidebar panel.

### Features

- **Tree browser** — navigate the Niagara folder hierarchy
- **Point table** — view, search, and filter all discovered points
- **Per-point toggles** — enable/disable individual points
- **Live values** — auto-refreshing every 10 seconds
- **Bulk operations** — enable/disable all, filtered, or by group
- **Profiles** — five built-in profiles (HVAC, Energy, Alarms, Lighting, Zone Comfort)
- **Auto-enable rules** — glob patterns for automatic point enabling
- **Device folders** — set folders as HA devices for custom grouping
- **Point renaming** — set custom entity names
- **Category badges** — color-coded classification for easy identification

See the full [Add-on Guide](https://github.com/rellis-erigon/Niagara--HA/blob/main/docs/addon-guide.md) for detailed usage instructions.

---

## Entity Mapping

| Niagara Point Type | HA Entity | Auto-detected |
|---|---|---|
| NumericPoint / NumericWritable | `sensor` | Unit, device class, state class, icon |
| BooleanPoint / BooleanWritable | `binary_sensor` | Device class, icon |
| EnumPoint | `sensor` | Options list, icon |
| StringPoint | `sensor` | Icon |

See the full [Entity Mapping Reference](https://github.com/rellis-erigon/Niagara--HA/blob/main/docs/entity-mapping.md) for details.

---

## Troubleshooting

- **No entities appearing:** Check the add-on logs for connection errors. Verify the Niagara host is reachable and oBIX is enabled.
- **Missing points:** Points may need to be exported to oBIX in Niagara Workbench. After exporting, restart the add-on.
- **SSL errors:** Set `verify_ssl` to `false` for self-signed certificates.
- **Too many entities:** Use `point_filter` to limit discovery, or use the web UI to selectively enable points.
- **Stale values:** Decrease `poll_interval_seconds` for more frequent updates.
- **High memory:** The add-on includes a fast YAML parser for large sites (130K+ points at ~89% less memory).

See the full [Troubleshooting Guide](https://github.com/rellis-erigon/Niagara--HA/blob/main/docs/troubleshooting.md) for more solutions.

---

## Migrating to the Native Integration

See the [Migration Guide](https://github.com/rellis-erigon/Niagara--HA/blob/main/docs/migration.md) for step-by-step instructions on moving to the native HA integration (v2.0).
