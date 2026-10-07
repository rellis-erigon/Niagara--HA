"""Reading the station's alarm console over oBIX.

A BMS keeps its operationally useful information in the alarm console, not
in the points. Until now the bridge inferred trouble from point *names* —
anything matching `*alarm*` became a `problem` binary sensor — which finds
the points an integrator happened to name that way and nothing else. It
cannot tell an active alarm from an acknowledged one, has no priority, no
timestamp, and no idea that twelve points on one chiller are one event.

oBIX 1.1 specifies alarming: an `obix:AlarmSubject` advertised in the lobby,
supporting a `query` operation returning `obix:Alarm` records. Niagara's
oBIX driver exposes the station's alarm service that way when the oBIX user
has permission to it.

The parsing here is deliberately tolerant. Stations differ in which alarm
sub-contracts they use, Niagara adds fields of its own, and a record that
cannot be fully understood is still worth reporting as an alarm — losing a
live alarm because one of its fields was unfamiliar would be the worst
outcome of the three.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("niagara-ha.alarms")

OBIX_NS = "http://obix.org/ns/schema/1.0"

# The contract an alarm subject declares, and the names Niagara's oBIX
# driver has used for it in the lobby.
ALARM_SUBJECT_CONTRACT = "obix:AlarmSubject"
ALARM_SUBJECT_NAMES = ("alarms", "alarmSubject", "alarmService")

# oBIX ackState values. Niagara extends the set, so anything unfamiliar is
# read as unacknowledged: claiming an alarm was acknowledged when it was not
# is the one error with operational consequences.
ACKED_STATES = frozenset({"acked", "ack", "acknowledged"})


def _tag(elem: ET.Element) -> str:
    return elem.tag.replace(f"{{{OBIX_NS}}}", "")


def _child(elem: ET.Element, name: str) -> Optional[ET.Element]:
    for child in elem:
        if child.get("name") == name:
            return child
    return None


def _val(elem: ET.Element, name: str) -> Optional[str]:
    child = _child(elem, name)
    if child is None:
        return None
    return child.get("val") or child.get("href")


def _contracts(elem: ET.Element) -> list[str]:
    return [c for c in (elem.get("is") or "").split() if c]


# Values of Niagara's toState that mean the alarm is over.
NORMAL_STATES = frozenset({"normal", "ok"})


@dataclass
class AlarmRecord:
    """One alarm, normalised away from whichever contracts carried it."""

    uri: str
    source: str = ""
    source_name: str = ""
    source_station: str = ""
    reported_by: str = ""
    timestamp: str = ""
    normal_timestamp: str = ""
    ack_state: str = ""
    ack_timestamp: str = ""
    ack_user: str = ""
    alarm_value: str = ""
    priority: Optional[int] = None
    display: str = ""
    message: str = ""
    alarm_class: str = ""
    to_state: str = ""
    from_state: str = ""
    present_value: str = ""
    offnormal_value: str = ""
    contracts: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        """Whether the alarm is still in the off-normal condition.

        Niagara states the transition outright in toState, which is better
        evidence than anything inferred: an alarm that went to normal is
        over whether or not the record also carries a normalTimestamp.

        Falling back to the timestamp covers stations that send only the
        spec's stateful fields. A record with neither is treated as active,
        because the only reason to be told about it is that the station is
        reporting it now.
        """
        if self.to_state:
            return self.to_state.strip().lower() not in NORMAL_STATES
        return not self.normal_timestamp

    @property
    def acked(self) -> bool:
        if not self.ack_state:
            return False
        return self.ack_state.strip().lower() in ACKED_STATES

    def to_dict(self) -> dict:
        return {
            "uri": self.uri,
            "source": self.source,
            "source_name": self.source_name,
            "source_station": self.source_station,
            "reported_by": self.reported_by,
            "timestamp": self.timestamp,
            "normal_timestamp": self.normal_timestamp,
            "ack_state": self.ack_state,
            "ack_timestamp": self.ack_timestamp,
            "ack_user": self.ack_user,
            "alarm_value": self.alarm_value,
            "priority": self.priority,
            "display": self.display,
            "message": self.message,
            "alarm_class": self.alarm_class,
            "to_state": self.to_state,
            "from_state": self.from_state,
            "present_value": self.present_value,
            "offnormal_value": self.offnormal_value,
            "contracts": self.contracts,
            "active": self.active,
            "acked": self.acked,
        }


def find_alarm_subject(lobby: ET.Element) -> Optional[str]:
    """The href of the station's alarm subject, from the oBIX lobby.

    Matched on the declared contract first. Niagara has shipped lobbies
    where the ref carries no contract at all, so the conventional names are
    accepted as a fallback — a wrong guess costs one failed request, while
    missing it means no alarms at all.
    """
    for child in lobby:
        if ALARM_SUBJECT_CONTRACT in _contracts(child):
            href = child.get("href")
            if href:
                return href
    for child in lobby:
        if child.get("name") in ALARM_SUBJECT_NAMES:
            href = child.get("href")
            if href:
                return href
    return None


def split_source_name(raw: str) -> tuple[str, str]:
    """Niagara's sourceName, as (station, point name).

    It arrives as "StationName:PointName" — occasionally with more colons
    where a point name contains one — so the split is on the first colon
    only. Without a colon the whole string is the point name, because a
    station that does not say which station it means is talking about
    itself.
    """
    text = (raw or "").strip()
    if not text:
        return "", ""
    station, sep, name = text.partition(":")
    if not sep:
        return "", station
    return station, name


def parse_alarm(elem: ET.Element, resolve_source=None) -> AlarmRecord:
    """Normalise one alarm record.

    Two shapes. The oBIX spec puts the source in a `<ref name="source">`
    pointing at the point; Niagara instead sends a `sourceName` string of
    "Station:PointName" and a separate `sourceStation`, with no ref at all.
    Reading only the spec shape produced 25 alarms with an empty source,
    which parse cleanly and are useless.

    `resolve_source` maps a source href back to a stored point path, so an
    alarm can be attributed to the device its point belongs to. It only
    applies to the ref form; the name form is resolved by the consumer,
    which is the side that knows the point list.
    """
    source_ref = _child(elem, "source")
    source = ""
    source_name = ""
    source_station = ""
    # Which station *holds the alarm database*, which on a Supervisor is
    # not the station the point is on. Kept separately rather than used as
    # the source's station, because placing an alarm on a device needs the
    # latter and the two routinely differ.
    reported_by = _val(elem, "sourceStation") or ""
    if source_ref is not None:
        source = source_ref.get("href") or ""
        source_name = source_ref.get("display") or source_ref.get("displayName") or ""
    if source and resolve_source is not None:
        source = resolve_source(source)
    if not source_name and source:
        source_name = source.rstrip("/").split("/")[-1]

    if not source_name:
        station, name = split_source_name(_val(elem, "sourceName") or "")
        source_name = name
        source_station = station

    priority_raw = _val(elem, "priority")
    priority = None
    if priority_raw is not None:
        try:
            priority = int(float(priority_raw))
        except (TypeError, ValueError):
            logger.debug("Alarm priority %r is not a number", priority_raw)

    return AlarmRecord(
        uri=elem.get("href") or "",
        source=source,
        source_name=source_name,
        source_station=source_station or reported_by,
        reported_by=reported_by,
        timestamp=_val(elem, "timestamp") or "",
        normal_timestamp=_val(elem, "normalTimestamp") or "",
        ack_state=_val(elem, "ackState") or "",
        ack_timestamp=_val(elem, "ackTimestamp") or "",
        ack_user=_val(elem, "ackUser") or "",
        # Niagara names the off-normal reading offnormalValue and the
        # current one presentValue; alarmValue is only the boolean fact
        # that it alarmed, which says nothing on its own.
        alarm_value=_val(elem, "offnormalValue") or _val(elem, "alarmValue") or "",
        priority=priority,
        display=elem.get("display") or elem.get("displayName") or "",
        message=_val(elem, "msgText") or "",
        alarm_class=_val(elem, "alarmClass") or "",
        to_state=_val(elem, "toState") or "",
        from_state=_val(elem, "fromState") or "",
        present_value=_val(elem, "presentValue") or "",
        offnormal_value=_val(elem, "offnormalValue") or "",
        contracts=_contracts(elem),
    )


def _looks_like_an_alarm(elem: ET.Element) -> bool:
    contracts = " ".join(_contracts(elem))
    if "Alarm" in contracts:
        return True
    # A record with no contract still counts if it carries the two fields
    # every alarm has. Niagara omits contracts on feed events, and names
    # the source in a string rather than a ref.
    if _child(elem, "timestamp") is None:
        return False
    return any(
        _child(elem, name) is not None
        for name in ("source", "sourceName", "sourceStation")
    )


def parse_alarm_list(root: ET.Element, resolve_source=None) -> list[AlarmRecord]:
    """Pull every alarm record out of a query or feed response.

    The records sit under a `list` in a query result and directly under the
    root in a feed, and some stations wrap the list one level deeper again.
    Rather than encode each shape, walk the tree and take what is an alarm.
    """
    found: list[AlarmRecord] = []
    seen: set[str] = set()

    def walk(elem: ET.Element, depth: int) -> None:
        if depth > 4:
            return
        for child in elem:
            if _tag(child) in {"obj", "ref"} and _looks_like_an_alarm(child):
                record = parse_alarm(child, resolve_source)
                key = record.uri or f"{record.source}@{record.timestamp}"
                if key not in seen:
                    seen.add(key)
                    found.append(record)
                continue
            walk(child, depth + 1)

    if _looks_like_an_alarm(root) and _tag(root) == "obj":
        # A single alarm returned on its own.
        single = parse_alarm(root, resolve_source)
        if single.timestamp or single.source:
            return [single]
    walk(root, 0)
    return found


def build_filter(limit: int = 200) -> str:
    """The `obix:AlarmFilter` body for a query of the current alarms.

    Deliberately without start or end: bounding the window is what decides
    whether you get history or the console, and the console is what is
    wanted. `limit` keeps a station with a runaway alarm from returning
    tens of thousands of records into memory.
    """
    return (
        f'<obj is="obix:AlarmFilter">'
        f'<int name="limit" val="{int(limit)}"/>'
        f"</obj>"
    )


def summarise(records: list[AlarmRecord]) -> dict:
    """Counts the integration turns into sensors."""
    active = [r for r in records if r.active]
    return {
        "total": len(records),
        "active": len(active),
        "unacked": sum(1 for r in active if not r.acked),
        "highest_priority": min(
            (r.priority for r in active if r.priority is not None), default=None,
        ),
    }
