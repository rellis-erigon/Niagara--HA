"""Tests for the History screen's API.

The screen is the whole interface to this feature, so the contract matters:
a history already chosen must not be offered again, a history the station
does not have must not be addable, and the integration must only ever be
handed trends that are actually switched on.
"""
import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

pytest.importorskip("flask", reason="Flask is needed to import web.py")

import history_store  # noqa: E402
import web  # noqa: E402

CATALOGUE = {
    "checked_at": 1_760_000_000.0,
    "error": None,
    "histories": [
        {"name": "J1/AHU1_SupplyTemp", "href": "J1/AHU1_SupplyTemp/",
         "count": 52560, "has_query": True},
        {"name": "J1/AHU1_ReturnTemp", "href": "J1/AHU1_ReturnTemp/",
         "count": 52560, "has_query": True},
        {"name": "J1/Meter_Total", "href": "J1/Meter_Total/",
         "count": 9000, "has_query": True},
    ],
    "links": {
        "/config/Drivers/J1/points/AHU1/SupplyTemp/": "J1/AHU1_SupplyTemp",
    },
}

SELECTIONS = {
    "/config/Drivers/J1/points/AHU1/SupplyTemp/": {
        "path": "/config/Drivers/J1/points/AHU1/SupplyTemp/",
        "name": "SupplyTemp", "group": "J1/HVAC/AHU1", "unit": "°C",
        "enabled": True, "type": "numeric",
    },
    "/config/Drivers/J1/points/AHU1/ReturnTemp/": {
        "path": "/config/Drivers/J1/points/AHU1/ReturnTemp/",
        "name": "ReturnTemp", "group": "J1/HVAC/AHU1", "unit": "°C",
        "enabled": False, "type": "numeric",
    },
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        web, "HISTORY_CATALOGUE_FILE", tmp_path / "histories_catalogue.json",
    )
    monkeypatch.setattr(
        web, "HISTORY_PROBE_FILE", tmp_path / "histories_probe.json",
    )
    monkeypatch.setattr(
        history_store, "HISTORIES_FILE", tmp_path / "histories.yaml",
    )
    monkeypatch.setattr(web, "_load_selections", lambda: SELECTIONS)
    (tmp_path / "histories_catalogue.json").write_text(json.dumps(CATALOGUE))
    web.app.config["TESTING"] = True
    with web.app.test_client() as test_client:
        test_client.tmp = tmp_path
        yield test_client


def post(client, path, **body):
    return client.post(path, json=body).get_json()


# -- The screen ---------------------------------------------------------

def test_the_screen_starts_off_with_nothing_selected(client):
    body = client.get("/api/histories").get_json()
    assert body["enabled"] is False
    assert body["selected"] == []
    assert body["available_total"] == 3


def test_the_screen_suggests_a_pairing_for_each_offered_trend(client):
    body = client.get("/api/histories").get_json()
    offered = {h["name"]: h for h in body["available"]}
    linked = offered["J1/AHU1_SupplyTemp"]
    assert linked["group"] == "J1/HVAC/AHU1"
    assert linked["confidence"] == "station"
    assert offered["J1/Meter_Total"]["confidence"] == "none"


def test_a_chosen_trend_is_not_offered_again(client):
    post(client, "/api/histories/add", history="J1/AHU1_SupplyTemp")
    body = client.get("/api/histories").get_json()
    assert [h["name"] for h in body["available"]] == [
        "J1/AHU1_ReturnTemp", "J1/Meter_Total",
    ]
    assert [h["history"] for h in body["selected"]] == ["J1/AHU1_SupplyTemp"]


def test_the_screen_reports_how_many_points_the_station_linked(client):
    assert client.get("/api/histories").get_json()["linked_points"] == 1


def test_the_catalogue_can_be_searched_rather_than_sent_whole(client):
    body = client.get("/api/histories?q=return").get_json()
    assert [h["name"] for h in body["available"]] == ["J1/AHU1_ReturnTemp"]


def test_the_offered_list_is_capped(client):
    body = client.get("/api/histories?limit=1").get_json()
    assert len(body["available"]) == 1
    assert body["available_total"] == 3


def test_a_station_with_no_history_service_says_why(client):
    (client.tmp / "histories_catalogue.json").write_text(json.dumps({
        "histories": [], "links": {}, "checked_at": 1.0,
        "error": "The oBIX lobby advertises no obix:HistoryService.",
    }))
    body = client.get("/api/histories").get_json()
    assert "HistoryService" in body["error"]
    assert body["available"] == []


# -- The master switch --------------------------------------------------

def test_the_switch_can_be_turned_on_and_off(client):
    assert post(client, "/api/histories/enabled", enabled=True)["enabled"] is True
    assert client.get("/api/histories").get_json()["enabled"] is True
    assert post(client, "/api/histories/enabled", enabled=False)["enabled"] is False


# -- Adding ------------------------------------------------------------

def test_adding_pairs_the_trend_to_its_device(client):
    body = post(client, "/api/histories/add", history="J1/AHU1_SupplyTemp")
    assert body["entry"]["group"] == "J1/HVAC/AHU1"
    assert body["entry"]["unit"] == "°C"
    assert body["paired_by"] == "station"


def test_adding_with_an_explicit_point_overrides_the_suggestion(client):
    body = post(
        client, "/api/histories/add", history="J1/Meter_Total",
        point="/config/Drivers/J1/points/AHU1/ReturnTemp/",
    )
    assert body["entry"]["group"] == "J1/HVAC/AHU1"
    assert body["paired_by"] == "manual"


def test_a_trend_the_station_does_not_have_cannot_be_added(client):
    response = client.post("/api/histories/add", json={"history": "J9/Nope"})
    assert response.status_code == 404


def test_adding_without_a_name_is_refused(client):
    assert client.post("/api/histories/add", json={}).status_code == 400


def test_an_unpairable_trend_can_still_be_added(client):
    """A trend worth keeping should not be blocked on naming it a device."""
    body = post(client, "/api/histories/add", history="J1/Meter_Total")
    assert body["entry"]["group"] == ""
    assert body["entry"]["enabled"] is True


# -- Toggling and removing ---------------------------------------------

def test_one_trend_can_be_disabled_without_losing_its_pairing(client):
    post(client, "/api/histories/add", history="J1/AHU1_SupplyTemp")
    post(client, "/api/histories/toggle",
         history="J1/AHU1_SupplyTemp", enabled=False)
    selected = client.get("/api/histories").get_json()["selected"][0]
    assert selected["enabled"] is False
    assert selected["group"] == "J1/HVAC/AHU1"


def test_toggling_something_not_selected_is_a_404(client):
    assert client.post(
        "/api/histories/toggle", json={"history": "J1/Nope", "enabled": True},
    ).status_code == 404


def test_removing_a_trend_puts_it_back_on_offer(client):
    post(client, "/api/histories/add", history="J1/AHU1_SupplyTemp")
    post(client, "/api/histories/remove", history="J1/AHU1_SupplyTemp")
    body = client.get("/api/histories").get_json()
    assert body["selected"] == []
    assert "J1/AHU1_SupplyTemp" in [h["name"] for h in body["available"]]


# -- Pairing on demand --------------------------------------------------

def test_the_pairing_endpoint_explains_its_evidence(client):
    body = client.get(
        "/api/histories/pair?history=J1/AHU1_SupplyTemp",
    ).get_json()
    assert body["confidence"] == "station"
    assert body["point_name"] == "SupplyTemp"
    assert body["point_enabled"] is True


def test_the_pairing_endpoint_reports_a_point_nobody_enabled(client):
    """A trend is often the reason to enable a point."""
    body = client.get("/api/histories/pair?history=J1/ReturnTemp").get_json()
    assert body["point_enabled"] is False
    assert body["confidence"] == "name"


# -- What the integration is given -------------------------------------

def test_the_integration_is_given_nothing_while_the_switch_is_off(client):
    post(client, "/api/histories/add", history="J1/AHU1_SupplyTemp")
    body = client.get("/api/integration/histories").get_json()
    assert body["enabled"] is False
    assert body["histories"] == []


def test_the_integration_is_given_only_enabled_trends(client):
    post(client, "/api/histories/enabled", enabled=True)
    post(client, "/api/histories/add", history="J1/AHU1_SupplyTemp")
    post(client, "/api/histories/add", history="J1/AHU1_ReturnTemp")
    post(client, "/api/histories/toggle",
         history="J1/AHU1_ReturnTemp", enabled=False)
    body = client.get("/api/integration/histories").get_json()
    assert [h["history"] for h in body["histories"]] == ["J1/AHU1_SupplyTemp"]
    assert body["histories"][0]["unit"] == "°C"
    assert body["histories"][0]["point_type"] == "numeric"


def test_data_for_a_trend_that_is_not_enabled_is_refused(client):
    """Otherwise a stale integration could keep pulling a disabled trend."""
    response = client.get("/api/integration/histories/data?history=J1/Nope")
    assert response.status_code == 403


def test_asking_for_data_without_a_name_is_refused(client):
    assert client.get("/api/integration/histories/data").status_code == 400


# -- The watermark ------------------------------------------------------

def test_the_integration_can_report_how_far_it_got(client):
    post(client, "/api/histories/enabled", enabled=True)
    post(client, "/api/histories/add", history="J1/AHU1_SupplyTemp")
    post(client, "/api/histories/synced",
         history="J1/AHU1_SupplyTemp",
         through="2026-10-07T09:00:00+10:00", imported=120)
    selected = client.get("/api/histories").get_json()["selected"][0]
    assert selected["last_synced"] == "2026-10-07T09:00:00+10:00"
    assert selected["last_count"] == 120


def test_a_watermark_without_a_timestamp_is_refused(client):
    assert client.post(
        "/api/histories/synced", json={"history": "J1/A"},
    ).status_code == 400


# -- The probe ----------------------------------------------------------

def test_before_the_first_connect_the_probe_says_so(client):
    body = client.get("/api/histories/probe").get_json()
    assert "not connected" in body["error"]


def test_the_probe_is_served_once_it_exists(client):
    (client.tmp / "histories_probe.json").write_text(json.dumps({
        "service_advertised": "histories/", "history_count": 3,
        "linked_points": 1, "checked_at": 1.0,
    }))
    body = client.get("/api/histories/probe").get_json()
    assert body["history_count"] == 3
