# Niagara BMS Bridge for Home Assistant

A Home Assistant add-on that connects to Tridium Niagara 4 Building Management Systems via the oBIX REST interface, pulling building data into HA entities with zero changes required on the BMS side (beyond enabling oBIX).

## Installation

1. Add this repository to your Home Assistant add-on store:
   - Go to **Settings → Add-ons → Add-on Store → ⋮ → Repositories**
   - Add: `https://github.com/rellis-erigon/niagara--ha`

2. Install **Niagara BMS Bridge** from the store.

3. Configure the add-on with your Niagara station details.

4. Start the add-on — entities will appear automatically via MQTT Discovery.

## Requirements

- Home Assistant with the **Mosquitto MQTT broker** add-on installed
- A Tridium Niagara 4 station with the **oBIX servlet enabled**
- Network connectivity from HA to the Niagara station (HTTPS)

## What gets exposed

All discoverable Niagara points are mapped to Home Assistant entities:

| Niagara Type | HA Entity | Example |
|---|---|---|
| NumericPoint | `sensor` | Zone temperature, damper position |
| BooleanPoint | `binary_sensor` | Fan status, occupancy |
| EnumPoint | `sensor` (with options) | Operating mode |
| StringPoint | `sensor` | Equipment name |

Units, device classes, and friendly names are mapped automatically where possible.

## Documentation

See the [add-on documentation](niagara-ha/DOCS.md) for full setup instructions.
