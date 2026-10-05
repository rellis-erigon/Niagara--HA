# BMS Panel Guide

The Niagara BMS integration includes a built-in management panel in the Home Assistant sidebar for browsing your BMS point tree, managing device groupings, and exploring what your Niagara station exposes.

---

## Accessing the Panel

After installing the native integration, a **Niagara BMS** entry appears in your HA sidebar with an office building icon. Click it to open the panel.

> **Note:** This panel is part of the [Native Integration](integration-guide.md) (v2.0). The MQTT add-on has its own separate web UI — see the [Add-on Guide](addon-guide.md).

---

## Panel Overview

The panel has two main areas:

### Sidebar — Browse Tree

The left sidebar shows the Niagara folder hierarchy:

- **All Devices** is the root level — click it in the breadcrumb to return to the top
- Click any folder to navigate into it and see its children
- The **breadcrumb trail** at the top shows your current location — click any segment to jump back
- Each folder shows its **total point count**
- The item count at the top shows how many folders are at the current level

### Main Area — Point Table

The main area displays a paginated table of points under your current selection:

| Column | Description |
|---|---|
| **Name** | Decoded point name (Niagara hex escapes like `$2d` are converted to readable characters) |
| **Value** | Live value, refreshed every 10 seconds automatically |
| **Category** | Auto-classified type based on name and unit |
| **Type** | oBIX value type (numeric, boolean, enum, string) |
| **Group** | The HA device this point is assigned to |

---

## Device Folder Management

The most important feature of the panel is managing which Niagara folders become HA devices.

### Setting a Folder as a Device

1. Browse to the folder level you want
2. Click the **"Use as Device"** button next to any folder
3. The button changes to a green **"Device"** checkmark and the row highlights
4. A yellow **Apply Changes** bar appears at the top of the sidebar

### Removing a Device Folder

Click the green **"Device"** button again to remove it. The folder reverts to being grouped by the default depth setting.

### Applying Changes

After adding or removing device folders:

1. The **Apply Changes** bar shows how many device folders are currently selected
2. Click **Apply Changes** to reload the integration with the new groupings
3. Home Assistant recalculates all device assignments
4. Entities are reorganized under the new device structure

> **Important:** Clicking Apply Changes reloads the entire integration. This is a brief interruption (a few seconds) while entities are re-created with the updated groupings.

### How Device Folders Work

When you mark a folder as a device:

- All points under that folder (including subfolders) are grouped into a single HA device
- The device is named using the folder path (e.g., "Niagara BMS — AHU-1")
- Points nested deeper under the folder are all consolidated under one device

Without device folders, points are grouped by the **device depth** setting from the integration configuration.

---

## Search and Filtering

### Text Search

Type in the search box to filter points by name or path. The search is debounced — results update as you type after a brief pause.

### Category Filter

The dropdown next to the search box filters by auto-detected point category:

| Category | Matches |
|---|---|
| Temperature | temp, zone temp, supply/return air, OAT, SAT, etc. |
| Fan | fan, VFD, supply/return/exhaust fan |
| Pump | pump, CHW/HW/CW pump |
| Valve | valve, damper |
| Pressure | pressure, psi, static |
| Power | power, energy, kW, demand |
| Humidity | humidity, RH, dew point |
| Flow | flow, CFM, GPM, velocity |
| Setpoint | setpoint, set point, SP, limit |
| Status | status, alarm, fault, mode, command |
| CO2 | CO2, carbon |
| Other | Anything that doesn't match the above |

Select **All Categories** to remove the filter.

---

## Profiles

The panel includes five built-in profiles for common BMS monitoring scenarios. Click the **Profiles** button in the toolbar to open the profiles modal.

| Profile | Description | Example Patterns |
|---|---|---|
| **HVAC** | Air handling, fans, pumps, valves, temperatures, setpoints | `*Temp*`, `*Fan*`, `*AHU*`, `*Supply*` |
| **Energy** | Power meters, energy consumption, demand | `*Power*`, `*Energy*`, `*kWh*`, `*Meter*` |
| **Alarms** | Faults, alarms, trips, emergencies | `*Alarm*`, `*Fault*`, `*Trip*`, `*Fire*` |
| **Lighting** | Lighting levels, switches, lux sensors | `*Light*`, `*Lux*`, `*Dimmer*` |
| **Zone Comfort** | Zone temperatures, humidity, CO2, occupancy | `*Zone*Temp*`, `*Humid*`, `*CO2*`, `*Occup*` |

Click a profile to see a preview of how many points match and a sample list. In the native integration panel, profiles are informational — use them to understand your point breakdown and then enable matching entities through the HA entity registry.

---

## Pagination

Points are displayed 50 per page. Use the pagination controls at the bottom to navigate through large point lists.

---

## Tips

- **Start from the top:** Browse your folder tree from "All Devices" to understand the hierarchy before selecting device folders
- **Use search to find specific points:** If you know a point name, search for it directly rather than navigating the tree
- **Set device folders at a meaningful level:** Choose folders that represent physical equipment or zones (e.g., AHU-1, Floor-3, Chiller Plant) rather than too high (entire building = one device) or too low (one device per point)
- **Auto-detect first:** Start with device depth `0` (auto-detect). It analyzes your point tree and picks a grouping that produces a manageable number of devices. Use device folders to override specific groupings afterward.
- **Category view:** Use the category filter to quickly see all temperature sensors, alarms, or fans across your entire site

---

## Next Steps

- [Integration Guide](integration-guide.md) — full setup and configuration
- [Entity Mapping Reference](entity-mapping.md) — how points become HA entities
- [Energy Dashboard Guide](energy-dashboard.md) — monitoring energy, water, and gas
