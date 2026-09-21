"""Tests for Watch values-list parsing.

Regression cover for the failure where Niagara returned <err> for every
subscribed URI, those errors were counted as values, and the add-on stayed
in Watch mode serving nulls instead of falling back to legacy polling.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from obix_client import OBIX_NS, ObixClient  # noqa: E402

# Captured verbatim from a Niagara 4 station rejecting every subscribed URI.
WATCH_OUT_ALL_ERRORS = f"""<?xml version="1.0" encoding="UTF-8"?>
<obj is="obix:WatchOut" xmlns="{OBIX_NS}">
 <list name="values" of="obix:obj">
  <err href="/config/Drivers/NiagaraNetwork/Jace2/points/A/" is="obix:BadUriErr"
       display="com.tridium.obix.util.BadUriErr"/>
  <err href="/config/Drivers/NiagaraNetwork/Jace2/points/B/" is="obix:BadUriErr"
       display="com.tridium.obix.util.BadUriErr"/>
 </list>
</obj>"""

WATCH_OUT_MIXED = f"""<?xml version="1.0" encoding="UTF-8"?>
<obj is="obix:WatchOut" xmlns="{OBIX_NS}">
 <list name="values" of="obix:obj">
  <real href="/config/Drivers/NiagaraNetwork/Jace2/points/Temp/" val="21.5"
        status="ok" display="21.5 {{ok}}"/>
  <bool href="/config/Drivers/NiagaraNetwork/Jace2/points/Fan/" val="true"/>
  <err href="/config/Drivers/NiagaraNetwork/Jace2/points/Bad/" is="obix:BadUriErr"/>
 </list>
</obj>"""


@pytest.fixture
def client():
    return ObixClient(host="192.0.2.1", username="u", password="p")


def _values(xml: str):
    root = ET.fromstring(xml)
    return root.find(f".//{{{OBIX_NS}}}list[@name='values']")


def test_err_elements_are_not_values(client):
    """An <err> must parse to None, not a dict with value None."""
    parsed = [client._parse_watch_element(c) for c in _values(WATCH_OUT_ALL_ERRORS)]
    assert parsed == [None, None]
    assert client._watch_rejected == 2


def test_real_values_parse(client):
    elems = list(_values(WATCH_OUT_MIXED))
    temp = client._parse_watch_element(elems[0])
    assert temp == {
        "path": "/config/Drivers/NiagaraNetwork/Jace2/points/Temp/",
        "value": "21.5",
        "status": "ok",
    }
    fan = client._parse_watch_element(elems[1])
    assert fan["value"] == "true"
    assert fan["status"] == "ok"  # defaulted when absent


def test_href_is_used_unchanged(client):
    """Niagara returns the same path we subscribed with — no rewriting."""
    subscribed = "/config/Drivers/NiagaraNetwork/Jace2/points/Temp/"
    parsed = client._parse_watch_element(list(_values(WATCH_OUT_MIXED))[0])
    assert parsed["path"] == subscribed


def test_mixed_list_keeps_only_real_values(client):
    parsed = [c for c in (
        client._parse_watch_element(e) for e in _values(WATCH_OUT_MIXED)
    ) if c]
    assert len(parsed) == 2
    assert client._watch_rejected == 1


def test_element_without_href_is_skipped(client):
    elem = ET.fromstring(f'<real xmlns="{OBIX_NS}" val="1.0"/>')
    assert client._parse_watch_element(elem) is None


def test_value_tag_without_val_is_skipped(client):
    elem = ET.fromstring(f'<real xmlns="{OBIX_NS}" href="/a/"/>')
    assert client._parse_watch_element(elem) is None


# -- Path stability ------------------------------------------------------
#
# read_point used to return a point whose path came from _parse_point, which
# falls back to parent_path + name when the element has no href — doubling the
# last segment. The polling loop assigns its result back over active_points,
# so one successful read rewrote every path into a form the station rejects,
# and every subsequent poll failed permanently.

def test_read_point_keeps_requested_path_without_href(client, monkeypatch):
    path = "/config/Drivers/NiagaraNetwork/J2/points/Site/Active_Power_P1/"
    monkeypatch.setattr(client, "_get", lambda p: ET.fromstring(
        f'<real xmlns="{OBIX_NS}" name="Active_Power_P1" val="12.5"/>'))
    point = client.read_point(path)
    assert point is not None
    assert point.path == path


def test_read_point_keeps_requested_path_with_foreign_href(client, monkeypatch):
    path = "/config/Drivers/NiagaraNetwork/J2/points/Site/Temp/"
    monkeypatch.setattr(client, "_get", lambda p: ET.fromstring(
        f'<real xmlns="{OBIX_NS}" name="Temp" href="/somewhere/else/" val="21.0"/>'))
    point = client.read_point(path)
    assert point is not None
    assert point.path == path
