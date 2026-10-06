"""Tests for the trend selection and its pairing.

Pairing is the part worth testing hard. The station's own historyName is
authoritative; a name match is a guess and must be reported as one, and an
ambiguous name must pair to nothing rather than to whichever point happened
to be first.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import history_store  # noqa: E402


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        history_store, "HISTORIES_FILE", tmp_path / "histories.yaml",
    )
    return tmp_path / "histories.yaml"


def point(name, group, unit="", enabled=True, custom_unit=""):
    return {
        "name": name, "group": group, "unit": unit,
        "enabled": enabled, "custom_unit": custom_unit,
    }


POINTS = {
    "/config/Drivers/J1/points/AHU1/SupplyTemp/":
        point("SupplyTemp", "J1/HVAC/AHU1", "°C"),
    "/config/Drivers/J1/points/AHU1/ReturnTemp/":
        point("ReturnTemp", "J1/HVAC/AHU1", "°C"),
    "/config/Drivers/J1/points/AHU2/SupplyTemp/":
        point("SupplyTemp", "J1/HVAC/AHU2", "°C"),
}

LINKS = {
    "/config/Drivers/J1/points/AHU1/SupplyTemp/": "J1/AHU1_SupplyTemp",
}


# -- The master switch --------------------------------------------------

def test_syncing_is_off_until_switched_on():
    """Importing years of trend must never be the default."""
    assert history_store.load()["enabled"] is False


def test_the_switch_persists():
    history_store.set_global_enabled(True)
    assert history_store.load()["enabled"] is True
    history_store.set_global_enabled(False)
    assert history_store.load()["enabled"] is False


def test_turning_the_switch_off_keeps_the_selection():
    """The reason to stop is usually a struggling station; losing forty
    chosen trends would make nobody use the switch."""
    history_store.add("J1/A")
    history_store.add("J1/B")
    history_store.set_global_enabled(False)
    assert set(history_store.load()["histories"]) == {"J1/A", "J1/B"}


# -- Adding one at a time -----------------------------------------------

def test_a_history_is_added_enabled_and_timestamped():
    entry = history_store.add("J1/AHU1_SupplyTemp", point="/p/", group="G")
    assert entry["enabled"] is True
    assert entry["added"] is not None
    assert entry["point"] == "/p/"


def test_adding_the_same_history_again_keeps_when_it_was_added():
    first = history_store.add("J1/A")
    again = history_store.add("J1/A", point="/p/")
    assert again["added"] == first["added"]
    assert again["point"] == "/p/"


def test_a_history_with_no_name_is_refused():
    with pytest.raises(ValueError):
        history_store.add("   ")


def test_a_leading_slash_does_not_create_a_second_entry():
    history_store.add("/J1/A")
    history_store.add("J1/A")
    assert list(history_store.load()["histories"]) == ["J1/A"]


def test_removing_a_history_that_is_not_there_says_so():
    assert history_store.remove("J1/nope") is False


def test_a_removed_history_is_gone():
    history_store.add("J1/A")
    assert history_store.remove("J1/A") is True
    assert history_store.load()["histories"] == {}


def test_a_single_history_can_be_disabled_without_removing_it():
    history_store.add("J1/A")
    history_store.set_enabled("J1/A", False)
    assert history_store.load()["histories"]["J1/A"]["enabled"] is False


def test_toggling_an_unknown_history_is_none_not_a_new_entry():
    assert history_store.set_enabled("J1/nope", True) is None
    assert history_store.load()["histories"] == {}


# -- What gets synced ---------------------------------------------------

def test_nothing_syncs_while_the_master_switch_is_off():
    history_store.add("J1/A")
    assert history_store.enabled_entries() == {}


def test_only_enabled_entries_sync():
    history_store.set_global_enabled(True)
    history_store.add("J1/A")
    history_store.add("J1/B", enabled=False)
    assert list(history_store.enabled_entries()) == ["J1/A"]


# -- The watermark ------------------------------------------------------

def test_the_watermark_is_the_stations_time_not_the_wall_clock():
    """A station whose clock differs must not lose or repeat a window."""
    history_store.add("J1/A")
    history_store.record_sync("J1/A", "2026-10-07T09:00:00+10:00", 120)
    entry = history_store.load()["histories"]["J1/A"]
    assert entry["last_synced"] == "2026-10-07T09:00:00+10:00"
    assert entry["last_count"] == 120
    assert entry["last_run"] is not None


def test_a_watermark_for_an_unknown_history_is_ignored():
    history_store.record_sync("J1/nope", "2026-10-07T09:00:00Z", 1)
    assert history_store.load()["histories"] == {}


def test_a_new_history_has_no_watermark_so_it_backfills():
    assert history_store.add("J1/A")["last_synced"] is None


# -- Pairing ------------------------------------------------------------

def test_the_stations_own_link_pairs_a_history_to_its_device():
    paired = history_store.pair("J1/AHU1_SupplyTemp", LINKS, POINTS)
    assert paired["point"] == "/config/Drivers/J1/points/AHU1/SupplyTemp/"
    assert paired["group"] == "J1/HVAC/AHU1"
    assert paired["unit"] == "°C"
    assert paired["confidence"] == "station"


def test_a_name_match_pairs_but_is_reported_as_a_guess():
    paired = history_store.pair("J1/ReturnTemp", {}, POINTS)
    assert paired["point"].endswith("/AHU1/ReturnTemp/")
    assert paired["confidence"] == "name"


def test_an_ambiguous_name_pairs_to_nothing():
    """Two AHUs both have a SupplyTemp; picking one would be a coin toss."""
    paired = history_store.pair("J1/SupplyTemp", {}, POINTS)
    assert paired["point"] == ""
    assert paired["confidence"] == "none"


def test_the_stations_link_beats_an_ambiguous_name():
    paired = history_store.pair("J1/AHU1_SupplyTemp", LINKS, POINTS)
    assert paired["confidence"] == "station"


def test_an_unknown_history_pairs_to_nothing():
    paired = history_store.pair("J9/Boiler_Flow", LINKS, POINTS)
    assert paired == {
        "point": "", "group": "", "unit": "", "confidence": "none",
    }


def test_a_unit_override_on_the_point_wins():
    points = {
        "/p/Flow/": point("Flow", "G", unit="l/s", custom_unit="L/min"),
    }
    paired = history_store.pair("J1/Flow", {"/p/Flow/": "J1/Flow"}, points)
    assert paired["unit"] == "L/min"


def test_pairing_finds_a_point_nobody_enabled():
    """A trend is often the reason to enable a point, not the other way round."""
    points = {"/p/T/": point("T", "G", "°C", enabled=False)}
    paired = history_store.pair("J1/T", {"/p/T/": "J1/T"}, points)
    assert paired["point"] == "/p/T/"


# -- Robustness ---------------------------------------------------------

def test_a_damaged_file_reads_as_empty_rather_than_failing(store):
    store.write_text("histories: [this is not a mapping\n")
    assert history_store.load() == {"enabled": False, "histories": {}}


def test_a_file_holding_the_wrong_type_reads_as_empty(store):
    store.write_text("- a\n- b\n")
    assert history_store.load()["histories"] == {}


def test_the_file_is_written_with_an_explanation(store):
    history_store.add("J1/A")
    assert "_comment" in store.read_text()


def test_no_temporary_file_is_left_behind(store):
    history_store.add("J1/A")
    assert not store.with_suffix(".tmp").exists()
