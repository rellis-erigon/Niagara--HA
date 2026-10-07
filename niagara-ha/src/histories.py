"""Reading Niagara's trend logs over oBIX.

The station already stores the trends. Until now the bridge threw them
away: the point walker returns early at any folder carrying `historyConfig`
or `historyName`, because their contents are logging configuration rather
than building values and they outnumbered the real points 6:1. Correct for
discovery, but it also discarded the one thing that links a point to its
history, so Home Assistant rebuilt long-term statistics from live polls
alone. Every add-on restart left a gap that could never be filled, and a
newly enabled point had no past at all.

oBIX 1.1 specifies histories: an `obix:History` object with a `query`
operation taking an `obix:HistoryFilter` and returning
`obix:HistoryQueryOut` — a list of `obix:HistoryRecord`, each a timestamp
and a value. Niagara exposes one per history under `/obix/histories/`.

Pairing is taken from the station rather than guessed: the `historyName` on
a point's history extension *is* the station's own statement of which
history belongs to which point, and the point already knows its device.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

logger = logging.getLogger("niagara-ha.histories")

OBIX_NS = "http://obix.org/ns/schema/1.0"

HISTORY_SERVICE_CONTRACT = "obix:HistoryService"
HISTORY_CONTRACT = "obix:History"
# Names Niagara's oBIX driver has used for the history service in the lobby.
HISTORY_SERVICE_NAMES = ("histories", "historyService", "history")

# A single query's ceiling. Niagara will happily return a year of 1-minute
# samples — half a million records — and the add-on holds them in memory
# while parsing. Gaps are filled across several queries instead.
MAX_RECORDS = 5000


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
    return child.get("val")


def _contracts(elem: ET.Element) -> list[str]:
    return [c for c in (elem.get("is") or "").split() if c]


@dataclass
class HistoryMeta:
    """What the station says about one history, before any records."""

    name: str
    href: str = ""
    count: Optional[int] = None
    start: str = ""
    end: str = ""
    tz: str = ""
    interval: str = ""
    has_query: bool = False
    query_href: str = ""
    display: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "href": self.href,
            "count": self.count,
            "start": self.start,
            "end": self.end,
            "tz": self.tz,
            "interval": self.interval,
            "has_query": self.has_query,
            "display": self.display,
        }


@dataclass
class HistoryRecord:
    """One logged sample."""

    timestamp: str
    value: Optional[float] = None
    status: str = ""

    def to_dict(self) -> dict:
        return {"t": self.timestamp, "v": self.value, "s": self.status}


@dataclass
class HistoryLink:
    """The station's own statement that a point logs to a history."""

    point: str
    history: str
    extension: str = ""
    kind: str = ""

    def to_dict(self) -> dict:
        return {
            "point": self.point,
            "history": self.history,
            "extension": self.extension,
            "kind": self.kind,
        }


def find_history_service(lobby: ET.Element) -> Optional[str]:
    """The href of the station's history service, from the oBIX lobby."""
    for child in lobby:
        if HISTORY_SERVICE_CONTRACT in _contracts(child):
            href = child.get("href")
            if href:
                return href
    for child in lobby:
        if child.get("name") in HISTORY_SERVICE_NAMES:
            href = child.get("href")
            if href:
                return href
    return None


def normalise_history_name(raw: str) -> str:
    """A historyName as the path segment pair the service is indexed by.

    Niagara writes it as "/StationName/HistoryId"; the service exposes it at
    "histories/StationName/HistoryId/". Both forms, and the bare id, appear
    on real stations.
    """
    return raw.strip().strip("/")


def parse_history_meta(elem: ET.Element, name: str = "") -> HistoryMeta:
    """Read a history object's own description of itself."""
    meta = HistoryMeta(
        name=name or elem.get("name") or "",
        href=elem.get("href") or "",
        start=_val(elem, "start") or "",
        end=_val(elem, "end") or "",
        tz=_val(elem, "tz") or "",
        interval=_val(elem, "interval") or "",
        display=elem.get("display") or elem.get("displayName") or "",
    )
    count_raw = _val(elem, "count")
    if count_raw is not None:
        try:
            meta.count = int(float(count_raw))
        except (TypeError, ValueError):
            logger.debug("History %s reports a non-numeric count %r", name, count_raw)

    query = _child(elem, "query")
    if query is not None:
        meta.has_query = True
        meta.query_href = query.get("href") or ""
    return meta


# What Niagara calls the per-station container under the history service.
HISTORY_DEVICE_DISPLAY = "BHistoryDevice"

# Children of a history device that are not trends.
NOT_A_HISTORY = frozenset({
    "status", "faultCause", "enabled", "displayName", "count",
    "query", "feed", "rollup", "tz", "start", "end", "interval",
})


def history_devices(service: ET.Element) -> list[tuple[str, str]]:
    """The per-station containers under the history service.

    The service does not list trends directly. It lists one history device
    per station, and each has to be fetched to see the trends under it —
    reading only the top level reported zero histories on a station holding
    thousands.
    """
    devices: list[tuple[str, str]] = []
    for child in service:
        if _tag(child) != "ref":
            continue
        name = child.get("name") or ""
        href = child.get("href") or ""
        if not name or not href or name in NOT_A_HISTORY:
            continue
        devices.append((name, href))
    return devices


def parse_history_list(
    root: ET.Element, prefix: str = "",
) -> list[HistoryMeta]:
    """The histories listed in one history device, or a flat service.

    `prefix` is the device's name, so a trend comes back under the
    "Station/TrendId" form the service is indexed by rather than a bare id
    that two stations could both claim.
    """
    found: list[HistoryMeta] = []
    seen: set[str] = set()

    def add(name: str, href: str, display: str) -> None:
        full = f"{prefix}/{name}".strip("/") if prefix else name
        if full and full not in seen:
            seen.add(full)
            found.append(HistoryMeta(name=full, href=href, display=display))

    def walk(elem: ET.Element, depth: int) -> None:
        if depth > 2:
            return
        for child in elem:
            if _tag(child) not in {"ref", "obj", "list"}:
                continue
            name = child.get("name") or ""
            if not name or name in NOT_A_HISTORY:
                continue
            href = child.get("href") or ""
            if HISTORY_CONTRACT in _contracts(child) or (
                _tag(child) == "ref" and href
            ):
                add(name, href, child.get("display") or "")
                continue
            walk(child, depth + 1)

    walk(root, 0)
    return found


def parse_history_records(root: ET.Element) -> list[HistoryRecord]:
    """Pull the samples out of an `obix:HistoryQueryOut`.

    A record is a timestamp and a value. Niagara names the value `value`;
    the spec allows the contract to rename it, so any numeric child that is
    not the timestamp is taken as the value.
    """
    records: list[HistoryRecord] = []

    def read_record(elem: ET.Element) -> Optional[HistoryRecord]:
        timestamp = _val(elem, "timestamp")
        if not timestamp:
            return None
        raw = _val(elem, "value")
        if raw is None:
            for child in elem:
                if child.get("name") == "timestamp":
                    continue
                if _tag(child) in {"real", "int", "bool"}:
                    raw = child.get("val")
                    break
        value: Optional[float] = None
        if raw is not None:
            text = str(raw).strip().lower()
            if text in {"true", "false"}:
                value = 1.0 if text == "true" else 0.0
            else:
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    value = None
        status = _val(elem, "status") or elem.get("status") or ""
        return HistoryRecord(timestamp=timestamp, value=value, status=status)

    def walk(elem: ET.Element, depth: int) -> None:
        if depth > 4:
            return
        for child in elem:
            if _tag(child) == "obj" and _child(child, "timestamp") is not None:
                record = read_record(child)
                if record is not None:
                    records.append(record)
                continue
            walk(child, depth + 1)

    walk(root, 0)
    return records


def build_history_filter(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = MAX_RECORDS,
) -> str:
    """An `obix:HistoryFilter` for a window of samples.

    Both bounds are sent when known. An unbounded query against a station
    with years of 1-minute trends is how you make a JACE stop answering, so
    the limit is never omitted.
    """
    parts = [f'<int name="limit" val="{int(min(limit, MAX_RECORDS))}"/>']
    if start is not None:
        parts.append(f'<abstime name="start" val="{to_obix_time(start)}"/>')
    if end is not None:
        parts.append(f'<abstime name="end" val="{to_obix_time(end)}"/>')
    return f'<obj is="obix:HistoryFilter">{"".join(parts)}</obj>'


def to_obix_time(when: datetime) -> str:
    """A datetime as oBIX writes an abstime."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.isoformat(timespec="milliseconds")


def parse_time(raw: str) -> Optional[datetime]:
    """An oBIX abstime as an aware datetime, or None if unreadable.

    Niagara emits offsets like "+10:00" and occasionally a trailing "Z";
    both are accepted. A naive timestamp is read as UTC rather than guessed
    at, because guessing a local zone silently shifts a whole trend.
    """
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.debug("Unreadable history timestamp %r", raw)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def find_history_links(
    extension_path: str, extension: ET.Element,
) -> Optional[HistoryLink]:
    """The point-to-history link a history extension folder declares.

    `extension_path` is the extension's own path; the point is its parent.
    This is the station's own linkage, which is why it beats matching a
    history's name against a point's name — those agree until an integrator
    renames one of them.
    """
    raw = _val(extension, "historyName")
    if not raw:
        config = _child(extension, "historyConfig")
        if config is not None:
            raw = _val(config, "id") or config.get("val")
    if not raw:
        return None

    parts = [p for p in extension_path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    point_path = "/" + "/".join(parts[:-1]) + "/"
    return HistoryLink(
        point=point_path,
        history=normalise_history_name(raw),
        extension=extension_path,
        kind=(extension.get("is") or "").strip(),
    )


def hourly_buckets(
    records: Iterable[HistoryRecord],
) -> list[dict]:
    """Group samples into the hourly buckets Home Assistant stores.

    Home Assistant's long-term statistics are hourly: a mean, a minimum and
    a maximum per hour. Sending it raw samples would be rejected, and
    sending one bucket per sample would overwrite each hour with the last
    reading in it.

    An hour with no usable sample is left out rather than interpolated — a
    gap in the trend is information, and inventing a value to fill it would
    make the statistics claim coverage the station does not have.
    """
    buckets: dict[datetime, list[float]] = {}
    for record in records:
        if record.value is None:
            continue
        when = parse_time(record.timestamp)
        if when is None:
            continue
        hour = when.astimezone(timezone.utc).replace(
            minute=0, second=0, microsecond=0,
        )
        buckets.setdefault(hour, []).append(record.value)

    out = []
    for hour in sorted(buckets):
        values = buckets[hour]
        out.append({
            "start": hour.isoformat(),
            "mean": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
            "samples": len(values),
        })
    return out


def gap_windows(
    since: Optional[datetime],
    until: datetime,
    span: timedelta = timedelta(days=7),
    max_windows: int = 12,
) -> list[tuple[datetime, datetime]]:
    """Split a backfill into windows a station can answer one at a time.

    A single query for two years of trend is what makes a JACE stop
    responding to anything else, which on a live building is a bigger
    problem than a missing graph. `max_windows` bounds one sync run; the
    next run continues from wherever this one reached.
    """
    if since is None:
        since = until - span
    if since >= until:
        return []
    windows = []
    cursor = since
    while cursor < until and len(windows) < max_windows:
        edge = min(cursor + span, until)
        windows.append((cursor, edge))
        cursor = edge
    return windows
