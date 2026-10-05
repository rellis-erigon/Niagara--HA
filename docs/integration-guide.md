# Native Integration Guide (v2.0)

The Niagara BMS native integration connects directly to your Niagara 4 station via oBIX REST — no MQTT broker or add-on required.

---

## Installation

### Via HACS (Recommended)

1. Open **HACS** in your Home Assistant sidebar
2. Click the three-dot menu (top right) → **Custom repositories**
3. Enter the repository URL: `rellis-erigon/Niagara--HA`
4. Select category: **Integration**
5. Click **Add**
6. Find **Niagara BMS** in the HACS integration list → click **Download**
7. **Restart Home Assistant**

### Manual Installation

1. Download or clone this repository
2. Copy the `custom_components/niagara/` folder into your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

---

## Configuration

### Step 1 — Add the Integration

1. Go to **Settings → Devices & Services**
2. Click **Add Integration** (bottom right)
3. Search for **Niagara** and select **Niagara BMS**

### Step 2 — Connection Details

Enter your Niagara station connection information:

| Field | Description | Example |
|---|---|---|
| **Host** | IP address or hostname of the Niagara station | `192.168.1.100` |
| **Port** | HTTPS port on the station | `443` |
| **Username** | oBIX user account (see [Niagara Setup](niagara-setup.md)) | `obixUser` |
| **Password** | Password for the oBIX user | `••••••••` |
| **Use HTTPS** | Enable HTTPS connection (almost always `true`) | `true` |
| **Verify SSL** | Verify the SSL certificate (set `false` for self-signed certs) | `false` |

The integration tests the connection live. If it fails, check:
- The station is reachable from your HA host
- The oBIX user credentials are correct
- HTTPS and the oBIX driver are enabled on the station

### Step 3 — Discovery Settings

After a successful connection, the integration discovers all oBIX points and shows you a count. Configure how points are organized:

| Field | Default | Description |
|---|---|---|
| **Device name** | `Niagara BMS` | Prefix for device names in HA (e.g., "Niagara BMS — AHU-1") |
| **Poll interval** | `30` | Seconds between value updates (5–3600) |
| **Point filter** | *(empty)* | Path prefix to limit discovery (e.g., `/config/AHU/`) — empty discovers everything |
| **Device depth** | `0` | Folder levels that define a device. `0` = auto-detect (targets 20–200 devices) |
| **Area depth** | `1` | Which folder level maps to HA areas (1 = first folder below root) |

Click **Submit** to complete the setup.

---

## How It Works

### Point Discovery

On setup, the integration walks the entire oBIX point tree on your Niagara station. Every readable point is mapped to an HA entity:

| Niagara Type | HA Entity | Notes |
|---|---|---|
| NumericPoint / NumericWritable | `sensor` | Unit, device class, state class auto-detected |
| BooleanPoint / BooleanWritable | `binary_sensor` | Device class auto-detected from name |
| EnumPoint | `sensor` | Options list populated from Niagara range |
| StringPoint | `sensor` | Display-only |

### Entities Are Disabled by Default

To avoid creating thousands of unwanted entities on large BMS sites, **all entities are created as disabled** in the HA entity registry. Enable the ones you want:

1. Go to **Settings → Devices & Services → Niagara BMS**
2. Click on a device
3. Click on the entity you want to enable
4. Toggle the **Enabled** switch

Or enable entities in bulk from the **Entities** page using the filter and multi-select.

### Device Grouping

Points are organized into HA devices based on the Niagara folder hierarchy. The grouping depth controls how many folder levels define a single device:

- **Depth 0 (auto):** The integration analyzes your point tree and picks a depth that produces a manageable number of devices (targeting 20–200)
- **Depth 1:** Top-level folders become devices (e.g., `JACE` = one device)
- **Depth 3:** Three folder levels define a device (e.g., `JACE/BMS/AHU-1` = one device)

You can also set specific folders as devices using the [BMS Panel](panel-guide.md).

### Area Assignment

Niagara folders automatically map to HA areas. The **area depth** setting controls which folder level is used:

- **Depth 1:** The first folder level becomes the area (e.g., `Building-A`)
- **Depth 2:** The second folder level becomes the area (e.g., `Floor-3`)

### oBIX Watch Polling

The integration uses the oBIX Watch service for efficient polling:

1. A Watch object is created on the Niagara station
2. All discovered points are added to the Watch (in batches of 500)
3. On each poll cycle, a single `pollChanges` request retrieves only points that have changed
4. The Watch lease is automatically renewed to prevent expiry

If the Watch service is not available on your station, the integration falls back to individual point reads.

---

## Changing Settings After Setup

Go to **Settings → Devices & Services → Niagara BMS → Configure** to change:

| Option | Description |
|---|---|
| **Poll interval** | How often to check for value changes |
| **Device name** | Prefix used in device names |
| **Device depth** | How the folder tree maps to devices |
| **Area depth** | Which folder level maps to HA areas |

Changing any option triggers an automatic integration reload — no restart needed.

---

## The BMS Management Panel

The integration installs a sidebar panel (**Niagara BMS**) for browsing and managing your BMS points. See the [BMS Panel Guide](panel-guide.md) for details.

---

## Multiple Stations

You can add multiple Niagara stations. Each creates a separate integration entry with its own devices and entities. Repeat the setup process for each station — they are identified by `host:port`.

---

## Removing the Integration

1. Go to **Settings → Devices & Services**
2. Find the **Niagara BMS** entry
3. Click the three-dot menu → **Delete**

All entities and devices from that station are removed. The panel is removed when the last station is deleted.

---

## Next Steps

- [BMS Panel Guide](panel-guide.md) — browse points and manage device folders
- [Energy Dashboard Guide](energy-dashboard.md) — set up energy monitoring
- [Entity Mapping Reference](entity-mapping.md) — see all auto-detection rules
- [Troubleshooting](troubleshooting.md) — common issues and solutions
