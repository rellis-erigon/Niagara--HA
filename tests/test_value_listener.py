"""Tests for the value listener.

It holds a request open at the add-on so a reading reaches a dashboard in
one hop rather than two. The failures that matter are quiet ones: a
listener that stops without saying so, one that busy-loops against the
add-on when something is wrong, and one that reschedules the periodic
refresh out of existence.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _ha_stub  # noqa: E402

_ha_stub.install()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.niagara.coordinator import (  # noqa: E402
    LONG_POLL_SECONDS,
    NiagaraCoordinator,
    NiagaraPoint,
)


def coordinator(points):
    coord = object.__new__(NiagaraCoordinator)
    coord.addon_url = "http://addon:8099"
    coord.points = {p.path: p for p in points}
    coord.scan_interval = 30
    coord._value_seq = 0
    coord._listener_task = None
    coord._listener_supported = True
    coord.notified = 0
    coord.async_update_listeners = lambda: setattr(
        coord, "notified", coord.notified + 1,
    )
    return coord


def points():
    return [
        NiagaraPoint(path="/p/a/", name="a", value="20.0", status="ok"),
        NiagaraPoint(path="/p/b/", name="b", value="1", status="ok"),
    ]


# -- Applying what arrives ----------------------------------------------

def test_a_changed_reading_is_applied_and_counted():
    coord = coordinator(points())
    moved = coord._apply_values({
        "/p/a/": {"value": "21.5", "status": "ok", "age": 2.0},
    })
    assert moved == 1
    assert coord.points["/p/a/"].value == "21.5"
    assert coord.points["/p/a/"].age == 2.0


def test_the_same_reading_again_counts_as_no_change():
    """Otherwise every held request would wake every entity for nothing."""
    coord = coordinator(points())
    assert coord._apply_values({
        "/p/a/": {"value": "20.0", "status": "ok", "age": 1.0},
    }) == 0


def test_a_status_change_alone_counts():
    """A reading that has gone to fault must reach the entity."""
    coord = coordinator(points())
    assert coord._apply_values({
        "/p/a/": {"value": "20.0", "status": "fault", "age": 1.0},
    }) == 1


def test_a_reading_for_an_unknown_point_is_ignored():
    coord = coordinator(points())
    assert coord._apply_values({"/p/gone/": {"value": "1"}}) == 0


def test_a_null_reading_clears_the_value_rather_than_keeping_the_old_one():
    coord = coordinator(points())
    coord._apply_values({"/p/a/": {"value": None, "status": "fault"}})
    assert coord.points["/p/a/"].value is None


def test_the_pre_envelope_shape_is_still_applied():
    """An add-on predating the timestamped format sends bare values."""
    coord = coordinator(points())
    coord._apply_values({"/p/a/": "21.5"})
    assert coord.points["/p/a/"].value == "21.5"


# -- The listening loop -------------------------------------------------

class _Listener:
    """Drives _async_listen with scripted replies, then stops it."""

    def __init__(self, coord, replies):
        self.coord = coord
        self.replies = list(replies)
        self.calls = []
        self.slept = []
        coord._fetch_value_changes = self._fetch

    async def _fetch(self, wait):
        self.calls.append(wait)
        if not self.replies:
            raise asyncio.CancelledError
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def run(self):
        async def sleep(seconds):
            self.slept.append(seconds)

        original = asyncio.sleep
        asyncio.sleep = sleep
        try:
            await self.coord._async_listen()
        except asyncio.CancelledError:
            pass
        finally:
            asyncio.sleep = original


def listen(replies, coord=None):
    coord = coord or coordinator(points())
    driver = _Listener(coord, replies)
    asyncio.run(driver.run())
    return coord, driver


def test_a_delta_updates_the_entities():
    coord, _ = listen([
        {"seq": 7, "values": {"/p/a/": {"value": "21.5", "status": "ok"}}},
    ])
    assert coord.points["/p/a/"].value == "21.5"
    assert coord.notified == 1


def test_the_watermark_advances_so_the_next_request_asks_for_less():
    coord, _ = listen([
        {"seq": 7, "values": {"/p/a/": {"value": "21.5", "status": "ok"}}},
    ])
    assert coord._value_seq == 7


def test_an_empty_delta_does_not_wake_the_entities():
    """A held request that times out is normal and means nothing happened."""
    coord, _ = listen([{"seq": 7, "values": {}}])
    assert coord.notified == 0
    assert coord._value_seq == 7


def test_an_old_add_on_stops_the_listener_rather_than_hot_looping():
    """Without the envelope there is nothing to wait on, so retrying would
    be a full fetch as fast as the network allows."""
    coord, driver = listen([None, {"seq": 1, "values": {}}])
    assert coord._listener_supported is False
    assert len(driver.calls) == 1


def test_a_failure_backs_off_instead_of_retrying_immediately():
    coord, driver = listen([
        TimeoutError("add-on busy"),
        TimeoutError("add-on busy"),
        {"seq": 1, "values": {}},
    ])
    assert driver.slept == [2, 4]


def test_the_backoff_resets_after_a_good_reply():
    _, driver = listen([
        TimeoutError("x"),
        {"seq": 1, "values": {}},
        TimeoutError("x"),
    ])
    assert driver.slept == [2, 2]


def test_the_backoff_is_capped():
    from custom_components.niagara.coordinator import LISTEN_BACKOFF_MAX

    _, driver = listen([TimeoutError("x")] * 12)
    assert max(driver.slept) <= LISTEN_BACKOFF_MAX


def test_cancellation_is_not_swallowed_by_the_retry():
    """A listener that keeps running after unload holds the session open."""
    coord = coordinator(points())

    async def cancelled(wait):
        raise asyncio.CancelledError

    coord._fetch_value_changes = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(coord._async_listen())


def test_the_wait_asked_for_is_under_the_add_ons_own_ceiling():
    """The add-on decides when to answer; this must not time out first."""
    _, driver = listen([{"seq": 1, "values": {}}])
    assert set(driver.calls) == {LONG_POLL_SECONDS}
    assert LONG_POLL_SECONDS < 45


def test_the_listener_notifies_without_rescheduling_the_refresh():
    """async_set_updated_data would reset the refresh timer. On a station
    where something moves every few seconds that defers the periodic
    housekeeping — discovery, alarms, trends — indefinitely."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(
        inspect.getsource(NiagaraCoordinator._async_listen)
    ))
    # Comments and the docstring both name it, and the docstring says why
    # it is not used — so compare the calls, not the text.
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "async_update_listeners" in called
    assert "async_set_updated_data" not in called
