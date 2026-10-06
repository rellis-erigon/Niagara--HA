# Two-way control — plan

> **Decision, 2026-10-07: not being built. The Niagara bridge stays
> read-only.**
>
> This document is kept because the research in it is worth having, not
> as a backlog item. Do not start on it without that decision being
> revisited explicitly.
>
> The reasoning, briefly: a read-only bridge cannot break a building and
> this one could, across 224 air conditioners. A Niagara write holds a
> priority level until released, and a point held from Home Assistant
> looks entirely normal in the BMS while the station's own schedule
> quietly stops working. The value on offer — nudging a setpoint from a
> dashboard — does not pay for that risk on a live hotel.
>
> The measurement below still stands and is useful on its own: it
> explains why the HVAC points cannot be written *today* even if someone
> wanted to.

The bridge reads a BMS of 20,000 points and writes nothing. This is the
plan to change that, in the order the work actually unblocks.

Written 2026-10-07 against station `BunningsMercureJace2`, 2,801 exported
points.

## The finding that shapes everything

**Nothing worth writing is writable today.** Of 2,801 exported points,
303 report `writable: true`:

| What they are | Count |
|---|---|
| Resettable meter totals — `TodaysTotal`, `Yesterday`, `ThisMonth` | 132 |
| String metadata — `Name`, `UnitServes` | 170 |
| `virtualsEnabled`, a station flag | 1 |

And the points anyone would actually want to drive:

| Point | Instances | Writable |
|---|---|---|
| `StartStopCommand` | 224 | **no** |
| `AirConModeCommand` | 224 | **no** |
| `TempAdjust` | 224 | **no** |
| `AirConModeStatus` | 220 | no (correctly — it is a status) |

Zero of 897. So the first phase is not code.

Two things make an oBIX point read-only, and they need different fixes:

1. **The point is not a writable point.** A Niagara `NumericPoint` is
   read-only by construction; only `NumericWritable`, `BooleanWritable`
   and `EnumWritable` accept a write. If the station models these as
   plain points, they must be changed in the station.
2. **The oBIX user lacks write permission.** Niagara reports `writable`
   *per user*. A point that is writable can still export as read-only to
   a user with read-only rights on its category.

**(2) is far more likely here and far cheaper to test.** The same station
exports `Name` and `UnitServes` as writable, which are ordinary string
properties — so the user is not globally read-only. It is worth checking
the category permissions on the HVAC points before changing any point
types.

### Phase 0 — unblock the station *(not our code)*

- Grant the oBIX user operator write on the HVAC command points, and
  re-read `/api/integration/points`.
- Success is `writable: true` on `StartStopCommand`. Nothing downstream
  can be tested until that flips.
- If it does not flip, the points themselves are read-only types and the
  station needs the change instead.

A `niagara.check_writability` service would make this a one-click check
and is worth building first, because it is the only phase that can be
done today.

## Phase 1 — the write path

`ObixClient` has `_post` and nothing that writes. Add one method, and be
deliberate about three things oBIX makes easy to get wrong.

**Writing.** A write is a POST to the point's `set` operation carrying a
typed value: `<real val="21.5"/>`, `<bool val="true"/>`, `<enum
val="Cool"/>`. The type must match the point or Niagara rejects it.

**Priority.** A Niagara writable point has a sixteen-level priority
array. A write lands at a level, and whatever sits at the highest
priority wins. Writing at level 1 overrides everything including safety
interlocks; level 8 is the conventional "manual operator" level and is
what a person at a panel uses. **Default to 8, make it configurable, and
never write at 1.**

**Releasing.** Writing at a level keeps it there until released. A
setpoint nudged from Home Assistant and never released sits at operator
priority forever, and the BMS schedule silently stops working. So:
every writable point needs a *release to auto* action as well as a
write, and the card should show when a point is being held.

This is the phase with the real hazard. A read-only bridge cannot break
a building; this one can.

## Phase 2 — safety rails

`writable: true` from oBIX means "oBIX will accept a write". It does not
mean "writing this is sensible". The 132 meter totals prove it — writing
to `TodaysTotal` corrupts consumption history and the energy dashboard
derived from it.

So **writability is never inferred**:

- A point gains a separate **Allow writing** flag in the add-on, off by
  default, independent of *Expose*. Exposing a point is saying "I want to
  see this"; writing is a different decision.
- A pattern backstop refuses totals and accumulators outright, the same
  anchored matching the templates already use. A point named
  `*Total`, `*Yesterday`, `*ThisMonth` is never writable from here, flag
  or no flag.
- The add-on logs every write with the point, the old value, the new
  value and the priority level. A building that moves unexpectedly needs
  an audit trail.

## Phase 3 — entities

Only `sensor` and `binary_sensor` exist today. Add, keyed off the point
type and the write flag:

| Point | Becomes |
|---|---|
| boolean, writable | `switch` |
| numeric, writable | `number` with the point's own min/max and precision |
| enum, writable | `select` with the enum range the client already parses |

The device template work makes a fourth possible and better: a **climate
entity per FCU**, composed from the `fcu` template's slots —
`room_temperature` as current, `setpoint` as target, `mode` as the HVAC
mode, `run_status` as the action. One entity instead of four, and the
HVAC faceplate's buttons start working, since that card already drives a
climate entity and falls back to "read-only" without one.

## Phase 4 — the cards follow

Nothing in the card layer needs redesigning:

- `hvac-controller-card` already has `temp_up`, `temp_down`,
  `power_toggle`, `mode_cycle` and `fan_cycle` wired to a climate
  entity, and already says *"This controller is read-only"* when there
  is none. Give it the climate entity from Phase 3 and it works.
- `plant-equipment-card` and `pump-system-card` are deliberately
  read-only and should stay so.
- A **held at operator priority** indicator is new work: a small lamp on
  the faceplate, fed by a `binary_sensor` the integration derives from
  the priority array, so a card never shows a setpoint as "normal" when
  Home Assistant is holding it.

## Phase 5 — proving it

- **Write, read back, confirm.** oBIX accepting a write does not mean the
  point took it. Re-read after writing and surface a mismatch rather than
  showing the optimistic value.
- **A dry-run mode** that logs what would be written and writes nothing,
  so a template's bindings can be checked on a live station safely.
- **One unit first.** 224 air conditioners is not where to find out the
  priority level was wrong.

## Order, and why

1. **Phase 0** — the only phase possible today, and everything waits on it.
2. **Phase 2** before Phase 1 ships — the rails go in with the write path,
   not after it.
3. **Phase 1**, write + release + audit log.
4. **Phase 3** entities, starting with `number` on one FCU setpoint.
5. **Phase 4** climate and the cards.
6. **Phase 5** throughout, not at the end.

## What would make this a bad idea

Worth stating plainly, because a BMS runs a building:

- Writing at the wrong priority can override a safety interlock. The
  station's own logic must stay above anything written from here.
- A held point looks normal in the BMS until someone checks the priority
  array. Releasing has to be as easy as writing, and visible.
- Home Assistant restarts, loses network, and gets upgraded. None of
  those should leave a building held at operator priority — consider a
  dead-man release, where a held point returns to auto if Home Assistant
  stops confirming it.
