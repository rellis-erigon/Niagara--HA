"""Tests for device-class inference.

Regression cover for classes that contradicted the point they described:
"%" mapped straight to humidity, missing units were invented, and classes
with no entry in VALID_UNITS_FOR_CLASS accepted any unit at all.

Names here are taken from a real Niagara station, still Niagara-encoded.
"""
import sys
from pathlib import Path

import pytest
from homeassistant.components.sensor import SensorDeviceClass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.niagara.sensor import (  # noqa: E402
    VALID_UNITS_FOR_CLASS,
    infer_device_class_and_unit,
)

PATH = "/config/Drivers/NiagaraNetwork/J2/points/Site/X/"


def infer(name, unit=None, path=PATH):
    return infer_device_class_and_unit(name, path, unit)


# -- "%" is not humidity -------------------------------------------------

@pytest.mark.parametrize("name", [
    "Pump$201$20Inverter$20Output$20$25",
    "Average$20System$20Efficiency",
    "DCW$2eTank_1_Lvl",
    "CarparkExhaust Fan3 Speed",
    "AHU1 Damper Position",
    "Chilled Water Valve",
])
def test_percent_alone_gets_no_class(name):
    """A BMS percentage is a position or a speed far more often than humidity."""
    device_class, unit = infer(name, "%")
    assert device_class is None
    assert unit == "%"


@pytest.mark.parametrize("name", ["Room Humidity", "Zone RH", "Return Air Humid"])
def test_percent_becomes_humidity_when_the_name_says_so(name):
    assert infer(name, "%")[0] is SensorDeviceClass.HUMIDITY


def test_percent_becomes_battery_when_the_name_says_so():
    assert infer("Battery$20Percentage", "%")[0] is SensorDeviceClass.BATTERY


def test_tank_level_in_percent_is_not_water():
    """WATER needs a volume unit; a level in % is not a water meter."""
    assert infer("DCW$2eTank_1_Lvl", "%")[0] is None


# -- units are never invented --------------------------------------------

@pytest.mark.parametrize("name", [
    "Room Temp", "Supply Air Temp", "OAT", "Zone Temperature",
])
def test_no_unit_means_no_class(name):
    """Assuming °C silently mislabels every station configured in °F."""
    device_class, unit = infer(name, None)
    assert device_class is None
    assert unit is None


def test_unit_drives_the_class_regardless_of_name():
    """A point reporting °C is a temperature whatever it is called."""
    assert infer("UI$203", "°C")[0] is SensorDeviceClass.TEMPERATURE


def test_name_refines_only_when_the_unit_agrees():
    # "Power" with a temperature unit must not become POWER.
    assert infer("Power Room Temp", "°C")[0] is SensorDeviceClass.TEMPERATURE
    assert infer("Active Power", "kW")[0] is SensorDeviceClass.POWER


# -- word boundaries -----------------------------------------------------

def test_short_tokens_do_not_match_inside_words():
    """"rh" used to match "Overheat"; "sat" matched "Saturation"."""
    assert infer("Overheat Alarm Level", "%")[0] is None
    assert infer("Compressor Saturation", "%")[0] is None


# -- every assigned class keeps a unit it permits ------------------------

@pytest.mark.parametrize("unit", ["%", "kW", "kWh", "°C", "V", "A", "Hz", "ppm", "L", "m³"])
def test_assigned_class_always_permits_its_unit(unit):
    for name in ("Room Temp", "Active Power", "Energy Total", "Water Meter",
                 "Gas Total", "Battery", "Humidity", "CO2 Level", "Tank Lvl"):
        device_class, resolved = infer(name, unit)
        if device_class is None:
            continue
        valid = VALID_UNITS_FOR_CLASS.get(device_class)
        assert valid is None or resolved in valid, (
            f"{device_class} assigned with unit {resolved!r} from name {name!r}"
        )
