"""Discovery must reach a proxy nested inside a proxy, and say when it cannot.

A station on this site re-created its main incoming meter as an imported
subtree:

    .../Station2/points/SiteBMS/Electrical/MainMeter/OtherBMS/Electrical/MainMeter/<point>

That folder sits at depth 10 below `/config/`, so its points are at depth
11 — one past the walker's limit. The result was that the meter simply did
not exist as far as the bridge was concerned. Three separate rescans found
nothing and the absence was blamed on the station's oBIX export, which was
wrong: 1,938 points were discovered at depth 10 and **none at all** below
it, which is what a truncated walk looks like.

Two things are tested: the limit is high enough for a nested proxy, and
hitting it is reported rather than silent. The second matters more. A deeper limit will be hit again one day, and the only reason this took three
rescans to find is that nothing said "I stopped looking here".
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from obix_client import MAX_DISCOVERY_DEPTH, ObixClient  # noqa: E402


class TestTheLimitIsDeepEnough:
    def test_a_proxy_inside_a_proxy_is_reachable(self):
        """The real path that was missed, counted from `/config/`."""
        path = (
            "Drivers/NiagaraNetwork/Station2/points/SiteBMS/Electrical/"
            "MainMeter/OtherBMS/Electrical/MainMeter/TotalActivePower"
        )
        assert len(path.split("/")) <= MAX_DISCOVERY_DEPTH

    def test_there_is_room_for_one_more_nesting(self):
        """So the next proxy of a proxy does not need another release."""
        path = (
            "Drivers/NiagaraNetwork/Station2/points/SiteBMS/Electrical/"
            "MainMeter/OtherBMS/Electrical/MainMeter/Deeper/TotalActivePower"
        )
        assert len(path.split("/")) <= MAX_DISCOVERY_DEPTH


class TestHittingTheLimitIsReported:
    @pytest.fixture
    def client(self):
        return ObixClient(host="192.0.2.1", username="u", password="p")

    def test_nothing_is_reported_when_the_walk_completes(self, client, monkeypatch):
        monkeypatch.setattr(client, "_get", lambda path: _leaf())
        points: list = []
        client._truncated_at = []
        client._walk_tree("/config/", points, depth=0,
                          max_depth=MAX_DISCOVERY_DEPTH)
        assert client._truncated_at == []

    def test_the_cut_off_branch_is_recorded(self, client, monkeypatch):
        """A branch abandoned at the limit must be named, not dropped."""
        monkeypatch.setattr(client, "_get", lambda path: _leaf())
        points: list = []
        client._truncated_at = []
        client._walk_tree("/config/deep/", points,
                          depth=MAX_DISCOVERY_DEPTH + 1,
                          max_depth=MAX_DISCOVERY_DEPTH)
        assert client._truncated_at == ["/config/deep/"]

    def test_discovery_warns_once_with_a_count(self, client, monkeypatch, caplog):
        monkeypatch.setattr(client, "_get", lambda path: _leaf())

        def walk(path, points, depth, max_depth):
            client._truncated_at.extend(["/config/a/", "/config/b/"])

        monkeypatch.setattr(client, "_walk_tree", walk)
        with caplog.at_level("WARNING"):
            client.discover_points()
        warnings = [r.getMessage() for r in caplog.records
                    if r.levelname == "WARNING"]
        joined = " ".join(warnings)
        assert "2" in joined
        assert "deep" in joined.lower() or "depth" in joined.lower()


def _leaf():
    import xml.etree.ElementTree as ET

    from obix_client import OBIX_NS

    return ET.fromstring(f'<obj xmlns="{OBIX_NS}"/>')
