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
