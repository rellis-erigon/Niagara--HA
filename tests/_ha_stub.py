"""Minimal Home Assistant stand-ins, installed only when HA is absent.

CI installs the real `homeassistant` package and these never load. They
exist so the suite also runs inside the add-on container and on a developer
machine without a 400 MB dependency tree — the alternative was integration
tests nobody could run locally, which is how the climate platform's mode
mapping would have gone untested until it reached a station.

Only what the platform modules actually import is stubbed. Anything beyond
that should fail loudly rather than be quietly faked.
"""

import sys
import types
from enum import StrEnum, IntFlag


def homeassistant_available() -> bool:
    try:
        import homeassistant  # noqa: F401
    except ImportError:
        return False
    return True


def _module(name: str, **attrs) -> types.ModuleType:
    """Register a stub module, wiring it onto its parent as a submodule.

    `from homeassistant.components.sensor import X` needs both the dotted
    name in sys.modules and the attribute on the parent, and the parent
    needs a __path__ to count as a package.
    """
    mod = types.ModuleType(name)
    mod.__path__ = []  # Treat every stub as a package; children are cheap.
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    if "." in name:
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child, mod)
    return mod


class _HVACMode(StrEnum):
    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    HEAT_COOL = "heat_cool"
    AUTO = "auto"
    DRY = "dry"
    FAN_ONLY = "fan_only"


class _HVACAction(StrEnum):
    OFF = "off"
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"
    DRYING = "drying"
    FAN = "fan"


class _ClimateEntityFeature(IntFlag):
    TARGET_TEMPERATURE = 1
    TARGET_TEMPERATURE_RANGE = 2
    TARGET_HUMIDITY = 4
    FAN_MODE = 8
    PRESET_MODE = 16
    SWING_MODE = 32
    AUX_HEAT = 64
    TURN_OFF = 128
    TURN_ON = 256


class _FanEntityFeature(IntFlag):
    SET_SPEED = 1
    OSCILLATE = 2
    DIRECTION = 4
    PRESET_MODE = 8
    TURN_OFF = 16
    TURN_ON = 32


class _SensorDeviceClass(StrEnum):
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PRESSURE = "pressure"
    POWER = "power"
    ENERGY = "energy"
    VOLTAGE = "voltage"
    CURRENT = "current"
    FREQUENCY = "frequency"
    VOLUME = "volume"
    VOLUME_FLOW_RATE = "volume_flow_rate"
    WATER = "water"
    GAS = "gas"
    APPARENT_POWER = "apparent_power"
    POWER_FACTOR = "power_factor"
    REACTIVE_POWER = "reactive_power"
    BATTERY = "battery"
    CO2 = "carbon_dioxide"
    DURATION = "duration"
    SPEED = "speed"
    ENUM = "enum"
    SIGNAL_STRENGTH = "signal_strength"
    ILLUMINANCE = "illuminance"


class _SensorStateClass(StrEnum):
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class _BinarySensorDeviceClass(StrEnum):
    PROBLEM = "problem"
    RUNNING = "running"
    OPENING = "opening"
    DOOR = "door"
    MOISTURE = "moisture"
    MOTION = "motion"
    OCCUPANCY = "occupancy"
    SMOKE = "smoke"
    HEAT = "heat"
    COLD = "cold"
    POWER = "power"
    PRESENCE = "presence"
    SAFETY = "safety"
    TAMPER = "tamper"
    GAS = "gas"
    LOCK = "lock"
    PLUG = "plug"
    CONNECTIVITY = "connectivity"
    BATTERY = "battery"
    BATTERY_CHARGING = "battery_charging"
    UPDATE = "update"
    VIBRATION = "vibration"
    WINDOW = "window"
    SOUND = "sound"
    LIGHT = "light"


class _UnitOfTemperature(StrEnum):
    CELSIUS = "°C"
    FAHRENHEIT = "°F"
    KELVIN = "K"


class _Platform(StrEnum):
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    CLIMATE = "climate"
    FAN = "fan"


class _Entity:
    """Enough of the Entity contract for the property logic under test.

    Real HA resolves each entity property from its `_attr_` counterpart, so
    the stub does the same for the ones the platforms set. Anything not
    listed here raises, which is the point: a test must not pass locally on
    a property the real class would compute differently.
    """

    _attr_name = None

    def _attr(self, name, default=None):
        return getattr(self, f"_attr_{name}", default)

    @property
    def name(self):
        return self._attr("name")

    @property
    def supported_features(self):
        return self._attr("supported_features")

    @property
    def device_info(self):
        return self._attr("device_info")

    @property
    def unique_id(self):
        return self._attr("unique_id")

    @property
    def temperature_unit(self):
        return self._attr("temperature_unit")

    @property
    def hvac_modes(self):
        return self._attr("hvac_modes", [])

    @property
    def min_temp(self):
        return self._attr("min_temp")

    @property
    def max_temp(self):
        return self._attr("max_temp")


class _CoordinatorEntity(_Entity):
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator


class _Generic:
    """A base that accepts a subscript, as the real generic classes do."""

    def __class_getitem__(cls, _item):
        return cls


def _install_aiohttp() -> None:
    """The coordinator imports aiohttp at module level for its session.

    None of the state logic touches it, and aiohttp ships compiled wheels
    that will not install here, so it is stubbed to the two names used.
    """
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        _module(
            "aiohttp",
            ClientSession=object,
            ClientTimeout=lambda **kwargs: None,
            ClientError=type("ClientError", (Exception,), {}),
        )


def install() -> None:
    """Register the stubs. A no-op when the real package is importable."""
    _install_aiohttp()
    if homeassistant_available():
        return

    _module("homeassistant")
    _module("homeassistant.const", Platform=_Platform,
            UnitOfTemperature=_UnitOfTemperature)
    _module(
        "homeassistant.core",
        HomeAssistant=object,
        ServiceCall=object,
        ServiceResponse=dict,
        SupportsResponse=StrEnum("SupportsResponse", "NONE OPTIONAL ONLY"),
        callback=lambda f: f,
    )
    _module("homeassistant.config_entries", ConfigEntry=object,
            ConfigFlow=object, OptionsFlow=object)
    _module("homeassistant.exceptions",
            HomeAssistantError=type("HomeAssistantError", (Exception,), {}),
            ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}))

    _module("homeassistant.components")
    _module("homeassistant.components.climate", ClimateEntity=_Entity,
            ClimateEntityFeature=_ClimateEntityFeature,
            HVACAction=_HVACAction, HVACMode=_HVACMode)
    _module("homeassistant.components.fan", FanEntity=_Entity,
            FanEntityFeature=_FanEntityFeature)
    _module("homeassistant.components.sensor", SensorEntity=_Entity,
            SensorDeviceClass=_SensorDeviceClass,
            SensorStateClass=_SensorStateClass)
    _module("homeassistant.components.binary_sensor",
            BinarySensorEntity=_Entity,
            BinarySensorDeviceClass=_BinarySensorDeviceClass)
    _module("homeassistant.components.repairs", RepairsFlow=object)
    _module("homeassistant.data_entry_flow", FlowResult=dict)

    helpers = _module("homeassistant.helpers")
    _module("homeassistant.helpers.entity_platform",
            AddEntitiesCallback=object)
    _module("homeassistant.helpers.aiohttp_client",
            async_get_clientsession=lambda hass: None)
    _module("homeassistant.helpers.config_validation")
    _module("homeassistant.helpers.issue_registry",
            IssueSeverity=StrEnum("IssueSeverity", "CRITICAL ERROR WARNING"),
            async_create_issue=lambda *a, **k: None,
            async_delete_issue=lambda *a, **k: None)
    _module("homeassistant.helpers.update_coordinator",
            CoordinatorEntity=_CoordinatorEntity,
            DataUpdateCoordinator=_Generic,
            UpdateFailed=type("UpdateFailed", (Exception,), {}))
    _module("homeassistant.helpers.device_registry", DeviceInfo=dict,
            async_get=lambda hass: None)
    _module("homeassistant.helpers.entity_registry", EntityRegistry=object,
            async_get=lambda hass: None,
            async_entries_for_device=lambda *a, **k: [])
    helpers.device_registry = sys.modules["homeassistant.helpers.device_registry"]
    helpers.entity_registry = sys.modules["homeassistant.helpers.entity_registry"]
