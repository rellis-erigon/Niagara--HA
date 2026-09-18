# Niagara BMS Bridge

Connect your Tridium Niagara 4 Building Management System to Home Assistant.

This add-on reads points from a Niagara station via the oBIX REST interface and publishes them as Home Assistant entities through MQTT Discovery.

## Prerequisites

### On the Niagara station

Complete these four steps in Niagara Workbench. No paid modules or licenses are required.

**Step 1 — Enable HTTPS**

oBIX requires HTTPS on the Niagara station.

1. Open **Station → Services → WebService**
2. Open the **AX Property Sheet** view
3. Turn on **HTTPS**
4. Set the **HTTPS port** (default `443`)

**Step 2 — Install the oBIX Network Driver**

1. Navigate to **Station → Config → Drivers**
2. Click **New**
3. Set **Type to Add** = `Obix Network`

**Step 3 — Export Points to oBIX**

Points must be explicitly exported before oBIX can see them.

1. Navigate to the points you want to expose (e.g. under **Drivers → NiagaraNetwork → {device} → points**)
2. Right-click on each point (or a folder of points) → **Actions → Export**
3. Select **Obix Export** as the export type
4. Repeat for all points/folders you want available in Home Assistant

> **Tip:** You can export an entire folder to expose all points under it at once. Only exported points will be discoverable by this add-on.

**Step 4 — Add HTTPBasicScheme Authentication**

oBIX requires a non-default authentication scheme.

1. Navigate to **Station → Services → AuthenticationService → AuthenticationSchemes**
2. Open the **Palette** (sidebar)
3. Browse to **baja → AuthenticationSchemes → WebServicesSchemes → HTTPBasicScheme**
4. **Add** it to the AuthenticationSchemes list

**Step 5 — Create an oBIX User Account**

1. Navigate to **Station → Services → UserService**
2. **Duplicate** the Admin user
3. Rename the new user to `obixUser` (or any name you prefer)
4. Open the new user's **AX Property Sheet**
5. Set a **password**
6. Change **AuthenticationSchemeName** to `HTTPBasicScheme`

Use this username and password when configuring the add-on.

### On Home Assistant

1. **Install the Mosquitto MQTT broker add-on** — Go to Settings → Add-ons → Add-on Store → Mosquitto broker. This add-on requires MQTT to bridge data into HA.

2. **Enable MQTT integration** — Go to Settings → Devices & Services → Add Integration → MQTT. Use the default broker settings.

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `niagara_host` | *(required)* | IP or hostname of the Niagara station |
| `niagara_port` | `443` | HTTPS port |
| `niagara_user` | *(required)* | oBIX user account |
| `niagara_password` | *(required)* | Password |
| `use_https` | `true` | Use HTTPS connection |
| `verify_ssl` | `false` | Verify SSL cert (disable for self-signed) |
| `poll_interval_seconds` | `30` | Polling interval in seconds |
| `point_filter` | *(empty)* | Path prefix filter (e.g. `/config/AHU/`) |
| `mqtt_topic_prefix` | `niagara` | MQTT topic prefix |
| `log_level` | `info` | Logging level |

## How it works

1. The add-on connects to the Niagara station over HTTPS using Basic authentication.
2. It walks the oBIX point tree starting from `/config/` (or your filter path).
3. Each discovered point is mapped to an HA entity type:
   - `NumericPoint` / `IntegerPoint` → `sensor` (with unit and device class where possible)
   - `BooleanPoint` → `binary_sensor`
   - `EnumPoint` → `sensor` with options
   - `StringPoint` → `sensor`
4. MQTT Discovery messages are published so entities appear automatically in HA.
5. On each polling interval, the add-on reads updated values and publishes them.

## Troubleshooting

- **No entities appearing** — Check the add-on logs for connection errors. Verify the Niagara host is reachable from your HA instance and that oBIX is enabled.
- **Missing points** — Points must be exported to oBIX in Niagara Workbench before they can be discovered. See Step 3 above. After exporting, restart the add-on to trigger a rescan.
- **SSL errors** — Set `verify_ssl` to `false` if the Niagara station uses a self-signed certificate.
- **Too many entities** — Use the `point_filter` option to limit discovery to a specific path in the Niagara tree.
- **Stale values** — Decrease `poll_interval_seconds` for more frequent updates (minimum 5 seconds).
