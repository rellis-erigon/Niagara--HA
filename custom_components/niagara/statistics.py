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

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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
