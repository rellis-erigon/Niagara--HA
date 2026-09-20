# Changelog

## 0.6.9

- **Feature**: HA Energy dashboard — water meters (`device_class: water`, `state_class: total_increasing`), gas meters (`device_class: gas`), and battery sensors (`device_class: battery`) now auto-detected from point names and paths
- **Feature**: Volume unit support — m³, ft³, L, gal, CCF recognized with correct device classes
- **Feature**: Smart volume classification — volume sensors under HYDRAULICS/water/DCW paths get `device_class: water`, gas paths get `device_class: gas`
- **Feature**: oBIX unit mappings for cubic_meter, liter, gallon, cubic_foot, megawatt_hour, gigajoule, megajoule
- **Improvement**: Broader `total_increasing` detection — water and gas meters automatically get the right state class for the Energy dashboard

## 0.6.8

- **Fix**: Decode Niagara URL-encoded names — `DB$2dL4$2e2$2dPower` now displays as `DB-L4.2-Power` in entity names and device names
- Handles all `$XX` hex escapes (`$2d` = `-`, `$2e` = `.`, `$24` = `$`, etc.)

## 0.6.7

- **Fix**: "Use as Device" and "Apply Changes" now take effect immediately — newly enabled points appear in Home Assistant within one poll cycle instead of requiring a reconnect/restart
- **Fix**: Hot-reload builds active point list from all discovered points, not just previously active ones

## 0.6.6

- **Feature**: Energy dashboard support — energy sensors (kWh, Wh, MWh, GJ, MJ) now use `state_class: total_increasing` so they appear in HA's Energy dashboard
- **Feature**: Additional unit mappings — MWh, GJ, MJ, BTU, therm, mbar, bar now recognized with correct device classes
- **Improvement**: Smart state class inference — energy/consumption points get `total_increasing`, all other measurements get `measurement`

## 0.6.5

- **Fix**: Discovery now skips Niagara internal folders (`proxyExt`, `status`, etc.) during tree traversal — no more ghost entities from internal point properties
- **Fix**: Parent folder name checked against skip list when capturing `out` values — prevents `proxyExt/out` from becoming a visible entity
- **Fix**: Priority array inputs (`in1`–`in16`) added to skip list

## 0.6.4

- **Feature**: Automatic entity type detection — HA now picks the right card, icon, and device class for each point based on its name (temperature, humidity, fan, pump, valve, alarm, setpoint, etc.)
- **Feature**: Name-based icon inference for enum and string sensors (mode, status, command points get appropriate icons)

## 0.6.3

- **Fix**: oBIX unit URIs (`obix:units/celsius`) now converted to proper HA units (`°C`) — no more "0.0 obixunits/celsius" display in entities

## 0.6.2

- **Fix**: Restore "Apply Changes" button — always visible in Browse sidebar for manual re-apply of device groupings

## 0.6.1

- **Fix**: Discovery now captures only the output value per point — no longer creates separate entities for internal Niagara properties (in1-in16, fallbackValue, etc.)
- **Improvement**: "Use as Device" now auto-enables all points under the folder and applies grouping in one click — no separate "Apply Changes" step needed

## 0.6.0

- **Major**: oBIX Watch support — polls for changes with a single HTTP request per cycle instead of one GET per point (N+1 → 1)
- **Feature**: Auto-detects Watch and Batch services from the oBIX lobby
- **Feature**: Watch lifecycle management — auto lease renewal, re-creation on expiry, clean deletion on shutdown
- **Feature**: Batch read support for bulk point value fetching
- **Feature**: Automatic fallback to legacy polling when Watch service is unavailable
- **Feature**: Dynamic Watch updates — points added/removed from Watch when selections change without full re-creation
- **Feature**: Category-based filtering — points auto-classified as Temperature, Fan, Pump, Valve, Pressure, Power, Humidity, Flow, Setpoint, Status, CO2, or Other
- **Feature**: Category dropdown filter in toolbar to quickly find specific point types
- **Feature**: Color-coded category badges on each point row
- **Performance**: Session cookie reuse across all oBIX requests (reduces auth overhead on Niagara 4.9+)
- **Performance**: Watch.add batches points in groups of 500 to avoid oversized requests
- **Fix**: YAML write now quotes strings containing special characters (colons, brackets, etc.) to prevent points.yaml corruption with 42K+ BMS points

## 0.5.8

- **Fix**: Removed `/exports/` path filter from point discovery — all oBIX points are now discovered regardless of path, with only the name-based skip list filtering out Niagara metadata

## 0.5.7

- **Fix**: Restored `out`, `status`, and `in` point names that were incorrectly added to the skip list — these are how Niagara exposes actual BMS point values via oBIX
- **Fix**: Colon filter narrowed to only skip `pslot:`, `slot:`, `n:` prefixes (Niagara internal references), not all names containing `:`

## 0.5.6

- **Fix**: Filter out Niagara internal point properties (`conversion`, `deviceFacets`, `readValue`, `status`, `subscriptionStatus`, `tuningPolicyName`, `proxyExt`, `pointId`, `priorityArray`, etc.) that were appearing as separate HA entities
- **Fix**: Skip names containing `:` (Niagara slot references like `pslot:Drivers/...`) from discovery
- **Fix**: `SKIP_POINT_NAMES` check was missing from `_parse_point` — now applied during both discovery and polling

## 0.5.5

- **Fix**: Point values now retained in MQTT — HA keeps last known value across brief disconnects instead of showing "unknown"
- **Fix**: Connection-lost detection requires 3 consecutive full-failure polls before triggering reconnect (was 1), preventing unnecessary "unknown" state flapping
- **Fix**: `/exports/` path filter no longer applied during point polling — only during discovery. Prevents valid point reads from being silently discarded

## 0.5.4

- **Improvement**: Browse sidebar auto-fits to folder names and buttons instead of truncating
- **Feature**: "Apply Changes" button appears after toggling device folders — recalculates groupings and updates HA entities immediately

## 0.5.3

- **Feature**: GUI-based device folder selection — click "Use as Device" on any folder in the tree to make all its sub-points (including subfolders) appear as one HA device
- Device folders are saved in `device_folders.yaml` and hot-reloaded without restart
- Replaces the need to set `device_depth` manually in config

## 0.5.2

- **Feature**: `device_depth` config option — controls how many folder levels define a device. Set to 4 to group `JACE/BMS/HVAC/Room333/Cooling/Temp` under device "Room333" instead of splitting subfolders into separate devices. Default 0 uses the immediate parent folder.

## 0.5.1

- **Feature**: Last known point values persist across restarts — HA entities show cached values immediately on startup instead of "unavailable"
- Values are stored in `values.json` and restored on next launch

## 0.5.0

- **Feature**: Each Niagara folder becomes its own HA device — points in `HVAC/Room333/` create a "Niagara BMS — HVAC / Room333" device with just those points as entities
- **Improvement**: Entity names now show just the point name (e.g. "SupplyTemp") since the device provides folder context

## 0.4.3

- **Feature**: Rename points in the web UI — custom names are used as the entity name in MQTT/Home Assistant
- Click the pencil icon next to any point name to rename it
- Custom names persist across restarts and show in the HA entity list
- Reset button restores the original Niagara name

## 0.4.2

- **Improvement**: Only discover points under oBIX `/exports/` folder — hides all config/metadata noise
- **Improvement**: Strip `ObixNetwork` and `exports` from display names and tree navigation for cleaner UI

## 0.4.1

- **Fix**: Removed overly aggressive `/points/` path filter that was hiding legitimate BMS points

## 0.4.0

- **Feature**: Smart point import — bulk toggle, auto-enable rules (glob patterns), and 5 built-in profiles (HVAC, Energy, Alarms, Lighting, Zone Comfort)
- **Feature**: Drillable tree navigation — browse points by Niagara device/system/area hierarchy with breadcrumb navigation
- **Performance**: Fast YAML parser replaces yaml.safe_load — ~89% less memory (904 MB → 96 MB for 130K points)
- **Performance**: Drop intermediate data structures after use to reduce sustained RAM
- **Fix**: YAML parse errors in points.yaml causing toggle 404s (switched to yaml.safe_dump)
- **Fix**: Filter out Niagara config properties (stationName, hostName, etc.) from point discovery

## 0.3.2

- **Feature**: Live point values displayed in the web UI with auto-refresh every 10 seconds
- **Fix**: Disabled points are now fully cleared from MQTT broker on startup and hot-reload
- **Fix**: Auto-reconnect when all point reads fail (detects dead Niagara sessions)
- **Fix**: Partial poll failures logged with count for easier troubleshooting

## 0.3.1

- **Performance**: Only publish MQTT state updates when values actually change (~90% reduction in MQTT traffic)
- **Performance**: Remove retain flag from state/attribute publishes (only discovery and availability retain)
- **Performance**: Cache parsed points.yaml in web UI with mtime-based invalidation
- **Performance**: Only republish status attributes when they change
- **Fix**: Suppress urllib3 InsecureRequestWarning when SSL verification is disabled
- **Fix**: Web UI now works under Home Assistant ingress proxy (relative URL resolution)
- **Fix**: Web UI shows helpful message when no points discovered yet instead of stuck "Loading..."

## 0.3.0

- Web UI for point management served via Home Assistant ingress
- Group sidebar with enable/disable toggles per group
- Search, filter, and paginate across all discovered points
- Concurrent polling with configurable worker count (1-20)
- Hot-reload of points.yaml without restarting the add-on
- Bulk enable/disable operations

## 0.2.1

- Grouped devices: each Niagara folder becomes a separate HA device
- Point selection: users choose which points to publish via points.yaml
- New points default to disabled
- MQTT auto-detection from Home Assistant with optional manual override
- Availability topic on all entities
- Initial state publish on discovery

## 0.1.3

- Fix "status unknown" on all devices
- Publish initial state values alongside discovery
- Handle None values correctly (skip instead of publishing empty string)
- Add availability_topic to all discovery payloads

## 0.1.2

- Remove MQTT from locked add-on options
- Use Home Assistant MQTT service discovery by default

## 0.1.0

- Initial release
- oBIX REST client for Niagara 4
- Auto-discovery of all oBIX points
- MQTT Discovery for Home Assistant entity creation
- Point type mapping: numeric, boolean, enum, string
- Configurable polling interval
- Path-based point filtering
- Self-signed SSL support
