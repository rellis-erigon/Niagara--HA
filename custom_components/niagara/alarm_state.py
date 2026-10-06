"""Shared shaping of the station's alarm console for entity state.

Three platforms report on the same records — a count on the station, a
problem flag per device, and an event when a new alarm arrives — so the
formatting lives here rather than being written three times slightly
differently.
"""

from typing import Any

# How many alarms travel in an entity's attributes. A state attribute has to
# stay small: Home Assistant writes every attribute into the state machine
# and the recorder on each change, and a station having a bad morning can
# hold hundreds of alarms. The count is always exact; the list is a sample.
ATTRIBUTE_LIMIT = 20


def alarm_detail(record: dict) -> dict[str, Any]:
    """One alarm, trimmed to what is worth carrying in an attribute."""
    return {
        "source": record.get("source_name") or record.get("source") or "",
        "path": record.get("source") or "",
        "time": record.get("timestamp") or "",
        "acked": bool(record.get("acked")),
        "priority": record.get("priority"),
        "value": record.get("alarm_value") or "",
    }


def by_priority(records: list[dict]) -> list[dict]:
    """Most urgent first. Niagara counts 1 as the most urgent.

    An alarm with no priority sorts last rather than first: without the
    field there is no evidence it is urgent, and putting it at the top of a
    dashboard would push a genuine priority 1 off the bottom.
    """
    return sorted(
        records,
        key=lambda r: (
            r.get("priority") if isinstance(r.get("priority"), int) else 10_000,
            r.get("timestamp") or "",
        ),
    )


def alarm_attributes(records: list[dict]) -> dict[str, Any]:
    """Attributes describing a set of alarms."""
    ordered = by_priority(records)
    unacked = [r for r in ordered if not r.get("acked")]
    attrs: dict[str, Any] = {
        "alarm_count": len(ordered),
        "unacked_count": len(unacked),
        "alarms": [alarm_detail(r) for r in ordered[:ATTRIBUTE_LIMIT]],
    }
    if len(ordered) > ATTRIBUTE_LIMIT:
        attrs["alarms_truncated"] = True
    if ordered:
        first = ordered[0]
        attrs["highest_priority"] = first.get("priority")
        attrs["most_urgent"] = alarm_detail(first)["source"]
        attrs["oldest"] = min(
            (r.get("timestamp") or "" for r in ordered if r.get("timestamp")),
            default="",
        )
    return attrs


def alarm_key(record: dict) -> str:
    """A stable identity for one alarm occurrence.

    The station's own uri where it gives one. Otherwise the source and the
    timestamp together, which is what makes a *new* alarm on a point that
    alarmed before distinguishable from the same one still standing — using
    the source alone would silence every repeat.
    """
    return record.get("uri") or (
        f"{record.get('source', '')}@{record.get('timestamp', '')}"
    )
