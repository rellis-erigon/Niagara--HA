# Troubleshooting

Common issues and solutions for both the native integration and the MQTT add-on.

---

## Connection Issues

### No entities appearing

**Check the HA log** for connection errors:
- Native integration: **Settings → System → Logs** → filter for `niagara`
- Add-on: **Settings → Add-ons → Niagara BMS Bridge → Log**

**Common causes:**
1. **Station unreachable:** Verify the Niagara host is reachable from HA (`ping <host>` from an HA terminal)
2. **Wrong port:** Confirm the HTTPS port on the station (default 443)
3. **oBIX not enabled:** Ensure the oBIX Network Driver is installed (see [Niagara Setup](niagara-setup.md))
4. **Wrong credentials:** Verify the username and password, and that the user has `HTTPBasicScheme` authentication
5. **Firewall:** Check that no firewall blocks HA → Niagara on the HTTPS port

### SSL certificate errors

Most Niagara stations use self-signed certificates. Set **Verify SSL** to `false` in the integration configuration or add-on options.

If you need SSL verification, import the station's certificate into the HA trust store.

### "Cannot connect" during setup

The integration tests the connection live during the config flow. If it fails:

1. Try accessing `https://<host>:<port>/obix/about/` in a browser — you should get an XML response
2. Verify the oBIX user can log in (you'll be prompted for credentials)
3. Check the HA log for the specific error message

### Connection drops / reconnects

**Native integration:** The `DataUpdateCoordinator` handles transient failures automatically. Check the log for `UpdateFailed` messages. If the station is consistently unreliable, increase the poll interval.

**MQTT add-on:** The add-on uses exponential backoff for reconnection (10s → 300s max). Three consecutive full poll failures trigger a full reconnect. Check the add-on log for details.

---

## Point Discovery Issues

### Missing points

1. **Check the point filter:** If you set a path filter during setup (e.g., `/config/AHU/`), only points under that path are discovered. Remove or widen the filter.
2. **Points not exported to oBIX:** On some Niagara configurations, points must be explicitly exported. See [Exporting Points](niagara-setup.md#exporting-points-if-needed).
3. **Internal properties showing up:** The integration filters out 60+ known Niagara internal properties. If you see unexpected internal points, check if they match the [filtered names list](entity-mapping.md#filtered-point-names).

### Too many entities

**Native integration:** All entities are disabled by default. Only enable the ones you need through the HA entity registry.

**Add-on:** All points start disabled. Use the web UI to enable only what you need, or apply a [profile](addon-guide.md#profiles) to enable a specific category of points.

**For very large sites (10,000+ points):**
- Use the `point_filter` option to limit discovery to specific paths
- Apply profiles rather than enabling everything
- Consider splitting across multiple integration entries with different filters

### Points showing wrong values

1. **Check the oBIX value directly:** Browse to `https://<host>:<port>/obix/config/<point-path>` — if the value is wrong there, the issue is on the Niagara side
2. **Unit conversion:** Some oBIX units may not be in the conversion table. Check the [unit mapping](entity-mapping.md#unit-conversion)
3. **Enum values:** Enum points show the text label from the Niagara range definition

### Points not updating

1. **Check poll interval:** Lower the poll interval in options for more frequent updates (minimum 5 seconds)
2. **Watch vs legacy:** Check the log to see if oBIX Watch is active. Watch mode is more efficient but requires Watch service on the station.
3. **oBIX user permissions:** Ensure the oBIX user has read access to the points

---

## Device Grouping Issues

### Too many or too few devices

Adjust the **device depth** setting:
- **Lower depth** (1–2): Fewer, larger devices
- **Higher depth** (4–6): More, smaller devices
- **Depth 0 (auto):** The integration analyzes your tree and targets 20–200 devices

Or use the [BMS Panel](panel-guide.md) to manually select which folders become devices.

### Points in the wrong device

This usually means the device depth or device folder selection doesn't match your building hierarchy. Use the panel to browse your tree and set device folders at the right level.

### "Ungrouped" device

Points with very short paths (fewer folder levels than the device depth) end up in an "Ungrouped" device. Lower the device depth or set device folders manually.

---

## Energy Dashboard Issues

### Energy sensors not appearing in the dashboard

1. **Entity must be enabled** — disabled entities don't appear in selectors
2. **Check device class** — must be `energy`, `water`, or `gas` (see **Developer Tools → States → entity attributes**)
3. **Check state class** — must be `total_increasing` for consumption sensors
4. **Wait for data** — the Energy dashboard needs at least two data points before it shows data

### Wrong device class

The auto-detection relies on point names and units. If a point is misclassified:
- Rename the point in Niagara to include relevant keywords (e.g., add "Energy" or "Water" to the name)
- Or override the device class in HA: **Settings → Devices → Entity → Override device class**

---

## MQTT Add-on Specific Issues

### "MQTT broker not found"

1. Verify the Mosquitto add-on is installed and running
2. Check that the MQTT integration is configured in HA
3. If using an external MQTT broker, set `mqtt_host`, `mqtt_port`, `mqtt_user`, and `mqtt_password` manually

### Points toggled but not appearing in HA

1. Check the add-on log for MQTT publish errors
2. Verify MQTT Discovery is working: check **Settings → Devices & Services → MQTT** for discovered devices
3. Try restarting the add-on — the hot-reload should pick up changes, but a restart forces republishing

### Web UI not loading

1. Check that the add-on is running
2. Try accessing via ingress: **Settings → Add-ons → Niagara BMS Bridge → Open Web UI**
3. Check browser console for JavaScript errors
4. The web UI requires HA ingress authentication — make sure you're logged into HA

### High memory usage

The add-on uses a fast YAML parser that reduces memory usage by ~89% compared to standard yaml.safe_load. For sites with 100,000+ points:
- Use `point_filter` to limit discovery scope
- Reduce `poll_workers` if memory is tight
- Check the log for memory-related warnings

---

## Native Integration Specific Issues

### Panel not appearing in sidebar

The panel is registered when the integration loads. If it's missing:
1. Check that the integration is loaded: **Settings → Devices & Services → Niagara BMS**
2. Try reloading the integration (three-dot menu → **Reload**)
3. Clear browser cache and refresh

### "Already configured" error

Each Niagara station is identified by `host:port`. You can't add the same station twice. To reconfigure, delete the existing entry and add it again, or use the **Configure** option to change settings.

---

## Getting Help

1. **Check the logs first** — most issues have clear error messages
2. **Enable debug logging:**
   - Native integration: Add to `configuration.yaml`:
     ```yaml
     logger:
       logs:
         custom_components.niagara: debug
     ```
   - Add-on: Set `log_level` to `debug` in the add-on configuration
3. **Open an issue:** [GitHub Issues](https://github.com/rellis-erigon/Niagara--HA/issues) with:
   - HA version, integration/add-on version
   - Relevant log entries
   - Your Niagara station version
   - Number of discovered points
