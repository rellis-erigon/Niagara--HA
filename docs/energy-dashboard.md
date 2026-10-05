# Energy Dashboard Guide

The Niagara BMS integration automatically configures energy, water, gas, and battery sensors for Home Assistant's built-in Energy dashboard. No manual setup of device classes or state classes is needed — the integration detects them from your Niagara point names and units.

---

## What Gets Auto-Detected

### Energy Sensors

Points with energy-related units or names are configured with `device_class: energy` and `state_class: total_increasing`:

| Detection Method | Examples |
|---|---|
| **By unit** | kWh, Wh, MWh, GJ, MJ, BTU, therm |
| **By name** | Points containing `energy`, `kwh`, `consumption` |

These appear in the Energy dashboard under **Electricity grid → Individual devices**.

### Water Sensors

Points with water-related names or volume units in water contexts:

| Detection Method | Examples |
|---|---|
| **By name** | Points containing `water`, `DCW`, `DHW`, `hydraulic`, `irrigation`, `potable`, `sewage` |
| **By unit + context** | Volume units (m³, ft³, L, gal) under water-related paths |

These appear in the Energy dashboard under **Water consumption**.

### Gas Sensors

Points with gas-related names or volume units in gas contexts:

| Detection Method | Examples |
|---|---|
| **By name** | Points containing `gas`, `natural gas`, `LNG`, `LPG`, `propane`, `methane` |
| **By unit + context** | Volume units (m³, ft³, CCF) under gas-related paths |

These appear in the Energy dashboard under **Gas consumption**.

### Battery Sensors

Points with battery-related names:

| Detection Method | Examples |
|---|---|
| **By name** | Points containing `battery`, `SOC`, `state of charge` |

These appear as battery level indicators in HA.

### Power Sensors

Points with power units or names are configured with `device_class: power` and `state_class: measurement`:

| Detection Method | Examples |
|---|---|
| **By unit** | kW, W |
| **By name** | Points containing `power`, `kW`, `demand` |

Power sensors are used in the Energy dashboard for real-time consumption views.

---

## Volume Unit Refinement

When a point has a generic volume unit (m³, ft³, L, gal, CCF), the integration looks at the point name and path to determine whether it measures water or gas:

- **Water keywords:** water, DCW, DHW, CHW, hydraulic, irrigation, potable, sewage, drain, tank
- **Gas keywords:** gas, natural gas, LNG, LPG, propane, methane

If neither matches, the default is `water`.

---

## Setting Up the Energy Dashboard

### Step 1 — Enable Your Energy Entities

Since all entities are disabled by default in the native integration:

1. Go to **Settings → Devices & Services → Niagara BMS**
2. Find your energy/power/water/gas entities
3. Enable each one (toggle the **Enabled** switch)

> **Tip:** Use the HA entity filter to search for entities with `device_class: energy` or `device_class: water`.

For the MQTT add-on, enable the relevant points in the web UI.

### Step 2 — Configure the Energy Dashboard

1. Go to **Energy** in the HA sidebar (or **Settings → Dashboards → Energy**)
2. Under **Electricity grid**:
   - Click **Add consumption** and select your energy sensors (kWh entities)
3. Under **Individual devices** (optional):
   - Add individual power meters for per-device tracking
4. Under **Water consumption** (if applicable):
   - Click **Add water source** and select your water sensors
5. Under **Gas consumption** (if applicable):
   - Click **Add gas source** and select your gas sensors

### Step 3 — Wait for Data

The Energy dashboard needs at least two data points to calculate consumption. Initial data appears within a few hours, and the dashboard fully populates after 24 hours.

---

## Supported Units

| Type | HA Device Class | State Class | Supported Units |
|---|---|---|---|
| Energy | `energy` | `total_increasing` | kWh, Wh, MWh, GJ, MJ |
| Water | `water` | `total_increasing` | m³, L, gal, ft³ |
| Gas | `gas` | `total_increasing` | m³, ft³, CCF |
| Battery | `battery` | `measurement` | % |
| Power | `power` | `measurement` | kW, W |
| Temperature | `temperature` | `measurement` | °C, °F |
| Humidity | `humidity` | `measurement` | % |
| Pressure | `pressure` | `measurement` | psi, kPa, Pa, mbar, bar |
| Voltage | `voltage` | `measurement` | V |
| Current | `current` | `measurement` | A |
| Frequency | `frequency` | `measurement` | Hz |
| CO2 | `co2` | `measurement` | ppm |
| Volume | `volume` | varies | m³, ft³, L, gal |

---

## Troubleshooting

**Energy entities not appearing in the dashboard selector?**
- Make sure the entity is enabled in the HA entity registry
- Verify the entity has `state_class: total_increasing` (check entity attributes in Developer Tools → States)
- The Energy dashboard only accepts entities with the correct device class and state class combination

**Values resetting to zero?**
- This is normal for `total_increasing` sensors — HA handles meter resets automatically
- Niagara totalizers that reset at midnight or on manual reset are handled correctly

**Wrong device class?**
- The auto-detection relies on point names and units from Niagara. If a point is misclassified, rename it in Niagara to include relevant keywords, or override the device class in HA's entity customization

---

## Next Steps

- [Entity Mapping Reference](entity-mapping.md) — full auto-detection rules
- [Integration Guide](integration-guide.md) — configuration options
- [Troubleshooting](troubleshooting.md) — common issues
