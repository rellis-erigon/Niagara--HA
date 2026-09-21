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


# -- User templates ------------------------------------------------------

@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    path = tmp_path / "templates"
    monkeypatch.setattr(dt, "USER_TEMPLATE_DIR", path)
    return path


MINIMAL = {
    "id": "fridge_custom",
    "name": "Custom Fridge",
    "slots": [{"key": "temperature", "name": "Temperature", "required": True,
               "units": ["°C"], "match": ["*temp*"],
               "validation": {"min": -40, "max": 30}}],
}


def test_save_and_load_user_template(user_dir):
    dt.save_user_template(MINIMAL)
    loaded = dt.load_templates()
    assert "fridge_custom" in loaded
    template = loaded["fridge_custom"]
    assert template.builtin is False
    assert template.required_slots[0].validation == {"min": -40.0, "max": 30.0}


def test_user_template_shadows_a_builtin(user_dir):
    """Customizing a built-in must not need the built-in deleted."""
    custom = dict(MINIMAL, id="pump", name="Our Pumps")
    dt.save_user_template(custom)
    loaded = dt.load_templates()
    assert loaded["pump"].name == "Our Pumps"
    assert loaded["pump"].builtin is False


def test_deleting_a_shadow_restores_the_builtin(user_dir):
    dt.save_user_template(dict(MINIMAL, id="pump", name="Our Pumps"))
    assert dt.delete_user_template("pump") is True
    restored = dt.load_templates()["pump"]
    assert restored.builtin is True
    assert restored.name != "Our Pumps"


def test_deleting_a_builtin_is_refused(user_dir):
    """There is no user file to remove, so nothing happens."""
    assert dt.delete_user_template("electricity_meter") is False
    assert dt.load_templates()["electricity_meter"].builtin is True


@pytest.mark.parametrize("payload,reason", [
    ({"id": "Bad Id", "slots": []}, "uppercase and spaces"),
    ({"id": "", "slots": []}, "empty id"),
    ({"id": "ok", "slots": "nope"}, "slots not a list"),
    ({"id": "ok", "slots": [{"key": ""}]}, "empty slot key"),
    ({"id": "ok", "slots": [{"key": "a"}, {"key": "a"}]}, "duplicate slot key"),
    ({"id": "ok", "slots": [{"key": "a", "validation": {"min": "cold"}}]}, "min not a number"),
    ({"id": "ok", "slots": [{"key": "a", "units": "°C"}]}, "units not a list"),
])
def test_invalid_templates_are_refused(payload, reason):
    with pytest.raises(dt.TemplateError):
        dt.validate_template_payload(payload)


def test_slot_name_defaults_to_its_key():
    payload = dt.validate_template_payload({"id": "ok", "slots": [{"key": "door"}]})
    assert payload["slots"][0]["name"] == "door"


def test_empty_lists_are_not_written():
    payload = dt.validate_template_payload(
        {"id": "ok", "slots": [{"key": "a", "units": [], "match": ["  "]}]})
    assert "units" not in payload["slots"][0]
    assert "match" not in payload["slots"][0]


def test_fridge_template_binds_a_walk_in():
    templates = dt.load_templates()
    walkin = [
        point("L3-Walkin-Fridge-Door", point_type="boolean"),
        point("L3-Walkin-Fridge-Temp", "°C"),
        point("L3-Walking-Fridge-Comp", point_type="boolean"),
    ]
    assert dt.suggest_template(templates, walkin, "Kitchens/Main/Fridge-1")[0] == "fridge"
    bindings = dt.bind_template(templates["fridge"], walkin)
    assert bindings["temperature"].endswith("-Temp/")
    assert bindings["door"].endswith("-Door/")
    assert bindings["compressor"].endswith("-Comp/")


# -- Cards ---------------------------------------------------------------

CARD_TEMPLATE = {
    "id": "carded", "name": "Carded",
    "slots": [{"key": "temperature", "name": "Temperature", "required": True},
              {"key": "door", "name": "Door"}],
    "card": {"type": "entities", "title": "{{device_name}}", "entities": [
        {"entity": "{{slot.temperature}}", "name": "Temperature"},
        {"entity": "{{slot.door}}", "name": "Door"},
    ]},
}


def test_card_renders_with_every_slot_bound(user_dir):
    dt.save_user_template(CARD_TEMPLATE)
    template = dt.load_templates()["carded"]
    card = dt.render_card(template, {
        "temperature": "/p/temp/", "door": "/p/door/", "device_name": "Fridge 1",
    })
    assert card["title"] == "Fridge 1"
    assert [e["entity"] for e in card["entities"]] == ["/p/temp/", "/p/door/"]


def test_unbound_slot_rows_are_dropped(user_dir):
    """A card naming an entity that does not exist renders broken."""
    dt.save_user_template(CARD_TEMPLATE)
    template = dt.load_templates()["carded"]
    card = dt.render_card(template, {"temperature": "/p/temp/", "device_name": "F"})
    assert len(card["entities"]) == 1
    assert card["entities"][0]["entity"] == "/p/temp/"


def test_template_without_a_card_renders_none(user_dir):
    dt.save_user_template(MINIMAL)
    assert dt.render_card(dt.load_templates()["fridge_custom"], {}) is None


def test_card_must_have_a_type():
    with pytest.raises(dt.TemplateError):
        dt.validate_template_payload(
            {"id": "x", "slots": [], "card": {"entities": []}})


def test_card_must_be_an_object():
    with pytest.raises(dt.TemplateError):
        dt.validate_template_payload({"id": "x", "slots": [], "card": "entities"})


def test_builtin_templates_carry_cards():
    templates = dt.load_templates()
    assert templates["fcu"].card.get("type") == "entities"
    assert templates["electricity_meter"].card.get("type") == "entities"


# -- Import / export -----------------------------------------------------

def test_export_then_import_round_trips(user_dir):
    dt.save_user_template(CARD_TEMPLATE)
    exported = dt.export_templates([dt.load_templates()["carded"]])
    assert dt.delete_user_template("carded") is True

    saved, errors = dt.import_templates(exported)
    assert saved == ["carded"]
    assert errors == []
    restored = dt.load_templates()["carded"]
    assert restored.card["type"] == "entities"
    assert [s.key for s in restored.slots] == ["temperature", "door"]


def test_import_keeps_good_templates_when_one_is_bad(user_dir):
    text = (
        "id: good_one\nname: Good\nslots:\n  - key: temperature\n"
        "---\n"
        "id: BAD ID\nname: Bad\nslots: []\n"
    )
    saved, errors = dt.import_templates(text)
    assert saved == ["good_one"]
    assert len(errors) == 1
    assert "good_one" in dt.load_templates()


def test_import_rejects_unparseable_yaml(user_dir):
    with pytest.raises(dt.TemplateError):
        dt.import_templates("id: x\n  bad: [indent")


def test_import_rejects_yaml_with_no_templates(user_dir):
    with pytest.raises(dt.TemplateError):
        dt.import_templates("# just a comment\n")


def test_export_all_includes_every_template(user_dir):
    templates = dt.load_templates()
    exported = dt.export_templates(sorted(templates.values(), key=lambda t: t.id))
    for template_id in ("fcu", "electricity_meter", "ups_3phase", "fridge"):
        assert f"id: {template_id}" in exported
