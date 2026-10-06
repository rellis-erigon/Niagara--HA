"""Tests for reading the station's trend logs.

The XML is the shape Niagara's oBIX driver returns. The parts that matter
are the ones that are easy to get quietly wrong: a naive timestamp read as
local time shifts a whole trend, and an hour with no sample must stay a gap
rather than be interpolated into a number the station never recorded.
"""
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import histories  # noqa: E402

NS = 'xmlns="http://obix.org/ns/schema/1.0"'


def xml(text):
    return ET.fromstring(text)


LOBBY = xml(f"""
<obj {NS} href="/obix/">
  <ref name="about" is="obix:About" href="about/"/>
  <ref name="histories" is="obix:HistoryService" href="histories/"/>
</obj>
""")

LOBBY_NO_CONTRACT = xml(f"""
<obj {NS} href="/obix/"><ref name="histories" href="histories/"/></obj>
""")

LOBBY_NONE = xml(f"""
<obj {NS} href="/obix/"><ref name="about" href="about/"/></obj>
""")

HISTORY_OBJ = xml(f"""
<obj {NS} href="/obix/histories/J1/AHU1_SupplyTemp/" is="obix:History"
     display="AHU-1 Supply Air">
  <int name="count" val="52560"/>
  <abstime name="start" val="2025-10-07T00:00:00.000+10:00"/>
  <abstime name="end" val="2026-10-07T09:00:00.000+10:00"/>
  <str name="tz" val="Australia/Brisbane"/>
  <reltime name="interval" val="PT10M"/>
  <op name="query" href="/obix/histories/J1/AHU1_SupplyTemp/query/"
      in="obix:HistoryFilter" out="obix:HistoryQueryOut"/>
  <feed name="feed" href="/obix/histories/J1/AHU1_SupplyTemp/feed/"/>
</obj>
""")

SERVICE_LIST = xml(f"""
<obj {NS} href="/obix/histories/" is="obix:HistoryService">
  <ref name="J1" href="J1/">
    <ref name="AHU1_SupplyTemp" is="obix:History" href="J1/AHU1_SupplyTemp/"/>
    <ref name="AHU1_ReturnTemp" is="obix:History" href="J1/AHU1_ReturnTemp/"/>
  </ref>
</obj>
""")

QUERY_OUT = xml(f"""
<obj {NS} is="obix:HistoryQueryOut">
  <int name="count" val="4"/>
  <abstime name="start" val="2026-10-07T08:00:00.000+10:00"/>
  <abstime name="end" val="2026-10-07T09:30:00.000+10:00"/>
  <list name="data" of="obix:HistoryRecord">
    <obj>
      <abstime name="timestamp" val="2026-10-07T08:00:00.000+10:00"/>
      <real name="value" val="18.0"/>
    </obj>
    <obj>
      <abstime name="timestamp" val="2026-10-07T08:30:00.000+10:00"/>
      <real name="value" val="20.0"/>
    </obj>
    <obj>
      <abstime name="timestamp" val="2026-10-07T09:00:00.000+10:00"/>
      <real name="value" val="22.0"/>
    </obj>
    <obj>
      <abstime name="timestamp" val="2026-10-07T09:30:00.000+10:00"/>
      <real name="value" val="24.0"/>
    </obj>
  </list>
</obj>
""")

EXTENSION = xml(f"""
<obj {NS} href="/obix/config/Drivers/J1/points/AHU1/SupplyTemp/NumericInterval/"
     is="obix:obj">
  <str name="historyName" val="/J1/AHU1_SupplyTemp"/>
  <reltime name="interval" val="PT10M"/>
  <int name="capacity" val="52560"/>
</obj>
""")


# -- Finding the service ------------------------------------------------

def test_service_found_by_contract():
    assert histories.find_history_service(LOBBY) == "histories/"


def test_service_found_by_name_without_a_contract():
    assert histories.find_history_service(LOBBY_NO_CONTRACT) == "histories/"


def test_no_service_is_none():
    assert histories.find_history_service(LOBBY_NONE) is None


# -- One history's description ------------------------------------------

def test_history_meta_is_read():
    meta = histories.parse_history_meta(HISTORY_OBJ, "J1/AHU1_SupplyTemp")
    assert meta.count == 52560
    assert meta.start.startswith("2025-10-07")
    assert meta.tz == "Australia/Brisbane"
    assert meta.interval == "PT10M"
    assert meta.has_query is True
    assert meta.query_href.endswith("/query/")
    assert meta.display == "AHU-1 Supply Air"


def test_a_history_without_a_query_op_is_flagged():
    """It can be listed but not synced, and the screen has to say so."""
    bare = xml(f'<obj {NS} is="obix:History"><int name="count" val="5"/></obj>')
    assert histories.parse_history_meta(bare, "x").has_query is False


def test_a_non_numeric_count_is_dropped_not_crashed():
    odd = xml(f'<obj {NS} is="obix:History"><str name="count" val="lots"/></obj>')
    assert histories.parse_history_meta(odd, "x").count is None


def test_the_service_listing_finds_the_histories_under_the_station():
    found = {m.name for m in histories.parse_history_list(SERVICE_LIST)}
    assert found == {"J1/AHU1_SupplyTemp", "J1/AHU1_ReturnTemp"}


# -- Samples ------------------------------------------------------------

def test_records_are_read_in_order():
    records = histories.parse_history_records(QUERY_OUT)
    assert [r.value for r in records] == [18.0, 20.0, 22.0, 24.0]


def test_a_boolean_trend_becomes_one_and_zero():
    """A run-status trend is as worth keeping as a temperature."""
    boolean = xml(f"""
    <obj {NS} is="obix:HistoryQueryOut"><list name="data">
      <obj><abstime name="timestamp" val="2026-10-07T08:00:00Z"/>
           <bool name="value" val="true"/></obj>
      <obj><abstime name="timestamp" val="2026-10-07T08:10:00Z"/>
           <bool name="value" val="false"/></obj>
    </list></obj>
    """)
    assert [r.value for r in histories.parse_history_records(boolean)] == [1.0, 0.0]


def test_a_value_under_another_name_is_still_found():
    """The spec lets a contract rename it; Niagara usually calls it value."""
    renamed = xml(f"""
    <obj {NS} is="obix:HistoryQueryOut"><list name="data">
      <obj><abstime name="timestamp" val="2026-10-07T08:00:00Z"/>
           <real name="avg" val="19.5"/></obj>
    </list></obj>
    """)
    assert histories.parse_history_records(renamed)[0].value == 19.5


def test_a_record_with_no_timestamp_is_skipped():
    broken = xml(f"""
    <obj {NS} is="obix:HistoryQueryOut"><list name="data">
      <obj><real name="value" val="19.5"/></obj>
    </list></obj>
    """)
    assert histories.parse_history_records(broken) == []


def test_an_unreadable_value_keeps_the_sample_but_not_a_number():
    odd = xml(f"""
    <obj {NS} is="obix:HistoryQueryOut"><list name="data">
      <obj><abstime name="timestamp" val="2026-10-07T08:00:00Z"/>
           <str name="value" val="n/a"/></obj>
    </list></obj>
    """)
    records = histories.parse_history_records(odd)
    assert len(records) == 1 and records[0].value is None


# -- Timestamps ---------------------------------------------------------

def test_an_offset_timestamp_keeps_its_zone():
    parsed = histories.parse_time("2026-10-07T08:00:00.000+10:00")
    assert parsed.utcoffset() == timedelta(hours=10)


def test_a_zulu_timestamp_is_utc():
    assert histories.parse_time("2026-10-07T08:00:00Z").utcoffset() == timedelta(0)


def test_a_naive_timestamp_is_read_as_utc_not_guessed():
    """Guessing a local zone silently shifts a whole trend."""
    assert histories.parse_time("2026-10-07T08:00:00").utcoffset() == timedelta(0)


def test_an_unreadable_timestamp_is_none():
    assert histories.parse_time("not a time") is None
    assert histories.parse_time("") is None


# -- Hourly buckets -----------------------------------------------------

def test_samples_in_one_hour_become_one_bucket():
    buckets = histories.hourly_buckets(histories.parse_history_records(QUERY_OUT))
    assert len(buckets) == 2
    first = buckets[0]
    assert first["mean"] == 19.0      # 18 and 20
    assert first["min"] == 18.0
    assert first["max"] == 20.0
    assert first["samples"] == 2


def test_buckets_are_keyed_in_utc_whatever_the_station_reports():
    """The station reports +10:00; 08:00 there is 22:00 UTC the day before."""
    buckets = histories.hourly_buckets(histories.parse_history_records(QUERY_OUT))
    assert buckets[0]["start"].startswith("2026-10-06T22:00")


def test_an_hour_with_no_sample_is_left_as_a_gap():
    """Interpolating would claim coverage the station does not have."""
    sparse = [
        histories.HistoryRecord("2026-10-07T00:00:00Z", 1.0),
        histories.HistoryRecord("2026-10-07T03:00:00Z", 4.0),
    ]
    buckets = histories.hourly_buckets(sparse)
    assert len(buckets) == 2


def test_samples_with_no_value_do_not_create_a_bucket():
    assert histories.hourly_buckets([
        histories.HistoryRecord("2026-10-07T00:00:00Z", None),
    ]) == []


def test_buckets_come_out_in_time_order():
    shuffled = [
        histories.HistoryRecord("2026-10-07T05:00:00Z", 5.0),
        histories.HistoryRecord("2026-10-07T01:00:00Z", 1.0),
        histories.HistoryRecord("2026-10-07T03:00:00Z", 3.0),
    ]
    starts = [b["start"] for b in histories.hourly_buckets(shuffled)]
    assert starts == sorted(starts)


# -- The query body -----------------------------------------------------

def test_the_filter_always_carries_a_limit():
    """An unbounded query is how a JACE stops answering anything else."""
    assert 'name="limit"' in histories.build_history_filter()


def test_the_limit_is_capped_however_much_is_asked_for():
    body = histories.build_history_filter(limit=10_000_000)
    assert f'val="{histories.MAX_RECORDS}"' in body


def test_both_bounds_are_sent_when_known():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 8, tzinfo=timezone.utc)
    body = histories.build_history_filter(start, end)
    assert 'name="start"' in body and 'name="end"' in body


def test_a_naive_bound_is_sent_as_utc():
    body = histories.build_history_filter(datetime(2026, 1, 1))
    assert "+00:00" in body


# -- Windows ------------------------------------------------------------

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def test_a_long_backfill_is_split_into_windows():
    windows = histories.gap_windows(NOW - timedelta(days=30), NOW)
    assert len(windows) == 5
    assert windows[0][0] == NOW - timedelta(days=30)
    assert windows[-1][1] == NOW


def test_windows_are_contiguous_with_no_gap_between_them():
    windows = histories.gap_windows(NOW - timedelta(days=21), NOW)
    for earlier, later in zip(windows, windows[1:]):
        assert earlier[1] == later[0]


def test_one_run_is_bounded_so_a_station_is_not_hammered():
    windows = histories.gap_windows(NOW - timedelta(days=3650), NOW)
    assert len(windows) == 12


def test_a_first_run_takes_the_recent_window_not_everything():
    windows = histories.gap_windows(None, NOW)
    assert len(windows) == 1
    assert windows[0][1] == NOW


def test_nothing_to_do_is_no_windows():
    assert histories.gap_windows(NOW, NOW) == []
    assert histories.gap_windows(NOW + timedelta(hours=1), NOW) == []


# -- Pairing a history to its point -------------------------------------

def test_the_station_declares_which_point_a_history_belongs_to():
    link = histories.find_history_links(
        "/config/Drivers/J1/points/AHU1/SupplyTemp/NumericInterval/", EXTENSION,
    )
    assert link.point == "/config/Drivers/J1/points/AHU1/SupplyTemp/"
    assert link.history == "J1/AHU1_SupplyTemp"


def test_an_extension_with_no_history_name_yields_no_link():
    bare = xml(f'<obj {NS}><int name="capacity" val="100"/></obj>')
    assert histories.find_history_links("/a/b/ext/", bare) is None


def test_a_history_name_is_normalised_to_the_service_form():
    assert histories.normalise_history_name("/J1/Temp") == "J1/Temp"
    assert histories.normalise_history_name("J1/Temp/") == "J1/Temp"
    assert histories.normalise_history_name("  Temp  ") == "Temp"
