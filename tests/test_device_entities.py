"""Tests for device-level entity grouping and the registry purge.

The purge deletes any entity whose unique id is not a known point path.
Climate and fan entities are keyed on the device group instead, so they are
stale by that rule the moment they are created — the first refresh after
setup would have deleted every one of them. These tests exist because that
bug was in the first working version of the platform.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ha_stub  # noqa: E402

_ha_stub.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.niagara.climate import CLIMATE_TEMPLATES  # noqa: E402
from custom_components.niagara.coordinator import (  # noqa: E402
    NiagaraCoordinator,
    NiagaraPoint,
    stable_id,
)
from custom_components.niagara.fan import FAN_TEMPLATES  # noqa: E402

HOST = "http://addon:8099"


def coordinator(points):
    """A coordinator with just the state these methods read."""
    coord = object.__new__(NiagaraCoordinator)
    coord.addon_url = HOST
    coord.points = {p.path: p for p in points}
    coord.device_name_depth = 1
    return coord


def point(group, slot, device_type, name=None, unit=None):
    return NiagaraPoint(
        path=f"/config/points/{group}/{slot}/",
        name=name or slot,
        group=group,
        slot=slot,
        device_type=device_type,
        unit=unit,
    )


FCU_POINTS = [
    point("Jace/L1/Room101", "room_temperature", "fcu", unit="°C"),
    point("Jace/L1/Room101", "setpoint", "fcu", unit="°C"),
    point("Jace/L1/Room101", "mode", "fcu"),
    point("Jace/L1/Room101", "fan_status", "fcu"),
    point("Jace/L1/Room101", "command", "fcu"),
]


# -- Grouping -----------------------------------------------------------

def test_points_group_into_one_device():
    devices = coordinator(FCU_POINTS).devices()
    assert list(devices) == ["Jace/L1/Room101"]
    device = devices["Jace/L1/Room101"]
    assert device.device_type == "fcu"
    assert set(device.slots) == {
        "room_temperature", "setpoint", "mode", "fan_status", "command",
    }


def test_two_rooms_stay_two_devices():
    others = [
        point("Jace/L1/Room102", "room_temperature", "fcu", unit="°C"),
        point("Jace/L1/Room102", "mode", "fcu"),
    ]
    devices = coordinator(FCU_POINTS + others).devices()
    assert len(devices) == 2


def test_untyped_points_form_no_device():
    """Only a published, typed device has a device_type."""
    loose = [
        NiagaraPoint(path="/p/a/", name="a", group="Jace/Misc"),
        NiagaraPoint(path="/p/b/", name="b", group="Jace/Misc", slot="x"),
    ]
    assert coordinator(loose).devices() == {}


def test_a_point_with_a_type_but_no_slot_is_not_a_device():
    orphan = [NiagaraPoint(path="/p/a/", name="a", group="G", device_type="fcu")]
    assert coordinator(orphan).devices() == {}


# -- The purge ----------------------------------------------------------

def test_climate_unique_id_survives_the_purge():
    coord = coordinator(FCU_POINTS)
    expected = f"niagara_climate_{stable_id(HOST + '/Jace/L1/Room101')}"
    assert expected in coord.device_entity_unique_ids()


def test_point_unique_ids_and_device_ids_do_not_collide():
    coord = coordinator(FCU_POINTS)
    point_ids = {f"niagara_{stable_id(p.path)}" for p in FCU_POINTS}
    assert not point_ids & coord.device_entity_unique_ids()


def test_a_device_missing_its_required_slot_claims_no_unique_id():
    """Otherwise the purge would spare an entity that was never created."""
    partial = [p for p in FCU_POINTS if p.slot != "room_temperature"]
    assert coordinator(partial).device_entity_unique_ids() == set()


def test_fan_device_gets_a_fan_unique_id_only():
    fan_points = [
        point("Jace/Plant/EF-1", "run_status", "fan"),
        point("Jace/Plant/EF-1", "speed", "fan", unit="%"),
    ]
    ids = coordinator(fan_points).device_entity_unique_ids()
    assert ids == {f"niagara_fan_{stable_id(HOST + '/Jace/Plant/EF-1')}"}


def test_ahu_can_claim_both_a_climate_and_a_fan_id():
    """An AHU is both; the two must not share a unique id."""
    ahu = [
        point("Jace/Plant/AHU-1", "supply_air_temp", "ahu", unit="°C"),
        point("Jace/Plant/AHU-1", "supply_fan_status", "ahu"),
    ]
    ids = coordinator(ahu).device_entity_unique_ids()
    assert len(ids) == 2
    assert any("climate" in i for i in ids)
    assert any("fan" in i for i in ids)


def test_every_listed_template_declares_the_slot_the_purge_checks():
    """device_entity_unique_ids looks for one of these two keys.

    A new template added to either table without one of them would silently
    claim a unique id for an entity the platform never creates.
    """
    for roles in CLIMATE_TEMPLATES.values():
        assert "current_temperature" in roles
    for roles in FAN_TEMPLATES.values():
        assert "running" in roles
