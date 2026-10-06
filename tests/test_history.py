"""Tests for importing trend logs into long-term statistics.

Everything here guards a failure that corrupts data rather than merely
looking wrong: importing a cumulative total without its sum, writing a
partial hour the recorder is still accumulating, or looping forever against
a live station because a window stopped advancing.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ha_stub  # noqa: E402

_ha_stub.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.niagara import history  # noqa: E402
from custom_components.niagara.coordinator import stable_id  # noqa: E402

NOW = datetime(2026, 10, 7, 12, 34, tzinfo=timezone.utc)
POINT = "/config/Drivers/J1/points/AHU1/SupplyTemp/"


# -- The hour boundary --------------------------------------------------

def test_the_current_hour_is_where_the_recorder_is_still_working():
    assert history.current_hour(NOW) == datetime(
        2026, 10, 7, 12, tzinfo=timezone.utc,
    )


def test_the_hour_is_computed_in_utc_whatever_zone_arrives():
    brisbane = datetime(
        2026, 10, 7, 22, 30, tzinfo=timezone(timedelta(hours=10)),
    )
    assert history.current_hour(brisbane) == datetime(
        2026, 10, 7, 12, tzinfo=timezone.utc,
    )


# -- Timestamps ---------------------------------------------------------

@pytest.mark.parametrize("raw,offset", [
    ("2026-10-07T08:00:00+10:00", timedelta(hours=10)),
    ("2026-10-07T08:00:00Z", timedelta(0)),
    ("2026-10-07T08:00:00", timedelta(0)),
])
def test_timestamps_are_read_with_their_zone(raw, offset):
    assert history._parse(raw).utcoffset() == offset


@pytest.mark.parametrize("raw", [None, "", "yesterday"])
def test_an_unreadable_timestamp_is_none(raw):
    assert history._parse(raw) is None


# -- Rows ---------------------------------------------------------------

def bucket(hours_ago, mean=20.0, low=18.0, high=22.0):
    """One hourly bucket, that many whole hours before the current one.

    Relative rather than a fixed date: the current hour is deliberately
    never imported, so a fixture pinned to a wall-clock date passes or
    fails depending on what time the suite is run.
    """
    start = history.current_hour() - timedelta(hours=hours_ago)
    return {
        "start": start.isoformat(),
        "mean": mean, "min": low, "max": high, "samples": 6,
    }


def hour_of(payload_bucket):
    return history._parse(payload_bucket["start"])


def test_buckets_become_rows_with_mean_min_and_max():
    rows = history._rows([bucket(3), bucket(2)])
    assert len(rows) == 2
    assert rows[0]["mean"] == 20.0
    assert rows[0]["min"] == 18.0
    assert rows[0]["max"] == 22.0


def test_a_bucket_with_an_unreadable_start_is_dropped_not_guessed():
    rows = history._rows([{"start": "nonsense", "mean": 1.0}, bucket(3)])
    assert len(rows) == 1


def test_the_metadata_says_it_is_not_a_sum():
    """A mean series with has_sum set would make the recorder expect a
    cumulative total it is never given."""
    meta = history._metadata("sensor.x", "°C")
    assert meta["has_sum"] is False
    assert meta["statistic_id"] == "sensor.x"
    assert meta["unit_of_measurement"] == "°C"


def test_the_metadata_source_is_the_recorder():
    """Any other source is rejected for a statistic id that is an entity."""
    assert history._metadata("sensor.x", None)["source"] == "recorder"


def test_the_metadata_declares_a_mean_series():
    meta = history._metadata("sensor.x", "°C")
    assert meta.get("mean_type") is not None or meta.get("has_mean") is True


# -- Choosing what to import against ------------------------------------

class _Registry:
    def __init__(self, entities=None, disabled=()):
        self._entities = entities or {}
        self._disabled = set(disabled)

    def async_get_entity_id(self, domain, platform, unique_id):
        return self._entities.get(unique_id)

    def async_get(self, entity_id):
        if entity_id in self._disabled:
            return type("Entry", (), {"disabled_by": "user"})()
        return type("Entry", (), {"disabled_by": None})()


class _States:
    def __init__(self, states=None):
        self._states = states or {}

    def get(self, entity_id):
        return self._states.get(entity_id)


def state(unit="°C", state_class="measurement"):
    attrs = {}
    if unit:
        attrs["unit_of_measurement"] = unit
    if state_class:
        attrs["state_class"] = state_class
    return type("State", (), {"attributes": attrs})()


def target(entry, registry=None, states=None, monkeypatch=None):
    hass = type("Hass", (), {"states": states or _States()})()
    reg = registry if registry is not None else _Registry(
        {f"niagara_{stable_id(POINT)}": "sensor.ahu1_supply_temp"},
    )
    from homeassistant.helpers import entity_registry as er

    original = er.async_get
    er.async_get = lambda _hass: reg
    try:
        return history.resolve_target(hass, None, entry)
    finally:
        er.async_get = original


def test_a_paired_trend_resolves_to_its_sensor():
    entity_id, unit, reason = target(
        {"point": POINT, "unit": "°C"},
        states=_States({"sensor.ahu1_supply_temp": state()}),
    )
    assert entity_id == "sensor.ahu1_supply_temp"
    assert unit == "°C"
    assert reason is None


def test_an_unpaired_trend_says_so():
    _, _, reason = target({"point": ""})
    assert "not paired" in reason


def test_a_trend_whose_point_has_no_sensor_says_to_enable_it():
    _, _, reason = target({"point": POINT}, registry=_Registry({}))
    assert "enable the point" in reason


def test_a_disabled_sensor_is_not_imported_against():
    _, _, reason = target(
        {"point": POINT},
        registry=_Registry(
            {f"niagara_{stable_id(POINT)}": "sensor.x"}, disabled=["sensor.x"],
        ),
    )
    assert "disabled" in reason


def test_a_cumulative_total_is_refused_with_the_reason():
    """Importing a total without its sum corrupts the energy dashboard."""
    _, _, reason = target(
        {"point": POINT},
        states=_States({
            "sensor.ahu1_supply_temp": state("kWh", "total_increasing"),
        }),
    )
    assert "cumulative sum" in reason


def test_the_live_unit_beats_the_one_the_add_on_reported():
    """The entity's unit is what the statistics have to agree with."""
    _, unit, _ = target(
        {"point": POINT, "unit": "°C"},
        states=_States({"sensor.ahu1_supply_temp": state("°F")}),
    )
    assert unit == "°F"


def test_a_sensor_with_no_state_yet_still_resolves():
    """It exists in the registry; its first reading has just not arrived."""
    entity_id, unit, reason = target({"point": POINT, "unit": "kPa"})
    assert entity_id and unit == "kPa" and reason is None


# -- A sync run ---------------------------------------------------------

class _Session:
    """Stands in for the add-on, recording what was asked of it."""

    def __init__(self, listing, pages):
        self.listing = listing
        self.pages = list(pages)
        self.requests = []
        self.posts = []

    def get(self, url, params=None, timeout=None):
        self.requests.append((url, dict(params or {})))
        if url.endswith("/api/integration/histories"):
            return _Response(self.listing)
        page = self.pages.pop(0) if self.pages else {
            "buckets": [], "complete": True,
        }
        return _Response(page)

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return _Response({})


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return self.payload


class _Coordinator:
    def __init__(self, session):
        self.addon_url = "http://addon:8099"
        self._session = session

    def _get_session(self):
        return self._session


def run_sync(listing, pages, states=None, registry=None, only=None):
    _ha_stub.reset_imported()
    session = _Session(listing, pages)
    coordinator = _Coordinator(session)
    hass = type("Hass", (), {"states": states or _States(
        {"sensor.ahu1_supply_temp": state()},
    )})()

    from homeassistant.helpers import entity_registry as er

    original = er.async_get
    er.async_get = lambda _hass: registry if registry is not None else _Registry(
        {f"niagara_{stable_id(POINT)}": "sensor.ahu1_supply_temp"},
    )
    try:
        syncer = history.HistorySync(hass, coordinator)
        result = asyncio.run(syncer.async_run(only))
    finally:
        er.async_get = original
    return result, session


ENABLED = {
    "enabled": True,
    "histories": [{
        "history": "J1/AHU1_SupplyTemp", "point": POINT,
        "group": "J1/HVAC/AHU1", "unit": "°C", "last_synced": None,
    }],
}


def test_nothing_is_imported_while_syncing_is_off():
    result, session = run_sync({"enabled": False, "histories": []}, [])
    assert result["synced"] == 0
    assert "off in the add-on" in result["reason"]
    assert _ha_stub.imported_statistics() == []


def test_a_trend_is_imported_hour_by_hour():
    result, _ = run_sync(
        ENABLED, [{"buckets": [bucket(3), bucket(2)], "complete": True}],
    )
    assert result["synced"] == 2
    (metadata, rows), = _ha_stub.imported_statistics()
    assert metadata["statistic_id"] == "sensor.ahu1_supply_temp"
    assert len(rows) == 2


def test_the_current_hour_is_never_imported():
    """The recorder is still accumulating it."""
    this_hour = history.current_hour()
    result, _ = run_sync(ENABLED, [{
        "buckets": [
            {"start": (this_hour - timedelta(hours=1)).isoformat(),
             "mean": 1.0, "min": 1.0, "max": 1.0},
            {"start": this_hour.isoformat(),
             "mean": 2.0, "min": 2.0, "max": 2.0},
        ],
        "complete": True,
    }])
    assert result["synced"] == 1


def test_the_watermark_is_reported_back_to_the_add_on():
    last = bucket(2)
    _, session = run_sync(
        ENABLED, [{"buckets": [bucket(3), last], "complete": True}],
    )
    url, body = session.posts[0]
    assert url.endswith("/api/histories/synced")
    assert body["history"] == "J1/AHU1_SupplyTemp"
    assert body["imported"] == 2
    # One hour past the last imported hour, so the next run resumes after it.
    assert history._parse(body["through"]) == hour_of(last) + timedelta(hours=1)


def test_an_existing_watermark_becomes_the_start_of_the_request():
    listing = {
        "enabled": True,
        "histories": [{**ENABLED["histories"][0],
                       "last_synced": "2026-10-01T00:00:00+00:00"}],
    }
    _, session = run_sync(listing, [{"buckets": [bucket(3)], "complete": True}])
    data_requests = [r for r in session.requests if "data" in r[0]]
    assert data_requests[0][1]["start"].startswith("2026-10-01")


def test_a_trend_already_current_is_not_re_requested():
    future = (history.current_hour() + timedelta(hours=2)).isoformat()
    listing = {
        "enabled": True,
        "histories": [{**ENABLED["histories"][0], "last_synced": future}],
    }
    result, session = run_sync(listing, [])
    assert result["histories"][0]["skipped"] == "already current"
    assert not [r for r in session.requests if "data" in r[0]]


def test_a_capped_reply_is_continued_from_where_it_stopped():
    second = bucket(4)
    result, session = run_sync(ENABLED, [
        {"buckets": [bucket(5), second], "complete": False},
        {"buckets": [bucket(3)], "complete": True},
    ])
    assert result["synced"] == 3
    data_requests = [r for r in session.requests if "data" in r[0]]
    assert len(data_requests) == 2
    assert history._parse(data_requests[1][1]["start"]) == (
        hour_of(second) + timedelta(hours=1)
    )


def test_a_window_that_stops_advancing_does_not_loop_forever():
    """An infinite request loop against a live station is the worst outcome
    available here, so no forward progress ends the run."""
    stuck = [{"buckets": [bucket(3)], "complete": False}] * 50
    result, session = run_sync(ENABLED, stuck)
    assert len([r for r in session.requests if "data" in r[0]]) < 20
    assert result["synced"] >= 1


def test_a_run_is_bounded_even_when_every_reply_is_capped():
    # Each page advances by an hour, so only the per-run cap stops it.
    pages = [
        {"buckets": [bucket(100 - i)], "complete": False}
        for i in range(90)
    ]
    _, session = run_sync(ENABLED, pages)
    assert len([r for r in session.requests if "data" in r[0]]) <= (
        history.MAX_WINDOWS_PER_RUN
    )


def test_a_trend_whose_sensor_is_missing_is_skipped_with_a_reason():
    result, _ = run_sync(ENABLED, [], registry=_Registry({}))
    assert "enable the point" in result["histories"][0]["skipped"]
    assert _ha_stub.imported_statistics() == []


def test_a_total_increasing_sensor_is_never_imported():
    result, _ = run_sync(
        ENABLED, [{"buckets": [bucket(3)], "complete": True}],
        states=_States({
            "sensor.ahu1_supply_temp": state("kWh", "total_increasing"),
        }),
    )
    assert "cumulative sum" in result["histories"][0]["skipped"]
    assert _ha_stub.imported_statistics() == []


def test_syncing_one_named_trend_leaves_the_others_alone():
    listing = {
        "enabled": True,
        "histories": [
            ENABLED["histories"][0],
            {"history": "J1/Other", "point": POINT, "unit": "°C",
             "last_synced": None},
        ],
    }
    result, _ = run_sync(
        listing, [{"buckets": [bucket(3)], "complete": True}],
        only="J1/AHU1_SupplyTemp",
    )
    assert [h["history"] for h in result["histories"]] == ["J1/AHU1_SupplyTemp"]


def test_asking_for_a_trend_that_is_not_enabled_says_so():
    result, _ = run_sync(ENABLED, [], only="J1/Nope")
    assert "not enabled" in result["reason"]


def test_an_empty_reply_imports_nothing_and_sets_no_watermark():
    result, session = run_sync(ENABLED, [{"buckets": [], "complete": True}])
    assert result["synced"] == 0
    assert session.posts == []


def test_two_runs_cannot_overlap():
    """A second run while one is in flight would re-request the same window."""
    syncer = history.HistorySync(None, None)
    syncer._running = True
    assert "already running" in asyncio.run(syncer.async_run())["skipped"]
