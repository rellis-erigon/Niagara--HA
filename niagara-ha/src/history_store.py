"""What the user has chosen to sync from the station's trend logs.

Deliberately opt-in, one history at a time. A station carries thousands of
trends, most of them logging something nobody will look at, and importing
them all would write years of statistics into Home Assistant's database on
the first run. Enabling a trend is a decision, the same way publishing a
device is.

The file lives beside points.yaml so it is covered by Home Assistant's own
backups.
"""

import logging
import time
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger("niagara-ha.history_store")

HISTORIES_FILE = Path("/config/niagara-ha/histories.yaml")

FILE_COMMENT = (
    "Trend logs synced into Home Assistant's long-term statistics. "
    "Only entries with enabled: true are synced, and only while the "
    "top-level enabled flag is on."
)


def _empty() -> dict:
    return {"enabled": False, "histories": {}}


def load() -> dict:
    """The stored selection, or an empty one."""
    try:
        with open(HISTORIES_FILE) as handle:
            raw = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        return _empty()
    except (yaml.YAMLError, OSError) as e:
        # A damaged file must not take the screen down; the user can see an
        # empty list and re-add, which is better than a 500 with no path out.
        logger.error("Could not read %s: %s", HISTORIES_FILE, e)
        return _empty()

    if not isinstance(raw, dict):
        return _empty()
    histories = raw.get("histories")
    return {
        "enabled": bool(raw.get("enabled", False)),
        "histories": histories if isinstance(histories, dict) else {},
    }


def save(state: dict) -> None:
    """Persist atomically, so a reader never sees a half-written file."""
    HISTORIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": FILE_COMMENT,
        "enabled": bool(state.get("enabled", False)),
        "histories": state.get("histories") or {},
    }
    tmp = HISTORIES_FILE.with_suffix(".tmp")
    with open(tmp, "w") as handle:
        yaml.safe_dump(payload, handle, sort_keys=True, default_flow_style=False)
    tmp.replace(HISTORIES_FILE)


def set_global_enabled(enabled: bool) -> dict:
    """The master switch for history syncing.

    Separate from the per-history flags so the whole feature can be stopped
    without losing which trends were chosen — the reason to stop it is
    usually that a station is struggling, and having to re-pick forty trends
    afterwards would make nobody use the switch.
    """
    state = load()
    state["enabled"] = bool(enabled)
    save(state)
    return state


def add(
    history: str,
    point: str = "",
    group: str = "",
    unit: str = "",
    enabled: bool = True,
) -> dict:
    """Add or update one history. Returns the stored entry."""
    history = history.strip().strip("/")
    if not history:
        raise ValueError("a history name is required")

    state = load()
    existing = state["histories"].get(history) or {}
    entry = {
        "point": point or existing.get("point", ""),
        "group": group or existing.get("group", ""),
        "unit": unit or existing.get("unit", ""),
        "enabled": bool(enabled),
        "added": existing.get("added") or time.time(),
        # Reset on every add so a re-added history backfills again rather
        # than silently resuming from a watermark set before it was removed.
        "last_synced": existing.get("last_synced") if existing else None,
    }
    state["histories"][history] = entry
    save(state)
    return entry


def remove(history: str) -> bool:
    state = load()
    if history not in state["histories"]:
        return False
    del state["histories"][history]
    save(state)
    return True


def set_enabled(history: str, enabled: bool) -> Optional[dict]:
    state = load()
    entry = state["histories"].get(history)
    if entry is None:
        return None
    entry["enabled"] = bool(enabled)
    save(state)
    return entry


def record_sync(history: str, through: str, imported: int) -> None:
    """Note how far a history has been synced.

    The watermark is what stops every run re-reading from the beginning.
    Stored as the station's own timestamp rather than the wall clock, so a
    station whose clock differs from Home Assistant's does not lose or
    repeat a window.
    """
    state = load()
    entry = state["histories"].get(history)
    if entry is None:
        return
    entry["last_synced"] = through
    entry["last_count"] = imported
    entry["last_run"] = time.time()
    save(state)


def pair(
    history: str,
    links: dict[str, str],
    points_by_path: dict[str, dict],
) -> dict:
    """Work out which point and device a history belongs to.

    `links` maps a point path to the history name its extension declares —
    the station's own statement, which is the only source that stays correct
    when a history or a point is renamed. Falls back to matching the
    history's trailing segment against a point name, which is right often
    enough to be worth offering and is always shown as a guess.
    """
    history = history.strip().strip("/")

    for point_path, linked in links.items():
        if linked != history:
            continue
        point = points_by_path.get(point_path) or {}
        return {
            "point": point_path,
            "group": point.get("group", ""),
            "unit": point.get("custom_unit") or point.get("unit", ""),
            "confidence": "station",
        }

    tail = history.split("/")[-1].lower()
    candidates = [
        (path, point) for path, point in points_by_path.items()
        if (point.get("name") or "").lower() == tail
    ]
    if len(candidates) == 1:
        path, point = candidates[0]
        return {
            "point": path,
            "group": point.get("group", ""),
            "unit": point.get("custom_unit") or point.get("unit", ""),
            "confidence": "name",
        }

    return {"point": "", "group": "", "unit": "", "confidence": "none"}


def enabled_entries() -> dict[str, dict]:
    """The histories to sync: the master switch and the entry's own flag."""
    state = load()
    if not state["enabled"]:
        return {}
    return {
        name: entry for name, entry in state["histories"].items()
        if entry.get("enabled")
    }
