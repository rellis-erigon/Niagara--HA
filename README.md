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
                                     │  climate.*        │
                                     │  fan.*            │
                                     │  event.* (alarms) │
                                     │  devices & areas  │
                                     └──────────────────┘
```

1. Add the integration via **Settings → Devices & Services → Add Integration**
2. Enter your Niagara station connection details — the integration tests the connection live
3. Points are discovered automatically from the oBIX tree
4. Each Niagara folder becomes a separate HA device, with points as entities
5. HA areas are auto-assigned from the Niagara folder hierarchy
6. Values arrive as they change: the integration holds a request open at
   the add-on, which answers the moment a reading moves, and sends only
   what moved. The configured interval (default 30s) becomes the floor for
   housekeeping — point discovery, alarms, trend imports — and a safety net
   if the held request fails
7. Enable/disable individual entities from the HA UI — no config files to edit

---

## Two-way control

**The bridge is read-only, by decision rather than by omission.**

A Niagara write holds a priority level until it is released, and a point
held from Home Assistant looks normal in the BMS while the station's own
schedule stops working. On a live building that is not a trade worth
making for dashboard convenience.

[PLAN-TWO-WAY.md](PLAN-TWO-WAY.md) records what it would take, and the
measurement behind the decision: 897 HVAC command points export as
read-only, while the 303 writable points are meter totals and label
strings.

The `climate` and `fan` entities still declare their controllable features,
because Home Assistant omits the setpoint from a climate entity's state
unless `TARGET_TEMPERATURE` is declared — the card would show a bare room
temperature and the data would be invisible. Calling
`climate.set_temperature` or `fan.turn_on` therefore fails with a clear
error rather than silently doing nothing. When two-way control lands, the
setters become real and no entity id or attribute name changes.

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

### Device Mapping

Every point becomes a sensor, as above. A device that has been **typed and
published** in the add-on also becomes the entity its equipment actually is,
because the template has already said which point is the room temperature
and which is the fan:

| Template | HA Entity | Built from |
|---|---|---|
| Fan Coil / Split Unit (`fcu`) | `climate` | Room temperature, setpoint, mode, fan status, start/stop |
| Air Handling Unit (`ahu`) | `climate` + `fan` | Supply air temperature, supply fan status |
| Fan / VFD (`fan`) | `fan` | Run status, speed, start/stop |

This is what gives you the native thermostat card, `current_temperature` in
automations and templates, and exposure to Google Assistant, Alexa and
HomeKit — none of which can do anything useful with a sensor called
`IndoorFanStatus`.

Notes:

- **A stopped unit reports `off`**, not the mode it would run in. A Niagara
  FCU keeps reporting `HEATING` on its mode point while stopped; that is the
  mode it *would* use, so the start/stop point wins.
- **A VFD speed in Hz or rpm is not a percentage.** Only a point reporting
  `%` becomes the fan's percentage; Hz and rpm stay on their own sensor and
  appear as a `speed` attribute.
- **These entities are enabled by default**, unlike the per-point sensors.
  A published device is a deliberate statement that the device is real.
- **Read-only.** The features are declared so the native cards show the real
  data, but every write raises an error rather than appearing to succeed.
  See [Two-way control](#two-way-control).
- New devices get their entities on the next reload of the integration.

### Alarms

If the station exposes its alarm service over oBIX, the console reaches
Home Assistant as well — and is worth preferring over the per-point
`problem` sensors, which only ever came from a point whose *name* matched
`*alarm*`:

| Entity | What it is |
|---|---|
| `sensor.*_active_alarms` | Every alarm the station is currently reporting |
| `sensor.*_unacknowledged_alarms` | Only the ones nobody has acknowledged |
| `Station alarm` on each published device | Whether the console holds an alarm for that device |
| `event.*_alarm` | Fires once per new alarm |

Both counts carry the alarms as attributes, most urgent first (Niagara
counts 1 as critical). The list is capped at 20 for the recorder's sake;
the count is always exact.

There are two counts rather than one because a site with thirty standing
alarms everyone has seen is a different situation from one with a single
new alarm, and one number cannot distinguish them.

These entities only exist when the station actually exposes the alarm
service. If they are missing, **`/api/alarms/probe`** in the add-on says
why: an empty alarm list otherwise looks the same whether the driver is not
exporting the alarm service, the oBIX user lacks permission to it, the
console is genuinely clear, or the records arrived in a shape the parser did
not recognise.

To expose it in Workbench: the oBIX export must include the alarm service,
and the oBIX user's permissions must grant read on it. A rescan forces the
probe to run again.

### Trend logs

The station already stores its trends. The **History** screen in the add-on
imports them into Home Assistant's long-term statistics, so a graph covers
what the building did rather than only what Home Assistant was running to
see.

1. Open the add-on, go to **History**, and turn on **Sync trend logs to
   Home Assistant**.
2. Find a trend in **Available on the station**. **Preview** reads the most
   recent samples straight from the station, so you can confirm it is the
   one you want.
3. **Add** it. It is paired to its device automatically — the station
   records which point each trend belongs to, and the point knows its
   device. The **Evidence** column says where the pairing came from:

   | Evidence | Meaning |
   |---|---|
   | `from station` | The point's history extension names this trend. Reliable. |
   | `name match` | The trend's name matches exactly one point's name. A guess — check it. |
   | `unpaired` | No match, or more than one. Add it and set the point yourself. |

4. Trends are imported hourly. To move a backfill along, call
   `niagara.sync_history`.

Each trend has its own switch, and the master switch stops everything
without losing the selection.

**What is not imported:**

- **Cumulative totals.** A `total_increasing` sensor's statistics carry a
  running sum, which cannot be derived from a window of samples without
  knowing every meter reset before it. Getting it wrong corrupts the energy
  dashboard, so these are skipped and the reason appears in the log.
- **The current hour**, which the recorder is still accumulating.
- Trends whose point has no sensor in Home Assistant — enable the point
  first.

An imported hour **replaces** whatever Home Assistant had computed for that
hour. That is the intent: the station logged the building on a fixed
interval whether Home Assistant was up or not.

A backfill is bounded per run, so a station holding years of trend catches
up over several runs rather than occupying the JACE for an hour.

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
