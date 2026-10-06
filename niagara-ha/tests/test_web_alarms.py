"""Tests for the alarm endpoints.

An empty alarm list has four causes that look identical from the API — the
station exports no alarm service, the oBIX user cannot see it, the console
is genuinely clear, or the records arrived in a shape the parser did not
recognise. These check that the response says which.

web.py had no tests at all before this; it is the largest module in the
add-on and holds the UI, the REST API and the template editor.
"""
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

# web.py imports Flask at module level. Skipping rather than failing keeps
# this file honest about its one dependency: CI's add-on job installs only
# pytest, requests and PyYAML, so adding Flask there is what turns these
# ten tests on.
pytest.importorskip("flask", reason="Flask is needed to import web.py")

import web  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A test client whose caches are files this test owns."""
    monkeypatch.setattr(web, "ALARMS_FILE", tmp_path / "alarms.json")
    monkeypatch.setattr(web, "ALARM_PROBE_FILE", tmp_path / "alarms_probe.json")
    web.app.config["TESTING"] = True
    with web.app.test_client() as test_client:
        test_client.tmp = tmp_path
        yield test_client


def write(client, name, payload):
    (client.tmp / name).write_text(json.dumps(payload))


ALARM = {
    "uri": "/obix/alarms/a1/",
    "source": "/config/Drivers/J1/points/AHU1/Trip/",
    "source_name": "AHU-1 Fire Trip",
    "timestamp": "2026-10-07T08:12:33+10:00",
    "normal_timestamp": "",
    "ack_state": "unacked",
    "acked": False,
    "active": True,
    "priority": 1,
    "alarm_value": "true",
}
CLEARED = {**ALARM, "uri": "/obix/alarms/a2/", "active": False, "acked": True}

WORKING_PROBE = {
    "subject_advertised": "alarms/",
    "subject_readable": True,
    "query_op": "/alarms/query/",
    "record_count": 1,
    "checked_at": 1_760_000_000.0,
}


def test_before_the_first_connect_the_probe_says_so():
    """Rather than an empty report that reads like "no alarms"."""
    with web.app.test_client() as client:
        web.ALARM_PROBE_FILE = Path("/nonexistent/alarms_probe.json")
        body = client.get("/api/alarms/probe").get_json()
    assert "not connected" in body["error"]


def test_a_clear_console_is_supported_and_empty(client):
    write(client, "alarms_probe.json", WORKING_PROBE)
    write(client, "alarms.json", {
        "ts": 1_760_000_100.0, "records": [],
        "summary": {"total": 0, "active": 0, "unacked": 0,
                    "highest_priority": None},
    })
    body = client.get("/api/alarms").get_json()
    assert body["supported"] is True
    assert body["records"] == []
    assert body["error"] is None


def test_a_station_without_the_alarm_service_says_why(client):
    write(client, "alarms_probe.json", {
        "subject_advertised": None,
        "error": "The oBIX lobby advertises no obix:AlarmSubject.",
        "checked_at": 1_760_000_000.0,
    })
    body = client.get("/api/alarms").get_json()
    assert body["supported"] is False
    assert "AlarmSubject" in body["error"]


def test_the_console_is_returned_with_its_summary(client):
    write(client, "alarms_probe.json", WORKING_PROBE)
    write(client, "alarms.json", {
        "ts": 1_760_000_100.0, "records": [ALARM, CLEARED],
        "summary": {"total": 2, "active": 1, "unacked": 1,
                    "highest_priority": 1},
    })
    body = client.get("/api/alarms").get_json()
    assert len(body["records"]) == 2
    assert body["summary"]["unacked"] == 1
    assert body["age"] is not None


def test_the_integration_endpoint_sends_only_active_alarms(client):
    """A station after a noisy week would otherwise send thousands."""
    write(client, "alarms_probe.json", WORKING_PROBE)
    write(client, "alarms.json", {
        "ts": 1_760_000_100.0, "records": [ALARM, CLEARED], "summary": {},
    })
    body = client.get("/api/integration/alarms").get_json()
    assert [r["uri"] for r in body["records"]] == ["/obix/alarms/a1/"]


def test_a_poll_error_reaches_the_caller(client):
    write(client, "alarms_probe.json", WORKING_PROBE)
    write(client, "alarms.json", {
        "ts": 1_760_000_100.0, "error": "read timed out", "records": [],
    })
    body = client.get("/api/alarms").get_json()
    assert body["error"] == "read timed out"


def test_a_half_written_cache_does_not_take_the_api_down(client):
    """The poll loop writes atomically, but a truncated file must not 500."""
    (client.tmp / "alarms.json").write_text('{"records": [')
    write(client, "alarms_probe.json", WORKING_PROBE)
    response = client.get("/api/alarms")
    assert response.status_code == 200
    assert response.get_json()["records"] == []


def test_a_cache_holding_the_wrong_type_is_ignored(client):
    (client.tmp / "alarms.json").write_text("[1, 2, 3]")
    write(client, "alarms_probe.json", WORKING_PROBE)
    assert client.get("/api/alarms").get_json()["records"] == []


def test_the_summary_is_never_missing_keys(client):
    """The integration reads these without checking."""
    write(client, "alarms_probe.json", WORKING_PROBE)
    body = client.get("/api/alarms").get_json()
    assert set(body["summary"]) == {
        "total", "active", "unacked", "highest_priority",
    }


def test_the_raw_reply_is_kept_when_nothing_parsed(client):
    """The only way to tell an empty console from an unknown format."""
    write(client, "alarms_probe.json", {
        **WORKING_PROBE, "record_count": 0,
        "raw": '<obj is="obix:AlarmQueryOut"><something-unexpected/></obj>',
    })
    body = client.get("/api/alarms/probe").get_json()
    assert "something-unexpected" in body["raw"]
