"""The contract between the templates and the faceplates they draw with.

The faceplates live in a different repository, and until this existed
nothing tied the two together. Both failure modes are silent: a renamed
role leaves the template binding a key no region reads, and a faceplate
missing from the bundled catalogue makes the card quietly fall back to the
template's default. Neither throws, so both go unnoticed until someone
looks at a dashboard and sees a device with holes in it.

This is the enforcement. It runs in CI against the real bundled catalogue
and the real templates, so the build fails rather than the dashboard.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import device_templates as dt  # noqa: E402
import faceplate_contract as fc  # noqa: E402


@pytest.fixture(scope="module")
def catalogue():
    payload = json.loads((ROOT / "faceplates.json").read_text())
    return payload["faceplates"]


@pytest.fixture(scope="module")
def templates():
    return dt._load_dir(ROOT / "templates", builtin=True)


# -- The real thing -----------------------------------------------------

def test_every_builtin_template_agrees_with_the_faceplates(templates, catalogue):
    """The whole point. A rename or a stale catalogue fails here."""
    problems = fc.check_templates(templates, catalogue)
    assert not problems, "\n".join(p["message"] for p in problems)


def test_every_faceplate_a_template_names_is_in_the_catalogue(templates, catalogue):
    """Separately asserted because it is the one that bit: the catalogue is
    a hand-copied snapshot and had gone eight faces out of date."""
    known = {f["id"] for f in catalogue}
    for template_id, template in sorted(templates.items()):
        for use in fc.card_faceplates(getattr(template, "card", None)):
            assert use["faceplate"] in known, (
                f"template {template_id} draws with {use['faceplate']!r}, "
                "which is not in faceplates.json — refresh it from the "
                "cards repository"
            )


def test_the_catalogue_is_not_empty(catalogue):
    """A missing or truncated catalogue would make every check above pass
    by having nothing to compare against."""
    assert len(catalogue) > 20
    assert all(f.get("id") and f.get("card") for f in catalogue)


def test_every_faceplate_declares_its_roles(catalogue):
    """A face with no roles listed cannot be checked against, so the
    checks above would silently stop covering it."""
    missing = [f["id"] for f in catalogue if not f.get("roles")]
    assert not missing, f"faceplates declaring no roles: {missing}"


# -- The checks themselves ----------------------------------------------

FACES = [
    {"id": "underbench-fridge", "card": "plant-equipment-card",
     "roles": ["temperature", "setpoint", "door", "compressor", "alarm"],
     "options": [{"key": "doors"}, {"key": "drawers"}]},
    {"id": "pm2200", "card": "bms-meter-card",
     "roles": ["energy_total", "power_active"], "options": []},
]

GOOD = {"type": "vertical-stack", "cards": [
    {"type": "custom:plant-equipment-card", "faceplate": "underbench-fridge",
     "options": {"doors": 6},
     "entities": {"temperature": "x", "door": "y"}},
    {"type": "entities", "entities": ["z"]},
]}


def test_a_card_that_agrees_reports_nothing():
    assert fc.check_card(GOOD, FACES) == []


def test_a_renamed_role_is_caught():
    """The failure this exists for: the card keeps binding the old key and
    the region draws its placeholder."""
    card = {"type": "custom:plant-equipment-card",
            "faceplate": "underbench-fridge",
            "entities": {"temperature": "x", "cabinet_temp": "y"}}
    problems = fc.check_card(card, FACES, "template fridge")
    assert [p["kind"] for p in problems] == ["unknown_roles"]
    assert problems[0]["roles"] == ["cabinet_temp"]
    assert "renamed" in problems[0]["message"]


def test_a_faceplate_missing_from_the_catalogue_is_caught():
    card = {"type": "custom:plant-equipment-card", "faceplate": "storage-tanks",
            "entities": {"tank1_level": "x"}}
    problems = fc.check_card(card, FACES, "template water_tank")
    assert problems[0]["kind"] == "unknown_faceplate"
    assert "faceplates.json" in problems[0]["message"]


def test_a_face_on_the_wrong_card_is_caught():
    card = {"type": "custom:bms-meter-card", "faceplate": "underbench-fridge",
            "entities": {"temperature": "x"}}
    kinds = [p["kind"] for p in fc.check_card(card, FACES)]
    assert "wrong_card" in kinds


def test_an_option_the_face_does_not_take_is_caught():
    card = {"type": "custom:plant-equipment-card",
            "faceplate": "underbench-fridge", "options": {"shelves": 3},
            "entities": {"temperature": "x"}}
    problems = fc.check_card(card, FACES)
    assert problems[0]["kind"] == "unknown_options"
    assert problems[0]["options"] == ["shelves"]


def test_binding_fewer_roles_than_the_face_declares_is_fine():
    """Unbound regions hide themselves; that is the design, not a fault."""
    card = {"type": "custom:plant-equipment-card",
            "faceplate": "underbench-fridge", "entities": {"temperature": "x"}}
    assert fc.check_card(card, FACES) == []


def test_a_card_level_role_is_not_mistaken_for_a_region():
    """`name` is consumed by the card, not by a region."""
    card = {"type": "custom:plant-equipment-card",
            "faceplate": "underbench-fridge",
            "entities": {"temperature": "x", "name": "sensor.n"}}
    assert fc.check_card(card, FACES) == []


def test_several_faceplates_in_one_card_are_all_checked():
    card = {"type": "vertical-stack", "cards": [
        {"type": "custom:plant-equipment-card", "faceplate": "underbench-fridge",
         "entities": {"nope": "x"}},
        {"type": "custom:bms-meter-card", "faceplate": "pm2200",
         "entities": {"also_nope": "y"}},
    ]}
    assert len(fc.check_card(card, FACES)) == 2


def test_a_card_with_no_faceplate_is_not_checked():
    assert fc.check_card({"type": "entities", "entities": ["x"]}, FACES) == []


def test_a_face_declaring_no_roles_is_not_used_to_reject_bindings():
    """Otherwise a catalogue entry with the roles field missing would
    report every binding on it as unknown."""
    faces = [{"id": "mystery", "card": "plant-equipment-card", "roles": []}]
    card = {"type": "custom:plant-equipment-card", "faceplate": "mystery",
            "entities": {"anything": "x"}}
    assert fc.check_card(card, faces) == []


# -- Per-device choices -------------------------------------------------

def test_a_device_set_to_a_missing_face_is_caught():
    devices = {"Site/Kitchen/UB1": {
        "template": "fridge", "faceplate": "walkin-fridge",
        "faceplate_options": {},
    }}
    problems = fc.check_devices(devices, {}, FACES)
    assert problems[0]["kind"] == "unknown_faceplate"
    assert "Site/Kitchen/UB1" in problems[0]["message"]


def test_a_device_option_the_face_does_not_take_is_caught():
    devices = {"Site/Kitchen/UB1": {
        "template": "fridge", "faceplate": "underbench-fridge",
        "faceplate_options": {"doors": 6, "shelves": 2},
    }}
    problems = fc.check_devices(devices, {}, FACES)
    assert problems[0]["options"] == ["shelves"]


def test_a_device_on_its_templates_default_is_not_checked():
    devices = {"Site/DB1": {"template": "electricity_meter", "faceplate": ""}}
    assert fc.check_devices(devices, {}, FACES) == []


def test_the_real_devices_on_this_station_would_be_checked(catalogue):
    """The shape the store actually holds, so the check cannot silently
    stop matching it."""
    devices = {"G": {
        "template": "fridge", "faceplate": "underbench-fridge",
        "faceplate_options": {"doors": 6, "drawers": 0},
        "bindings": {}, "state": "published", "display": "both",
    }}
    assert fc.check_devices(devices, {}, catalogue) == []
