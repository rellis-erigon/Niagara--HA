"""Event platform for Niagara BMS alarms.

An alarm arriving is a thing that happened, which is what an `event` entity
is for. The counts and the per-device problem flags describe the *state* of
the console; this fires once per new alarm, so an automation can notify on
it without polling a count and working out what changed.
"""

import logging

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alarm_state import alarm_detail, alarm_key, by_priority
from .const import DOMAIN
from .coordinator import NiagaraCoordinator
from .entity import NiagaraStationEntity

_LOGGER = logging.getLogger(__name__)

EVENT_ALARM = "alarm"
EVENT_UNACKED = "unacknowledged_alarm"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NiagaraCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.alarms_supported:
        _LOGGER.debug("Station exposes no alarm service — no alarm event entity")
        return
    async_add_entities([NiagaraAlarmEvent(coordinator)])


class NiagaraAlarmEvent(NiagaraStationEntity, EventEntity):
    """Fires when a new alarm appears in the station's console."""

    KEY = "alarm_event"
    _attr_name = "Alarm"
    _attr_icon = "mdi:bell-alert"
    _attr_event_types = [EVENT_ALARM, EVENT_UNACKED]

    def __init__(self, coordinator: NiagaraCoordinator) -> None:
        super().__init__(coordinator)
        # Seeded on the first refresh rather than here, so the alarms
        # already standing when Home Assistant restarts do not all fire as
        # if they were new. A restart is not an alarm.
        self._seen: set[str] | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        records = self.coordinator.alarms
        keys = {alarm_key(r) for r in records}

        if self._seen is None:
            self._seen = keys
            super()._handle_coordinator_update()
            return

        fresh = [r for r in records if alarm_key(r) not in self._seen]
        self._seen = keys

        if fresh:
            # One entity, so one event per refresh: the most urgent new
            # alarm, with the rest counted. Firing several in a row would
            # leave only the last visible in the entity's state anyway.
            ordered = by_priority(fresh)
            first = ordered[0]
            detail = alarm_detail(first)
            self._trigger_event(
                EVENT_UNACKED if not first.get("acked") else EVENT_ALARM,
                {**detail, "new_alarm_count": len(ordered)},
            )
        super()._handle_coordinator_update()
