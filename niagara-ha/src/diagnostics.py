"""Surface the things that quietly go wrong, without anyone having to ask.

Most of these checks exist because somebody had to find the problem by hand
first: meters that never reached the energy dashboard because their unit was
wrong, a total bound to a register that resets every midnight, an override
that said % on a point plainly reporting a temperature. None of that shows
up as an error anywhere. Everything keeps running, and the numbers are
simply wrong.

Each check is a pure function over already-loaded state so it can be tested
without a station, and each finding says what to do about it rather than
only what is wrong.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from device_templates import decode_niagara_name, is_resetting_register

ACK_FILE = Path("/config/niagara-ha/diagnostics_ack.json")

ERROR = "error"
WARNING = "warning"
INFO = "info"
_RANK = {INFO: 0, WARNING: 1, ERROR: 2}

# A value older than this is not being refreshed. The poll loop runs well
# inside this, so anything beyond it has stopped rather than slowed.
STALE_SECONDS = 600

BAD_STATUSES = frozenset({"fault", "down", "disabled", "stale"})

# Units that make a sensor eligible for the energy dashboard. Home Assistant
# will not offer a meter whose unit it cannot reconcile with kWh, and it says
# nothing about why — the meter simply is not in the list.
ENERGY_UNITS = frozenset({"Wh", "kWh", "MWh", "GJ"})
GAS_UNITS = frozenset({"m³", "ft³", "Wh", "kWh", "MWh"})
WATER_UNITS = frozenset({"L", "gal", "m³", "ft³"})

# What a point's name implies about its unit. Used only to catch an override
# that contradicts the name outright — not to guess units, which is what the
# templates are for.
UNIT_EXPECTATIONS: list[tuple[re.Pattern, frozenset]] = [
    (re.compile(r"temp|tempera|\bt\b|setpoint|sp\b", re.I), frozenset({"°C", "°F", "K"})),
    (re.compile(r"humid|\brh\b", re.I), frozenset({"%"})),
    (re.compile(r"volt|voltage|\bv\b|l[123]-n|l[123]n", re.I), frozenset({"V", "mV", "kV"})),
    (re.compile(r"current|amp|\ba\b|\bi\b", re.I), frozenset({"A", "mA"})),
    (re.compile(r"co2|carbon.?diox", re.I), frozenset({"ppm"})),
    (re.compile(r"pressure|press|\bpa\b", re.I), frozenset({"Pa", "kPa", "bar", "psi", "inH2O"})),
]


@dataclass
class Finding:
    """One thing worth a person's attention."""

    id: str
    severity: str
    title: str
    detail: str
    action: str = ""
    items: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "action": self.action,
            "count": len(self.items),
            # A check that matches thousands of points must not produce a
            # response nobody can load. The count is the honest number; the
            # list is a sample to make it concrete.
            "items": self.items[:50],
            "truncated": max(0, len(self.items) - 50),
        }


def worst(findings: list[Finding]) -> str:
    return max((f.severity for f in findings), key=lambda s: _RANK.get(s, 0),
               default=INFO)


# -- checks --------------------------------------------------------------

def check_stale_values(
    selections: dict[str, dict], values: dict[str, dict],
    now: float | None = None,
) -> Finding | None:
    """Enabled points whose reading has stopped being refreshed."""
    now = now if now is not None else time.time()
    stale = []
    for path, entry in selections.items():
        if not entry.get("enabled"):
            continue
        value = values.get(path)
        if value is None:
            stale.append({"path": path, "name": _name(entry, path), "age": None})
            continue
        timestamp = value.get("ts")
        if timestamp and now - timestamp > STALE_SECONDS:
            stale.append({
                "path": path, "name": _name(entry, path),
                "age": int(now - timestamp),
            })
    if not stale:
        return None
    return Finding(
        id="stale_values",
        severity=WARNING,
        title=f"{len(stale)} enabled points are not reporting",
        detail=(
            "These points are enabled but their value has not been refreshed "
            f"in over {STALE_SECONDS // 60} minutes. Home Assistant will keep "
            "showing the last reading, which is indistinguishable from a live "
            "one."
        ),
        action="Check the point still exists in Niagara and its proxy is subscribed.",
        items=sorted(stale, key=lambda s: -(s["age"] or 0)),
    )


def check_bad_status(
    selections: dict[str, dict], values: dict[str, dict],
) -> Finding | None:
    """Enabled points Niagara itself is flagging."""
    flagged: dict[str, list] = {}
    for path, entry in selections.items():
        if not entry.get("enabled"):
            continue
        status = (values.get(path, {}).get("status") or "").lower()
        if status in BAD_STATUSES:
            flagged.setdefault(status, []).append(
                {"path": path, "name": _name(entry, path), "status": status}
            )
    if not flagged:
        return None
    items = [i for group in flagged.values() for i in group]
    summary = ", ".join(f"{len(v)} {k}" for k, v in sorted(flagged.items()))
    return Finding(
        id="bad_status",
        severity=ERROR if "fault" in flagged else WARNING,
        title=f"{len(items)} enabled points have a bad status",
        detail=(
            f"Niagara reports {summary}. A stale proxy still serves its last "
            "value, so these read as healthy numbers in Home Assistant."
        ),
        action="Investigate in Niagara; a stale point usually means an unsubscribed proxy.",
        items=items,
    )


def check_bound_but_disabled(
    devices: dict[str, dict], selections: dict[str, dict],
) -> Finding | None:
    """Slots bound to a point that is switched off, so no entity appears."""
    items = []
    for group, entry in devices.items():
        for slot_key, path in (entry.get("bindings") or {}).items():
            if not path:
                continue
            point = selections.get(path)
            if point is not None and not point.get("enabled"):
                items.append({
                    "group": _group_label(group), "slot": slot_key,
                    "path": path, "name": _name(point, path),
                    "published": entry.get("state") == "published",
                })
    if not items:
        return None
    published = sum(1 for i in items if i["published"])
    return Finding(
        id="bound_but_disabled",
        severity=ERROR if published else WARNING,
        title=f"{len(items)} bound slots point at a disabled point",
        detail=(
            "The slot is filled, so the device looks complete, but the point "
            "behind it is switched off and will never produce an entity."
            + (f" {published} of these are on published devices." if published else "")
        ),
        action="Enable the point, or rebind the slot to one that is enabled.",
        items=items,
    )


def check_resetting_totals(
    devices: dict[str, dict], templates: dict, selections: dict[str, dict],
) -> Finding | None:
    """Cumulative slots bound to a register that resets on a schedule."""
    items = []
    for group, entry in devices.items():
        template = templates.get(entry.get("template"))
        if template is None:
            continue
        monotonic = {s.key for s in template.slots if s.validation.get("monotonic")}
        for slot_key, path in (entry.get("bindings") or {}).items():
            if slot_key not in monotonic or not path:
                continue
            name = _name(selections.get(path, {}), path)
            if is_resetting_register(name):
                items.append({
                    "group": _group_label(group), "slot": slot_key,
                    "path": path, "name": name,
                })
    if not items:
        return None
    return Finding(
        id="resetting_totals",
        severity=ERROR,
        title=f"{len(items)} running totals are bound to a resetting register",
        detail=(
            "The point's name says it resets — daily, monthly, this week. A "
            "slot that expects a lifetime total will read every reset as the "
            "meter going backwards, and long-run statistics never recover "
            "cleanly from that."
        ),
        action="Rebind the slot to the lifetime total register.",
        items=items,
    )


def check_decreasing_totals(
    devices: dict[str, dict], templates: dict, observations: dict[str, dict],
) -> Finding | None:
    """Cumulative slots that have actually been observed going backwards."""
    items = []
    for group, entry in devices.items():
        template = templates.get(entry.get("template"))
        if template is None:
            continue
        monotonic = {s.key for s in template.slots if s.validation.get("monotonic")}
        for slot_key, path in (entry.get("bindings") or {}).items():
            if slot_key not in monotonic or not path:
                continue
            decreases = int((observations.get(path) or {}).get("decreases", 0))
            if decreases:
                items.append({
                    "group": _group_label(group), "slot": slot_key,
                    "path": path, "decreases": decreases,
                })
    if not items:
        return None
    return Finding(
        id="decreasing_totals",
        severity=ERROR,
        title=f"{len(items)} running totals have gone backwards",
        detail=(
            "A lifetime total that falls is either a resetting register, a "
            "misconfigured Modbus point, or a meter that was replaced. Home "
            "Assistant treats each fall as a meter reset and adds the whole "
            "new reading to your consumption."
        ),
        action=(
            "Check the register in Niagara before trusting the statistics. "
            "Sorted by how often it has happened."
        ),
        items=sorted(items, key=lambda i: -i["decreases"]),
    )


def check_unit_overrides(selections: dict[str, dict]) -> Finding | None:
    """Manual unit overrides that contradict what the point is called."""
    items = []
    for path, entry in selections.items():
        override = (entry.get("custom_unit") or "").strip()
        if not override:
            continue
        name = _name(entry, path)
        for pattern, expected in UNIT_EXPECTATIONS:
            if pattern.search(name) and override not in expected:
                items.append({
                    "path": path, "name": name, "unit": override,
                    "expected": sorted(expected),
                })
                break
    if not items:
        return None
    return Finding(
        id="unit_override_conflict",
        severity=WARNING,
        title=f"{len(items)} unit overrides disagree with the point name",
        detail=(
            "An override was set by hand that does not match what the point "
            "appears to measure. The override wins, so the entity will carry "
            "the wrong unit and, with it, the wrong device class."
        ),
        action="Check the override; clear it to fall back to what Niagara reports.",
        items=items,
    )


def check_energy_eligibility(
    devices: dict[str, dict], templates: dict, selections: dict[str, dict],
) -> Finding | None:
    """Published meters Home Assistant will refuse to offer for energy."""
    expectations = {
        "energy": ENERGY_UNITS, "energy_generated": ENERGY_UNITS,
        "gas": GAS_UNITS, "water": WATER_UNITS, "volume": WATER_UNITS,
    }
    items = []
    for group, entry in devices.items():
        if entry.get("state") != "published":
            continue
        template = templates.get(entry.get("template"))
        if template is None:
            continue
        for slot in template.slots:
            if not slot.validation.get("monotonic"):
                continue
            path = (entry.get("bindings") or {}).get(slot.key)
            if not path:
                continue
            point = selections.get(path, {})
            # Mirror what the entity will actually carry. When Niagara
            # reports no unit the integration falls back to the slot's first
            # declared unit, so judging on the point alone flagged meters
            # that were already perfectly eligible.
            unit = (point.get("custom_unit") or point.get("unit") or "").strip()
            if not unit and slot.units:
                unit = slot.units[0]
            allowed = expectations.get(slot.key) or (
                set(slot.units) if slot.units else ENERGY_UNITS
            )
            if unit not in allowed:
                items.append({
                    "group": _group_label(group), "slot": slot.key,
                    "path": path, "unit": unit or "(none)",
                    "expected": sorted(allowed),
                })
    if not items:
        return None
    return Finding(
        id="energy_ineligible",
        severity=WARNING,
        title=f"{len(items)} published meters cannot go on the energy dashboard",
        detail=(
            "Home Assistant only offers a sensor for energy, gas or water if "
            "its unit is one it can reconcile. A meter with the wrong unit is "
            "simply absent from the picker, with nothing said about why."
        ),
        action="Set the point's unit override to match what the meter actually reports.",
        items=items,
    )


def check_version_drift(addon: str, integration: str | None) -> Finding | None:
    """The two halves are released as a pair and must not drift."""
    if not integration:
        return Finding(
            id="integration_missing",
            severity=WARNING,
            title="The Home Assistant integration is not installed",
            detail=(
                "The add-on is running but nothing is reading from it, so no "
                "entities exist."
            ),
            action="Add the Niagara BMS integration under Devices & Services.",
        )
    if addon and integration and addon != integration:
        return Finding(
            id="version_drift",
            severity=ERROR,
            title=f"Add-on {addon} is paired with integration {integration}",
            detail=(
                "The two halves are released together and assume each other's "
                "API. A mismatch usually means the integration files were "
                "updated without restarting Home Assistant, or the other way "
                "round."
            ),
            action="Update the older half and restart Home Assistant Core.",
        )
    return None


def _name(entry: dict, path: str) -> str:
    raw = entry.get("name") or path.rstrip("/").split("/")[-1]
    return decode_niagara_name(raw)


def _group_label(group: str) -> str:
    """A device folder as a person would read it.

    Group keys carry Niagara's hex escapes — DB$2dL3 rather than DB-L3 — and
    a diagnostics list full of those is unreadable.
    """
    return " / ".join(
        decode_niagara_name(part) for part in group.strip("/").split("/") if part
    )


# -- acknowledgements ----------------------------------------------------
#
# Not every finding is a fault. Some points are switched off deliberately,
# and a view that keeps insisting otherwise is a view people stop opening.
# An acknowledgement records the count at the time it was made: if the
# problem grows, it comes back.

def apply_acknowledgements(
    findings: list[Finding], acks: dict[str, dict],
) -> tuple[list[Finding], list[dict]]:
    """Split findings into live ones and ones already accepted."""
    live, accepted = [], []
    for finding in findings:
        ack = acks.get(finding.id)
        if ack and len(finding.items) <= int(ack.get("count", 0)):
            entry = finding.to_dict()
            entry["acknowledged"] = True
            entry["note"] = ack.get("note", "")
            entry["acknowledged_at"] = ack.get("at")
            accepted.append(entry)
        else:
            live.append(finding)
    return live, accepted


def run_all(
    selections: dict[str, dict], values: dict[str, dict], devices: dict[str, dict],
    templates: dict, observations: dict[str, dict],
    addon_version: str = "", integration_version: str | None = None,
    now: float | None = None, acks: dict[str, dict] | None = None,
) -> dict:
    """Every check, worst first, with acknowledged ones set aside."""
    findings = [
        f for f in (
            check_version_drift(addon_version, integration_version),
            check_bad_status(selections, values),
            check_stale_values(selections, values, now),
            check_resetting_totals(devices, templates, selections),
            check_decreasing_totals(devices, templates, observations),
            check_bound_but_disabled(devices, selections),
            check_energy_eligibility(devices, templates, selections),
            check_unit_overrides(selections),
        ) if f is not None
    ]
    findings.sort(key=lambda f: -_RANK.get(f.severity, 0))
    findings, accepted = apply_acknowledgements(findings, acks or {})
    return {
        "severity": worst(findings),
        "errors": sum(1 for f in findings if f.severity == ERROR),
        "warnings": sum(1 for f in findings if f.severity == WARNING),
        "findings": [f.to_dict() for f in findings],
        "acknowledged": accepted,
        "checked": 8,
    }


def load_acknowledgements() -> dict[str, dict]:
    try:
        data = json.loads(ACK_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_acknowledgements(acks: dict[str, dict]) -> None:
    ACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ACK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(acks, indent=1))
    tmp.replace(ACK_FILE)
