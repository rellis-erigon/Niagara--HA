# Changelog

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
