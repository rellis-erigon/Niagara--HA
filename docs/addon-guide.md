# MQTT Add-on Guide (v0.6.x)

The Niagara BMS Bridge add-on connects to your Niagara 4 station via oBIX and publishes points to Home Assistant through MQTT Discovery. It includes a full-featured web UI for managing which points are published.

> **Note:** For new installations, consider the [Native Integration](integration-guide.md) instead — it requires no MQTT broker and has simpler setup. The add-on is best for large sites that need fine-grained point selection, auto-enable rules, or built-in profiles.

---

## Prerequisites

1. **Niagara station** with oBIX enabled (see [Niagara Setup Guide](niagara-setup.md))
2. **Mosquitto MQTT broker** add-on installed in Home Assistant:
   - Go to **Settings → Add-ons → Add-on Store**
   - Install **Mosquitto broker**
   - Start the add-on
3. **MQTT integration** enabled:
   - Go to **Settings → Devices & Services → Add Integration → MQTT**
   - Use the default broker settings (auto-discovered from Mosquitto)

---

## Installation

### As a Home Assistant Add-on

1. Go to **Settings → Add-ons → Add-on Store**
2. Click the three-dot menu → **Repositories**
3. Add this repository URL: `https://github.com/rellis-erigon/Niagara--HA`
4. Find **Niagara BMS Bridge** in the store → click **Install**
5. Configure the add-on (see below)
6. Start the add-on

---

## Configuration

Configure the add-on in **Settings → Add-ons → Niagara BMS Bridge → Configuration**:

### Connection Settings

| Option | Default | Description |
|---|---|---|
| `niagara_host` | *(required)* | IP or hostname of the Niagara station |
| `niagara_port` | `443` | HTTPS port on the station |
| `niagara_user` | *(required)* | oBIX user account |
| `niagara_password` | *(required)* | Password for the oBIX user |
| `use_https` | `true` | Use HTTPS for the oBIX connection |
| `verify_ssl` | `false` | Verify the SSL certificate (set `false` for self-signed) |

### Polling Settings

| Option | Default | Description |
|---|---|---|
| `poll_interval_seconds` | `30` | How often to poll for value changes (5–3600 seconds) |
| `poll_workers` | `5` | Number of concurrent polling workers (1–20). Higher values poll faster on large sites but use more resources |
| `point_filter` | *(empty)* | Path prefix to limit discovery (e.g., `/config/AHU/`). Empty discovers the entire tree |

### Device Settings

| Option | Default | Description |
|---|---|---|
| `device_name` | `Niagara BMS` | Prefix for device names in Home Assistant |
| `device_depth` | `0` | Folder levels for device grouping. `0` = immediate parent folder |

### MQTT Settings

| Option | Default | Description |
|---|---|---|
| `mqtt_host` | *(auto-detected)* | MQTT broker host. Leave empty to use the HA Mosquitto add-on |
| `mqtt_port` | `1883` | MQTT broker port |
| `mqtt_user` | *(auto-detected)* | MQTT username. Leave empty for auto-detection |
| `mqtt_password` | *(auto-detected)* | MQTT password. Leave empty for auto-detection |
| `mqtt_topic_prefix` | `niagara` | Prefix for all MQTT topics |

### Advanced Settings

| Option | Default | Description |
|---|---|---|
| `auto_enable_patterns` | `[]` | Glob patterns to auto-enable newly discovered points (e.g., `["*Temp*", "*Fan*"]`) |
| `log_level` | `info` | Logging level: `debug`, `info`, `warning`, `error` |

---

## How It Works

1. **Discovery:** On startup, the add-on walks the Niagara oBIX point tree and saves all discovered points to `points.yaml`
2. **Point selection:** Use the web UI to choose which points to publish (all points start disabled)
3. **MQTT Discovery:** Enabled points are published as HA entities via MQTT Discovery messages
4. **Polling:** The add-on polls for value changes at the configured interval and publishes updates via MQTT
5. **Hot-reload:** Changes to point selections take effect within one poll cycle — no restart needed

### oBIX Watch vs Legacy Polling

The add-on automatically detects if your Niagara station supports the oBIX Watch service:

- **Watch mode** (preferred): Creates a Watch object and polls for changes with a single HTTP request per cycle. Much more efficient for large point counts.
- **Legacy mode** (fallback): Reads each point individually with configurable concurrent workers.

The add-on logs which mode is active on startup.

### Value Caching

Point values are cached in `values.json`. On restart, entities immediately show the last known value instead of "unavailable."

---

## Web UI

The add-on includes a full-featured web interface for managing points. Access it from **Settings → Add-ons → Niagara BMS Bridge → Open Web UI** (or click the sidebar panel).

### Browse Tree

The left sidebar shows the Niagara folder hierarchy:

- Click folders to navigate deeper into the tree
- Use the breadcrumb trail to navigate back up
- Each folder shows its total point count
- Click **"Use as Device"** on any folder to group its points into a single HA device

### Point Table

The main area shows a table of all discovered points with:

| Column | Description |
|---|---|
| **Name** | Point name (click the pencil icon to rename) |
| **Toggle** | Enable/disable switch for publishing to HA |
| **Value** | Live value, auto-refreshes every 10 seconds |
| **Category** | Auto-classified type (Temperature, Fan, Pump, etc.) |
| **Type** | oBIX value type (numeric, boolean, enum, string) |
| **Group** | The device this point belongs to |

### Search and Filter

- **Search box:** Filter by point name or path
- **Category dropdown:** Show only points of a specific category (Temperature, Fan, Pressure, etc.)
- **Status filter:** Show All / Enabled / Disabled points

### Bulk Operations

- **Enable All / Disable All:** Toggle every discovered point at once
- **Enable Filtered / Disable Filtered:** Toggle only the points matching current filters
- **Group toggle:** Enable/disable all points in a folder

### Profiles

Five built-in profiles for common BMS monitoring scenarios:

| Profile | What It Enables |
|---|---|
| **HVAC Monitoring** | Temperatures, fans, dampers, valves, AHU/FCU/VAV points, VFDs, setpoints |
| **Energy Metering** | Power (kW), energy (kWh), meters, demand, consumption |
| **Alarms Only** | Alarms, faults, trips, emergencies, smoke, fire |
| **Lighting** | Lights, lux sensors, dimmers, lamps, luminaires |
| **Zone Comfort** | Zone temperatures, humidity, CO2, air quality, occupancy |

To apply a profile:

1. Click the **Profiles** button in the toolbar
2. Click a profile to see a preview of matching points
3. Click **Apply** to enable all matching points
4. Optionally check **Save as rules** to auto-enable future matching points

### Auto-Enable Rules

Glob patterns that automatically enable newly discovered points:

1. Click the rules icon in the toolbar
2. Add patterns (e.g., `*Temp*`, `*AHU*Fan*`)
3. Click **Save**
4. Click **Apply to existing** to retroactively enable matching disabled points

Patterns use standard glob syntax: `*` matches any characters, `?` matches a single character.

### Device Folder Selection

Control how points are grouped into HA devices:

1. Browse to a folder in the tree
2. Click **"Use as Device"** — all points under that folder (including subfolders) become one HA device
3. Click **Apply Changes** in the sidebar to recalculate groupings
4. Changes take effect within one poll cycle

---

## File Storage

The add-on stores data under `/config/niagara-ha/`:

| File | Purpose |
|---|---|
| `points.yaml` | All discovered points with enabled/disabled status and custom names |
| `auto_enable_rules.yaml` | Glob patterns for auto-enabling points |
| `device_folders.yaml` | Manually selected device folder paths |
| `values.json` | Cached point values for instant restore on restart |

These files persist across add-on restarts and upgrades.

---

## Troubleshooting

See the [Troubleshooting Guide](troubleshooting.md) for common issues.

Check the add-on logs in **Settings → Add-ons → Niagara BMS Bridge → Log** for detailed error messages. Set `log_level` to `debug` for verbose output.

---

## Next Steps

- [Energy Dashboard Guide](energy-dashboard.md) — set up energy, water, and gas monitoring
- [Entity Mapping Reference](entity-mapping.md) — how points become HA entities
- [Migration Guide](migration.md) — migrating to the native integration
