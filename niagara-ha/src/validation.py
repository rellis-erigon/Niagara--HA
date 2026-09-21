"""Validate a typed device's bound points against what its template expects.

Binding says which point fills a slot. Validation asks whether that point's
actual values are worth trusting: does it parse, is its unit one the slot
accepts, does it sit in a plausible range, and does a cumulative total
actually accumulate.

The point of running this before exposure is that a wrong value is worse
than a missing one. A room temperature reading 847 °C or an energy meter
that silently resets corrupts long-run statistics in a way that is painful
to unpick months later.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("niagara-ha.validation")

OBSERVATIONS_FILE = Path("/config/niagara-ha/observations.json")

# Severities, worst first. A blocked device must not be published; a warning
# is surfaced but does not stop anything.
BLOCKED = "blocked"
WARNING = "warning"
OK = "ok"
_RANK = {OK: 0, WARNING: 1, BLOCKED: 2}

# A value older than this is treated as not reporting, independent of the
# integration's own staleness window.
STALE_SECONDS = 600


def worst(severities: list[str]) -> str:
    return max(severities, key=lambda s: _RANK.get(s, 0), default=OK)


# -- Observations --------------------------------------------------------
#
# Monotonic cannot be judged from one reading. The poll loop records the
# highest value seen per path and counts decreases, and validation reads
# that back.

def load_observations() -> dict[str, dict]:
    try:
        with open(OBSERVATIONS_FILE) as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as err:
        logger.debug("Could not read observations: %s", err)
        return {}
    return data if isinstance(data, dict) else {}


def save_observations(observations: dict[str, dict]) -> None:
    try:
        OBSERVATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = OBSERVATIONS_FILE.with_suffix(".tmp")
        with open(tmp, "w") as handle:
            json.dump(observations, handle)
        tmp.replace(OBSERVATIONS_FILE)
    except OSError as err:
        logger.debug("Could not write observations: %s", err)


def record_observation(
    observations: dict[str, dict], path: str, value: Any, now: float | None = None,
) -> None:
    """Track the running maximum and count decreases for one point."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return

    now = now if now is not None else time.time()
    entry = observations.get(path)
    if entry is None:
        observations[path] = {"max": number, "decreases": 0, "last": number}
        return

    previous = entry.get("last")
    if previous is not None and number < previous - 1e-9:
        entry["decreases"] = int(entry.get("decreases", 0)) + 1
        entry["last_decrease"] = now
    entry["last"] = number
    entry["max"] = max(float(entry.get("max", number)), number)


def monotonic_paths(templates: dict, devices: dict[str, dict]) -> set[str]:
    """Bound paths whose slot declares monotonic, across all typed devices."""
    paths: set[str] = set()
    for entry in devices.values():
        template = templates.get(entry.get("template"))
        if template is None:
            continue
        for slot in template.slots:
            if slot.validation.get("monotonic"):
                path = entry.get("bindings", {}).get(slot.key)
                if path:
                    paths.add(path)
    return paths


# -- Validation ----------------------------------------------------------

def _issue(severity: str, message: str) -> dict:
    return {"severity": severity, "message": message}


def validate_slot(
    slot, point: dict | None, value_entry: dict | None,
    observation: dict | None = None, now: float | None = None,
) -> tuple[str, list[dict]]:
    """Check one slot. Returns (severity, issues)."""
    issues: list[dict] = []

    if point is None:
        if slot.required:
            return BLOCKED, [_issue(BLOCKED, "Required slot is not bound")]
        return OK, []

    raw = value_entry.get("value") if value_entry else None
    if raw is None:
        severity = BLOCKED if slot.required else WARNING
        return severity, [_issue(severity, "Bound point is not reporting a value")]

    status = (value_entry or {}).get("status", "")
    if status and status.lower() in ("fault", "down", "disabled"):
        issues.append(_issue(WARNING, f"Point status is {status}"))

    timestamp = (value_entry or {}).get("ts")
    now = now if now is not None else time.time()
    if timestamp and now - timestamp > STALE_SECONDS:
        age = int(now - timestamp)
        issues.append(_issue(WARNING, f"Last update was {age}s ago"))

    unit = (point.get("unit") or "").strip()
    if slot.units:
        if not unit:
            issues.append(_issue(
                WARNING,
                f"Point reports no unit; slot expects {' or '.join(slot.units)}",
            ))
        elif unit not in slot.units:
            issues.append(_issue(
                BLOCKED,
                f"Unit {unit!r} is not one this slot accepts "
                f"({', '.join(slot.units)})",
            ))

    rules = slot.validation or {}
    numeric_slot = bool(slot.point_types) and any(
        t in ("numeric", "integer") for t in slot.point_types
    )
    number = None
    if numeric_slot or "min" in rules or "max" in rules:
        try:
            number = float(raw)
        except (TypeError, ValueError):
            issues.append(_issue(BLOCKED, f"Value {raw!r} is not a number"))

    if number is not None:
        minimum, maximum = rules.get("min"), rules.get("max")
        if minimum is not None and number < float(minimum):
            issues.append(_issue(
                BLOCKED, f"Value {number:g} is below the expected minimum {minimum:g}",
            ))
        if maximum is not None and number > float(maximum):
            issues.append(_issue(
                BLOCKED, f"Value {number:g} is above the expected maximum {maximum:g}",
            ))

    if rules.get("monotonic") and observation:
        decreases = int(observation.get("decreases", 0))
        if decreases:
            issues.append(_issue(
                WARNING,
                f"Value has decreased {decreases} time(s); a cumulative total "
                "should only rise, so statistics may be affected",
            ))

    options = point.get("enum_range") or []
    if options and str(raw) not in [str(o) for o in options]:
        issues.append(_issue(
            WARNING, f"Value {raw!r} is outside the point's declared range",
        ))

    severity = worst([i["severity"] for i in issues])
    if severity == BLOCKED and not slot.required:
        # Losing a whole meter because an optional frequency point reads 0
        # is worse than publishing it with that one reading flagged. Only a
        # required slot can block a device.
        severity = WARNING
    return severity, issues


def validate_device(
    template, bindings: dict[str, str], points_by_path: dict[str, dict],
    values: dict[str, dict], observations: dict[str, dict] | None = None,
    now: float | None = None,
) -> dict:
    """Validate every slot of one device. Returns a summary plus per-slot detail."""
    observations = observations or {}
    now = now if now is not None else time.time()

    slots = []
    severities = []
    for slot in template.slots:
        path = bindings.get(slot.key)
        point = points_by_path.get(path) if path else None
        severity, issues = validate_slot(
            slot, point, values.get(path) if path else None,
            observations.get(path) if path else None, now,
        )
        severities.append(severity)
        slots.append({
            "key": slot.key,
            "name": slot.name,
            "required": slot.required,
            "bound_path": path,
            "severity": severity,
            "issues": issues,
        })

    overall = worst(severities)
    return {
        "severity": overall,
        "publishable": overall != BLOCKED,
        "blocked": sum(1 for s in slots if s["severity"] == BLOCKED),
        "warnings": sum(1 for s in slots if s["severity"] == WARNING),
        "slots": slots,
    }
