"""Tests for the fan platform.

A VFD's speed point is a percentage on some drives and Hz or rpm on others.
Reporting 49 Hz as 49% would be quietly wrong on every drive in a plant
room, so only an actual percentage becomes the fan's percentage.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ha_stub  # noqa: E402

_ha_stub.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.components.fan import FanEntityFeature  # noqa: E402

from custom_components.niagara.coordinator import (  # noqa: E402
    NiagaraDevice,
    NiagaraPoint,
)
from custom_components.niagara.fan import FAN_TEMPLATES, NiagaraFan  # noqa: E402


class _Coordinator:
    """Returns each point as given, always fresh and healthy."""

    def __init__(self, points):
        self.addon_url = "http://addon:8099"
        self.host = self.addon_url
        self.device_name = "Niagara BMS"
        self.device_name_depth = 1
        self.stale_after = 90
        self.last_update_success = True
        self.data = {p.path: p for p in points}

    def get_area(self, point):
        return None


def build(speed_unit=None, speed_value=None, running="true", command=None):
    points = [NiagaraPoint(
        path="/p/run/", name="Sts", group="Plant/EF-1", slot="run_status",
        device_type="fan", value=running, status="ok", age=1.0,
    )]
    slots = {"run_status": points[0]}
    if speed_unit is not None:
        speed = NiagaraPoint(
            path="/p/spd/", name="Spd", group="Plant/EF-1", slot="speed",
            device_type="fan", value=speed_value, unit=speed_unit,
            status="ok", age=1.0,
        )
        points.append(speed)
        slots["speed"] = speed
    if command is not None:
        cmd = NiagaraPoint(
            path="/p/cmd/", name="Cmd", group="Plant/EF-1", slot="command",
            device_type="fan", value=command, status="ok", age=1.0,
        )
        points.append(cmd)
        slots["command"] = cmd
    device = NiagaraDevice(group="Plant/EF-1", device_type="fan", slots=slots)
    return NiagaraFan(_Coordinator(points), device, FAN_TEMPLATES["fan"])


def test_run_status_drives_is_on():
    assert build(running="true").is_on is True
    assert build(running="false").is_on is False


def test_unreadable_run_status_is_unknown_not_off():
    assert build(running=None).is_on is None


def test_percentage_speed_is_reported():
    entity = build(speed_unit="%", speed_value="63")
    assert entity.percentage == 63
    assert FanEntityFeature.SET_SPEED in entity.supported_features


def test_hertz_speed_is_not_a_percentage():
    """A drive at 49 Hz is not a fan at 49%."""
    entity = build(speed_unit="Hz", speed_value="49.9")
    assert entity.percentage is None
    assert FanEntityFeature.SET_SPEED not in entity.supported_features


def test_hertz_speed_is_still_reported_as_an_attribute():
    entity = build(speed_unit="Hz", speed_value="49.9")
    attrs = entity.extra_state_attributes
    assert attrs["speed"] == 49.9
    assert attrs["speed_unit"] == "Hz"


def test_rpm_speed_is_not_a_percentage():
    assert build(speed_unit="rpm", speed_value="1450").percentage is None


def test_percentage_is_clamped_to_the_valid_range():
    """A drive reporting 104% would otherwise fail HA's validation."""
    assert build(speed_unit="%", speed_value="104").percentage == 100
    assert build(speed_unit="%", speed_value="-3").percentage == 0


def test_no_speed_point_means_no_set_speed_feature():
    entity = build()
    assert entity.percentage is None
    assert FanEntityFeature.SET_SPEED not in entity.supported_features


def test_turn_on_off_offered_only_with_a_command_point():
    assert FanEntityFeature.TURN_ON not in build().supported_features
    assert FanEntityFeature.TURN_ON in build(command="false").supported_features


def test_the_device_name_is_the_entity_name():
    """Otherwise every fan reads "EF-1 EF-1"."""
    assert build()._attr_name is None


def test_unparseable_speed_is_unknown_not_zero():
    assert build(speed_unit="%", speed_value="null").percentage is None


def test_ahu_fan_has_no_speed_role():
    assert "speed" not in FAN_TEMPLATES["ahu"]
