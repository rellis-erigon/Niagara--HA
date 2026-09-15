# Niagara BMS Bridge

Connect your Tridium Niagara 4 Building Management System to Home Assistant.

This add-on reads points from a Niagara station via the oBIX REST interface and publishes them as Home Assistant entities through MQTT Discovery.

## Prerequisites

### On the Niagara station

1. **Enable the oBIX servlet** — In Workbench, navigate to the station's `WebService` and enable `ObixServlet`. This exposes the oBIX REST API at `https://<station>/obix/`.

2. **Create a user account** — Add a user (e.g. `obix_reader`) with a role that has read permissions on the points you want to expose. For read-only access, a viewer role is sufficient.

3. **Enable HTTPS** — The station should be serving HTTPS (the default on port 443). HTTP works but is not recommended.

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
- **SSL errors** — Set `verify_ssl` to `false` if the Niagara station uses a self-signed certificate.
- **Too many entities** — Use the `point_filter` option to limit discovery to a specific path in the Niagara tree.
- **Stale values** — Decrease `poll_interval_seconds` for more frequent updates (minimum 5 seconds).
