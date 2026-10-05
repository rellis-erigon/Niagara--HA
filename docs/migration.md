# Migration Guide: MQTT Add-on to Native Integration

This guide walks you through migrating from the MQTT-based add-on (v0.6.x) to the native HA integration (v2.0).

---

## Why Migrate?

| Feature | MQTT Add-on | Native Integration |
|---|---|---|
| MQTT broker required | Yes (Mosquitto) | No |
| Setup complexity | Add-on + MQTT + integration | Config flow only |
| Point management | Dedicated web UI | HA entity registry + panel |
| Device grouping | Web UI + device_folders.yaml | Config flow + panel |
| Polling | oBIX Watch or concurrent workers | oBIX Watch or legacy |
| Value persistence | values.json | HA state machine |
| Updates via HACS | Add-on repository | Integration repository |

The native integration is simpler to set up and maintain. The MQTT add-on is better for sites that need the richer web UI with profiles, auto-enable rules, and bulk operations.

---

## Before You Start

1. **Note your current setup:** Write down which points are enabled and any custom device groupings
2. **Keep both running:** Install the native integration alongside the add-on first, verify it works, then remove the add-on
3. **Update automations last:** Entity IDs will change, so update automations and dashboards after the migration

---

## Step-by-Step Migration

### Step 1 — Install the Native Integration

Follow the [Integration Guide](integration-guide.md) to install via HACS or manually.

### Step 2 — Add the Integration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Niagara** and select **Niagara BMS**
3. Enter the same connection details you used for the add-on:
   - Same host, port, username, password
   - Same HTTPS and SSL settings

### Step 3 — Configure Discovery Settings

1. Set the **device name** prefix (same as your add-on `device_name` if you want matching names)
2. Set the **poll interval** (same as your add-on `poll_interval_seconds`)
3. Set the **point filter** if you were using one
4. Set the **device depth** (same as your add-on `device_depth`, or `0` for auto-detect)

### Step 4 — Enable Your Entities

In the native integration, all entities are disabled by default. Enable the ones you need:

1. Go to **Settings → Devices & Services → Niagara BMS**
2. Click on a device
3. Enable entities one by one, or use the **Entities** page with filters for bulk enabling

If you were using **profiles** in the add-on, use the category filter on the Entities page to find and enable similar groups of points.

### Step 5 — Set Up Device Folders (Optional)

If you had custom device folders in the add-on:

1. Open the **Niagara BMS** panel in the HA sidebar
2. Browse to the same folders you had marked as devices
3. Click **"Use as Device"** on each one
4. Click **Apply Changes**

### Step 6 — Verify

1. Check that entities are showing correct values
2. Verify devices are grouped correctly
3. Test the Energy dashboard if you were using it
4. Check automations and scripts that reference Niagara entities

### Step 7 — Update Entity References

Entity unique IDs have changed between the add-on and native integration. You'll need to update:

- **Automations:** Update entity IDs in triggers, conditions, and actions
- **Scripts:** Update entity IDs
- **Dashboard cards:** Update entity IDs in Lovelace cards
- **Template sensors:** Update any template sensors that reference Niagara entities

> **Tip:** Use **Developer Tools → States** to find your new entity IDs. Search for `niagara` to see all entities from the native integration.

### Step 8 — Remove the Add-on

Once everything is working:

1. Go to **Settings → Add-ons → Niagara BMS Bridge**
2. Click **Stop**
3. Click **Uninstall**
4. If you no longer need MQTT for other integrations, you can also remove the Mosquitto add-on

---

## Entity ID Mapping

| Component | Add-on Format | Native Integration Format |
|---|---|---|
| Unique ID | `niagara_{md5(path)[:12]}` | `niagara_{md5(path)[:12]}` |
| Entity ID | `sensor.niagara_bms_{name}` | `sensor.niagara_bms_{group}_{name}` |
| Device ID | Based on group path | Based on group path |

The unique ID formula is the same, but the entity IDs that HA generates may differ because the native integration uses `has_entity_name = True` with device context.

---

## Keeping Both Running

You can run both the add-on and native integration simultaneously during migration. They connect to the same Niagara station independently. However:

- You'll see duplicate entities (one set from MQTT, one from the native integration)
- Disable the add-on entities you've migrated to avoid confusion
- The station handles multiple concurrent oBIX connections without issue

---

## Rolling Back

If you need to go back to the add-on:

1. Re-install the Niagara BMS Bridge add-on
2. Your `points.yaml`, `device_folders.yaml`, and `auto_enable_rules.yaml` files are still in `/config/niagara-ha/` (they persist after uninstall)
3. Start the add-on — it will pick up your previous configuration
4. Remove the native integration from **Settings → Devices & Services**
