# Niagara BMS for Home Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Niagara 4](https://img.shields.io/badge/Tridium-Niagara%204-orange.svg)](https://www.tridium.com/)
[![Version](https://img.shields.io/badge/version-2.0.0-blue.svg)](custom_components/niagara/manifest.json)

> Connect your Tridium Niagara 4 Building Management System to Home Assistant — no BMS-side changes beyond enabling oBIX.

A native Home Assistant custom integration that reads points from a Niagara station via the **oBIX REST interface** and creates HA entities directly — no MQTT broker required.

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

1. Add the integration via **Settings → Devices & Services → Add Integration**
2. Enter your Niagara station connection details — the integration tests the connection live
3. Points are discovered automatically from the oBIX tree
4. Each Niagara folder becomes a separate HA device, with points as entities
5. HA areas are auto-assigned from the Niagara folder hierarchy
6. A polling coordinator keeps values updated (configurable interval, default 30s)
7. Enable/disable individual entities from the HA UI — no config files to edit

---

## Features

- **Native HA integration** — config flow setup, no MQTT broker or add-on required
- **Auto-discovery** — walks the full Niagara oBIX point tree and finds every readable point
- **oBIX Watch support** — polls for changes with a single HTTP request per cycle instead of one GET per point
- **Grouped devices** — points organized into HA devices by Niagara folder hierarchy
- **Auto area assignment** — Niagara folders map to HA areas automatically
- **Smart entity mapping** — auto-detects units, device classes, state classes, and icons from point names and units
- **Energy dashboard ready** — energy (kWh), water (m³/L), and gas sensors get `total_increasing` state class
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

### Install via HACS

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

   | Setting | Example |
   |---|---|
   | Host | `192.168.1.100` |
   | Port | `443` |
   | Username | `obix_reader` |
   | Password | `••••••••` |
   | Use HTTPS | `true` |
   | Verify SSL | `false` |

4. The integration tests the connection and discovers points
5. Configure device naming, poll interval, and area mapping
6. Done — entities appear in HA immediately

---

## Entity Mapping

| Niagara Point Type | HA Entity | Auto-detected |
|---|---|---|
| NumericPoint / NumericWritable | `sensor` | Unit, device class (temperature, power, energy, pressure, humidity, CO2, voltage, current, frequency, water, gas, battery), state class, icon |
| BooleanPoint / BooleanWritable | `binary_sensor` | Device class (problem, running, opening, occupancy, motion), icon |
| EnumPoint | `sensor` | Options list from Niagara range, icon |
| StringPoint | `sensor` | Icon |

### Energy Dashboard

Energy, water, and gas sensors are automatically configured for the HA Energy dashboard:

| Type | Device Class | State Class | Units |
|---|---|---|---|
| Energy | `energy` | `total_increasing` | kWh, Wh, MWh, GJ, MJ |
| Water | `water` | `total_increasing` | m³, L, gal, ft³ |
| Gas | `gas` | `total_increasing` | m³, ft³, CCF |
| Battery | `battery` | `measurement` | % |

---

## Configuration Options

After setup, adjust settings via **Settings → Devices & Services → Niagara BMS → Configure**:

| Option | Default | Description |
|---|---|---|
| Poll interval | `30` | Seconds between point value updates |
| Device name | `Niagara BMS` | Prefix for HA device names |
| Device grouping depth | `0` (auto) | Folder levels that define a device (0 = full path) |
| Area mapping depth | `1` | Which folder level maps to HA areas |

---

## Niagara-Side Setup (One-Time)

Four steps in Workbench to prepare your Niagara 4 station for oBIX communication.

#### Step 1 — Enable HTTPS

1. Open **Station → Services → WebService**
2. Turn on **HTTPS** and set the port (default `443`)

#### Step 2 — Install the oBIX Network Driver

1. Navigate to **Station → Config → Drivers**
2. Click **New** → set Type to `Obix Network`

#### Step 3 — Add HTTPBasicScheme Authentication

1. Navigate to **Station → Services → AuthenticationService → AuthenticationSchemes**
2. From the Palette, add **baja → WebServicesSchemes → HTTPBasicScheme**

#### Step 4 — Create an oBIX User Account

1. Navigate to **Station → Services → UserService**
2. Duplicate the Admin user, rename to `obixUser`
3. Set a password and change **AuthenticationSchemeName** to `HTTPBasicScheme`

---

## Migrating from the MQTT Add-on

If you were using the previous MQTT-based add-on (v0.6.x):

1. Install the native integration via HACS (see above)
2. Add the integration in **Settings → Devices & Services**
3. Verify your entities appear correctly
4. Remove the old Niagara BMS add-on from **Settings → Add-ons**
5. If you no longer need MQTT for other integrations, you can remove the Mosquitto add-on too

Entity unique IDs have changed, so you'll need to update any automations or dashboard cards that reference specific entity IDs.

---

## Troubleshooting

**No entities appearing?**
Check the HA log for connection errors. Verify the Niagara host is reachable and oBIX is enabled.

**SSL errors?**
Set "Verify SSL" to false — most Niagara stations use self-signed certificates.

**Too many entities?**
Use the point path filter during setup to scope discovery. Or disable unwanted entities in the HA UI.

**Values not updating?**
Lower the poll interval in the integration options. Check that the oBIX user has read permissions.

---

## Tech Stack

- **Protocol:** oBIX (Open Building Information Exchange) over REST/HTTPS
- **Language:** Python 3
- **HA Integration:** Native custom component with DataUpdateCoordinator
- **Polling:** oBIX Watch (single request per cycle) with legacy fallback

---

## Contributing

Contributions welcome. Please open an issue first to discuss what you'd like to change.

## License

[MIT](LICENSE)
