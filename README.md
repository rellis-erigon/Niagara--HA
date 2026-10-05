# Niagara BMS for Home Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Niagara 4](https://img.shields.io/badge/Tridium-Niagara%204-orange.svg)](https://www.tridium.com/)
[![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)](custom_components/niagara/manifest.json)

> Connect your Tridium Niagara 4 Building Management System to Home Assistant — no BMS-side changes beyond enabling oBIX.

Two ways to integrate, pick the one that fits your setup:

| | **Native Integration (v2.0)** | **MQTT Add-on (v0.6.x)** |
|---|---|---|
| Setup | Config flow in HA UI | Add-on + Mosquitto broker |
| Protocol | Direct oBIX REST | oBIX → MQTT → HA |
| Point management | HA entity registry | Web UI with search, profiles, rules |
| Best for | New installs, simpler setup | Large sites needing fine-grained control |

---

## How It Works

```
┌──────────────────┐     HTTPS      ┌──────────────────┐
│  Niagara 4       │◄───Basic Auth───│  Home Assistant   │
│  Station         │    (oBIX REST)  │                   │
│  (oBIX servlet)  │───XML points──►│  sensor.*         │
└──────────────────┘                 │  binary_sensor.*  │
                                     │  devices & areas  │
                                     └──────────────────┘
```

1. The integration connects to your Niagara station over HTTPS using Basic authentication
2. It walks the oBIX point tree and discovers every readable point
3. Points are mapped to HA entities (sensors and binary sensors) with auto-detected units, device classes, and icons
4. Each Niagara folder becomes a separate HA device, with areas auto-assigned from the hierarchy
5. An oBIX Watch polls for changes efficiently — one HTTP request per cycle instead of one per point

---

## Features

- **Native HA integration** — config flow setup, no MQTT broker or add-on required
- **Auto-discovery** — walks the full Niagara oBIX point tree and finds every readable point
- **oBIX Watch support** — efficient change-only polling with a single HTTP request per cycle
- **Smart entity mapping** — auto-detects units, device classes, state classes, and icons from point names and oBIX units
- **Grouped devices** — points organized into HA devices by Niagara folder hierarchy
- **Auto area assignment** — Niagara folders map to HA areas automatically
- **BMS management panel** — built-in web panel for browsing points, managing device folders, and applying profiles
- **Energy dashboard ready** — energy (kWh), water (m³/L), and gas sensors get `total_increasing` state class automatically
- **Niagara name decoding** — `$2d`, `$2e` hex escapes decoded to readable names
- **Options flow** — change poll interval, device grouping, and area mapping without reconfiguring
- **HACS compatible** — install and update through the HA Community Store

---

## Quick Start

### Prerequisites

| Requirement | Details |
|---|---|
| **Home Assistant** | 2024.1.0 or later |
| **Niagara 4 Station** | oBIX servlet enabled, user account created |
| **Network** | HA must reach the Niagara station on its HTTPS port |

### Install via HACS (Recommended)

1. Open **HACS** → three-dot menu → **Custom repositories**
2. Add `rellis-erigon/Niagara--HA` as category **Integration**
3. Find **Niagara BMS** in HACS → **Download**
4. **Restart Home Assistant**

### Manual Install

1. Copy the `custom_components/niagara/` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant

### Configure

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Niagara** and select **Niagara BMS**
3. Enter your connection details:

   | Setting | Example | Notes |
   |---|---|---|
   | Host | `192.168.1.100` | IP or hostname of Niagara station |
   | Port | `443` | HTTPS port on the station |
   | Username | `obix_reader` | oBIX user account (see [Niagara Setup](#niagara-side-setup)) |
   | Password | `••••••••` | Password for the oBIX user |
   | Use HTTPS | `true` | Almost always true for Niagara |
   | Verify SSL | `false` | Set false for self-signed certs |

4. The integration tests the connection and discovers points automatically
5. Configure device naming, poll interval, and area mapping on the next screen
6. Done — entities appear in HA immediately

---

## Documentation

| Guide | Description |
|---|---|
| [Niagara Setup Guide](docs/niagara-setup.md) | Step-by-step oBIX configuration on your Niagara station |
| [Native Integration Guide](docs/integration-guide.md) | Full guide for the native HA integration (v2.0) |
| [MQTT Add-on Guide](docs/addon-guide.md) | Full guide for the MQTT-based add-on (v0.6.x) |
| [BMS Panel Guide](docs/panel-guide.md) | Using the built-in point management panel |
| [Energy Dashboard Guide](docs/energy-dashboard.md) | Setting up energy, water, and gas monitoring |
| [Entity Mapping Reference](docs/entity-mapping.md) | How Niagara points become HA entities |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and solutions |
| [Migration Guide](docs/migration.md) | Migrating from the MQTT add-on to native integration |
| [Add-on Changelog](niagara-ha/CHANGELOG.md) | Version history for the MQTT add-on |

---

## Entity Mapping (Quick Reference)

| Niagara Point Type | HA Entity | Auto-detected |
|---|---|---|
| NumericPoint / NumericWritable | `sensor` | Unit, device class (temperature, power, energy, pressure, humidity, CO2, voltage, current, frequency, water, gas, battery), state class, icon |
| BooleanPoint / BooleanWritable | `binary_sensor` | Device class (problem, running, opening, occupancy, motion), icon |
| EnumPoint | `sensor` | Options list from Niagara range, icon |
| StringPoint | `sensor` | Icon |

See [Entity Mapping Reference](docs/entity-mapping.md) for the full mapping tables.

---

## Configuration Options

After setup, adjust settings via **Settings → Devices & Services → Niagara BMS → Configure**:

| Option | Default | Description |
|---|---|---|
| Poll interval | `30` | Seconds between point value updates (5–3600) |
| Device name | `Niagara BMS` | Prefix for HA device names |
| Device grouping depth | `0` (auto) | Folder levels that define a device (0 = auto-detect) |
| Area mapping depth | `1` | Which folder level maps to HA areas |

---

## Tech Stack

- **Protocol:** oBIX (Open Building Information Exchange) over REST/HTTPS
- **Language:** Python 3
- **HA Integration:** Native custom component with `DataUpdateCoordinator`
- **Polling:** oBIX Watch (single request per cycle) with legacy fallback
- **Add-on:** Docker container with MQTT Discovery publishing

---

## Contributing

Contributions welcome. Please open an issue first to discuss what you'd like to change.

## License

[MIT](LICENSE)
