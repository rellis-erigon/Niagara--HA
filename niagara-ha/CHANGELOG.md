# Changelog

The add-on and the Home Assistant integration are released as a matched
pair and share a version number. CI fails the build if they drift.

## 3.19.0 — 2026-10-07

- **Feature**: Trend logs. The station has always stored them and the
  bridge threw them away — the point walker returns early at any folder
  carrying `historyConfig` or `historyName`, because their contents are
  logging configuration rather than building values and they outnumbered
  the real points 6:1. Correct for discovery, but it also discarded the one
  thing linking a point to its trend, so Home Assistant rebuilt long-term
  statistics from live polls alone: every restart left a gap that could
  never be filled, and a newly enabled point had no past at all.

  A new **History** screen in the add-on, with:

  - a master switch for the whole feature, separate from the per-trend
    flags so a struggling station can be left alone without losing which
    trends were chosen;
  - the station's trend catalogue, searchable, with the sample count and
    date range;
  - **add one at a time**. A station carries thousands of trends and
    importing them all would write years of statistics into Home
    Assistant's database on the first run;
  - **automatic pairing to the device**. The `historyName` on a point's
    history extension is the station's own statement of which trend belongs
    to which point, and the point already knows its device. The screen says
    whether a pairing came from the station or from a name match, and an
    ambiguous name pairs to nothing rather than to whichever point happened
    to be first;
  - a preview, reading the most recent samples straight from the station,
    to confirm a trend is the right one before syncing years of it.

- **Feature**: The chosen trends are imported into Home Assistant's
  long-term statistics against the point's own sensor, hourly, on a timer
  and through a new `niagara.sync_history` service. An imported hour
  replaces what Home Assistant had computed for it, deliberately: the
  station logged the building on a fixed interval whether Home Assistant
  was running or not.

  Two limits, both deliberate:

  - **Only `measurement` sensors.** A `total_increasing` sensor's
    statistics carry a cumulative sum, which cannot be derived from a
    window of samples without knowing every meter reset before it. Getting
    that wrong does not produce a slightly wrong graph, it corrupts the
    energy dashboard — so totals are skipped and the reason is logged.
  - **Never the current hour**, which the recorder is still accumulating.

  Each run is bounded, so a station holding years of trend catches up over
  several runs rather than occupying the JACE for an hour. The watermark
  lives with the add-on, beside the selection, so removing and re-adding
  the integration does not re-import everything.

- **Feature**: `/api/histories/probe` reports what the station exposes for
  trend logs, on the same terms as the alarm probe.

- **Internal**: 363 add-on tests, 195 integration.

## 3.18.0 — 2026-10-07

- **Feature**: The station's alarm console reaches Home Assistant. Until
  now the bridge inferred trouble from point *names* — anything matching
  `*alarm*` became a `problem` binary sensor — which finds the points an
  integrator happened to name that way and nothing else. It could not tell
  an active alarm from an acknowledged one, had no priority, no start time,
  and no idea that twelve points on one chiller were one event.

  oBIX 1.1 specifies alarming: an `obix:AlarmSubject` advertised in the
  lobby, with a `query` operation returning `obix:Alarm` records. The add-on
  finds it, polls it alongside the points, and serves it on
  `/api/integration/alarms`. New entities:

  - `sensor.*_active_alarms` and `sensor.*_unacknowledged_alarms` on a new
    station device, each carrying the alarms themselves as attributes, most
    urgent first. Two counts rather than one: a site with thirty standing
    alarms everyone has seen is a different situation from one with a single
    new alarm, and one number cannot tell them apart.
  - A `Station alarm` problem sensor on every published device, from the
    console rather than from a point name — so it knows the priority, the
    start time and the acknowledgement, and it finds alarms sitting on
    points nobody enabled.
  - An `event` entity that fires once per new alarm, so an automation can
    notify without polling a count and working out what changed. Alarms
    already standing when Home Assistant restarts do not fire: a restart is
    not an alarm.

  None of these appear unless the station actually exposes the alarm
  service, so a station without it gets nothing rather than a row of
  permanently-empty sensors. New alarm entities arrive on the next reload
  of the integration.

- **Feature**: `/api/alarms/probe` reports what the station exposes for
  alarming, refreshed on each connect and forced by a rescan. An empty alarm
  list has four causes that look identical — the driver is not exporting the
  alarm service, the oBIX user lacks permission to it, the console is
  genuinely clear, or the records came back in a shape the parser did not
  recognise — and the probe names which, keeping the raw reply when nothing
  parsed.

- **Internal**: `niagara-ha/tests/test_main.py` walks the poll loop's module
  for calls to names that were never defined. A sibling add-on was taken
  down on boot by an edit that landed a call without its definition, and
  nothing caught it until the service failed to start.

- **Internal**: First tests for `web.py`, the add-on's largest module. CI
  now installs Flask for the add-on job. 270 add-on tests, 160 integration.

## 3.17.0 — 2026-10-07

- **Feature**: Published HVAC devices become real Home Assistant entities
  instead of bundles of sensors. An FCU is now a `climate` entity — room
  temperature, setpoint, mode and running state in one place, with the
  native thermostat card, `current_temperature` available to automations
  and exposure to voice assistants. A fan or VFD becomes a `fan` entity,
  and an AHU becomes both where its slots are bound. The per-point sensors
  are unchanged and still there.

  A stopped unit reports `off`, not the mode it would run in: 147 of the
  195 units on the reference station sit in `HEATING` while stopped, so
  taking the mode point at face value would have reported most of a
  building as heating.

  The bridge remains read-only. The climate and fan entities declare their
  features so the native cards render the real data, and every setter
  raises instead of pretending to write. When two-way lands, the setters
  become real with no change to any entity id or attribute name.

- **Fix**: A slot's declared range now takes part in auto-binding, not just
  validation. `TempAdjust` means an absolute room setpoint on some FCU
  controllers and a ±3K offset dial on others, and the name cannot tell
  them apart — the reading can. On the reference station this moves 194
  setpoints out of the "Setpoint Adjust" slot, where they were labelled as
  adjustments and gave the climate entities no target temperature.

  Binding runs in two passes: readings that agree with the slot are
  preferred, then anything still unbound is filled on name alone. A reading
  out of range is a reason to prefer another point, never a reason to leave
  a slot empty — a meter reporting power factor as 99 rather than 0.99 still
  has exactly one power factor point. Existing devices keep their stored
  bindings until you rebind them.

- **Fix**: The entity purge no longer deletes device-level entities. It
  keys on the point path, so a climate or fan entity counted as stale the
  moment it was created and was removed on the next refresh.

- **Internal**: Pattern order within a slot's match list now carries real
  weight (200 a step) rather than being rounded away by the exact-name
  bonus, so a slot can declare a deliberate fallback at the end of its list
  without that fallback outranking the specific pattern above it. Replayed
  against the reference station this changes no existing binding.

- **Internal**: The integration test suite runs without a Home Assistant
  install, against stand-ins used only when the real package is absent. CI
  still runs against real Home Assistant. Three test files previously could
  not be run outside CI at all; the suite is now 127 tests.

## 3.11.0 — 2026-09-24

- **Feature**: Two templates. **Pump System (3 Pump)** for a triplex set —
  common pressure and setpoint, plus speed, frequency, current and power per
  pump, and run/fault contacts for sets built that way. Only the system
  pressure is required, because whether a site has run contacts or inverter
  telemetry is not knowable from here.
- **Feature**: **Fire Indicator Panel** — panel normal and fault, fire
  pumps, sprinkler flow, valve and pressure switches, manual call points.
  Monitoring only; a fire panel is its own system of record and nothing
  here should be treated as control.

## 3.10.2 — 2026-09-24

- **Fix**: Clear devices left with no entities even when nothing was
  removed. The cleanup only ran after entities were deleted, but regrouping
  empties a device without deleting anything — so moving the meter totals
  onto their boards left nineteen devices called "MeterTotal" holding
  nothing, each opening onto an empty page.

## 3.10.1 — 2026-09-24

- **Fix**: A point's children now belong to the equipment, not to the point.
  Niagara lets a point hold children — `MeterTotal` is a reading in its own
  right and also the parent of `LastMonth`, `ThisWeekTotal` and the rest —
  and those children were forming devices of their own. On this station that
  meant 22 separate devices all called "MeterTotal", none attached to the
  distribution board they measure. 133 points move to their board.

## 3.10.0 — 2026-09-24

- **Feature**: Force a re-scan of points from the panel or via
  `niagara.rescan`. Points added in Niagara previously did not appear until
  a reconnect or an add-on restart, which is no answer to "I just added a
  meter". The request travels through the filesystem because the Flask UI
  and the poll loop are separate processes, and is consumed before it is
  acted on, so a failed rescan cannot fire forever.

## 3.9.1 — 2026-09-24

- **Fix**: Stop reporting eligible meters as locked out of the energy
  dashboard. The check judged a meter by the unit Niagara reports for its
  point; when that is empty the entity falls back to the slot's declared
  unit. Twenty-one meters already carrying kWh were reported as ineligible.

## 3.9.0 — 2026-09-24

- **Feature**: Diagnostics view in the add-on panel. Eight checks for the
  things that fail quietly — points that stopped reporting, totals bound to
  a register that resets nightly, unit overrides that contradict the point
  name, meters that cannot reach the energy dashboard, version drift between
  the two halves. Findings can be accepted, which records the count at the
  time so a problem that grows comes back.
- **Feature**: Repair issues in Home Assistant for the findings where
  something untrue is being recorded or displayed. They clear themselves
  when the underlying problem goes.
- **Feature**: `niagara.fix_statistics_units` rewrites long-term statistics
  metadata to match the unit an entity now reports. When a unit changes the
  recorder stops recording and the meter goes flat on the energy dashboard
  with nothing said about why. `dry_run: true` previews it.
- **Feature**: `niagara.run_diagnostics` returns the full report for
  automations.
- **Feature**: Published meters are added to the energy dashboard
  automatically. Submeters go in as individual devices rather than grid
  import, because a building has many boards and one supply. Only ever adds;
  a source removed on purpose stays removed.

## 3.8.0 — 2026-09-23

- **Fix**: Never auto-bind a resetting register to a cumulative slot. A slot
  expecting a lifetime total reads every reset as the meter running
  backwards, and long-run statistics do not recover cleanly from that.

## 3.7.0 — 2026-09-22

- **Feature**: A point's unit of measurement can be overridden, for when
  Niagara reports the wrong one or none at all.

## 3.6.0 — 2026-09-22

- **Fix**: Subscribe to the oBIX Watch with absolute hrefs. Without the
  `/obix` prefix nothing was ever subscribed and Niagara served cached
  values flagged `{stale}` — which looked exactly like widespread plant
  failure and was diagnosed as such twice.

## 3.5.0 — 2026-09-22

- **Feature**: Name devices by the tail of their path rather than the whole
  thing. Full paths ran to about sixty characters and every dashboard
  truncated them.

## 3.4.0 — 2026-09-22

- **Fix**: Detect writability from the point's contract rather than a
  `writable` attribute Niagara does not set.

## 3.3.0 — 2026-09-22

- **Fix**: Remove entities whose point no longer exists.

## 3.2.1 — 2026-09-22

- **Fix**: Take a slot's declared unit when the point reports none.

## 3.2.0 — 2026-09-22

- **Fix**: Fast atomic point writes, decoded names, and history sub-points
  kept out of the slot picker.

## 3.1.0 — 2026-09-22

- **Feature**: Re-apply a template to devices already typed with it.

## 3.0.2 — 2026-09-22

- **Fix**: `TempAdjust` binds to the temperature adjust slot, not setpoint.

## 3.0.1 — 2026-09-22

- **Fix**: Separate setpoint adjust from setpoint, and stop an optional slot
  blocking a whole device.

## 3.0.0 — 2026-09-22

- **Feature**: Publish gate and slot-driven entity metadata. A device must
  be typed, validated and published before it reaches Home Assistant.

## 2.9.0 — 2026-09-21

- **Feature**: Import and export templates, and link them to Lovelace cards.

## 2.8.0 — 2026-09-21

- **Feature**: Validate slot values, read the real point status, and add a
  three-phase UPS template.

## 2.7.0 — 2026-09-21

- **Feature**: Free point selection, device-level enabling, custom templates.

## 2.6.0 — 2026-09-21

- **Feature**: Add sibling folders as devices in bulk, and enable slots
  wholesale.

## 2.5.3 — 2026-09-21

- **Fix**: Bind abbreviated HVAC point names (`Sts`, `Spd`).

## 2.5.2 — 2026-09-21

- **Fix**: Show every selected device folder, not only ones with enabled
  points.

## 2.5.1 — 2026-09-21

- **Feature**: Devices view for assigning types and binding slots.

## 2.5.0 — 2026-09-21

- **Feature**: Device type templates and point-to-slot binding.

## 2.4.1 — 2026-09-21

- **Fix**: Stop assigning device classes that contradict the point.

## 2.4.0 — 2026-09-21

- **Feature**: Timestamp and prune cached values so staleness is visible.

## 2.3.2 — 2026-09-21

- **Fix**: Discover the add-on URL from Supervisor instead of a default that
  was wrong.

## 2.3.1 — 2026-09-21

- **Fix**: Keep the requested path when refreshing a point.

## 2.3.0 — 2026-09-21

- **Change**: Add-on and integration versions unified.

## 1.1.0 — 2026-09-21

- Maintenance release.

## 1.0.0 — 2026-09-20

- **Change**: Restructured into two parts — a Home Assistant add-on that
  owns the oBIX connection and the management UI, and a thin integration
  that creates entities.

---

Entries below predate the two-part split, when this was a single add-on.

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
