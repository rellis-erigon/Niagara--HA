# Entity Mapping Reference

This document details exactly how Niagara BMS points are mapped to Home Assistant entities. The same rules apply to both the native integration and the MQTT add-on.

---

## Point Type Mapping

| Niagara oBIX Type | XML Tag | HA Platform | Entity Class |
|---|---|---|---|
| NumericPoint / NumericWritable | `<real>` | `sensor` | Numeric sensor |
| IntegerPoint / IntegerWritable | `<int>` | `sensor` | Numeric sensor |
| BooleanPoint / BooleanWritable | `<bool>` | `binary_sensor` | Binary sensor |
| EnumPoint / EnumWritable | `<enum>` | `sensor` | Enum sensor |
| StringPoint / StringWritable | `<str>` | `sensor` | String sensor |
| DateTimePoint | `<abstime>` | — | Discovered but not mapped |
| DurationPoint | `<reltime>` | — | Discovered but not mapped |

---

## Unit Conversion

oBIX uses its own unit system. The integration converts to HA-compatible units:

| oBIX Unit | HA Unit | Device Class |
|---|---|---|
| `celsius` | °C | temperature |
| `fahrenheit` | °F | temperature |
| `percent` | % | humidity |
| `kilowatt` | kW | power |
| `watt` | W | power |
| `kilowatt_hour` | kWh | energy |
| `watt_hour` | Wh | energy |
| `megawatt_hour` | MWh | energy |
| `gigajoule` | GJ | energy |
| `megajoule` | MJ | energy |
| `british_thermal_unit` | BTU | — |
| `volt` | V | voltage |
| `ampere` | A | current |
| `hertz` | Hz | frequency |
| `pascal` | Pa | pressure |
| `kilopascal` | kPa | pressure |
| `pounds_per_square_inch` | psi | pressure |
| `millibar` | mbar | pressure |
| `bar` | bar | pressure |
| `cubic_feet_per_minute` | ft³/min | — |
| `liters_per_second` | L/s | — |
| `gallons_per_minute` | gal/min | — |
| `revolutions_per_minute` | rpm | — |
| `inches_of_water` | inH2O | — |
| `cubic_meter` | m³ | volume |
| `cubic_meters_per_hour` | m³/h | — |
| `liter` | L | volume |
| `liters_per_hour` | L/h | — |
| `gallon` | gal | volume |
| `cubic_foot` | ft³ | volume |
| `cubic_feet_per_hour` | ft³/h | — |
| `degree` | ° | — |

Raw oBIX unit URIs (e.g., `obix:units/celsius`) are automatically stripped to the unit name before mapping.

---

## Sensor Device Class Detection

Device class is determined in this order:

### 1. Unit-Based Detection (Highest Priority)

If the point has a recognized unit, the device class is assigned directly:

| Unit(s) | Device Class |
|---|---|
| °F, °C | `temperature` |
| % | `humidity` |
| kW, W | `power` |
| kWh, Wh, MWh, GJ, MJ | `energy` |
| V | `voltage` |
| A | `current` |
| Hz | `frequency` |
| psi, kPa, Pa, mbar, bar | `pressure` |
| m³, ft³, L, gal, CCF | `volume` (refined to `water` or `gas` by name) |

### 2. Name-Based Detection (Fallback)

If no unit-based device class is found, the point name is checked against regex patterns:

| Pattern | Device Class | Fallback Unit |
|---|---|---|
| `temp`, `room_t`, `zone_t`, `supply_air`, `return_air`, `discharge`, `duct_t`, `oat`, `sat`, `rat`, `dat`, `chwt`, `hwt` | `temperature` | °C |
| `humid`, `rh`, `rel_hum` | `humidity` | % |
| `co2`, `carbon_di` | `co2` | ppm |
| `press`, `psi`, `static_p` | `pressure` | — |
| `power`, `kw`, `demand` | `power` | kW |
| `energy`, `kwh`, `consumption` | `energy` | kWh |
| `volt` | `voltage` | V |
| `current`, `amp` | `current` | A |
| `freq`, `hz` | `frequency` | Hz |
| `water_meter`, `water_consump`, `dcw`, `dhw`, `hydraulic` | `water` | L |
| `gas_meter`, `gas_consump`, `natural_gas` | `gas` | m³ |
| `batter`, `soc`, `state_of_charge` | `battery` | % |

---

## State Class Detection

| Condition | State Class |
|---|---|
| Device class is `energy`, `gas`, or `water` | `total_increasing` |
| Device class is `volume` with water/gas units | `total_increasing` |
| Unit is in energy units set (kWh, Wh, MWh, GJ, MJ, BTU, therm) | `total_increasing` |
| Everything else | `measurement` |

---

## Binary Sensor Device Class Detection

Boolean points are matched against name patterns:

| Pattern | Device Class | Meaning |
|---|---|---|
| `alarm`, `fault`, `trip`, `alert`, `emergency`, `smoke`, `fire` | `problem` | Something is wrong |
| `fan`, `motor`, `pump`, `compressor`, `run` | `running` | Equipment is running |
| `door`, `window`, `damper`, `valve` | `opening` | Something is open |
| `occup` | `occupancy` | Space is occupied |
| `motion`, `pir` | `motion` | Motion detected |

Boolean value parsing: `true`, `1`, `on`, `active`, `enabled` → **On**. Everything else → **Off**.

---

## Icon Detection

If no device class is assigned, icons are inferred from point names:

| Pattern | Icon |
|---|---|
| `fan`, `vfd` | `mdi:fan` |
| `pump` | `mdi:pump` |
| `valve`, `vlv` | `mdi:pipe-valve` |
| `damper`, `dpr`, `dmpr` | `mdi:valve` |
| `alarm`, `fault` | `mdi:alarm-light` |
| `setpoint`, `set_pt`, `stpt` | `mdi:thermostat` |
| `mode`, `command`, `cmd` | `mdi:cog` |
| `status`, `state` | `mdi:information-outline` |
| `speed`, `vfd`, `freq` | `mdi:speedometer` |
| `flow`, `cfm`, `gpm` | `mdi:waves-arrow-right` |
| `light`, `lux`, `luminaire` | `mdi:lightbulb` |

---

## Category Classification (Panel and Add-on UI)

Points are classified into categories for filtering in the management UI:

| Category | Name Patterns | Unit Matches |
|---|---|---|
| Temperature | temp, zone_t, supply_air, return_air, OAT, SAT, etc. | °F, °C |
| Fan | fan, SF, RF, EF, VFD | — |
| Pump | pump, CHW_P, HW_P, CW_P | — |
| Valve | valve, damper | — |
| Pressure | press, psi, static | psi, kPa, Pa, inH2O |
| Power | power, energy, kWh, kW, watt, demand | kW, W, kWh, Wh |
| Humidity | humid, RH, dew_point | % |
| Flow | flow, CFM, GPM, velocity | cfm, L/s, gpm |
| Setpoint | setpoint, set_pt, SP, limit | — |
| Status | status, alarm, fault, mode, command, run, stop | — |
| CO2 | co2, carbon | ppm |
| Other | No match | — |

---

## Niagara Name Decoding

Niagara 4 URL-encodes special characters in point names using `$XX` hex escapes:

| Encoded | Decoded | Character |
|---|---|---|
| `$2d` | `-` | Hyphen |
| `$2e` | `.` | Period |
| `$24` | `$` | Dollar sign |
| `$20` | ` ` | Space |

Example: `DB$2dL4$2e2$2dPower` → `DB-L4.2-Power`

---

## Unique IDs

Entity unique IDs are generated as: `niagara_{md5(point_path)[:12]}`

This ensures stable IDs across restarts but means entity IDs change if you reconfigure with a different station (different paths).

---

## Filtered Point Names

The following Niagara internal properties are automatically excluded from discovery:

`stationName`, `hostName`, `hostId`, `platformVersion`, `niagaraVersion`, `softwareVersion`, `osName`, `osVersion`, `osArch`, `vmName`, `vmVersion`, `vmVendor`, `timeZoneId`, `stationStartTime`, `vendorName`, `modelName`, `serialNumber`, `firmwareVersion`, `hardwareVersion`, `healthStatus`, `health`, `faultCause`, `deviceName`, `driverName`, `pollFrequency`, `pollEnabled`, `pollRate`, `tuningPolicyRef`, `tuningPolicyName`, `tuningPolicy`, `proxyExt`, `conversion`, `deviceFacets`, `facets`, `icon`, `href`, `enabled`, `overridden`, `actions`, `watchCount`, `lease`, `type`, `is`, `display`, `displayName`, `readValue`, `writeValue`, `subscriptionStatus`, `pointId`, `fallbackValue`, `statusText`, `priorityArray`, `inAlarm`, `ackState`, `in1`–`in16`

Names starting with `pslot:`, `slot:`, or `n:` are also excluded.
