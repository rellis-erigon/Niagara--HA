# Niagara BMS Bridge for Home Assistant

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![HA Add-on](https://img.shields.io/badge/Home%20Assistant-Add--on-blue.svg)](https://www.home-assistant.io/addons/)
[![Niagara 4](https://img.shields.io/badge/Tridium-Niagara%204-orange.svg)](https://www.tridium.com/)

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
3. Each point is mapped to the right HA entity type with units and device classes
4. MQTT Discovery publishes the entities — they appear in HA automatically
5. A polling loop keeps values updated (configurable interval, default 30s)

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

4. **Start** the add-on — entities appear within seconds

### Niagara-Side Setup (One-Time)

Only two things are needed on the BMS, no licenses or paid modules:

1. **Enable oBIX** — In Workbench, go to the station's `WebService` → enable `ObixServlet`
2. **Create a user** — Add a user (e.g. `obix_reader`) with read permissions on the points you want

That's it. Detailed instructions are in the [add-on docs](niagara-ha/DOCS.md).

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
| `mqtt_host` | `core-mosquitto` | MQTT broker host |
| `mqtt_port` | `1883` | MQTT broker port |
| `mqtt_user` | *(empty)* | MQTT username |
| `mqtt_password` | *(empty)* | MQTT password |
| `mqtt_topic_prefix` | `niagara` | Topic prefix for all MQTT messages |
| `log_level` | `info` | `debug`, `info`, `warning`, `error` |

---

## Roadmap

This project is built in phases. Phase 1 is the current release.

| Phase | Status | What it adds |
|---|---|---|
| **Phase 1** — Read-only bridge | **Current** | oBIX discovery, sensor entities, MQTT Discovery, polling |
| **Phase 2** — Control & history | Planned | Write support, history queries, alarms, oBIX Watch (real-time), BACnet option |
| **Phase 3** — Intelligence | Planned | Multi-station, Niagara MQTT connector, schedules, energy dashboard, Haystack tags |

---

## Troubleshooting

**No entities appearing?**
Check the add-on log for connection errors. Verify the Niagara host is reachable and oBIX is enabled.

**SSL errors?**
Set `verify_ssl` to `false` — most Niagara stations use self-signed certificates.

**Too many entities?**
Use `point_filter` to scope discovery to a specific part of the tree (e.g. `/config/AHU/`).

**Values not updating?**
Lower `poll_interval_seconds` for faster updates. Check that the oBIX user has read permissions on the target points.

---

## Tech Stack

- **Protocol:** oBIX (Open Building Information Exchange) over REST/HTTPS
- **Language:** Python 3.11
- **HA Integration:** MQTT Discovery via [paho-mqtt](https://pypi.org/project/paho-mqtt/)
- **Runtime:** Home Assistant Add-on (Docker)

---

## Contributing

Contributions welcome. Please open an issue first to discuss what you'd like to change.

## License

[MIT](LICENSE)
