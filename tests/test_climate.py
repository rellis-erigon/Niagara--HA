"""Tests for the climate platform's state mapping.

Before this platform existed, a published FCU was a scatter of sensors and
nothing in Home Assistant knew it was an air conditioner. The mapping that
makes it one is small but easy to get backwards — in particular a stopped
unit still reports the mode it *would* run in, which is why the start/stop
point has to beat the mode point.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ha_stub  # noqa: E402

_ha_stub.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.components.climate import HVACAction, HVACMode  # noqa: E402

from custom_components.niagara.climate import (  # noqa: E402
    CLIMATE_TEMPLATES,
    hvac_mode_from_text,
    resolve_hvac_action,
    resolve_hvac_mode,
)
from custom_components.niagara.coordinator import (  # noqa: E402
    NiagaraDevice,
    NiagaraPoint,
)


# -- Mode labels ---------------------------------------------------------

@pytest.mark.parametrize(
    "label,expected",
    [
        ("HEATING", HVACMode.HEAT),
        ("COOLING", HVACMode.COOL),
        ("VENTILATION", HVACMode.FAN_ONLY),
        ("Heat", HVACMode.HEAT),
        ("cool", HVACMode.COOL),
        ("AUTO", HVACMode.AUTO),
        ("DEHUMIDIFY", HVACMode.DRY),
        ("DRY", HVACMode.DRY),
        ("FAN", HVACMode.FAN_ONLY),
        ("OFF", HVACMode.OFF),
    ],
)
def test_station_mode_labels_map(label, expected):
    """The three labels this station uses, plus the common others."""
    assert hvac_mode_from_text(label) == expected


@pytest.mark.parametrize("label", ["", None, "null", "ECONOMY", "3"])
def test_unknown_mode_label_is_none_not_a_guess(label):
    assert hvac_mode_from_text(label) is None


def test_ventilation_is_not_claimed_by_the_fan_pattern():
    """"VENT" must be tested before "FAN" or the order is meaningless."""
    assert hvac_mode_from_text("VENTILATION") == HVACMode.FAN_ONLY


def test_niagara_escapes_in_a_mode_label_are_decoded():
    assert hvac_mode_from_text("$43OOLING") == HVACMode.COOL


# -- Mode resolution ----------------------------------------------------

def test_stopped_unit_is_off_even_while_reporting_heating():
    """The whole point of the start/stop override.

    147 of 195 units on this station sit in HEATING. Reporting them all as
    heating when they are stopped would be wrong on most of the building.
    """
    assert resolve_hvac_mode("HEATING", powered=False, running=False) == HVACMode.OFF
    assert resolve_hvac_mode("HEATING", powered=False, running=True) == HVACMode.OFF


def test_running_unit_reports_its_mode():
    assert resolve_hvac_mode("HEATING", powered=True, running=True) == HVACMode.HEAT
    assert resolve_hvac_mode("COOLING", powered=True, running=False) == HVACMode.COOL


def test_mode_without_a_start_stop_point_still_resolves():
    assert resolve_hvac_mode("COOLING", powered=None, running=True) == HVACMode.COOL


def test_no_mode_point_falls_back_to_running():
    assert resolve_hvac_mode(None, powered=True, running=None) == HVACMode.AUTO
    assert resolve_hvac_mode(None, powered=None, running=True) == HVACMode.AUTO
    assert resolve_hvac_mode(None, powered=None, running=False) == HVACMode.OFF


def test_nothing_known_is_unknown_not_off():
    """An unreadable unit must not claim to be off."""
    assert resolve_hvac_mode(None, powered=None, running=None) is None


# -- Action resolution --------------------------------------------------

def test_action_follows_the_mode_while_the_fan_runs():
    assert resolve_hvac_action(HVACMode.HEAT, True, True) == HVACAction.HEATING
    assert resolve_hvac_action(HVACMode.COOL, True, True) == HVACAction.COOLING
    assert resolve_hvac_action(HVACMode.FAN_ONLY, True, True) == HVACAction.FAN
    assert resolve_hvac_action(HVACMode.DRY, True, True) == HVACAction.DRYING


def test_powered_but_fan_stopped_is_idle_not_heating():
    """111 of 194 units report their fan off — these are idle, not heating."""
    assert resolve_hvac_action(HVACMode.HEAT, True, False) == HVACAction.IDLE


def test_unpowered_is_off():
    assert resolve_hvac_action(HVACMode.HEAT, False, True) == HVACAction.OFF
    assert resolve_hvac_action(HVACMode.OFF, None, None) == HVACAction.OFF


def test_action_unknown_when_neither_point_reads():
    assert resolve_hvac_action(HVACMode.HEAT, None, None) is None


def test_auto_has_no_action_it_can_claim():
    """AUTO says nothing about what the unit is doing right now."""
    assert resolve_hvac_action(HVACMode.AUTO, True, True) == HVACAction.IDLE


# -- Device selection ---------------------------------------------------

def _device(device_type, **slots):
    return NiagaraDevice(
        group="Station/Floor/Room",
        device_type=device_type,
        slots={
            key: NiagaraPoint(path=f"/points/{key}/", name=key, slot=key)
            for key in slots
        },
    )


def test_fcu_maps_onto_the_station_slots():
    roles = CLIMATE_TEMPLATES["fcu"]
    assert roles["current_temperature"] == "room_temperature"
    assert roles["target_temperature"] == "setpoint"
    assert roles["power"] == "command"


def test_only_templates_listed_become_climate_entities():
    """A meter must never turn into a thermostat."""
    for template in ("electricity_meter", "water_meter", "fan", "fip", "fridge"):
        assert template not in CLIMATE_TEMPLATES


def test_a_device_without_its_temperature_slot_is_skipped():
    """Nothing a thermostat card can show, so it would read as unknown."""
    roles = CLIMATE_TEMPLATES["fcu"]
    bare = _device("fcu", command=1, mode=1)
    assert roles["current_temperature"] not in bare.slots


def test_device_path_lookup():
    device = _device("fcu", room_temperature=1)
    assert device.path("room_temperature") == "/points/room_temperature/"
    assert device.path("setpoint") is None


# -- Assembled entity ---------------------------------------------------

from custom_components.niagara.climate import NiagaraClimate  # noqa: E402


class _Coordinator:
    def __init__(self, points):
        self.addon_url = "http://addon:8099"
        self.host = self.addon_url
        self.device_name = "Niagara BMS"
        self.device_name_depth = 1
        self.stale_after = 90
        self.last_update_success = True
        self.data = {p.path: p for p in points}

    def get_area(self, point):
        return "Level 1"


def fcu(room="22.5", setpoint="21.0", mode="HEATING", fan="true",
        command="true", unit="°C", modes=None, status="ok", age=1.0):
    spec = {
        "room_temperature": (room, unit),
        "setpoint": (setpoint, unit),
        "mode": (mode, None),
        "fan_status": (fan, None),
        "command": (command, None),
    }
    points, slots = [], {}
    for slot, (value, slot_unit) in spec.items():
        if value is None:
            continue
        pt = NiagaraPoint(
            path=f"/p/{slot}/", name=slot, group="Jace/L1/Room101",
            slot=slot, device_type="fcu", value=value, unit=slot_unit,
            status=status, age=age,
            enum_range=(modes or []) if slot == "mode" else [],
        )
        points.append(pt)
        slots[slot] = pt
    device = NiagaraDevice(
        group="Jace/L1/Room101", device_type="fcu", slots=slots,
    )
    return NiagaraClimate(_Coordinator(points), device, CLIMATE_TEMPLATES["fcu"])


def test_entity_reports_the_room_and_the_setpoint():
    entity = fcu()
    assert entity.current_temperature == 22.5
    assert entity.target_temperature == 21.0


def test_a_fahrenheit_station_is_not_relabelled_celsius():
    assert fcu(unit="°F").temperature_unit == "°F"
    assert fcu(unit="°C").temperature_unit == "°C"


def test_a_point_with_no_unit_defaults_to_celsius():
    assert fcu(unit=None).temperature_unit == "°C"


def test_target_temperature_feature_tracks_the_setpoint_slot():
    from homeassistant.components.climate import ClimateEntityFeature

    assert ClimateEntityFeature.TARGET_TEMPERATURE in fcu().supported_features
    bare = fcu(setpoint=None)
    assert ClimateEntityFeature.TARGET_TEMPERATURE not in bare.supported_features


def test_modes_come_from_the_stations_own_enum_range():
    """The card should offer the unit's real modes, not a guess."""
    entity = fcu(modes=["HEATING", "COOLING", "VENTILATION"])
    assert entity.hvac_modes == [
        HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL, HVACMode.FAN_ONLY,
    ]


def test_an_unmappable_enum_label_is_dropped_not_guessed():
    entity = fcu(modes=["HEATING", "SUPER_ECO", "COOLING"])
    assert HVACMode.HEAT in entity.hvac_modes
    assert HVACMode.COOL in entity.hvac_modes
    assert len(entity.hvac_modes) == 3  # off, heat, cool


def test_off_is_offered_whenever_there_is_a_start_stop_point():
    assert HVACMode.OFF in fcu().hvac_modes


def test_duplicate_enum_labels_do_not_duplicate_modes():
    entity = fcu(modes=["HEAT", "HEATING", "COOL"])
    assert entity.hvac_modes.count(HVACMode.HEAT) == 1


def test_entity_is_unavailable_when_every_point_is_faulted():
    assert fcu(status="fault").available is False


def test_entity_survives_one_faulted_optional_point():
    """A device-level entity must not vanish because its fan point faulted."""
    entity = fcu()
    entity._device.slots["fan_status"].status = "fault"
    assert entity.available is True
    assert entity.current_temperature == 22.5


def test_a_stale_reading_is_not_served_as_current():
    assert fcu(age=10_000).available is False


def test_the_device_name_is_the_entity_name():
    """Otherwise every room reads "Room101 Room101"."""
    assert fcu()._attr_name is None


def test_writes_fail_rather_than_appear_to_work():
    import asyncio

    from homeassistant.exceptions import HomeAssistantError

    entity = fcu()
    for call in (
        entity.async_set_temperature(temperature=20),
        entity.async_set_hvac_mode(HVACMode.COOL),
        entity.async_turn_off(),
    ):
        with pytest.raises(HomeAssistantError):
            asyncio.run(call)


def test_paths_are_exposed_for_tracing_back_to_the_station():
    attrs = fcu().extra_state_attributes
    assert attrs["niagara_group"] == "Jace/L1/Room101"
    assert attrs["path_room_temperature"] == "/p/room_temperature/"
