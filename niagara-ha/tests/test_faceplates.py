"""The faceplate catalogue and the swap it performs on a rendered card."""
import pytest

import faceplates as fp


def test_catalogue_loads():
    entries = fp.load_faceplates()
    assert entries, "the vendored catalogue should not be empty"
    assert all(e.get("id") and e.get("card") for e in entries)


def test_every_faceplate_publishes_its_roles():
    """The roles are the contract between a template and a faceplate. A
    face with none would silently bind nothing."""
    for entry in fp.load_faceplates():
        assert entry.get("roles"), f"{entry['id']} publishes no roles"


def test_faceplates_are_filtered_by_card():
    meters = fp.faceplates_for_card("custom:bms-meter-card")
    assert meters
    assert all(f["card"] == "bms-meter-card" for f in meters)
    assert not any(f["card"] == "pump-system-card" for f in meters)


def test_card_types_are_found_through_a_stack():
    card = {"type": "vertical-stack", "cards": [
        {"type": "custom:bms-meter-card"}, {"type": "entities"}]}
    assert fp.card_types_in(card) == ["custom:bms-meter-card"]


def test_swap_targets_the_card_that_can_wear_the_face():
    card = {"type": "vertical-stack", "cards": [
        {"type": "custom:pump-system-card", "faceplate": "vertical-pumpset"},
        {"type": "custom:bms-meter-card", "faceplate": "din-3phase-analyser"},
    ]}
    out = fp.apply_faceplate(card, "schneider-pm2200")
    assert out["cards"][0]["faceplate"] == "vertical-pumpset"
    assert out["cards"][1]["faceplate"] == "schneider-pm2200"


def test_unknown_faceplate_leaves_the_default_alone():
    """A bad setting must not cost the user a working card."""
    card = {"type": "custom:bms-meter-card", "faceplate": "din-3phase-analyser"}
    assert fp.apply_faceplate(card, "no-such-face") == card


def test_roles_are_read_from_the_card_that_uses_them():
    card = {"type": "vertical-stack", "cards": [
        {"type": "custom:bms-meter-card",
         "entities": {"energy_total": "x", "volts_l1": "y"}},
        {"type": "entities", "entities": [{"entity": "z"}]},
    ]}
    assert fp.roles_in(card, "custom:bms-meter-card") == {"energy_total", "volts_l1"}


def test_a_faceplate_sharing_no_role_is_not_a_candidate():
    """A water register and a three-phase analyser are both drawn by the
    meter card, but a register on a switchboard would come up blank."""
    card = {"type": "custom:bms-meter-card",
            "entities": {"energy_total": "x", "volts_l1": "y"}}
    bound = fp.roles_in(card, "custom:bms-meter-card")
    usable = {
        f["id"] for f in fp.faceplates_for_card("custom:bms-meter-card")
        if bound & set(f.get("roles") or [])
    }
    assert "din-3phase-analyser" in usable
    assert "multijet-water-register" not in usable


# -- how a device is drawn -----------------------------------------------

STACK = {
    "type": "vertical-stack",
    "cards": [
        {"type": "custom:bms-meter-card", "faceplate": "din-3phase-analyser"},
        {"type": "entities", "entities": [{"entity": "sensor.x"}]},
    ],
}


def test_both_leaves_the_stack_alone():
    assert fp.apply_display(STACK, "both") == STACK


def test_card_only_unwraps_to_the_faceplate():
    out = fp.apply_display(STACK, "card")
    assert out["type"] == "custom:bms-meter-card"


def test_entities_only_unwraps_to_the_list():
    out = fp.apply_display(STACK, "entities")
    assert out["type"] == "entities"


def test_an_unknown_mode_changes_nothing():
    assert fp.apply_display(STACK, "nonsense") == STACK


def test_asking_for_a_faceplate_that_is_not_there_keeps_the_card():
    """Better the whole card than an empty one: a template with no
    faceplate still has something worth showing."""
    plain = {"type": "vertical-stack",
             "cards": [{"type": "entities", "entities": []}]}
    assert fp.apply_display(plain, "card") == plain


def test_a_card_that_is_not_a_stack_is_left_alone():
    assert fp.apply_display({"type": "entities"}, "card") == {"type": "entities"}
