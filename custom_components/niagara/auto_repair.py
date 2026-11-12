"""Find and remove false meter resets without being asked.

`meter_guard.TotalDipGuard` stops most bad readings from reaching Home
Assistant, but not every way a total can glitch can be seen from here (a
reading taken across a restart, a fault upstream in the station). So the
statistics are also checked on a timer, over the last few days, and any hour
inflated by a reset that never happened has the excess taken back out —
the same work as the `niagara.repair_meter_resets` service.

Only isolated hours are repaired. A sensor flagged again and again is not a
meter that glitched; it is left alone and logged (see `statistics`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    AUTO_REPAIR_DAYS,
    AUTO_REPAIR_FIRST_DELAY_MIN,
    AUTO_REPAIR_INTERVAL_H,
    AUTO_REPAIR_MAX_PER_ENTITY,
    CONF_AUTO_REPAIR,
    DEFAULT_AUTO_REPAIR,
)

_LOGGER = logging.getLogger(__name__)


def auto_repair_enabled(entry: ConfigEntry) -> bool:
    return bool(
        {**entry.data, **entry.options}.get(CONF_AUTO_REPAIR, DEFAULT_AUTO_REPAIR)
    )


async def _async_run(hass: HomeAssistant, _now: datetime | None = None) -> None:
    from .statistics import async_repair_false_resets

    try:
        await async_repair_false_resets(
            hass,
            days=AUTO_REPAIR_DAYS,
            dry_run=False,
            max_per_entity=AUTO_REPAIR_MAX_PER_ENTITY,
        )
    except Exception:  # noqa: BLE001 - a failed check must not stop the next
        _LOGGER.exception("Automatic meter reset repair failed")


@callback
def async_start_auto_repair(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Schedule the checks for as long as `entry` stays loaded."""
    if not auto_repair_enabled(entry):
        return

    async def _run(now: datetime | None = None) -> None:
        await _async_run(hass, now)

    entry.async_on_unload(
        async_call_later(hass, timedelta(minutes=AUTO_REPAIR_FIRST_DELAY_MIN), _run)
    )
    entry.async_on_unload(
        async_track_time_interval(hass, _run, timedelta(hours=AUTO_REPAIR_INTERVAL_H))
    )
