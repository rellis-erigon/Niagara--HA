"""Tests for template loading, scoring and binding.

Point names here are taken verbatim from a real Niagara station, including
its two different naming conventions for the same equipment: one meter
reports MeterTotal / Phase_1_V, another TotalActivePower / V1.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import device_templates as dt  # noqa: E402


@pytest.fixture(autouse=True)
def builtin_templates(monkeypatch):
    monkeypatch.setattr(dt, "BUILTIN_TEMPLATE_DIR", ROOT / "templates")
    monkeypatch.setattr(dt, "USER_TEMPLATE_DIR", Path("/nonexistent"))


@pytest.fixture
def templates():
    return dt.load_templates()


def point(name, unit="", point_type="numeric", path=None):
    return {
        "name": name,
        "unit": unit,
        "type": point_type,
        "path": path or f"/config/points/{name}/",
    }


# Convention A: MeterTotal / Phase_1_V / Active_Power_P1
METER_A = [
    point("MeterTotal", "kWh"),
    point("$33Phase_Active_Power", "kW"),
    point("$33Phase$20A_power_Factor"),
    point("Frequency", "Hz"),
    point("Phase_1_V", "V"), point("Phase_2_V", "V"), point("Phase_3_V", "V"),
    point("Phase_1_A", "A"), point("Phase_2_A", "A"), point("Phase_3_A", "A"),
]

# Convention B: TotalActivePower / V1 / A1
METER_B = [
    point("TotalActivePower", "kWh"),
    point("Power", "kW"),
    point("V1", "V"), point("V2", "V"), point("V3", "V"),
    point("A1", "A"), point("A2", "A"), point("A3", "A"),
    point("DailyUsage"), point("MonthlyUsage"),
]

FCU = [
    point("RoomTemp", "°C"),
    point("TempAdjust", "°C"),
    point("StartStopCommand", point_type="boolean"),
    point("AirConModeStatus", point_type="enum"),
    point("IndoorFanStatus", point_type="boolean"),
    point("Alarm", point_type="boolean"),
    point("AirConModeCommand", point_type="enum"),
]


def test_builtin_templates_load(templates):
    assert "electricity_meter" in templates
    assert "generic" in templates
    meter = templates["electricity_meter"]
    assert meter.required_slots
    assert all(s.key for s in meter.slots)


def test_decode_niagara_escapes():
    assert dt.decode_niagara_name("$33Phase_Active_Power") == "3Phase_Active_Power"
    assert dt.decode_niagara_name("DB$2dL3") == "DB-L3"
    assert dt.decode_niagara_name("Basement$20Level") == "Basement Level"
    assert dt.decode_niagara_name("plain") == "plain"


@pytest.mark.parametrize("points,expected_total", [
    (METER_A, "MeterTotal"),
    (METER_B, "TotalActivePower"),
])
def test_electricity_meter_binds_both_conventions(templates, points, expected_total):
    bindings = dt.bind_template(templates["electricity_meter"], points)
    assert bindings["energy_total"].endswith(f"/{expected_total}/")
    for slot in ("voltage_l1", "voltage_l2", "voltage_l3",
                 "current_l1", "current_l2", "current_l3"):
        assert bindings[slot], f"{slot} unbound for {expected_total}"


def test_voltage_and_current_do_not_cross(templates):
    bindings = dt.bind_template(templates["electricity_meter"], METER_B)
    assert bindings["voltage_l1"].endswith("/V1/")
    assert bindings["current_l1"].endswith("/A1/")


def test_no_point_fills_two_slots(templates):
    bindings = dt.bind_template(templates["electricity_meter"], METER_A)
    bound = [v for v in bindings.values() if v]
    assert len(bound) == len(set(bound))


def test_wrong_unit_disqualifies_a_point(templates):
    """A kWh slot must not accept a point reporting °C."""
    slot = next(s for s in templates["electricity_meter"].slots
                if s.key == "energy_total")
    assert dt.score_candidate(slot, point("MeterTotal", "°C")) is None
    assert dt.score_candidate(slot, point("MeterTotal", "kWh")) is not None


def test_missing_unit_is_allowed_but_not_preferred(templates):
    slot = next(s for s in templates["electricity_meter"].slots
                if s.key == "energy_total")
    with_unit = dt.score_candidate(slot, point("MeterTotal", "kWh"))
    without = dt.score_candidate(slot, point("MeterTotal", ""))
    assert without is not None
    assert with_unit > without


def test_point_type_is_enforced(templates):
    slot = next(s for s in templates["fcu"].slots if s.key == "command")
    assert dt.score_candidate(slot, point("StartStopCommand", point_type="boolean"))
    assert dt.score_candidate(
        slot, point("StartStopCommand", point_type="numeric")) is None


def test_fcu_prefers_the_specific_status_slot(templates):
    """IndoorFanStatus belongs to fan_status, not the generic run_status."""
    bindings = dt.bind_template(templates["fcu"], FCU)
    assert bindings["fan_status"].endswith("/IndoorFanStatus/")
    assert bindings["room_temperature"].endswith("/RoomTemp/")
    assert bindings["command"].endswith("/StartStopCommand/")
    assert bindings["setpoint"].endswith("/TempAdjust/")


def test_required_slots_are_filled_before_optional(templates):
    bindings = dt.bind_template(templates["room_sensor"], [point("RoomTemp", "°C")])
    assert bindings["temperature"] is not None


def test_suggest_picks_the_meter(templates):
    assert dt.suggest_template(templates, METER_A)[0] == "electricity_meter"
    assert dt.suggest_template(templates, METER_B)[0] == "electricity_meter"


def test_device_name_breaks_a_tie(templates):
    """A folder called SOLAR is a solar inverter, not a generic meter."""
    solar = [point("DailyEnergy", "kWh"), point("ActivePower", "kW")]
    plain = dt.suggest_template(templates, solar)[0]
    named = dt.suggest_template(templates, solar, "Site/Boards/SOLAR")[0]
    assert named == "solar_inverter"
    assert plain != "solar_inverter" or plain == "solar_inverter"


def test_suggest_returns_none_when_nothing_fits(templates):
    assert dt.suggest_template(templates, [point("Widget", "")])[0] is None


def test_generic_template_is_never_suggested(templates):
    """It has no required slots, so it cannot win by matching nothing."""
    assert dt.suggest_template(templates, METER_A)[0] != "generic"


# -- Persistence ---------------------------------------------------------

@pytest.fixture
def types_file(tmp_path, monkeypatch):
    path = tmp_path / "device_types.yaml"
    monkeypatch.setattr(dt, "DEVICE_TYPES_FILE", path)
    return path


def test_round_trip(types_file):
    devices = {"Site/DB1": {
        "template": "electricity_meter",
        "bindings": {"energy_total": "/config/points/MeterTotal/"},
        "state": dt.STATE_DRAFT,
    }}
    dt.save_device_types(devices)
    assert dt.load_device_types() == devices


def test_missing_file_is_empty(types_file):
    assert dt.load_device_types() == {}


def test_entry_without_template_is_dropped(types_file):
    types_file.write_text("devices:\n  Site/DB1:\n    bindings: {}\n")
    assert dt.load_device_types() == {}


def test_unknown_state_falls_back_to_draft(types_file):
    types_file.write_text(
        "devices:\n  Site/DB1:\n    template: pump\n    state: banana\n")
    assert dt.load_device_types()["Site/DB1"]["state"] == dt.STATE_DRAFT


def test_empty_bindings_are_not_stored(types_file):
    dt.save_device_types({"Site/DB1": {
        "template": "pump", "bindings": {"run_status": None}, "state": "draft",
    }})
    assert dt.load_device_types()["Site/DB1"]["bindings"] == {}


# Carpark exhaust drives abbreviate hard: Sts for status, Spd for speed.
DRIVE = [
    point("Alarm", point_type="boolean"),
    point("Drive1Spd"),
    point("Name", point_type="string"),
    point("Sts", point_type="boolean"),
]


def test_abbreviated_drive_points_bind(templates):
    assert dt.suggest_template(templates, DRIVE, "Site/HVAC/Carpark/Drive1")[0] == "fan"
    bindings = dt.bind_template(templates["fan"], DRIVE)
    assert bindings["run_status"].endswith("/Sts/")
    assert bindings["speed"].endswith("/Drive1Spd/")
    assert bindings["fault"].endswith("/Alarm/")


def test_sts_pattern_does_not_capture_status_points(templates):
    """"sts" must not match "Status"; the FCU relies on that distinction."""
    bindings = dt.bind_template(templates["fcu"], FCU)
    assert bindings["fan_status"].endswith("/IndoorFanStatus/")
