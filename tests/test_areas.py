"""How the integration decides a device's area.

The area used to come from a single depth number, which put 275 of 323
devices on the reference station into one area named after the station.
The add-on's rules now decide; the depth number survives only as the
fallback for a station with no rules written.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ha_stub  # noqa: E402

_ha_stub.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.niagara.coordinator import (  # noqa: E402
    NiagaraCoordinator,
    NiagaraPoint,
)

JACE = "/config/Drivers/NiagaraNetwork/SiteJace2/points/SiteBMS"


def coordinator(area_depth=1):
    coord = object.__new__(NiagaraCoordinator)
    coord.area_depth = area_depth
    return coord


def point(path, area=None, group=""):
    return NiagaraPoint(path=path, name="x", group=group, area=area)


def test_a_rule_resolved_area_is_used():
    coord = coordinator()
    p = point(f"{JACE}/Kitchens/Main_Kitchen/Fridge-1/Temp/",
              area="Kitchen - Main")
    assert coord.get_area(p) == "Kitchen - Main"


def test_without_a_rule_it_falls_back_to_the_depth_number():
    """Kept only because it is what every station had before rules."""
    coord = coordinator(area_depth=1)
    p = point(f"{JACE}/Kitchens/Main_Kitchen/Fridge-1/Temp/")
    assert coord.get_area(p) == "SiteJace2"


def test_the_fallback_is_the_failure_the_rules_replace():
    """Every device under one station lands in one area — the whole
    building in a single bucket."""
    coord = coordinator(area_depth=1)
    paths = [
        f"{JACE}/Kitchens/Main_Kitchen/Fridge-1/Temp/",
        f"{JACE}/Electrical/DB-L5.1-Power/kWh/",
        f"{JACE}/HVAC/AC/Rooms/331/RoomTemp/",
    ]
    assert {coord.get_area(point(p)) for p in paths} == {"SiteJace2"}


def test_a_rule_separates_what_the_depth_number_cannot():
    coord = coordinator(area_depth=1)
    cases = [
        (f"{JACE}/Kitchens/Main_Kitchen/Fridge-1/Temp/", "Kitchen - Main"),
        (f"{JACE}/Electrical/DB-L5.1-Power/kWh/", "Switch Room"),
        (f"{JACE}/HVAC/AC/Rooms/331/RoomTemp/", "Room 331"),
    ]
    assert [coord.get_area(point(p, area=a)) for p, a in cases] == [
        "Kitchen - Main", "Switch Room", "Room 331"]


def test_an_empty_rule_area_is_not_used():
    """The add-on sends None when no rule claims a folder; an empty string
    must not blank the area either."""
    coord = coordinator(area_depth=1)
    assert coord.get_area(point(f"{JACE}/X/Y/", area="")) == "SiteJace2"


def test_a_short_path_has_no_fallback_area():
    coord = coordinator(area_depth=4)
    assert coord.get_area(point("/config/points/Loose/")) is None


@pytest.mark.parametrize("path,station", [
    (f"{JACE}/X/Y/", "sitejace2"),
    ("/config/Drivers/NiagaraNetwork/Site$20Two/points/A/", "site two"),
    ("/config/points/Loose/", ""),
])
def test_the_station_is_read_from_the_path(path, station):
    """apply_areas uses this to tell its own bad default from an area
    somebody chose."""
    assert NiagaraCoordinator._station_of(path) == station


def test_the_station_named_area_is_not_treated_as_knowledge():
    """It is the default from when the area was a depth number. Learning
    from it writes one rule per device cementing the bug — on the real
    station that was 275 of 322 devices."""
    import inspect

    from custom_components.niagara import services

    source = inspect.getsource(services._async_register_extra_services)
    learn = source[source.index("async def handle_learn_areas"):
                   source.index("async def handle_apply_areas")]
    assert "_station_of_path" in learn
    assert "area.lower() not in stations" in learn
    assert "ignored_station_default" in learn
