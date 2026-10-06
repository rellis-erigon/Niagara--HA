"""Importing the station's trend logs into Home Assistant's statistics.

The station already stores the trends. Home Assistant was rebuilding its
long-term statistics from live polls alone, so every add-on restart left a
gap that could never be filled and a newly enabled point had no past at
all. This closes that: the add-on reads the trend over oBIX, groups it into
the hourly buckets Home Assistant stores, and the hours are imported
against the point's own sensor.

Two deliberate limits:

**Only `measurement` sensors.** A `total_increasing` sensor's statistics
carry a cumulative `sum`, which cannot be computed from a window of samples
without knowing every meter reset before it. Getting that wrong does not
produce a slightly wrong graph, it corrupts the energy dashboard, so totals
are skipped and the reason is logged rather than guessed at.

**Never the current hour.** The recorder is still accumulating it, and an
import for a partial hour would be overwritten or would fight with it.

An imported hour *replaces* whatever Home Assistant had computed for that
hour. That is the intent: the station logged the building on a fixed
interval whether Home Assistant was running or not, so it is the better
record of what happened.
"""

import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import NiagaraCoordinator, stable_id

_LOGGER = logging.getLogger(__name__)

# Statistics Home Assistant can be given from a window of samples. A
# total_increasing sensor needs a cumulative sum, which this cannot know.
IMPORTABLE_STATE_CLASSES = frozenset({"measurement"})

# How long one sync run may keep asking the station for more. The add-on
# bounds each query; this bounds the run, so a two-year backfill proceeds
# over several runs instead of occupying the station for an hour.
MAX_WINDOWS_PER_RUN = 12


def _import_statistics(hass: HomeAssistant, metadata, rows) -> None:
    """Hand the rows to the recorder.

    Isolated so the one import that changed shape across Home Assistant
    versions is in a single place: `StatisticMetaData` moved from `has_mean`
    to `mean_type`, and an integration that gets it wrong fails at the last
    step with every query already paid for.
    """
    from homeassistant.components.recorder.statistics import (
        async_import_statistics,
    )

    async_import_statistics(hass, metadata, rows)


def _metadata(statistic_id: str, unit: str | None):
    """Statistic metadata for a mean/min/max series on an existing entity.

    `source` must be "recorder" for a statistic_id that is an entity id;
    anything else is rejected as an external statistic with a bad prefix.
    """
    from homeassistant.components.recorder.models import StatisticMetaData

    fields = {
        "has_sum": False,
        "name": None,
        "source": "recorder",
        "statistic_id": statistic_id,
        "unit_of_measurement": unit,
    }
    try:
        from homeassistant.components.recorder.models import StatisticMeanType

        fields["mean_type"] = StatisticMeanType.ARITHMETIC
    except ImportError:
        fields["has_mean"] = True
    return StatisticMetaData(**fields)


def _rows(buckets: list[dict]) -> list:
    """Hourly buckets as recorder rows, dropping any with an unreadable start."""
    from homeassistant.components.recorder.models import StatisticData

    rows = []
    for bucket in buckets:
        start = _parse(bucket.get("start"))
        if start is None:
            continue
        rows.append(StatisticData(
            start=start,
            mean=bucket.get("mean"),
            min=bucket.get("min"),
            max=bucket.get("max"),
        ))
    return rows


def _parse(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def current_hour(now: datetime | None = None) -> datetime:
    """The start of the hour the recorder is still accumulating."""
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(
        minute=0, second=0, microsecond=0,
    )


def resolve_target(
    hass: HomeAssistant, coordinator: NiagaraCoordinator, entry: dict,
) -> tuple[str | None, str | None, str | None]:
    """The entity a trend should be imported against.

    Returns (entity_id, unit, reason-it-cannot-be). A trend is only
    importable against an entity that exists and whose statistics are a
    mean series; everything else is reported so the screen and the log can
    say why rather than the sync silently doing nothing.
    """
    point = entry.get("point") or ""
    if not point:
        return None, None, "it is not paired to a point"

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"niagara_{stable_id(point)}",
    )
    if not entity_id:
        return None, None, (
            "its point has no sensor in Home Assistant — enable the point"
        )

    registry_entry = registry.async_get(entity_id)
    if registry_entry is not None and registry_entry.disabled_by is not None:
        return None, None, "its sensor is disabled in Home Assistant"

    state = hass.states.get(entity_id)
    unit = entry.get("unit") or None
    state_class = None
    if state is not None:
        unit = state.attributes.get("unit_of_measurement") or unit
        state_class = state.attributes.get("state_class")

    if state_class is not None and state_class not in IMPORTABLE_STATE_CLASSES:
        return None, None, (
            f"its sensor records {state_class} statistics, which need a "
            "cumulative sum this cannot derive from a window of samples"
        )

    return entity_id, unit, None


class HistorySync:
    """Pulls trend logs from the add-on and imports them hour by hour."""

    def __init__(self, hass: HomeAssistant, coordinator: NiagaraCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._running = False

    async def _get(self, path: str, **params) -> dict | None:
        session = self.coordinator._get_session()
        url = f"{self.coordinator.addon_url}{path}"
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                if response.status in (403, 404):
                    return None
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.warning("Trend request to %s failed: %s", path, err)
            return None

    async def _mark_synced(self, history: str, through: str, imported: int) -> None:
        """Tell the add-on how far this got.

        The watermark lives with the add-on because the selection does.
        Keeping it in Home Assistant would lose it whenever the integration
        was removed and re-added, and the next run would re-import
        everything the station holds.
        """
        session = self.coordinator._get_session()
        try:
            async with session.post(
                f"{self.coordinator.addon_url}/api/histories/synced",
                json={"history": history, "through": through,
                      "imported": imported},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                response.raise_for_status()
        except (aiohttp.ClientError, TimeoutError) as err:
            # Not fatal, but it means the next run repeats this window.
            _LOGGER.warning(
                "Could not record the sync watermark for %s: %s", history, err,
            )

    async def async_run(self, only: str | None = None) -> dict:
        """Sync every enabled trend, or just one. Returns what it did."""
        if self._running:
            return {"skipped": "a sync is already running"}
        self._running = True
        try:
            return await self._run(only)
        finally:
            self._running = False

    async def _run(self, only: str | None) -> dict:
        listing = await self._get("/api/integration/histories")
        if not listing or not listing.get("enabled"):
            return {"synced": 0, "reason": "trend syncing is off in the add-on"}

        entries = listing.get("histories") or []
        if only:
            entries = [e for e in entries if e.get("history") == only]
            if not entries:
                return {"synced": 0, "reason": f"{only} is not enabled for syncing"}

        results = []
        total = 0
        for entry in entries:
            outcome = await self._sync_one(entry)
            results.append(outcome)
            total += outcome.get("imported", 0)
        return {"synced": total, "histories": results}

    async def _sync_one(self, entry: dict) -> dict:
        name = entry.get("history", "")
        entity_id, unit, reason = resolve_target(
            self.hass, self.coordinator, entry,
        )
        if reason is not None:
            _LOGGER.info("Not syncing %s: %s", name, reason)
            return {"history": name, "imported": 0, "skipped": reason}

        until = current_hour()
        since = _parse(entry.get("last_synced"))
        if since is not None and since >= until:
            return {"history": name, "imported": 0, "skipped": "already current"}

        imported = 0
        last_hour: datetime | None = None
        cursor = since

        for _ in range(MAX_WINDOWS_PER_RUN):
            params = {"history": name, "end": until.isoformat()}
            if cursor is not None:
                params["start"] = cursor.isoformat()

            payload = await self._get("/api/integration/histories/data", **params)
            if payload is None:
                return {"history": name, "imported": imported,
                        "skipped": "the add-on could not read it"}

            buckets = [
                b for b in (payload.get("buckets") or [])
                if (_parse(b.get("start")) or until) < until
            ]
            if not buckets:
                break

            rows = _rows(buckets)
            if rows:
                _import_statistics(
                    self.hass, _metadata(entity_id, unit), rows,
                )
                imported += len(rows)
                # StatisticData is a TypedDict, so this is a plain lookup.
                last_hour = rows[-1]["start"]

            # A complete reply means the station had nothing more in the
            # window; anything else means it hit the per-query cap and the
            # next pass continues from the last hour it did return.
            if payload.get("complete", True):
                break
            next_cursor = _parse(buckets[-1].get("start"))
            if next_cursor is None or (cursor is not None and next_cursor <= cursor):
                # No forward progress: stop rather than loop on the same
                # window, which is how a sync becomes an infinite request
                # loop against a live station.
                break
            cursor = next_cursor + timedelta(hours=1)

        if imported and last_hour is not None:
            through = (last_hour + timedelta(hours=1)).isoformat()
            await self._mark_synced(name, through, imported)
            _LOGGER.info(
                "Imported %d hours of %s into %s", imported, name, entity_id,
            )

        return {
            "history": name, "entity_id": entity_id, "imported": imported,
        }
