"""The dip guard and the false-reset finder for running totals."""
from datetime import datetime, timedelta, timezone

from custom_components.niagara.meter_guard import (
    TotalDipGuard,
    find_false_resets,
)

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_rising_total_passes_through():
    g = TotalDipGuard()
    assert [g.filter(v, at(i)) for i, v in enumerate([10.0, 10.5, 12.0])] == [10.0, 10.5, 12.0]


def test_dip_that_recovers_is_never_reported():
    g = TotalDipGuard(grace=timedelta(minutes=30))
    g.filter(1000.0, at(0))
    assert g.filter(0.0, at(1)) == 1000.0
    assert g.holding
    assert g.filter(400.0, at(10)) == 1000.0
    assert g.filter(1000.2, at(12)) == 1000.2
    assert not g.holding


def test_lasting_drop_is_accepted_as_reset():
    g = TotalDipGuard(grace=timedelta(minutes=30))
    g.filter(5.0, at(0))
    assert g.filter(0.0, at(1)) == 5.0
    assert g.filter(0.1, at(20)) == 5.0
    # Still below the old reading at the end of the grace period.
    assert g.filter(0.2, at(31)) == 0.2
    assert g.filter(0.3, at(32)) == 0.3


def test_unavailable_keeps_last_good():
    g = TotalDipGuard()
    g.filter(50.0, at(0))
    assert g.filter(None, at(1)) is None
    assert g.filter(0.0, at(2)) == 50.0


def test_outage_never_counts_towards_grace():
    g = TotalDipGuard(grace=timedelta(minutes=30))
    g.filter(335.0, at(0))
    g.filter(0.0, at(1))          # first bad poll as the source fails
    for m in range(2, 300, 5):     # five hours offline
        assert g.offline() == 335.0
    assert g.filter(0.0, at(300)) == 335.0   # one stale 0 on return
    assert g.filter(335.2, at(301)) == 335.2


def test_seed_catches_a_dip_straddling_a_restart():
    g = TotalDipGuard()
    g.seed(300.0)
    assert g.filter(100.0, at(0)) == 300.0
    g.seed(1.0)  # a later seed must not override what is known
    assert g.last_good == 300.0


def _rows(pairs):
    return [
        {"start": at(60 * i), "state": s, "sum": t}
        for i, (s, t) in enumerate(pairs)
    ]


def test_finds_hour_where_sum_jumped_but_reading_did_not():
    rows = _rows([(313.0, 139.0), (316.9, 142.9), (316.9, 459.8), (317.5, 460.4)])
    found = find_false_resets(rows)
    assert len(found) == 1
    assert found[0].start == at(120)
    assert round(found[0].excess, 1) == 316.9


def test_ordinary_use_and_real_resets_are_left_alone():
    # Daily register: climbs, resets at midnight, climbs again.
    rows = _rows([(2.0, 10.0), (2.6, 10.6), (0.0, 10.6), (0.4, 11.0), (1.0, 11.6)])
    assert find_false_resets(rows) == []


def test_gaps_break_the_comparison():
    rows = _rows([(100.0, 0.0), (None, None), (100.0, 100.0)])
    assert find_false_resets(rows) == []
