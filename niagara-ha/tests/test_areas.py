"""Mapping device folders onto Home Assistant areas.

The old rule was a single depth number, which put 275 of 323 devices on
the reference station into one area named after the station. The useful
level is at a different depth in every branch, so these tests use the real
shapes from that station.
"""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import areas  # noqa: E402

R = areas.AreaRule

# The four shapes that defeat any positional rule.
ROOM = "Jace2/SiteBMS/HVAC/AC/Rooms/$3331"          # the device is the room
FRIDGE = "Jace2/SiteBMS/Kitchens/Main_Kitchen/Fridge$2d1"  # parent is the place
BOARD = "Jace2/SiteBMS/Electrical/DB$2dL5.1$2dPower"       # parent is the place
HYD = "Jace2/SiteBMS/MISC/HYDRAULICS"                # the device is the place


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(areas, "AREAS_FILE", tmp_path / "areas.yaml")
    return tmp_path / "areas.yaml"


# -- Matching -----------------------------------------------------------

def test_no_rules_claims_nothing():
    """A station with no rules must keep the behaviour it already had."""
    assert areas.resolve(FRIDGE, []) is None


def test_a_prefix_without_a_wildcard_covers_what_is_under_it():
    """Somebody writing "Kitchens" means everything in it, not a folder
    called exactly that."""
    rules = [R("Jace2/SiteBMS/Kitchens", "Kitchen - Main")]
    assert areas.resolve(FRIDGE, rules) == "Kitchen - Main"


def test_a_prefix_does_not_match_a_sibling_with_the_same_start():
    rules = [R("Jace2/SiteBMS/Kitchen", "Wrong")]
    assert areas.resolve(FRIDGE, rules) is None


def test_a_glob_matches_mid_path():
    rules = [R("*/Electrical/*", "Electrical")]
    assert areas.resolve(BOARD, rules) == "Electrical"


def test_the_first_matching_rule_wins():
    rules = [
        R("Jace2/SiteBMS/Kitchens/Main_Kitchen", "Kitchen - Main"),
        R("Jace2/SiteBMS/Kitchens", "Kitchens - other"),
    ]
    assert areas.resolve(FRIDGE, rules) == "Kitchen - Main"


def test_escapes_are_decoded_before_matching():
    """A rule should be writable with the names Workbench shows, not with
    $2d in it."""
    rules = [R("*/Kitchens/Main_Kitchen/Fridge-1", "Kitchen - Main")]
    assert areas.resolve(FRIDGE, rules) == "Kitchen - Main"


def test_the_device_folders_own_name_can_be_the_area():
    """For the 192 FCUs the device *is* the room."""
    rules = [R("*/HVAC/AC/Rooms/*", "Room {name}")]
    assert areas.resolve(ROOM, rules) == "Room 331"


def test_the_parent_folder_can_be_the_area():
    rules = [R("*/Electrical/*", "{parent}")]
    assert areas.resolve(BOARD, rules) == "Electrical"


def test_a_device_that_is_its_own_place_resolves():
    rules = [R("*/MISC/HYDRAULICS", "Hydrulics Room")]
    assert areas.resolve(HYD, rules) == "Hydrulics Room"


def test_an_empty_group_claims_nothing():
    assert areas.resolve("", [R("*", "Everything")]) is None


def test_a_rule_resolving_to_nothing_is_ignored():
    """{parent} on a top-level folder is empty, and an empty area name
    would blank the device's area rather than leave it alone."""
    assert areas.resolve("Jace2", [R("*", "{parent}")]) is None


# -- Learning from what is already there --------------------------------

def test_rules_are_learned_from_the_shared_prefix():
    """The hand-made assignments are the real knowledge about the
    building; inventing names from path segments would discard them."""
    rules = areas.learn({
        "Jace2/SiteBMS/Kitchens/Main_Kitchen/Fridge-1": "Kitchen - Main",
        "Jace2/SiteBMS/Kitchens/Main_Kitchen/Freezer-2": "Kitchen - Main",
        "Jace2/SiteBMS/Kitchens/Main_Kitchen/UnderBench-3": "Kitchen - Main",
    })
    assert len(rules) == 1
    assert rules[0].match == "Jace2/SiteBMS/Kitchens/Main_Kitchen"
    assert rules[0].area == "Kitchen - Main"
    assert "3 devices" in rules[0].note


def test_an_area_with_one_device_gets_an_exact_rule():
    """One device says nothing about where the boundary is."""
    rules = areas.learn({"Jace2/SiteBMS/MISC/HYDRAULICS": "Hydrulics Room"})
    assert rules[0].match == "Jace2/SiteBMS/MISC/HYDRAULICS"
    assert "one device" in rules[0].note


def test_an_area_spanning_unrelated_branches_names_each_device():
    """A rule matching everything would be worse than being verbose."""
    rules = areas.learn({"AAA/Thing": "Lifts", "BBB/Other": "Lifts"})
    assert {r.match for r in rules} == {"AAA/Thing", "BBB/Other"}
    assert all(r.area == "Lifts" for r in rules)
    assert all("share no folder of their own" in r.note for r in rules)


def test_a_prefix_shared_with_another_area_is_not_used():
    """The failure this guards: on the real station a loading bay, three
    comms rooms and the lifts all have their devices under Electrical, and
    one area has as good a claim to that folder as another. Taking the
    prefix gave the first of them all twenty-nine boards."""
    rules = areas.learn({
        "J/BMS/Electrical/DB-1": "Comms Room L4",
        "J/BMS/Electrical/DB-2": "Comms Room L4",
        "J/BMS/Electrical/DB-3": "Comms Room L5",
        "J/BMS/Electrical/DB-4": "Comms Room L5",
    })
    assert not any(r.match == "J/BMS/Electrical" for r in rules)
    assert len(rules) == 4
    assert all("shares a folder with other areas" in r.note for r in rules)


def test_an_exclusive_prefix_is_still_used():
    """Being cautious must not stop the easy case working."""
    rules = areas.learn({
        "J/BMS/Kitchens/Main/A": "Kitchen - Main",
        "J/BMS/Kitchens/Main/B": "Kitchen - Main",
        "J/BMS/Electrical/DB-1": "Switch Room",
        "J/BMS/Electrical/DB-2": "Switch Room",
    })
    assert {(r.match, r.area) for r in rules} == {
        ("J/BMS/Kitchens/Main", "Kitchen - Main"),
        ("J/BMS/Electrical", "Switch Room"),
    }


def test_a_nested_area_does_not_block_its_parent():
    """A sub-area inside a bigger one is normal. The parent's prefix is
    not exclusive, so each of its devices is named instead — which is
    correct, if wordy."""
    rules = areas.learn({
        "J/BMS/Plant/A": "Plant Room",
        "J/BMS/Plant/B": "Plant Room",
        "J/BMS/Plant/Pumps/P1": "Pump Room",
        "J/BMS/Plant/Pumps/P2": "Pump Room",
    })
    resolved = {g: areas.resolve(g, rules) for g in (
        "J/BMS/Plant/A", "J/BMS/Plant/B",
        "J/BMS/Plant/Pumps/P1", "J/BMS/Plant/Pumps/P2")}
    assert resolved == {
        "J/BMS/Plant/A": "Plant Room", "J/BMS/Plant/B": "Plant Room",
        "J/BMS/Plant/Pumps/P1": "Pump Room",
        "J/BMS/Plant/Pumps/P2": "Pump Room"}


def test_every_learned_rule_set_round_trips_exactly():
    """The invariant worth holding: learn from what is there, and every
    device that had an area still has it."""
    assignments = {
        "J/BMS/Kitchens/Main/A": "Kitchen - Main",
        "J/BMS/Kitchens/Main/B": "Kitchen - Main",
        "J/BMS/Electrical/DB-1": "Comms Room L4",
        "J/BMS/Electrical/DB-2": "Comms Room L5",
        "J/BMS/Electrical/DB-3": "Lifts",
        "J/BMS/MISC/HYD": "Hydraulics",
        "AAA/Odd": "Lifts",
    }
    rules = areas.learn(assignments)
    for group, area in assignments.items():
        assert areas.resolve(group, rules) == area, group


def test_longer_prefixes_are_ordered_first():
    """Otherwise the general rule shadows the specific one inside it."""
    rules = areas.learn({
        "Jace2/BMS/Kitchens/A/x": "Kitchen - Main",
        "Jace2/BMS/Kitchens/A/y": "Kitchen - Main",
        "Jace2/BMS/Kitchens/B/x": "Kitchen - Function",
        "Jace2/BMS/Kitchens/B/y": "Kitchen - Function",
        "Jace2/BMS/Electrical/a": "Electrical",
        "Jace2/BMS/Electrical/b": "Electrical",
    })
    lengths = [len(r.match) for r in rules]
    assert lengths == sorted(lengths, reverse=True)


def test_devices_with_no_area_are_not_learned_from():
    rules = areas.learn({"Jace2/BMS/X/a": "", "Jace2/BMS/X/b": None})
    assert rules == []


def test_learned_rules_resolve_the_devices_they_came_from():
    """The round trip is the point: learn, then every device that had an
    area keeps it."""
    assignments = {
        "Jace2/BMS/Kitchens/Main/Fridge-1": "Kitchen - Main",
        "Jace2/BMS/Kitchens/Main/Freezer-2": "Kitchen - Main",
        "Jace2/BMS/Electrical/DB-1": "Switch Room",
        "Jace2/BMS/Electrical/DB-2": "Switch Room",
    }
    rules = areas.learn(assignments)
    for group, area in assignments.items():
        assert areas.resolve(group, rules) == area, group


# -- Persistence --------------------------------------------------------

def test_rules_round_trip(store):
    rules = [R("*/Kitchens/*", "Kitchen - Main", "learned"),
             R("*/HVAC/AC/Rooms/*", "Room {name}")]
    areas.save(rules)
    back = areas.load()
    assert [(r.match, r.area) for r in back] == [
        ("*/Kitchens/*", "Kitchen - Main"), ("*/HVAC/AC/Rooms/*", "Room {name}")]


def test_order_survives_the_round_trip(store):
    """First match wins, so the order is the configuration."""
    rules = [R("a/b/c", "Specific"), R("a", "General")]
    areas.save(rules)
    assert [r.area for r in areas.load()] == ["Specific", "General"]


def test_a_missing_file_is_no_rules(store):
    assert areas.load() == []


def test_a_damaged_file_falls_back_to_no_rules(store):
    """Which is the behaviour every station had before rules existed —
    better than taking area mapping down to nothing it can explain."""
    store.write_text("rules: [this is not\n")
    assert areas.load() == []


def test_a_rule_missing_its_area_is_dropped(store):
    store.write_text("rules:\n- match: 'a/b'\n- match: 'c/d'\n  area: Good\n")
    loaded = areas.load()
    assert [r.area for r in loaded] == ["Good"]


def test_the_file_explains_itself(store):
    areas.save([R("a", "B")])
    assert "first match wins" in store.read_text()


# -- Preview ------------------------------------------------------------

def test_preview_shows_what_each_device_would_get():
    rules = [R("*/HVAC/AC/Rooms/*", "Room {name}"),
             R("*/Kitchens/*", "Kitchen - Main")]
    out = areas.preview([ROOM, FRIDGE, BOARD], rules)
    assert out[ROOM] == "Room 331"
    assert out[FRIDGE] == "Kitchen - Main"
    assert out[BOARD] is None


def test_a_prefix_covering_a_sliver_of_its_folder_is_rejected():
    """Two rooms assigned inside a folder of two hundred made that folder
    look exclusive, and the rule would have moved all two hundred into one
    of them. Exclusivity among the *assigned* devices is not enough."""
    assignments = {
        "J/BMS/HVAC/AC/Rooms/301": "L3 Wing",
        "J/BMS/HVAC/AC/Rooms/302": "L3 Wing",
    }
    everything = list(assignments) + [
        f"J/BMS/HVAC/AC/Rooms/{n}" for n in range(400, 600)
    ]
    rules = areas.learn(assignments, all_groups=everything)
    assert not any(r.match == "J/BMS/HVAC/AC/Rooms" for r in rules)
    assert {r.match for r in rules} == set(assignments)
    assert all("covers only" in r.note for r in rules)


def test_a_prefix_covering_most_of_its_folder_is_accepted():
    assignments = {f"J/BMS/MISC/HYD/{n}": "Hydraulics" for n in "ABCD"}
    everything = list(assignments) + ["J/BMS/MISC/HYD/Pumps"]
    rules = areas.learn(assignments, all_groups=everything)
    assert [r.match for r in rules] == ["J/BMS/MISC/HYD"]
    assert "80% of that folder" in rules[0].note


def test_the_coverage_check_still_round_trips_every_assignment():
    assignments = {
        "J/BMS/HVAC/AC/Rooms/301": "L3 Wing",
        "J/BMS/HVAC/AC/Rooms/302": "L3 Wing",
        "J/BMS/MISC/HYD/A": "Hydraulics",
        "J/BMS/MISC/HYD/B": "Hydraulics",
    }
    everything = list(assignments) + [
        f"J/BMS/HVAC/AC/Rooms/{n}" for n in range(400, 500)
    ]
    rules = areas.learn(assignments, all_groups=everything)
    for group, area in assignments.items():
        assert areas.resolve(group, rules) == area, group


def test_folders_nobody_assigned_stay_unclaimed():
    """We do not know where they are, and guessing would be worse than
    leaving them for somebody who does."""
    assignments = {"J/BMS/MISC/HYD/A": "Hydraulics",
                   "J/BMS/MISC/HYD/B": "Hydraulics"}
    everything = list(assignments) + ["J/BMS/HVAC/AC/Rooms/301"]
    rules = areas.learn(assignments, all_groups=everything)
    assert areas.resolve("J/BMS/HVAC/AC/Rooms/301", rules) is None
