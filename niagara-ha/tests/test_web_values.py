"""Tests for the value endpoint's delta and long-poll modes.

Latency used to be the sum of two independent polls, and every one of them
re-sent all 1,800 readings. Both are fixed by the same change sequence, and
both failure modes it introduces are nasty: a delta that silently omits a
change leaves an entity frozen, and a held request that never returns
blocks the integration's listener.
"""
import json
import sys
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

pytest.importorskip("flask", reason="Flask is needed to import web.py")

import web  # noqa: E402

SELECTIONS = {
    f"/p/{name}/": {"path": f"/p/{name}/", "name": name, "enabled": True}
    for name in ("a", "b", "c")
}
SELECTIONS["/p/off/"] = {"path": "/p/off/", "name": "off", "enabled": False}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "VALUES_FILE", tmp_path / "values.json")
    monkeypatch.setattr(web, "_load_selections", lambda: SELECTIONS)
    monkeypatch.setattr(web, "_values_cache", {"mtime": 0.0, "data": {}, "seq": 0})
    web.app.config["TESTING"] = True
    with web.app.test_client() as test_client:
        test_client.tmp = tmp_path
        yield test_client


def write(client, seq, **values):
    """Write a cache where each named point carries the given sequence."""
    payload = {
        "seq": seq,
        "values": {
            f"/p/{name}/": {
                "value": str(spec[0]), "status": "ok",
                "ts": time.time(), "seq": spec[1],
            }
            for name, spec in values.items()
        },
    }
    (client.tmp / "values.json").write_text(json.dumps(payload))
    # The cache keys on mtime, which has coarse resolution on some
    # filesystems; nudge it so a same-second rewrite is still seen.
    web._values_cache["mtime"] = 0.0


# -- The unchanged shape ------------------------------------------------

def test_a_bare_request_still_returns_the_flat_map(client):
    """A released integration expects exactly this; it must not change."""
    write(client, 5, a=(21.5, 5), b=(2, 3))
    body = client.get("/api/integration/values").get_json()
    assert set(body) == {"/p/a/", "/p/b/", "/p/c/"}
    assert body["/p/a/"]["value"] == "21.5"
    assert body["/p/c/"] is None
    assert "seq" not in body


def test_disabled_points_are_not_sent(client):
    write(client, 1, a=(1, 1), off=(9, 1))
    assert "/p/off/" not in client.get("/api/integration/values").get_json()


# -- Deltas -------------------------------------------------------------

def test_a_delta_carries_only_what_moved(client):
    """The whole point: a dozen readings instead of eighteen hundred."""
    write(client, 7, a=(21.5, 7), b=(2, 3), c=(3, 1))
    body = client.get("/api/integration/values?since=6").get_json()
    assert list(body["values"]) == ["/p/a/"]
    assert body["seq"] == 7
    assert body["full"] is False
    assert body["total"] == 3


def test_a_delta_from_zero_is_everything(client):
    """A listener that has seen nothing needs the whole picture first."""
    write(client, 7, a=(1, 7), b=(2, 3))
    body = client.get("/api/integration/values?since=0").get_json()
    assert set(body["values"]) == {"/p/a/", "/p/b/", "/p/c/"}
    assert body["full"] is True


def test_nothing_new_is_an_empty_delta_not_an_error(client):
    write(client, 7, a=(1, 7))
    body = client.get("/api/integration/values?since=7").get_json()
    assert body["values"] == {}
    assert body["seq"] == 7


def test_a_watermark_from_before_a_restart_gets_everything(client):
    """The add-on's sequence restarts from the cache it loaded. A caller
    holding a higher number would otherwise never be sent anything again."""
    write(client, 3, a=(1, 3), b=(2, 2))
    body = client.get("/api/integration/values?since=9999").get_json()
    assert body["full"] is True
    assert set(body["values"]) == {"/p/a/", "/p/b/", "/p/c/"}


def test_a_point_with_no_reading_yet_is_still_listed_in_a_full_delta(client):
    write(client, 1, a=(1, 1))
    body = client.get("/api/integration/values?since=0").get_json()
    assert body["values"]["/p/c/"] is None


def test_a_nonsense_watermark_is_refused(client):
    assert client.get("/api/integration/values?since=soon").status_code == 400


# -- Holding the request open ------------------------------------------

def test_a_change_already_waiting_returns_at_once(client):
    write(client, 7, a=(1, 7))
    started = time.monotonic()
    body = client.get("/api/integration/values?since=6&wait=5").get_json()
    assert time.monotonic() - started < 1.0
    assert list(body["values"]) == ["/p/a/"]


def test_the_hold_gives_up_rather_than_blocking_forever(client):
    write(client, 7, a=(1, 7))
    started = time.monotonic()
    body = client.get("/api/integration/values?since=7&wait=1").get_json()
    elapsed = time.monotonic() - started
    assert 0.9 < elapsed < 3.0
    assert body["values"] == {}


def test_the_hold_is_capped_however_long_is_asked_for(client):
    """A request held past the proxy's own timeout is a dropped connection,
    not a longer wait."""
    assert web.MAX_LONG_POLL_SECONDS <= 55


def test_a_nonsense_wait_is_refused(client):
    assert client.get("/api/integration/values?wait=forever").status_code == 400


def test_waiting_without_a_watermark_still_answers(client):
    """since defaults to 0, so any data at all satisfies the wait."""
    write(client, 2, a=(1, 2))
    body = client.get("/api/integration/values?wait=2").get_json()
    assert body["/p/a/"]["value"] == "1"


# -- Reading both cache shapes ------------------------------------------

def test_a_cache_written_before_sequences_still_serves(client):
    """A reader that starts between two versions must not see an empty
    station."""
    (client.tmp / "values.json").write_text(json.dumps({
        "/p/a/": {"value": "21.5", "status": "ok", "ts": time.time()},
    }))
    web._values_cache["mtime"] = 0.0
    body = client.get("/api/integration/values").get_json()
    assert body["/p/a/"]["value"] == "21.5"


def test_the_oldest_flat_cache_shape_still_serves(client):
    (client.tmp / "values.json").write_text(json.dumps({"/p/a/": "21.5"}))
    web._values_cache["mtime"] = 0.0
    body = client.get("/api/integration/values").get_json()
    assert body["/p/a/"]["value"] == "21.5"
    assert body["/p/a/"]["status"] == "unknown"


def test_a_sequenceless_cache_reports_sequence_zero(client):
    (client.tmp / "values.json").write_text(json.dumps({"/p/a/": "1"}))
    web._values_cache["mtime"] = 0.0
    assert client.get(
        "/api/integration/values?since=0",
    ).get_json()["seq"] == 0


def test_a_half_written_cache_does_not_take_the_endpoint_down(client):
    (client.tmp / "values.json").write_text('{"seq": 3, "values": {')
    web._values_cache["mtime"] = 0.0
    response = client.get("/api/integration/values")
    assert response.status_code == 200
    assert response.get_json()["/p/a/"] is None


def test_the_server_is_threaded_so_a_held_request_does_not_block_the_ui():
    """A held value request occupies a worker for up to 45 seconds."""
    source = (SRC / "web.py").read_text()
    run_call = source[source.index("app.run("):]
    assert "threaded=True" in run_call[:run_call.index(")") + 1]
