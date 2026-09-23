"""Tests for the diagnostics checks.

Each of these exists because the condition it describes was found by hand
first, and by hand is not a plan.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import diagnostics as d  # noqa: E402
from device_templates import Slot, Template  # noqa: E402

NOW = 1_700_000_000.0


def point(name, enabled=True, unit="", custom_unit=""):
    entry = {"name": name, "enabled": enabled, "unit": unit}
    if custom_unit:
        entry["custom_unit"] = custom_unit
    return entry


def value(number, status="ok", age=0):
    return {"value": number, "status": status, "ts": NOW - age}


def template(slot_key="energy", units=("kWh",), monotonic=True):
    return Template(
        id="meter", name="Meter", description="", icon="", device_match=[],
        slots=[Slot(
            key=slot_key, name="Energy", required=True, units=list(units),
            point_types=["numeric"], match=["*"],
            validation={"monotonic": True} if monotonic else {},
        )],
        card=None,
    )


# -- stale and status ----------------------------------------------------

def test_stale_values_flags_only_enabled_points():
    selections = {"/a/": point("A"), "/b/": point("B", enabled=False)}
    values = {"/a/": value(1, age=900), "/b/": value(1, age=900)}
    finding = d.check_stale_values(selections, values, NOW)
    assert [i["path"] for i in finding.items] == ["/a/"]


def test_stale_values_flags_a_point_with_no_value_at_all():
    finding = d.check_stale_values({"/a/": point("A")}, {}, NOW)
    assert finding.items[0]["age"] is None


def test_fresh_values_produce_no_finding():
    selections = {"/a/": point("A")}
    assert d.check_stale_values(selections, {"/a/": value(1, age=5)}, NOW) is None


def test_fault_status_is_an_error_but_stale_is_a_warning():
    selections = {"/a/": point("A")}
    faulted = d.check_bad_status(selections, {"/a/": value(1, status="fault")})
    assert faulted.severity == d.ERROR
    stale = d.check_bad_status(selections, {"/a/": value(1, status="stale")})
    assert stale.severity == d.WARNING


# -- bindings ------------------------------------------------------------

def test_bound_but_disabled_is_worse_on_a_published_device():
    selections = {"/a/": point("A", enabled=False)}
    draft = {"G": {"template": "meter", "state": "draft", "bindings": {"energy": "/a/"}}}
    published = {"G": {"template": "meter", "state": "published",
                       "bindings": {"energy": "/a/"}}}
    assert d.check_bound_but_disabled(draft, selections).severity == d.WARNING
    assert d.check_bound_but_disabled(published, selections).severity == d.ERROR


def test_an_enabled_bound_point_is_not_flagged():
    selections = {"/a/": point("A", enabled=True)}
    devices = {"G": {"template": "meter", "state": "draft",
                     "bindings": {"energy": "/a/"}}}
    assert d.check_bound_but_disabled(devices, selections) is None


def test_resetting_register_bound_to_a_total_is_caught():
    selections = {"/a/": point("DailyEnergyTotal")}
    devices = {"G": {"template": "meter", "bindings": {"energy": "/a/"}}}
    finding = d.check_resetting_totals(devices, {"meter": template()}, selections)
    assert finding.severity == d.ERROR
    assert finding.items[0]["name"] == "DailyEnergyTotal"


def test_a_lifetime_total_is_left_alone():
    selections = {"/a/": point("MeterTotalEnergy")}
    devices = {"G": {"template": "meter", "bindings": {"energy": "/a/"}}}
    assert d.check_resetting_totals(devices, {"meter": template()}, selections) is None


def test_a_non_monotonic_slot_is_never_checked_for_resets():
    selections = {"/a/": point("DailyPower")}
    devices = {"G": {"template": "meter", "bindings": {"energy": "/a/"}}}
    templates = {"meter": template(monotonic=False)}
    assert d.check_resetting_totals(devices, templates, selections) is None


def test_decreasing_totals_are_sorted_by_how_often():
    devices = {
        "A": {"template": "meter", "bindings": {"energy": "/a/"}},
        "B": {"template": "meter", "bindings": {"energy": "/b/"}},
    }
    observations = {"/a/": {"decreases": 3}, "/b/": {"decreases": 40}}
    finding = d.check_decreasing_totals(devices, {"meter": template()}, observations)
    assert [i["path"] for i in finding.items] == ["/b/", "/a/"]


# -- units ---------------------------------------------------------------

def test_a_percent_override_on_a_temperature_point_is_flagged():
    selections = {"/a/": point("RoomTemp", unit="°C", custom_unit="%")}
    finding = d.check_unit_overrides(selections)
    assert finding.items[0]["unit"] == "%"


def test_a_sensible_override_is_not_flagged():
    selections = {"/a/": point("RoomTemp", unit="", custom_unit="°C")}
    assert d.check_unit_overrides(selections) is None


def test_a_point_with_no_override_is_never_flagged():
    """Only overrides are checked; guessing units is the templates' job."""
    selections = {"/a/": point("RoomTemp", unit="%")}
    assert d.check_unit_overrides(selections) is None


# -- energy eligibility --------------------------------------------------

def test_a_published_meter_in_kw_cannot_go_on_the_energy_dashboard():
    selections = {"/a/": point("Total", unit="kW")}
    devices = {"G": {"template": "meter", "state": "published",
                     "bindings": {"energy": "/a/"}}}
    finding = d.check_energy_eligibility(devices, {"meter": template()}, selections)
    assert finding.items[0]["unit"] == "kW"


def test_a_meter_in_kwh_is_eligible():
    selections = {"/a/": point("Total", unit="kWh")}
    devices = {"G": {"template": "meter", "state": "published",
                     "bindings": {"energy": "/a/"}}}
    assert d.check_energy_eligibility(
        devices, {"meter": template()}, selections) is None


def test_an_unpublished_meter_is_not_nagged_about():
    selections = {"/a/": point("Total", unit="kW")}
    devices = {"G": {"template": "meter", "state": "draft",
                     "bindings": {"energy": "/a/"}}}
    assert d.check_energy_eligibility(
        devices, {"meter": template()}, selections) is None


def test_a_unit_override_decides_eligibility():
    """The override is what the entity carries, so it is what counts."""
    selections = {"/a/": point("Total", unit="kW", custom_unit="kWh")}
    devices = {"G": {"template": "meter", "state": "published",
                     "bindings": {"energy": "/a/"}}}
    assert d.check_energy_eligibility(
        devices, {"meter": template()}, selections) is None


# -- versions ------------------------------------------------------------

def test_version_drift_is_an_error():
    finding = d.check_version_drift("3.8.0", "3.7.0")
    assert finding.severity == d.ERROR
    assert "3.8.0" in finding.title and "3.7.0" in finding.title


def test_matching_versions_produce_nothing():
    assert d.check_version_drift("3.8.0", "3.8.0") is None


def test_a_missing_integration_is_reported_separately():
    finding = d.check_version_drift("3.8.0", None)
    assert finding.id == "integration_missing"


# -- assembly ------------------------------------------------------------

def test_run_all_orders_errors_first_and_counts_them():
    selections = {
        "/a/": point("Total", unit="kW"),
        "/b/": point("RoomTemp", custom_unit="%"),
    }
    devices = {"G": {"template": "meter", "state": "published",
                     "bindings": {"energy": "/a/"}}}
    report = d.run_all(
        selections, {"/a/": value(1), "/b/": value(20)}, devices,
        {"meter": template()}, {}, "3.8.0", "3.7.0", NOW,
    )
    assert report["severity"] == d.ERROR
    assert report["findings"][0]["severity"] == d.ERROR
    assert report["errors"] >= 1 and report["warnings"] >= 1


def test_a_healthy_station_reports_nothing():
    selections = {"/a/": point("Total", unit="kWh")}
    devices = {"G": {"template": "meter", "state": "published",
                     "bindings": {"energy": "/a/"}}}
    report = d.run_all(
        selections, {"/a/": value(100)}, devices, {"meter": template()},
        {}, "3.8.0", "3.8.0", NOW,
    )
    assert report["findings"] == []
    assert report["severity"] == d.INFO


def test_long_lists_are_truncated_but_counted_honestly():
    selections = {f"/p{i}/": point(f"P{i}") for i in range(120)}
    finding = d.check_stale_values(selections, {}, NOW)
    payload = finding.to_dict()
    assert payload["count"] == 120
    assert len(payload["items"]) == 50
    assert payload["truncated"] == 70


# -- acknowledgements ----------------------------------------------------

def _finding(count):
    return d.Finding(id="x", severity=d.WARNING, title="t", detail="det",
                     items=[{"n": i} for i in range(count)])


def test_an_acknowledged_finding_is_set_aside():
    live, accepted = d.apply_acknowledgements(
        [_finding(3)], {"x": {"count": 3, "note": "deliberate"}})
    assert live == []
    assert accepted[0]["acknowledged"] is True
    assert accepted[0]["note"] == "deliberate"


def test_a_finding_comes_back_when_it_grows():
    """Accepting 3 disabled points must not hide the fourth."""
    live, accepted = d.apply_acknowledgements(
        [_finding(4)], {"x": {"count": 3}})
    assert len(live) == 1 and accepted == []


def test_a_finding_that_shrinks_stays_acknowledged():
    live, accepted = d.apply_acknowledgements(
        [_finding(1)], {"x": {"count": 3}})
    assert live == [] and len(accepted) == 1


def test_acknowledged_findings_do_not_count_towards_severity():
    selections = {"/a/": point("RoomTemp", custom_unit="%")}
    report = d.run_all(
        selections, {"/a/": value(20)}, {}, {}, {}, "1.0", "1.0", NOW,
        acks={"unit_override_conflict": {"count": 1}},
    )
    assert report["warnings"] == 0
    assert report["severity"] == d.INFO
    assert len(report["acknowledged"]) == 1


def test_group_labels_are_decoded_for_reading():
    selections = {"/a/": point("DailyTotal")}
    devices = {"Site/DB$2dL3": {"template": "meter", "bindings": {"energy": "/a/"}}}
    finding = d.check_resetting_totals(devices, {"meter": template()}, selections)
    assert finding.items[0]["group"] == "Site / DB-L3"
