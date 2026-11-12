"""Keep a running total from being double-counted by a momentary dip.

Home Assistant reads any fall of more than 10% in a `total_increasing`
sensor as the meter being reset to zero. When the reading then comes back,
everything from zero up to the meter's full lifetime total is counted as
fresh consumption. A single bad poll — a register read as 0, an offset
briefly missing from a corrected total — therefore adds the meter's entire
history to that hour. One such dip put a year's worth of water into a
single hour of a water meter's statistics.

Two defences, both free of Home Assistant imports so they test standalone:

* `TotalDipGuard` holds the last good reading while a total is lower than
  it, and only lets a drop through once it has lasted `grace`. A dip that
  recovers inside the grace period never reaches Home Assistant. A genuine
  reset (a daily register at midnight, a replaced meter) is accepted after
  the grace period; nothing is lost, because Home Assistant counts from the
  new value once it does.

* `find_false_resets` looks at recorded hourly statistics for the damage a
  dip has already done: an hour whose sum grew by far more than the meter
  reading did, even though the reading ended the hour no lower than it
  started. That is a reset that never happened.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_GRACE = timedelta(minutes=30)


class TotalDipGuard:
    """Filter readings of one running total."""

    def __init__(self, grace: timedelta = DEFAULT_GRACE) -> None:
        self.grace = grace
        self.last_good: float | None = None
        self.dip_since: datetime | None = None

    def seed(self, value: float | None) -> None:
        """Start from a known reading, e.g. the state restored at startup."""
        if value is not None and self.last_good is None:
            self.last_good = value

    def filter(self, value: float | None, now: datetime) -> float | None:
        """Return what to report for `value` read at `now`."""
        if value is None:
            # Unavailable says nothing about the meter; keep what we know.
            return None
        if self.last_good is None or value >= self.last_good:
            self.last_good = value
            self.dip_since = None
            return value

        if self.dip_since is None:
            self.dip_since = now
        if now - self.dip_since < self.grace:
            return self.last_good

        # Lower for the whole grace period: a real reset.
        self.last_good = value
        self.dip_since = None
        return value

    def offline(self) -> float | None:
        """The source is unavailable: report the last good reading.

        Whatever an offline source returns (often 0) is not a reading, and
        time spent offline must not count towards the grace period — an
        outage longer than `grace` would otherwise be accepted as a reset,
        and the meter's whole total counted again when it came back.
        """
        self.dip_since = None
        return self.last_good

    @property
    def holding(self) -> bool:
        return self.dip_since is not None


@dataclass
class FalseReset:
    """One hour of statistics inflated by a reset that never happened."""

    start: datetime
    sum_increase: float
    reading_increase: float

    @property
    def excess(self) -> float:
        return self.sum_increase - self.reading_increase


def find_false_resets(
    rows: list[dict], min_excess: float = 0.001, ratio: float = 0.5,
) -> list[FalseReset]:
    """Hours whose sum grew by much more than the reading did.

    `rows` are hourly statistics in time order, each with `start`, `state`
    and `sum`. An hour is a false reset when the reading ended it no lower
    than the previous hour ended (so the meter was never really reset) and
    the sum still grew by more than the reading did, by more than `ratio`
    of the reading itself and more than `min_excess` (which absorbs float
    rounding when the reading is near zero). Ordinary consumption
    moves sum and reading together, and a genuine reset ends the hour lower
    than it began, so neither matches.
    """
    found: list[FalseReset] = []
    prev = None
    for row in rows:
        state, total = row.get("state"), row.get("sum")
        if state is None or total is None:
            prev = None
            continue
        if prev is not None:
            reading_increase = state - prev["state"]
            sum_increase = total - prev["sum"]
            excess = sum_increase - reading_increase
            if (
                reading_increase >= 0
                and excess > min_excess
                and excess > ratio * prev["state"]
            ):
                found.append(FalseReset(row["start"], sum_increase, reading_increase))
        prev = {"state": state, "sum": total}
    return found
