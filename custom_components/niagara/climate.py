"""Climate platform for Niagara BMS.

A published FCU is already described by its template — room temperature,
setpoint, mode, fan and start/stop all identified by slot — so it can be a
real `climate` entity instead of a handful of unrelated sensors. That gets
the native thermostat card, `current_temperature` in automations and voice
assistant exposure without anyone having to know what a Niagara point is.

The bridge is read-only by deliberate decision, so the setters raise rather
than write. The features are still declared: without TARGET_TEMPERATURE,
Home Assistant strips the setpoint out of the state entirely and the card
shows a bare number, which defeats the purpose. When two-way lands, the
setters become real and no entity id or attribute name changes.
"""

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NiagaraCoordinator, NiagaraDevice, decode_niagara_name
from .entity import NiagaraDeviceEntity

_LOGGER = logging.getLogger(__name__)

# Templates whose devices become a climate entity, and the slot each role
# reads from. A template absent here keeps its plain sensors.
CLIMATE_TEMPLATES: dict[str, dict[str, str]] = {
    "fcu": {
        "current_temperature": "room_temperature",
        "target_temperature": "setpoint",
        "mode": "mode",
        "running": "fan_status",
        "power": "command",
    },
    "ahu": {
        "current_temperature": "supply_air_temp",
        "running": "supply_fan_status",
    },
}

# Niagara enum labels vary by controller, so match on a substring rather
# than equality. Order matters: "FAN" would otherwise claim "FAN_ONLY".
MODE_WORDS: tuple[tuple[str, HVACMode], ...] = (
    ("HEAT", HVACMode.HEAT),
    ("COOL", HVACMode.COOL),
    ("DEHUM", HVACMode.DRY),
    ("DRY", HVACMode.DRY),
    ("VENT", HVACMode.FAN_ONLY),
    ("FAN", HVACMode.FAN_ONLY),
    ("AUTO", HVACMode.AUTO),
    ("OFF", HVACMode.OFF),
)

ACTION_BY_MODE: dict[HVACMode, HVACAction] = {
    HVACMode.HEAT: HVACAction.HEATING,
    HVACMode.COOL: HVACAction.COOLING,
    HVACMode.DRY: HVACAction.DRYING,
    HVACMode.FAN_ONLY: HVACAction.FAN,
}

READ_ONLY = (
    "The Niagara bridge is read-only — it cannot write to the station. "
    "Change this at the BMS."
)


def hvac_mode_from_text(text: str | None) -> HVACMode | None:
    """Map a Niagara mode label onto an HVAC mode."""
    if not text:
        return None
    upper = decode_niagara_name(text).upper()
    for word, mode in MODE_WORDS:
        if word in upper:
            return mode
    return None


def resolve_hvac_mode(
    mode_text: str | None, powered: bool | None, running: bool | None,
) -> HVACMode | None:
    """The mode a unit is in, with a stopped unit reported as off.

    A stopped FCU still reports HEATING on its mode point — that is the mode
    it would run in, not what it is doing — so the start/stop point has to
    win, or every idle unit in the building reads as heating.
    """
    if powered is False:
        return HVACMode.OFF
    mode = hvac_mode_from_text(mode_text)
    if mode is not None:
        return mode
    if powered is True or running is True:
        return HVACMode.AUTO
    if running is False:
        return HVACMode.OFF
    return None


def resolve_hvac_action(
    mode: HVACMode | None, powered: bool | None, running: bool | None,
) -> HVACAction | None:
    """What the unit is doing now, as distinct from the mode it is in."""
    if powered is False:
        return HVACAction.OFF
    if running is False:
        return HVACAction.IDLE
    if mode == HVACMode.OFF:
        return HVACAction.OFF
    if running is None and powered is None:
        return None
    return ACTION_BY_MODE.get(mode, HVACAction.IDLE)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: NiagaraCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ClimateEntity] = []
    for device in coordinator.devices().values():
        roles = CLIMATE_TEMPLATES.get(device.device_type)
        if roles is None:
            continue
        # Without a temperature there is nothing a thermostat card can show,
        # and the entity would read as permanently unknown.
        if roles["current_temperature"] not in device.slots:
            continue
        entities.append(NiagaraClimate(coordinator, device, roles))
    if entities:
        _LOGGER.info("Creating %d climate entities", len(entities))
    async_add_entities(entities)


class NiagaraClimate(NiagaraDeviceEntity, ClimateEntity):
    """A published HVAC device as a climate entity."""

    DOMAIN_KEY = "climate"
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

        current = device.slots[roles["current_temperature"]]
        self._attr_temperature_unit = (
            UnitOfTemperature.FAHRENHEIT if (current.unit or "").endswith("F")
            else UnitOfTemperature.CELSIUS
        )

        features = ClimateEntityFeature(0)
        if self._role_slot("target_temperature"):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE
        if self._role_slot("power"):
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        self._attr_supported_features = features

        self._attr_hvac_modes = self._discover_modes()

        if self._role_slot("target_temperature") is not None:
            # Room-setpoint bounds, matching the range the fcu template
            # declares for the slot. The template's validation is not sent
            # to the integration, so this is a second copy of that number;
            # the two only matter together if a station is commissioned
            # outside it, and nothing here can write a setpoint anyway.
            self._attr_min_temp = 5
            self._attr_max_temp = 40
            self._attr_target_temperature_step = 0.5

    def _role_slot(self, role: str):
        """The point filling a role, by template slot — regardless of health."""
        key = self._roles.get(role)
        return self._device.slots.get(key) if key else None

    def _discover_modes(self) -> list[HVACMode]:
        """The modes this unit can report.

        Taken from the mode point's own enum range where the station
        publishes one, so the card offers exactly the unit's real modes
        rather than a guess. OFF is always included when the unit has a
        start/stop point, because that is a state it genuinely reaches.
        """
        modes: list[HVACMode] = []
        mode_point = self._role_slot("mode")
        if mode_point is not None:
            for label in mode_point.enum_range:
                mapped = hvac_mode_from_text(label)
                if mapped and mapped not in modes:
                    modes.append(mapped)
        if not modes and mode_point is not None:
            modes = [HVACMode.HEAT, HVACMode.COOL, HVACMode.FAN_ONLY]
        if self._role_slot("power") is not None and HVACMode.OFF not in modes:
            modes.insert(0, HVACMode.OFF)
        # A unit with neither a mode nor a start/stop point still has to
        # offer something; it reports whether it is running.
        return modes or [HVACMode.OFF, HVACMode.AUTO]

    @property
    def _is_powered(self) -> bool | None:
        key = self._roles.get("power")
        return self._boolean(key) if key else None

    @property
    def _is_running(self) -> bool | None:
        key = self._roles.get("running")
        return self._boolean(key) if key else None

    @property
    def current_temperature(self) -> float | None:
        return self._number(self._roles["current_temperature"])

    @property
    def target_temperature(self) -> float | None:
        key = self._roles.get("target_temperature")
        return self._number(key) if key else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        key = self._roles.get("mode")
        return resolve_hvac_mode(
            self._text(key) if key else None, self._is_powered, self._is_running,
        )

    @property
    def hvac_action(self) -> HVACAction | None:
        return resolve_hvac_action(
            self.hvac_mode, self._is_powered, self._is_running,
        )

    # -- Writes ----------------------------------------------------------
    # Declared so the native card renders the real data. The bridge does
    # not write to the station, so each one fails loudly rather than
    # appearing to work.

    async def async_set_temperature(self, **kwargs) -> None:
        raise HomeAssistantError(READ_ONLY)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        raise HomeAssistantError(READ_ONLY)

    async def async_turn_on(self) -> None:
        raise HomeAssistantError(READ_ONLY)

    async def async_turn_off(self) -> None:
        raise HomeAssistantError(READ_ONLY)
