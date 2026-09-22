# Niagara BMS — Home Assistant Integration

Connects a Tridium Niagara 4 station to Home Assistant over oBIX. Two parts,
versioned together and always released as a matched pair.

## Architecture

### Add-on (`niagara-ha/`)

A Docker container on HA with s6-overlay. It owns everything stateful: the
Niagara connection, point discovery, the management UI, and all persistence.

| File | Role |
| --- | --- |
| `src/main.py` | Main loop: connect, discover, poll, write `values.json` |
| `src/obix_client.py` | oBIX protocol — HTTP, XML, Watch subscriptions, discovery |
| `src/point_manager.py` | `points.yaml` persistence, device folders, auto-enable rules |
| `src/device_templates.py` | Template schema, slot binding, device types, card rendering |
| `src/validation.py` | Slot validation, observations, severity |
| `src/web.py` | Flask UI + REST API (ingress, port 8099) |
| `src/mqtt_publisher.py` | Legacy MQTT publishing, optional |
| `templates/*.yaml` | Built-in device templates |
| `static/index.html` | Sidebar panel — Points and Devices views |

### Integration (`custom_components/niagara/`)

A thin client over the add-on's REST API. No Niagara protocol code, no UI.

| File | Role |
| --- | --- |
| `coordinator.py` | Polls the add-on; purges entities whose point is gone |
| `entity.py` | Base entity: naming, device info, availability |
| `sensor.py` / `binary_sensor.py` | Entity platforms and class inference |
| `config_flow.py` | Setup and options; discovers the add-on via Supervisor |
| `services.py` | `niagara.generate_card` |

### API between them

Read-only. The add-on never calls Home Assistant.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/integration/points` | Enabled points with type, unit, group, slot metadata |
| `GET /api/integration/values` | `{path: {value, status, age}}` per enabled point |
| `GET /api/health` | Liveness: counts, faulted values, value ages |
| `GET /api/devices/card` | A device's card with slots resolved to point paths |

### Data files (`/config/niagara-ha/`)

`points.yaml` (every discovered point and its enabled state), `values.json`
(`{path: {value, status, ts}}`), `device_folders.yaml`, `device_types.yaml`
(template, slot bindings, draft/published), `observations.json` (running
maximum and decrease counts for monotonic slots), `templates/*.yaml` (user
templates, shadowing built-ins of the same id).

## Things that are not obvious

**Watch URIs need the `/obix` prefix.** Points are stored relative to the
oBIX root (`/config/...`) because every GET is `base_url + path` and
`base_url` already ends in `/obix`. A Watch URI resolves against the *server*
root, so it must be `/obix/config/...`. Without the prefix Niagara answers
every subscription with `BadUriErr`, which reads as "this station has no
Watch service". It does. See `_obix_href` / `_stored_path`.

**Subscription is what keeps proxy points fresh.** NiagaraNetwork proxy
points are marked `{stale}` while nothing is subscribed, and a plain GET
returns the last cached value with that flag. This is why a BMS graphics page
shows live data — opening it subscribes. Polling instead of watching made
1,095 healthy points look dead.

**Status lives in the display string, not the status attribute.** Niagara
writes `22.7 °C {ok}` or `0.00 °C {stale}`; the oBIX `status` attribute is
empty. See `_status_from_display`.

**Writability comes from the contract.** `control:NumericWritable` against
`control:NumericPoint`. No point on the reference station sets an oBIX
`writable` attribute, so reading only that marks everything writable.

**An `<err>` in a Watch values list is not a value.** It carries no `val`.
Counting them made a completely failed Watch look healthy.

**`read_point` must keep the path it was asked for.** `_parse_point` derives
a path from the element's href, falling back to `parent_path + name`, which
doubles the last segment. The polling loop writes its results back over the
active point list, so one bad path poisons that point permanently.

**History extensions are not points.** Niagara hangs them off a point as
child folders whose contents are logging configuration. A folder carrying
`historyConfig` or `historyName` is one; skipping them cut discovery from
20,426 to 18,672 and a meter from 85 points to 19.

**`points.yaml` is written by hand, not by PyYAML.** It is rewritten on every
enable/disable and `yaml.dump` on 20,000 entries takes seconds. See
`fast_dump_points` — 0.095s — and `_fast_load_points_yaml` for the reader.
Both write through a temp file and rename.

**`_parse_kv` must coerce booleans.** It only special-cased `enabled`, so
`writable` came back as the string `"false"`, which is truthy.

## Design decisions

1. **A device is a folder of points**, chosen by the user in the tree.
2. **A template declares what a device type exposes**, as named slots with
   expected units, device class and plausible range. Binding proposes which
   point fills each slot; matching decides what is *suggested*, never what is
   *permitted*, so any point in the device can fill any slot.
3. **Validation gates publishing.** Only a required slot can block a device —
   losing a whole meter because an optional frequency point reads 0 Hz is
   worse than publishing it with that reading flagged.
4. **The publish gate is opt-in** (`strict_publishing`, default off).
   Defaulting it on would remove every existing entity from a running install
   until each device was published one at a time.
5. **Entities start disabled** in principle, because a station can have
   20,000 points. In practice sensors register enabled and binary sensors
   disabled — an unexplained discrepancy, see below.
6. **Path parsing** strips boilerplate segments (`config`, `Drivers`,
   `NiagaraNetwork`, `points`, `out`, `exports`, `obix`) via `SKIP_SEGMENTS`,
   shared by both halves.
7. **Niagara `$XX` name escapes** are decoded everywhere a name is shown.

## Known issues

- **Entities do not appear without a reload.** Removal works — the
  coordinator purges entities whose point is gone — but newly enabled points
  need a reload. This is the outstanding half of R6.
- **Writable points are not exposed.** On the reference station the only
  writable points are meter totaliser registers and history configuration;
  the commands worth having (`StartStopCommand`, `TempAdjust`,
  `AirConModeCommand`) are exported as read-only contracts. R5 is blocked on
  the Niagara side re-exporting them as Writable.
- **The API is unauthenticated** on `0.0.0.0:8099`, and mutating routes
  include destructive ones. R8.
- **Flask's development server** is used in production. R8.
- **Sensors register enabled, binary sensors disabled**, though both inherit
  the same base class with `_attr_entity_registry_enabled_default = False`
  and neither overrides it. Unexplained.
- **The device identifier embeds `addon_url`**, so changing it would orphan
  every device. Not fixed because fixing it would itself orphan them once.
- **Changing a unit breaks existing statistics.** HA suppresses long-term
  statistics when a sensor's unit changes; the fix is
  `recorder/update_statistics_metadata`, which Developer Tools → Statistics
  exposes.

## Development

- Branch: `main`. CI runs both test suites and fails when the add-on and
  integration versions disagree.
- Tests: `pytest niagara-ha/tests` (no HA needed) and `pytest tests`
  (needs `homeassistant`).
- The reference station has ~18,700 points across two Jaces, 192 guest rooms,
  22 distribution boards and 7 switchboards. Performance matters.
- A config entry reload does **not** re-import Python. Integration code
  changes need a full `ha core restart`.
