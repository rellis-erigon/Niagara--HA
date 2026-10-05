# Changelog

## Native Integration v2.0.0

The native HA custom integration — no MQTT broker or add-on required.

- **Native HA integration** — config flow setup, direct oBIX REST communication, DataUpdateCoordinator for polling
- **Auto-discovery** — walks the full oBIX point tree and creates entities for every readable point
- **oBIX Watch support** — efficient change-only polling with a single HTTP request per cycle
- **Smart entity mapping** — auto-detects units (30 oBIX unit conversions), device classes (temperature, power, energy, pressure, humidity, CO2, voltage, current, frequency, water, gas, battery), state classes, and icons from point names and units
- **Grouped devices** — points organized into HA devices by Niagara folder hierarchy with auto-detect depth (targets 20–200 devices)
- **Auto area assignment** — Niagara folders map to HA areas based on configurable depth
- **BMS management panel** — built-in sidebar panel for browsing points, setting device folders, and viewing profiles
- **Energy dashboard ready** — energy (kWh), water (m³/L), and gas sensors configured with `total_increasing` state class automatically
- **Niagara name decoding** — `$XX` hex escapes decoded to readable characters
- **Options flow** — change poll interval, device grouping, and area mapping without reconfiguring
- **Entities disabled by default** — avoids overwhelming HA on large BMS sites
- **Manual device folder selection** — set specific folders as HA devices via the panel
- **HACS compatible** — install and update through the HA Community Store
- **Binary sensor support** — boolean points with auto-detected device classes (problem, running, opening, occupancy, motion)
- **Enum sensor support** — options list populated from Niagara range definitions
- **Volume unit refinement** — volume sensors classified as water or gas based on name and path keywords

---

## MQTT Add-on Changelog

See [niagara-ha/CHANGELOG.md](niagara-ha/CHANGELOG.md) for the full add-on version history (v0.1.0 through v0.6.9).

### Highlights

- **v0.6.9** — Water, gas, battery support for Energy dashboard
- **v0.6.0** — oBIX Watch support for efficient polling
- **v0.5.3** — GUI-based device folder selection
- **v0.5.0** — Device grouping by Niagara folders
- **v0.4.0** — Smart point import with profiles, rules, and tree navigation
- **v0.3.0** — Web UI for point management
- **v0.1.0** — Initial release with oBIX discovery and MQTT publishing
