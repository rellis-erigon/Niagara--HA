"""Tests for the alarm entities.

The console is the part of a BMS that matters operationally, and getting it
subtly wrong is worse than not having it: an alarm reported as acknowledged
when nobody has seen it, or a count that silently drops the one that
matters, is a safety-adjacent failure rather than a cosmetic one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ha_stub  # noqa: E402

_ha_stub.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.niagara.alarm_state import (  # noqa: E402
    ATTRIBUTE_LIMIT,
    alarm_attributes,
    alarm_key,
    by_priority,
)
from custom_components.niagara.coordinator import (  # noqa: E402
    NiagaraCoordinator,
    NiagaraPoint,
    stable_id,
)
from custom_components.niagara.event import (  # noqa: E402
    EVENT_ALARM,
    EVENT_UNACKED,
    NiagaraAlarmEvent,
)

HOST = "http://addon:8099"


def alarm(source, ts="2026-10-07T08:00:00+10:00", priority=None,
          acked=False, uri=None, name="", value="true"):
    return {
        "uri": uri or f"/obix/alarms/{source.strip('/').replace('/', '-')}/",
        "source": source,
        "source_name": name or source.rstrip("/").split("/")[-1],
        "timestamp": ts,
        "normal_timestamp": "",
        "ack_state": "acked" if acked else "unacked",
        "acked": acked,
        "active": True,
        "alarm_value": value,
        "priority": priority,
    }


def coordinator(points=(), alarms=(), supported=True):
    coord = object.__new__(NiagaraCoordinator)
    coord.addon_url = HOST
    coord.points = {p.path: p for p in points}
    coord.device_name_depth = 1
    coord.device_name = "Niagara BMS"
    coord.alarms = list(alarms)
    coord.alarm_summary = {}
    coord.alarms_supported = supported
    coord.last_update_success = True
    coord.area_depth = 1
    coord.stale_after = 90
    coord.data = {p.path: p for p in points}
    return coord


# -- Ordering -----------------------------------------------------------

def test_priority_one_is_the_most_urgent():
    """Niagara counts down: 1 is critical, 255 is trivia."""
    ordered = by_priority([
        alarm("/p/a/", priority=200),
        alarm("/p/b/", priority=1),
        alarm("/p/c/", priority=40),
    ])
    assert [r["priority"] for r in ordered] == [1, 40, 200]


def test_an_alarm_with_no_priority_sorts_last():
    """No priority is no evidence of urgency; it must not top a dashboard."""
    ordered = by_priority([alarm("/p/a/"), alarm("/p/b/", priority=255)])
    assert ordered[0]["priority"] == 255


def test_equal_priorities_fall_back_to_time():
    ordered = by_priority([
        alarm("/p/a/", ts="2026-10-07T09:00:00+10:00", priority=1),
        alarm("/p/b/", ts="2026-10-07T07:00:00+10:00", priority=1),
    ])
    assert ordered[0]["source"] == "/p/b/"


# -- Attributes ---------------------------------------------------------

def test_counts_are_exact_even_when_the_list_is_sampled():
    """The list is capped for the recorder's sake; the count must not be."""
    many = [alarm(f"/p/{i}/", priority=i + 1) for i in range(ATTRIBUTE_LIMIT + 15)]
    attrs = alarm_attributes(many)
    assert attrs["alarm_count"] == ATTRIBUTE_LIMIT + 15
    assert len(attrs["alarms"]) == ATTRIBUTE_LIMIT
    assert attrs["alarms_truncated"] is True


def test_the_sample_keeps_the_most_urgent_not_the_first_seen():
    many = [alarm(f"/p/{i}/", priority=100) for i in range(ATTRIBUTE_LIMIT + 5)]
    many.append(alarm("/p/critical/", priority=1))
    attrs = alarm_attributes(many)
    assert attrs["highest_priority"] == 1
    assert attrs["most_urgent"] == "critical"
    assert any(a["source"] == "critical" for a in attrs["alarms"])


def test_unacked_are_counted_separately():
    attrs = alarm_attributes([
        alarm("/p/a/", acked=True), alarm("/p/b/"), alarm("/p/c/"),
    ])
    assert attrs["alarm_count"] == 3
    assert attrs["unacked_count"] == 2


def test_no_alarms_gives_zero_counts_and_no_stray_fields():
    attrs = alarm_attributes([])
    assert attrs["alarm_count"] == 0
    assert attrs["alarms"] == []
    assert "highest_priority" not in attrs
    assert "alarms_truncated" not in attrs


def test_the_oldest_alarm_is_reported():
    attrs = alarm_attributes([
        alarm("/p/a/", ts="2026-10-07T09:00:00+10:00"),
        alarm("/p/b/", ts="2026-10-05T03:00:00+10:00"),
    ])
    assert attrs["oldest"] == "2026-10-05T03:00:00+10:00"


# -- Alarm identity -----------------------------------------------------

def test_the_stations_uri_identifies_an_alarm():
    assert alarm_key(alarm("/p/a/", uri="/obix/alarms/xyz/")) == "/obix/alarms/xyz/"


def test_without_a_uri_the_source_and_time_identify_it():
    record = {"source": "/p/a/", "timestamp": "2026-10-07T08:00:00+10:00"}
    assert alarm_key(record) == "/p/a/@2026-10-07T08:00:00+10:00"


def test_a_repeat_alarm_on_the_same_point_is_a_different_alarm():
    """Keying on the source alone would silence every repeat."""
    first = {"source": "/p/a/", "timestamp": "2026-10-07T08:00:00+10:00"}
    again = {"source": "/p/a/", "timestamp": "2026-10-07T11:00:00+10:00"}
    assert alarm_key(first) != alarm_key(again)


# -- Attributing an alarm to a device -----------------------------------

AHU = [
    NiagaraPoint(
        path="/config/Drivers/J1/points/AHU1/SupplyTemp/",
        name="SupplyTemp", group="J1/HVAC/AHU1",
        slot="supply_air_temp", device_type="ahu",
    ),
    NiagaraPoint(
        path="/config/Drivers/J1/points/AHU1/FanSts/",
        name="FanSts", group="J1/HVAC/AHU1",
        slot="supply_fan_status", device_type="ahu",
    ),
]


def test_an_alarm_on_an_exported_point_lands_on_its_device():
    coord = coordinator(AHU, [alarm("/config/Drivers/J1/points/AHU1/FanSts/")])
    assert list(coord.alarms_by_group()) == ["J1/HVAC/AHU1"]


def test_an_alarm_on_a_sibling_point_nobody_enabled_still_lands():
    """Alarm extensions commonly sit on points that were never exported."""
    coord = coordinator(AHU, [alarm("/config/Drivers/J1/points/AHU1/FireTrip/")])
    assert list(coord.alarms_by_group()) == ["J1/HVAC/AHU1"]


def test_an_unplaceable_alarm_is_not_attached_to_the_wrong_device():
    coord = coordinator(AHU, [alarm("/config/Drivers/J9/points/Boiler/Lockout/")])
    assert coord.alarms_by_group() == {}


def test_several_alarms_on_one_device_group_together():
    coord = coordinator(AHU, [
        alarm("/config/Drivers/J1/points/AHU1/FanSts/"),
        alarm("/config/Drivers/J1/points/AHU1/FilterDp/"),
    ])
    assert len(coord.alarms_by_group()["J1/HVAC/AHU1"]) == 2


def test_an_alarm_with_no_source_is_station_level_only():
    coord = coordinator(AHU, [alarm("")])
    assert coord.alarms_by_group() == {}


# -- The purge ----------------------------------------------------------

def test_alarm_entity_ids_survive_the_purge():
    coord = coordinator(AHU, [])
    ids = coord.alarm_entity_unique_ids()
    station = stable_id(HOST)
    assert f"niagara_alarms_active_{station}" in ids
    assert f"niagara_alarms_unacked_{station}" in ids
    assert f"niagara_alarm_event_{station}" in ids
    assert (
        f"niagara_device_alarm_{stable_id(HOST + '/J1/HVAC/AHU1')}" in ids
    )


def test_a_station_without_alarming_claims_no_alarm_ids():
    """Otherwise the purge spares entities the platforms never created."""
    assert coordinator(AHU, [], supported=False).alarm_entity_unique_ids() == set()


def test_alarm_ids_do_not_collide_with_point_or_device_ids():
    coord = coordinator(AHU, [])
    point_ids = {f"niagara_{stable_id(p.path)}" for p in AHU}
    assert not coord.alarm_entity_unique_ids() & point_ids
    assert not coord.alarm_entity_unique_ids() & coord.device_entity_unique_ids()


# -- The event entity ---------------------------------------------------

def event_entity(alarms):
    coord = coordinator(AHU, alarms)
    entity = NiagaraAlarmEvent(coord)
    return coord, entity


def test_alarms_already_standing_at_startup_do_not_fire():
    """A Home Assistant restart is not an alarm."""
    coord, entity = event_entity([alarm("/p/a/"), alarm("/p/b/")])
    entity._handle_coordinator_update()
    assert getattr(entity, "triggered", []) == []


def test_a_new_alarm_fires_once():
    coord, entity = event_entity([alarm("/p/a/")])
    entity._handle_coordinator_update()
    coord.alarms = [alarm("/p/a/"), alarm("/p/b/", priority=5)]
    entity._handle_coordinator_update()
    assert len(entity.triggered) == 1
    kind, data = entity.triggered[0]
    assert kind == EVENT_UNACKED
    assert data["source"] == "b"


def test_the_same_alarm_standing_does_not_fire_again():
    coord, entity = event_entity([alarm("/p/a/")])
    entity._handle_coordinator_update()
    coord.alarms = [alarm("/p/a/"), alarm("/p/b/")]
    entity._handle_coordinator_update()
    entity.triggered.clear()
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    assert entity.triggered == []


def test_an_alarm_clearing_and_returning_fires_again():
    coord, entity = event_entity([alarm("/p/a/", ts="T1")])
    entity._handle_coordinator_update()
    coord.alarms = []
    entity._handle_coordinator_update()
    coord.alarms = [alarm("/p/a/", ts="T2")]
    entity._handle_coordinator_update()
    assert len(entity.triggered) == 1


def test_several_new_alarms_fire_the_most_urgent_and_count_the_rest():
    coord, entity = event_entity([])
    entity._handle_coordinator_update()
    coord.alarms = [
        alarm("/p/a/", priority=90),
        alarm("/p/urgent/", priority=1),
        alarm("/p/c/", priority=50),
    ]
    entity._handle_coordinator_update()
    assert len(entity.triggered) == 1
    kind, data = entity.triggered[0]
    assert data["source"] == "urgent"
    assert data["new_alarm_count"] == 3


def test_an_alarm_already_acknowledged_fires_the_plainer_event():
    coord, entity = event_entity([])
    entity._handle_coordinator_update()
    coord.alarms = [alarm("/p/a/", acked=True)]
    entity._handle_coordinator_update()
    assert entity.triggered[0][0] == EVENT_ALARM


def test_both_event_types_are_declared():
    """HA rejects an event type the entity did not declare."""
    _, entity = event_entity([])
    assert set(entity._attr_event_types) == {EVENT_ALARM, EVENT_UNACKED}


# -- The count sensors and the per-device flag --------------------------

from custom_components.niagara.binary_sensor import NiagaraDeviceAlarm  # noqa: E402
from custom_components.niagara.coordinator import NiagaraDevice  # noqa: E402
from custom_components.niagara.sensor import NiagaraAlarmCount  # noqa: E402


def counts(alarms):
    coord = coordinator(AHU, alarms)
    return (
        NiagaraAlarmCount(coord, unacked_only=False),
        NiagaraAlarmCount(coord, unacked_only=True),
    )


def test_the_two_counts_differ():
    """Thirty standing alarms everyone has seen is not one new alarm."""
    active, unacked = counts([
        alarm("/p/a/", acked=True),
        alarm("/p/b/", acked=True),
        alarm("/p/c/"),
    ])
    assert active.native_value == 3
    assert unacked.native_value == 1


def test_a_clear_console_reads_zero_not_unknown():
    active, unacked = counts([])
    assert active.native_value == 0
    assert unacked.native_value == 0


def test_the_counts_do_not_share_a_unique_id():
    active, unacked = counts([])
    assert active.unique_id != unacked.unique_id


def test_the_count_carries_the_alarm_list():
    active, _ = counts([alarm("/p/a/", priority=3, name="Chiller Lockout")])
    attrs = active.extra_state_attributes
    assert attrs["most_urgent"] == "Chiller Lockout"
    assert attrs["alarms"][0]["priority"] == 3


def device_alarm(alarms):
    coord = coordinator(AHU, alarms)
    device = NiagaraDevice(
        group="J1/HVAC/AHU1", device_type="ahu",
        slots={p.slot: p for p in AHU},
    )
    return coord, NiagaraDeviceAlarm(coord, device)


def test_the_device_flag_is_on_only_with_an_alarm_of_its_own():
    _, clear = device_alarm([])
    assert clear.is_on is False
    _, alarmed = device_alarm([alarm("/config/Drivers/J1/points/AHU1/FanSts/")])
    assert alarmed.is_on is True


def test_another_devices_alarm_does_not_light_this_one():
    _, entity = device_alarm([alarm("/config/Drivers/J9/points/Boiler/Lockout/")])
    assert entity.is_on is False


def test_the_device_flag_stays_available_when_its_points_fault():
    """The alarming device is the likeliest one to have faulted points."""
    coord, entity = device_alarm([alarm("/config/Drivers/J1/points/AHU1/FanSts/")])
    for point in AHU:
        point.status = "fault"
    coord.data = {}
    assert entity.available is True
    assert entity.is_on is True
