"""A Watch that dies must not freeze every value in Home Assistant.

Observed on a live station: the Watch expired, and `pollChanges` then
returned an oBIX `<err>` whose `display` was nothing but the request path.
The expiry check looked for the words "not found" or "expired" in that
message, found neither, and left `_watch_uri` set — so every poll called
pollChanges, got an error, returned no changes, and left all 1,134 points
holding the values they had when the Watch died.

Nothing raised and nothing was marked unavailable. Over twenty minutes
exactly one of 772 Niagara entities in Home Assistant changed, and each one
still read as a healthy number. That is the worst shape a fault can take
here, and it is the same shape as the stale-proxy problem the templates
already guard against.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from obix_client import NiagaraPoint, ObixClient, ObixError  # noqa: E402


@pytest.fixture
def client():
    client = ObixClient(host="192.0.2.1", username="u", password="p")
    client._watch_uri = "http://192.0.2.1/obix/watchService/watch61/"
    client._watch_points = {"/a/", "/b/"}
    return client


def _fails_with(client, message, monkeypatch):
    def boom(url, body):
        raise ObixError(message)

    monkeypatch.setattr(client, "_post_url", boom)


class TestAWatchThatKeepsFailing:
    def test_the_watch_is_given_up_and_polling_falls_back(
        self, client, monkeypatch
    ):
        """The message is only a path, which names no cause at all."""
        _fails_with(
            client,
            "/Drivers/ObixNetwork/server/lobby/watchService/watch61/pollChanges",
            monkeypatch,
        )

        for _ in range(client.WATCH_FAILURES_BEFORE_GIVING_UP):
            client._watch_poll_changes()

        assert client._watch_uri is None
        assert client._watch_points == set()

    def test_one_failure_is_tolerated(self, client, monkeypatch):
        """A single blip should not throw away a working subscription."""
        _fails_with(client, "transient", monkeypatch)

        client._watch_poll_changes()

        assert client._watch_uri is not None

    def test_a_success_clears_the_count(self, client, monkeypatch):
        _fails_with(client, "transient", monkeypatch)
        client._watch_poll_changes()

        monkeypatch.setattr(
            client, "_post_url",
            lambda url, body: __import__("xml.etree.ElementTree",
                                         fromlist=["ElementTree"])
            .fromstring("<obj/>"),
        )
        client._watch_poll_changes()
        assert client._watch_consecutive_failures == 0

        _fails_with(client, "transient", monkeypatch)
        client._watch_poll_changes()
        assert client._watch_uri is not None

    def test_an_expiry_that_says_so_is_given_up_at_once(
        self, client, monkeypatch
    ):
        """The old wording check still works, and still works faster."""
        _fails_with(client, "Watch not found", monkeypatch)

        client._watch_poll_changes()

        assert client._watch_uri is None


class TestPollingAfterTheWatchIsGivenUp:
    def test_points_are_read_directly_instead(self, client, monkeypatch):
        """Values must resume, not merely stop being wrong."""
        client._watch_uri = None
        points = [NiagaraPoint(path="/a/", name="A")]
        called = []

        def legacy(pts, max_workers):
            called.append(len(pts))
            return pts

        monkeypatch.setattr(client, "_poll_via_legacy", legacy)
        client.poll_points(points)
        assert called == [1]

    def test_a_live_watch_is_still_used(self, client, monkeypatch):
        points = [NiagaraPoint(path="/a/", name="A")]
        used = []
        monkeypatch.setattr(client, "_poll_via_watch",
                            lambda pts: used.append("watch") or pts)
        client.poll_points(points)
        assert used == ["watch"]
