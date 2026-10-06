"""Fan platform for Niagara BMS.

A published fan or VFD has its run status, speed and fault already named by
its template, so it can be a real `fan` entity rather than three sensors
that happen to sit on the same device. Read-only, for the reason given in
climate.py: the setters are declared so the native card shows the speed,
and they fail loudly rather than pretending to write.
"""

import logging

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NiagaraCoordinator, NiagaraDevice
from .entity import NiagaraDeviceEntity

_LOGGER = logging.getLogger(__name__)

# Templates whose devices become a fan entity, and the slot each role reads
# from. "running" is required — without it there is no on/off to report.
FAN_TEMPLATES: dict[str, dict[str, str]] = {
    "fan": {"running": "run_status", "speed": "speed", "power": "command"},
    "ahu": {"running": "supply_fan_status"},
}

READ_ONLY = (
    "The Niagara bridge is read-only — it cannot write to the station. "
    "Change this at the BMS."
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NiagaraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[FanEntity] = []
    for device in coordinator.devices().values():
        roles = FAN_TEMPLATES.get(device.device_type)
        if roles is None:
            continue
        if roles["running"] not in device.slots:
            continue
        entities.append(NiagaraFan(coordinator, device, roles))
    if entities:
        _LOGGER.info("Creating %d fan entities", len(entities))
    async_add_entities(entities)


class NiagaraFan(NiagaraDeviceEntity, FanEntity):
    """A published fan or VFD as a fan entity."""

    DOMAIN_KEY = "fan"
    _attr_name = None  # The device name is the entity name.
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        coordinator: NiagaraCoordinator,
        device: NiagaraDevice,
        roles: dict[str, str],
    ) -> None:
        super().__init__(coordinator, device)
        self._roles = roles

        speed = device.slots.get(roles.get("speed", ""))
        # Only a percentage is a percentage. A VFD reporting 43 Hz or
        # 1,450 rpm would read as 43% or clip at 100, so those stay on
        # their own sensor and the fan reports on/off only.
        self._speed_is_percent = bool(speed and (speed.unit or "").strip() == "%")

        features = FanEntityFeature(0)
        if self._speed_is_percent:
            features |= FanEntityFeature.SET_SPEED
        if roles.get("power") and roles["power"] in device.slots:
            features |= FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
        self._attr_supported_features = features

    @property
    def is_on(self) -> bool | None:
        running = self._boolean(self._roles["running"])
        if running is not None:
            return running
        key = self._roles.get("power")
        return self._boolean(key) if key else None

    @property
    def percentage(self) -> int | None:
        if not self._speed_is_percent:
            return None
        value = self._number(self._roles["speed"])
        if value is None:
            return None
        return max(0, min(100, round(value)))

    @property
    def extra_state_attributes(self) -> dict | None:
        attrs = dict(super().extra_state_attributes or {})
        # A speed the fan entity cannot express as a percentage is still
        # worth reporting, with its unit, rather than silently dropped.
        if not self._speed_is_percent and self._roles.get("speed"):
            value = self._number(self._roles["speed"])
            if value is not None:
                point = self._device.slots[self._roles["speed"]]
                attrs["speed"] = value
                if point.unit:
                    attrs["speed_unit"] = point.unit
        return attrs

    # -- Writes ----------------------------------------------------------

    async def async_set_percentage(self, percentage: int) -> None:
        raise HomeAssistantError(READ_ONLY)

    async def async_turn_on(self, **kwargs) -> None:
        raise HomeAssistantError(READ_ONLY)

    async def async_turn_off(self, **kwargs) -> None:
        raise HomeAssistantError(READ_ONLY)
