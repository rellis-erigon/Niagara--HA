# Niagara BMS Bridge for Home Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![HA Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-blue.svg)](https://www.home-assistant.io/addons/)
[![Niagara 4](https://img.shields.io/badge/Tridium-Niagara%204-orange.svg)](https://www.tridium.com/)
[![Version](https://img.shields.io/badge/version-0.2.1-blue.svg)](niagara-ha/CHANGELOG.md)

> Connect your Tridium Niagara 4 Building Management System to Home Assistant — no BMS-side changes beyond enabling oBIX.

This add-on reads points from a Niagara station via the **oBIX REST interface** and publishes them as Home Assistant entities through **MQTT Discovery**. Temperature sensors, fan statuses, damper positions, operating modes — anything in the Niagara point tree shows up in HA automatically.

---

## How It Works

```
┌──────────────────┐     HTTPS      ┌──────────────────┐
│  Niagara 4       │◄───Basic Auth───│  Niagara-HA      │
│  Station         │    (oBIX REST)  │  Add-on          │
│  (oBIX servlet)  │───XML points──►│  (Docker)        │
└──────────────────┘                 └────────┬─────────┘
                                              │ MQTT
                                              ▼
                                     ┌──────────────────┐
                                     │  Home Assistant   │
                                     │  MQTT Discovery   │
                                     │  ──────────────   │
                                     │  sensor.*         │
                                     │  binary_sensor.*  │
                                     └──────────────────┘
```

1. The add-on connects to your Niagara station over HTTPS using Basic auth
2. It discovers all points in the oBIX tree (or a filtered subset)
3. Points are grouped by their Niagara hierarchy — each group becomes a separate HA device
4. You choose which points to publish via a selection file (`points.yaml`)
5. MQTT Discovery publishes the enabled entities — they appear in HA automatically
6. A polling loop keeps values updated (configurable interval, default 30s)

---

## Features

- **Auto-discovery** — walks the full Niagara oBIX point tree and finds every readable point
- **Grouped devices** — points are organized into HA devices by their Niagara folder (e.g. "Reports", "AHU", "Boiler")
- **Point selection** — a `points.yaml` file lets you enable/disable individual points before they reach HA
- **Smart entity mapping** — auto-detects units (°F, °C, kW, psi...) and device classes (temperature, power, energy...)
- **Availability tracking** — entities show "unavailable" when the bridge can't reach Niagara, not "unknown"
- **MQTT auto-detection** — picks up the HA Mosquitto broker automatically, with manual override available

---

## Quick Start

### Prerequisites

| Requirement | Details |
|---|---|
| **Home Assistant** | Any recent version with Supervisor |
| **MQTT Broker** | Install the [Mosquitto broker add-on](https://github.com/home-assistant/addons/tree/master/mosquitto) |
| **Niagara 4 Station** | oBIX servlet enabled, user account created |
| **Network** | HA must reach the Niagara station on its HTTPS port |

### Install

1. **Add the repository** to Home Assistant:
   - Go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
   - Paste: `https://github.com/rellis-erigon/niagara--ha`

2. **Install** "Niagara BMS Bridge" from the store

3. **Configure** the add-on:

   | Setting | Example |
   |---|---|
   | Niagara Host | `192.168.1.100` |
   | Port | `443` |
   | Username | `obix_reader` |
   | Password | `••••••••` |
   | Poll Interval | `30` seconds |
   | Point Filter | `/config/AHU/` (optional) |

4. **Start** the add-on — it will discover all points and write the selection file

5. **Select your points** — edit `/config/niagara-ha/points.yaml` (via File Editor add-on or SSH), set `enabled: true` on the points you want, then restart the add-on

### Point Selection

On first start, the add-on discovers every point on the Niagara station and writes them to `/config/niagara-ha/points.yaml`. **All points are disabled by default** — you choose which ones to publish to Home Assistant.

The file is organized by group and looks like this:

```yaml
points:
- enabled: false
  group: AHU
  name: SupplyAirTemp
  path: /config/AHU/SupplyAirTemp/out
  type: numeric
- enabled: false
  group: AHU
  name: FanStatus
  path: /config/AHU/FanStatus/out
  type: boolean
- enabled: false
  group: Reports
  name: OutdoorTemp
  path: /config/Reports/OutdoorTemp/out
  type: numeric
```

Set `enabled: true` on the points you want, save the file, and restart the add-on. New points found on subsequent restarts are added as disabled — your existing selections are preserved.

### Grouped Devices

Points are automatically grouped by their top-level Niagara folder. Each group becomes a separate device in Home Assistant:

- **Niagara BMS — AHU** (all points under `/config/AHU/`)
- **Niagara BMS — Reports** (all points under `/config/Reports/`)
- **Niagara BMS — Boiler** (all points under `/config/Boiler/`)

You can customize the base name with the **Device Name** option (default: "Niagara BMS").

### Niagara-Side Setup (One-Time)

Four steps in Workbench to prepare your Niagara 4 station for oBIX communication. No paid modules or licenses required.

#### Step 1 — Enable HTTPS

oBIX requires HTTPS on the Niagara station.

1. Open **Station → Services → WebService**
2. Open the **AX Property Sheet** view
3. Turn on **HTTPS**
4. Set the **HTTPS port** (default `443`)

#### Step 2 — Install the oBIX Network Driver

The station needs the oBIX driver to serve oBIX requests.

1. Navigate to **Station → Config → Drivers**
2. Click **New**
3. Set **Type to Add** = `Obix Network`

#### Step 3 — Add HTTPBasicScheme Authentication

oBIX requires a non-default authentication scheme.

1. Navigate to **Station → Services → AuthenticationService → AuthenticationSchemes**
2. Open the **Palette** (sidebar)
3. Browse to **baja → AuthenticationSchemes → WebServicesSchemes → HTTPBasicScheme**
4. **Add** it to the AuthenticationSchemes list

#### Step 4 — Create an oBIX User Account

The add-on needs a dedicated user with admin rights to read all points.

1. Navigate to **Station → Services → UserService**
2. **Duplicate** the Admin user
3. Rename the new user to `obixUser` (or any name you prefer)
4. Open the new user's **AX Property Sheet**
5. Set a **password**
6. Change **AuthenticationSchemeName** to `HTTPBasicScheme`

Use this username and password in the add-on configuration.

> **Tip:** For more details, see the full [add-on documentation](niagara-ha/DOCS.md).

---

## Entity Mapping

All discoverable Niagara points are automatically mapped:

| Niagara Point Type | HA Entity | Auto-detected |
|---|---|---|
| NumericPoint / NumericWritable | `sensor` | Unit (°F, °C, kW, psi...), device class (temperature, power, energy...) |
| BooleanPoint / BooleanWritable | `binary_sensor` | On/off state |
| EnumPoint | `sensor` | Options list from Niagara range |
| IntegerPoint | `sensor` | Unit if available |
| StringPoint | `sensor` | — |

Points are named using their Niagara tree path (e.g. `AHU-1 / Supply Air Temp`) and given stable unique IDs so they survive restarts and rediscovery.

---

## Configuration Reference

### Add-on Options

| Option | Default | Description |
|---|---|---|
| `niagara_host` | *(required)* | IP or hostname of the Niagara station |
| `niagara_port` | `443` | HTTPS port |
| `niagara_user` | *(required)* | oBIX user account |
| `niagara_password` | *(required)* | Password |
| `use_https` | `true` | Use HTTPS (recommended) |
| `verify_ssl` | `false` | Verify SSL certificate |
| `poll_interval_seconds` | `30` | Polling interval (5–3600 seconds) |
| `point_filter` | *(empty)* | Path prefix to limit discovery |
| `device_name` | `Niagara BMS` | Base name for HA devices |
| `mqtt_host` | *(auto-detect)* | Override MQTT broker host |
| `mqtt_port` | `1883` | MQTT broker port |
| `mqtt_user` | *(auto-detect)* | Override MQTT username |
| `mqtt_password` | *(auto-detect)* | Override MQTT password |
| `mqtt_topic_prefix` | `niagara` | Topic prefix for all MQTT messages |
| `log_level` | `info` | `debug`, `info`, `warning`, `error` |

### Files

| File | Location | Purpose |
|---|---|---|
| `points.yaml` | `/config/niagara-ha/points.yaml` | Point selection — enable/disable individual points |
| `options.json` | `/data/options.json` | Add-on config (managed by HA UI) |

---

## Roadmap

This project is built in phases. Phase 1 is the current release.

| Phase | Status | What it adds |
|---|---|---|
| **Phase 1** — Read-only bridge | **Current** | oBIX discovery, grouped devices, point selection, sensor entities, MQTT Discovery, polling |
| **Phase 2** — Control & history | Planned | Write support, history queries, alarms, oBIX Watch (real-time), BACnet option |
| **Phase 3** — Intelligence | Planned | Multi-station, Niagara MQTT connector, schedules, energy dashboard, Haystack tags |

---

## Troubleshooting

**No entities appearing?**
Check the add-on log for connection errors. Verify the Niagara host is reachable and oBIX is enabled. Make sure you've enabled points in `/config/niagara-ha/points.yaml` — all points are disabled by default.

**SSL errors?**
Set `verify_ssl` to `false` — most Niagara stations use self-signed certificates.

**Too many entities?**
Use `point_filter` to scope discovery to a specific part of the tree (e.g. `/config/AHU/`). Or disable unwanted points in `points.yaml`.

**Values not updating?**
Lower `poll_interval_seconds` for faster updates. Check that the oBIX user has read permissions on the target points.

**Entities showing "unavailable"?**
The bridge can't reach the Niagara station. Check the add-on logs and verify network connectivity.

**MQTT not connecting?**
By default, the add-on auto-detects the HA Mosquitto broker. If using an external broker, fill in the MQTT host/port/user/password fields in the add-on config.

---

## Tech Stack

- **Protocol:** oBIX (Open Building Information Exchange) over REST/HTTPS
- **Language:** Python 3
- **HA Integration:** MQTT Discovery via [paho-mqtt](https://pypi.org/project/paho-mqtt/)
- **Runtime:** Home Assistant Add-on (Docker + s6-overlay)

---

## Contributing

Contributions welcome. Please open an issue first to discuss what you'd like to change.

## License

[MIT](LICENSE)
