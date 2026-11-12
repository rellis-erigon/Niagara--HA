"""Reconcile recorded statistics with what an entity now reports.

When a point's unit changes — because an override was set, or a template
assigned one — the long-term statistics keep the unit they were first
recorded under. Home Assistant then refuses to record anything further for
that entity and logs a conflict, which is the only sign anything is wrong.
The energy dashboard shows a meter that simply stops.

Fixing it by hand means finding every affected statistic id and rewriting
its metadata. Doing 159 of those by hand is how this module came to exist.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_REPAIR_LOCK = asyncio.Lock()


def _current_unit(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return state.attributes.get("unit_of_measurement")


async def async_find_unit_conflicts(hass: HomeAssistant) -> list[dict]:
    """Statistics whose recorded unit no longer matches the entity's.

    Only this integration's entities are considered. Rewriting metadata for
    something we do not own is not ours to do.
    """
    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import list_statistic_ids
    except ImportError:
        _LOGGER.debug("Recorder is not available")
        return []

    registry = er.async_get(hass)
    ours = {
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN
    }
    if not ours:
        return []

    try:
        metadata = await get_instance(hass).async_add_executor_job(
            list_statistic_ids, hass
        )
    except Exception as err:  # noqa: BLE001 - diagnosis must not break setup
        _LOGGER.debug("Could not list statistics: %s", err)
        return []

    conflicts = []
    for entry in metadata or []:
        statistic_id = entry.get("statistic_id")
        if statistic_id not in ours:
            continue
        recorded = entry.get("display_unit_of_measurement") or entry.get(
            "unit_of_measurement"
        )
        current = _current_unit(hass, statistic_id)
        # An entity with no unit at all is usually one that is unavailable
        # right now, not one whose unit was removed.
        if current is None or recorded == current:
            continue
        conflicts.append({
            "statistic_id": statistic_id,
            "recorded_unit": recorded,
            "current_unit": current,
        })
    return conflicts


async def async_fix_statistics_units(hass: HomeAssistant) -> list[dict]:
    """Rewrite statistics metadata to the unit the entity now reports."""
    conflicts = await async_find_unit_conflicts(hass)
    if not conflicts:
        return []

    try:
        from homeassistant.components.recorder.statistics import (
            async_update_statistics_metadata,
        )
    except ImportError:
        _LOGGER.warning("Recorder does not expose statistics metadata updates")
        return []

    fixed = []
    for conflict in conflicts:
        try:
            async_update_statistics_metadata(
                hass,
                conflict["statistic_id"],
                new_unit_of_measurement=conflict["current_unit"],
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Could not update statistics for %s: %s",
                conflict["statistic_id"], err,
            )
            continue
        fixed.append(conflict)

    if fixed:
        _LOGGER.info(
            "Rewrote the statistics unit for %d entities; "
            "the first is %s (%s -> %s)",
            len(fixed), fixed[0]["statistic_id"],
            fixed[0]["recorded_unit"], fixed[0]["current_unit"],
        )
    return fixed


async def async_repair_false_resets(
    hass: HomeAssistant,
    entity_id: str | None = None,
    days: int = 90,
    dry_run: bool = True,
    max_per_entity: int | None = None,
) -> list[dict]:
    """Find, and unless `dry_run` remove, false resets in meter statistics.

    A running total that dipped for one poll is recorded by Home Assistant
    as a reset followed by the meter's entire lifetime of consumption, all
    in one hour. See `meter_guard`. This finds those hours in the hourly
    long-term statistics of this integration's `total_increasing` sensors
    and takes the excess back out of the running sum from that hour on, the
    same adjustment Developer Tools → Statistics makes by hand.

    A sensor with more than `max_per_entity` such hours is reported but not
    changed: that many is not a meter that glitched, it is a reading that is
    not a running total at all, and rewriting its sum would only hide that.
    Runs one at a time, and waits for the recorder to apply its adjustments,
    so two runs can never take the same excess out twice.
    """
    async with _REPAIR_LOCK:
        return await _async_repair_false_resets(
            hass, entity_id, days, dry_run, max_per_entity,
        )


async def _async_repair_false_resets(
    hass: HomeAssistant,
    entity_id: str | None,
    days: int,
    dry_run: bool,
    max_per_entity: int | None,
) -> list[dict]:
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from .meter_guard import find_false_resets

    try:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import (
            statistics_during_period,
        )
    except ImportError as err:
        _LOGGER.warning("Recorder statistics are not available: %s", err)
        return []

    recorder = get_instance(hass)

    def async_adjust_statistics(hass, statistic_id, start_time, adjustment, unit):
        # The recorder instance owns this (it queues the change on the
        # recorder thread); older releases had a module-level function.
        if hasattr(recorder, "async_adjust_statistics"):
            recorder.async_adjust_statistics(statistic_id, start_time, adjustment, unit)
            return
        from homeassistant.components.recorder.statistics import (
            async_adjust_statistics as adjust,
        )
        adjust(hass, statistic_id, start_time, adjustment, unit)

    registry = er.async_get(hass)
    candidates = []
    for entry in registry.entities.values():
        if entry.platform != DOMAIN or not entry.entity_id.startswith("sensor."):
            continue
        if entity_id and entry.entity_id != entity_id:
            continue
        state = hass.states.get(entry.entity_id)
        if state is None or state.attributes.get("state_class") != "total_increasing":
            continue
        candidates.append((entry.entity_id, state.attributes.get("unit_of_measurement")))

    end = dt_util.utcnow()
    start = end - timedelta(days=days)
    results: list[dict] = []
    skipped: list[tuple[str, int]] = []
    for stat_id, unit in candidates:
        # Without `units`, values come back in the unit the entity shows now,
        # which is also the unit the adjustment below is given in.
        stats = await recorder.async_add_executor_job(
            statistics_during_period,
            hass, start, end, {stat_id}, "hour", None, {"state", "sum"},
        )
        rows = []
        for row in stats.get(stat_id, []):
            begins = row.get("start")
            if isinstance(begins, (int, float)):
                begins = dt_util.utc_from_timestamp(begins)
            rows.append({"start": begins, "state": row.get("state"), "sum": row.get("sum")})

        found = find_false_resets(rows)
        if max_per_entity is not None and len(found) > max_per_entity:
            skipped.append((stat_id, len(found)))
            continue
        for bad in found:
            item = {
                "entity_id": stat_id,
                "hour": bad.start.isoformat(),
                "excess": round(bad.excess, 3),
                "unit": unit,
            }
            if not dry_run:
                async_adjust_statistics(hass, stat_id, bad.start, -bad.excess, unit)
                item["fixed"] = True
            results.append(item)

    if any(item.get("fixed") for item in results) and hasattr(
        recorder, "async_block_till_done",
    ):
        await recorder.async_block_till_done()

    for stat_id, count in skipped:
        _LOGGER.warning(
            "%s jumped like a false meter reset %d times in %d days; left "
            "alone, as it does not behave like a running total. If it is an "
            "instantaneous reading, give it a measurement state class",
            stat_id, count, days,
        )
    for item in results:
        _LOGGER.warning(
            "%s false meter reset: %s at %s, %s %s",
            "Removed" if item.get("fixed") else "Found",
            item["entity_id"], item["hour"], item["excess"], item["unit"],
        )
    if not results and not skipped:
        _LOGGER.info("No false meter resets found in the last %d days", days)
    return results
