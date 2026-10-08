"""Attributes must not change on every poll.

Home Assistant writes a new `states` row whenever the state *or the
attributes* change. An attribute carrying a value that moves every poll
therefore turns every entity into a recorder firehose, regardless of
whether its reading moved.

That is not hypothetical. `seconds_since_update` was an attribute on every
Niagara entity, and on this station it produced 5.9 million database rows
a day — 44.7 million rows, 9.9 GB — for 2,256 entities most of which were
reporting the same number every time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ha_stub  # noqa: E402

_ha_stub.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.niagara.coordinator import (  # noqa: E402
    NiagaraCoordinator,
    NiagaraDevice,
    NiagaraPoint,
)
from custom_components.niagara.entity import (  # noqa: E402
    NiagaraEntity,
    NiagaraDeviceEntity,
)

PATH = "/config/Drivers/NiagaraNetwork/J2/points/BMS/Electrical/DB1/V1/"


class _Coordinator:
    def __init__(self, point):
        self.addon_url = "http://addon:8099"
        self.host = self.addon_url
        self.device_name = "Niagara BMS"
        self.device_name_depth = 1
        self.area_depth = 1
        self.stale_after = 90
        self.last_update_success = True
        self.points = {point.path: point}
        self.data = {point.path: point}

    def get_group(self, point):
        return point.group

    def get_area(self, point):
        return None


def entity(value="238.6", status="ok", age=1.0):
    point = NiagaraPoint(
        path=PATH, name="V1", group="J2/BMS/Electrical/DB1",
        value=value, status=status, age=age, point_type="numeric",
    )
    return NiagaraEntity(_Coordinator(point), point)


def test_the_age_is_not_an_attribute():
    """The whole point. It changed every poll and cost 5.9 million rows a
    day for readings that had not moved."""
    attrs = entity().extra_state_attributes
    assert "seconds_since_update" not in attrs


def test_attributes_are_identical_across_polls_when_nothing_changed():
    """A frozen reading must produce a byte-identical attribute set, or
    the recorder writes a row anyway."""
    first = entity(age=1.0).extra_state_attributes
    later = entity(age=1800.0).extra_state_attributes
    assert first == later


def test_a_status_change_does_still_show():
    """Status changes rarely and matters, so it stays."""
    ok = entity(status="ok").extra_state_attributes
    bad = entity(status="fault").extra_state_attributes
    assert ok != bad
    assert bad["niagara_status"] == "fault"


def test_the_path_is_kept_and_is_static():
    attrs = entity().extra_state_attributes
    assert attrs["niagara_path"] == PATH


def test_no_attribute_value_is_a_live_counter():
    """A guard against reintroducing any per-poll value under a new name.
    Every attribute must be identical for two entities differing only in
    age."""
    a = entity(age=0.5).extra_state_attributes
    b = entity(age=9999.0).extra_state_attributes
    differing = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    assert not differing, f"attributes that vary with age: {differing}"


def test_device_level_attributes_are_static_too():
    """Climate and fan entities carry their slot paths, which never move."""
    point = NiagaraPoint(
        path=PATH, name="V1", group="J2/BMS/Electrical/DB1",
        slot="supply_air_temp", device_type="ahu", age=1.0,
    )
    device = NiagaraDevice(
        group="J2/BMS/Electrical/DB1", device_type="ahu",
        slots={"supply_air_temp": point},
    )

    class _Dev(NiagaraDeviceEntity):
        DOMAIN_KEY = "test"

    first = _Dev(_Coordinator(point), device).extra_state_attributes
    point.age = 5000.0
    later = _Dev(_Coordinator(point), device).extra_state_attributes
    assert first == later
