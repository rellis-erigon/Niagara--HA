"""Tests for what gets added to the energy dashboard.

The decisions live in a pure function precisely so they can be tested
without a running Home Assistant, because the cost of getting them wrong is
a wrong number on the one screen people use to check their bills.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "niagara"))

import energy  # noqa: E402


EMPTY = {"energy_sources": [], "device_consumption": []}


def meter(entity_id, slot, name="Meter"):
    return {"entity_id": entity_id, "slot": slot, "name": name}


def test_a_submeter_goes_in_as_an_individual_device():
    """Submeters must not be grid import, or the same power counts twice."""
    prefs, changes = energy.plan_additions(
        EMPTY, [meter("sensor.db_l3_total", "energy_total", "DB-L3")])
    assert prefs["device_consumption"] == [
        {"stat_consumption": "sensor.db_l3_total", "name": "DB-L3"}
    ]
    assert not any(
        s.get("flow_from") for s in prefs["energy_sources"]
    )


def test_solar_becomes_a_production_source():
    prefs, _ = energy.plan_additions(
        EMPTY, [meter("sensor.pv", "energy_generated")])
    solar = [s for s in prefs["energy_sources"] if s["type"] == "solar"]
    assert solar[0]["stat_energy_from"] == "sensor.pv"


def test_water_and_gas_get_their_own_source_types():
    prefs, _ = energy.plan_additions(EMPTY, [
        meter("sensor.w", "volume_total"), meter("sensor.g", "gas_total"),
    ])
    types = {s["type"] for s in prefs["energy_sources"]}
    assert "water" in types and "gas" in types


def test_an_empty_grid_source_is_added_so_the_dashboard_renders():
    prefs, changes = energy.plan_additions(EMPTY, [meter("sensor.a", "energy_total")])
    grid = [s for s in prefs["energy_sources"] if s["type"] == "grid"]
    assert len(grid) == 1
    assert any("grid source" in c for c in changes)


def test_an_existing_grid_source_is_left_alone():
    existing = {
        "energy_sources": [{
            "type": "grid",
            "flow_from": [{"stat_energy_from": "sensor.main"}],
            "flow_to": [], "cost_adjustment_day": 0.0,
        }],
        "device_consumption": [],
    }
    prefs, _ = energy.plan_additions(existing, [meter("sensor.a", "energy_total")])
    grid = [s for s in prefs["energy_sources"] if s["type"] == "grid"]
    assert len(grid) == 1
    assert grid[0]["flow_from"][0]["stat_energy_from"] == "sensor.main"


def test_an_entity_already_configured_anywhere_is_not_added_again():
    existing = {
        "energy_sources": [{
            "type": "grid",
            "flow_from": [{"stat_energy_from": "sensor.main"}],
            "flow_to": [], "cost_adjustment_day": 0.0,
        }],
        "device_consumption": [],
    }
    prefs, changes = energy.plan_additions(
        existing, [meter("sensor.main", "energy_total")])
    assert prefs["device_consumption"] == []
    assert changes == []


def test_a_meter_removed_deliberately_stays_removed_within_one_pass():
    """Adding is idempotent: a second pass over the result changes nothing."""
    first, _ = energy.plan_additions(EMPTY, [meter("sensor.a", "energy_total")])
    second, changes = energy.plan_additions(
        first, [meter("sensor.a", "energy_total")])
    assert changes == []
    assert second["device_consumption"] == first["device_consumption"]


def test_an_unknown_slot_is_ignored():
    prefs, changes = energy.plan_additions(EMPTY, [meter("sensor.x", "temperature")])
    assert prefs["device_consumption"] == []
    assert not any("sensor.x" in c for c in changes)


def test_a_meter_with_no_entity_id_is_skipped():
    prefs, changes = energy.plan_additions(EMPTY, [meter("", "energy_total")])
    assert prefs["device_consumption"] == []


def test_unrelated_preferences_are_preserved():
    existing = dict(EMPTY, currency="AUD")
    prefs, _ = energy.plan_additions(existing, [meter("sensor.a", "energy_total")])
    assert prefs["currency"] == "AUD"


# -- Submeters must not be summed ----------------------------------------
#
# Home Assistant sums every water source into one total, and unlike
# electricity there is no device-level section to put a submeter in. Four
# water meters on this station — a main supply plus irrigation, kitchens
# and a pool — were all added as sources, counting the same water several
# times over.

def water(entity, name, slot="volume_total"):
    return {"entity_id": entity, "slot": slot, "name": name}


def sources(prefs, kind):
    return [s for s in prefs["energy_sources"] if s.get("type") == kind]


def test_one_water_meter_is_added():
    """No ambiguity with one, so nothing changes for a simple site."""
    prefs, changes = energy.plan_additions(
        {}, [water("sensor.main", "Main-Water-Meter")])
    assert [s["stat_energy_from"] for s in sources(prefs, "water")] == [
        "sensor.main"]


def test_several_water_meters_are_all_left_off():
    """The real case: one is the supply and the rest are downstream of it,
    and nothing here knows which."""
    prefs, changes = energy.plan_additions({}, [
        water("sensor.main", "Main-Water-Meter"),
        water("sensor.irrigation", "Irrigation-Water-Meter"),
        water("sensor.kitchens", "Kitchens-Water-Meter"),
        water("sensor.pool", "Pool-Water-Meter"),
    ])
    assert sources(prefs, "water") == []


def test_it_says_which_meters_it_left_off_and_why():
    """Silently adding nothing would read as the feature not working."""
    _prefs, changes = energy.plan_additions({}, [
        water("sensor.main", "Main-Water-Meter"),
        water("sensor.pool", "Pool-Water-Meter"),
    ])
    message = " ".join(changes)
    assert "only one can be the supply" in message
    assert "Main-Water-Meter" in message and "Pool-Water-Meter" in message


def test_a_water_meter_is_not_added_beside_one_already_configured():
    """Somebody has already chosen the supply; a second source would
    double the total."""
    prefs = {"energy_sources": [
        {"type": "water", "stat_energy_from": "sensor.chosen"}]}
    out, changes = energy.plan_additions(
        prefs, [water("sensor.pool", "Pool-Water-Meter")])
    assert [s["stat_energy_from"] for s in sources(out, "water")] == [
        "sensor.chosen"]
    assert "already configured" in " ".join(changes)


def test_several_gas_meters_are_treated_the_same_way():
    prefs, _ = energy.plan_additions({}, [
        water("sensor.g1", "Gas-Main", slot="gas_total"),
        water("sensor.g2", "Gas-Kitchen", slot="gas_total"),
    ])
    assert sources(prefs, "gas") == []


def test_ambiguous_water_does_not_stop_electricity_submeters():
    """The two decisions are independent; a water puzzle must not drop the
    boards."""
    prefs, _ = energy.plan_additions({}, [
        water("sensor.main", "Main-Water-Meter"),
        water("sensor.pool", "Pool-Water-Meter"),
        {"entity_id": "sensor.db1", "slot": "energy_total", "name": "DB-1"},
    ])
    assert [d["stat_consumption"] for d in prefs["device_consumption"]] == [
        "sensor.db1"]


def test_ambiguous_water_does_not_stop_solar():
    prefs, _ = energy.plan_additions({}, [
        water("sensor.main", "Main-Water-Meter"),
        water("sensor.pool", "Pool-Water-Meter"),
        {"entity_id": "sensor.pv", "slot": "energy_generated", "name": "Solar"},
    ])
    assert [s["stat_energy_from"] for s in sources(prefs, "solar")] == [
        "sensor.pv"]


def test_a_meter_already_on_the_dashboard_is_not_counted_as_ambiguity():
    """Re-running must not start refusing the one meter it already added."""
    prefs = {"energy_sources": [
        {"type": "water", "stat_energy_from": "sensor.main"}]}
    out, _ = energy.plan_additions(
        prefs, [water("sensor.main", "Main-Water-Meter")])
    assert len(sources(out, "water")) == 1
