"""Tests for slot validation.

Written against the case that motivated it: 70 hotel rooms reporting exactly
0.0 °C with status ok, because their BACnet proxy had stopped updating. The
add-on was genuinely re-reading them every cycle, so staleness detection was
blind — only a plausibility rule catches a confidently served dead number.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import validation as v  # noqa: E402
from device_templates import Slot  # noqa: E402

NOW = 1_800_000_000.0


def slot(**kw):
    kw.setdefault("key", "temperature")
    kw.setdefault("name", "Temperature")
    return Slot(**kw)


def point(unit="°C", enum_range=None):
    return {"path": "/p/", "name": "RoomTemp", "unit": unit,
            "type": "numeric", "enum_range": enum_range or []}


def value(val, status="ok", age=0.0):
    return {"value": val, "status": status, "ts": NOW - age}


def test_ok_value_passes():
    severity, issues = v.validate_slot(
        slot(units=["°C"], validation={"min": -20, "max": 60}),
        point(), value("22.7"), None, NOW)
    assert severity == v.OK
    assert issues == []


def test_out_of_range_is_blocked():
    severity, issues = v.validate_slot(
        slot(required=True, units=["°C"], validation={"min": 5, "max": 40}),
        point(), value("847.0"), None, NOW)
    assert severity == v.BLOCKED
    assert "above" in issues[0]["message"]


def test_below_minimum_is_blocked():
    """A hotel room at 0.0 °C, the case this exists for."""
    severity, _ = v.validate_slot(
        slot(required=True, units=["°C"], validation={"min": 5, "max": 40}),
        point(), value("0.0"), None, NOW)
    assert severity == v.BLOCKED


def test_stale_status_warns():
    severity, issues = v.validate_slot(
        slot(units=["°C"]), point(), value("0.0", status="fault"), None, NOW)
    assert severity == v.WARNING
    assert "fault" in issues[0]["message"]


def test_old_timestamp_warns():
    severity, issues = v.validate_slot(
        slot(units=["°C"]), point(),
        value("21.0", age=v.STALE_SECONDS + 60), None, NOW)
    assert severity == v.WARNING
    assert "ago" in issues[0]["message"]


def test_missing_required_slot_is_blocked():
    severity, issues = v.validate_slot(slot(required=True), None, None, None, NOW)
    assert severity == v.BLOCKED
    assert "Required" in issues[0]["message"]


def test_missing_optional_slot_is_fine():
    assert v.validate_slot(slot(required=False), None, None, None, NOW)[0] == v.OK


def test_no_value_blocks_a_required_slot():
    severity, _ = v.validate_slot(
        slot(required=True, units=["°C"]), point(), value(None), None, NOW)
    assert severity == v.BLOCKED


def test_wrong_unit_is_blocked():
    severity, issues = v.validate_slot(
        slot(required=True, units=["kWh"]), point(unit="°C"), value("5"), None, NOW)
    assert severity == v.BLOCKED
    assert "not one this slot accepts" in issues[0]["message"]


def test_missing_unit_only_warns():
    severity, issues = v.validate_slot(
        slot(units=["°C"]), point(unit=""), value("21.0"), None, NOW)
    assert severity == v.WARNING
    assert "no unit" in issues[0]["message"]


def test_non_numeric_value_is_blocked():
    severity, _ = v.validate_slot(
        slot(required=True, units=["°C"], point_types=["numeric"],
             validation={"min": 0}),
        point(), value("banana"), None, NOW)
    assert severity == v.BLOCKED


def test_monotonic_decrease_warns():
    severity, issues = v.validate_slot(
        slot(key="energy_total", units=["kWh"], validation={"monotonic": True}),
        point(unit="kWh"), value("100"), {"decreases": 3}, NOW)
    assert severity == v.WARNING
    assert "decreased 3" in issues[0]["message"]


def test_monotonic_with_no_decrease_is_clean():
    assert v.validate_slot(
        slot(key="energy_total", units=["kWh"], validation={"monotonic": True}),
        point(unit="kWh"), value("100"), {"decreases": 0}, NOW)[0] == v.OK


def test_enum_outside_declared_range_warns():
    severity, issues = v.validate_slot(
        slot(units=[]), point(unit="", enum_range=["COOLING", "HEATING"]),
        value("TURBO"), None, NOW)
    assert severity == v.WARNING
    assert "outside" in issues[0]["message"]


# -- Observations --------------------------------------------------------

def test_first_observation_seeds_the_maximum():
    obs = {}
    v.record_observation(obs, "/p/", "100", NOW)
    assert obs["/p/"]["max"] == 100.0
    assert obs["/p/"]["decreases"] == 0


def test_rising_values_record_no_decrease():
    obs = {}
    for reading in ("100", "101", "105"):
        v.record_observation(obs, "/p/", reading, NOW)
    assert obs["/p/"]["decreases"] == 0
    assert obs["/p/"]["max"] == 105.0


def test_a_drop_is_counted():
    obs = {}
    for reading in ("100", "101", "40"):
        v.record_observation(obs, "/p/", reading, NOW)
    assert obs["/p/"]["decreases"] == 1
    assert obs["/p/"]["max"] == 101.0


def test_unparseable_readings_are_ignored():
    obs = {}
    v.record_observation(obs, "/p/", "n/a", NOW)
    assert obs == {}


# -- Aggregation ---------------------------------------------------------

def test_worst_severity_wins():
    assert v.worst([v.OK, v.WARNING, v.BLOCKED]) == v.BLOCKED
    assert v.worst([v.OK, v.WARNING]) == v.WARNING
    assert v.worst([]) == v.OK


def test_blocked_device_is_not_publishable():
    class T:
        slots = [slot(required=True, units=["°C"], validation={"min": 5})]
    report = v.validate_device(
        T(), {"temperature": "/p/"}, {"/p/": point()},
        {"/p/": value("0.0")}, {}, NOW)
    assert report["severity"] == v.BLOCKED
    assert report["publishable"] is False
    assert report["blocked"] == 1


def test_clean_device_is_publishable():
    class T:
        slots = [slot(required=True, units=["°C"], validation={"min": 5, "max": 40})]
    report = v.validate_device(
        T(), {"temperature": "/p/"}, {"/p/": point()},
        {"/p/": value("22.4")}, {}, NOW)
    assert report["severity"] == v.OK
    assert report["publishable"] is True


# -- The publish gate ----------------------------------------------------
#
# Publishing is what puts a device in front of Home Assistant, so a device
# whose values contradict its template must not get through.

class _Template:
    def __init__(self, slots):
        self.slots = slots


def test_a_blocked_device_reports_its_blocking_issues():
    template = _Template([
        slot(key="temperature", required=True, units=["°C"],
             validation={"min": 5, "max": 40}),
        slot(key="door", required=False),
    ])
    report = v.validate_device(
        template, {"temperature": "/p/"}, {"/p/": point()},
        {"/p/": value("0.0")}, {}, NOW)

    assert report["publishable"] is False
    blocking = [i["message"] for s in report["slots"] for i in s["issues"]
                if i["severity"] == v.BLOCKED]
    assert len(blocking) == 1
    assert "below" in blocking[0]


def test_warnings_alone_do_not_block_publishing():
    """A stale reading is worth surfacing, not worth refusing."""
    template = _Template([slot(key="temperature", required=True, units=["°C"])])
    report = v.validate_device(
        template, {"temperature": "/p/"}, {"/p/": point()},
        {"/p/": value("21.0", status="fault")}, {}, NOW)

    assert report["severity"] == v.WARNING
    assert report["publishable"] is True
    assert report["warnings"] == 1


def test_an_unbound_required_slot_blocks_publishing():
    template = _Template([slot(key="temperature", required=True)])
    report = v.validate_device(template, {}, {}, {}, {}, NOW)
    assert report["publishable"] is False
    assert report["blocked"] == 1


def test_a_device_with_only_optional_slots_unbound_is_publishable():
    template = _Template([
        slot(key="temperature", required=True, units=["°C"],
             validation={"min": 5, "max": 40}),
        slot(key="door", required=False),
    ])
    report = v.validate_device(
        template, {"temperature": "/p/"}, {"/p/": point()},
        {"/p/": value("22.0")}, {}, NOW)
    assert report["publishable"] is True
    assert report["severity"] == v.OK


def test_an_optional_slot_never_blocks():
    """Losing a whole meter over an optional frequency reading 0 is worse
    than publishing it with that one value flagged."""
    severity, issues = v.validate_slot(
        slot(required=False, units=["Hz"], validation={"min": 40, "max": 70}),
        point(unit="Hz"), value("0"), None, NOW)

    assert severity == v.WARNING
    # The issue itself is still reported at full severity, so the UI can
    # show why the reading is not to be trusted.
    assert any(i["severity"] == v.BLOCKED for i in issues)


def test_a_required_slot_still_blocks():
    severity, _ = v.validate_slot(
        slot(required=True, units=["Hz"], validation={"min": 40, "max": 70}),
        point(unit="Hz"), value("0"), None, NOW)
    assert severity == v.BLOCKED
