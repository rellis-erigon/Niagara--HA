"""Tests for reading the station's alarm console.

The XML here is the shape Niagara's oBIX driver returns, including the
awkward parts: a returned-to-normal timestamp sent as `null="true"` with no
value, several contracts on one record, and feed events that carry no
contract at all.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import alarms  # noqa: E402

NS = 'xmlns="http://obix.org/ns/schema/1.0"'


def xml(text):
    return ET.fromstring(text)


LOBBY_WITH_CONTRACT = xml(f"""
<obj {NS} href="/obix/">
  <ref name="about" is="obix:About" href="about/"/>
  <ref name="watchService" is="obix:WatchService" href="watchService/"/>
  <ref name="alarms" is="obix:AlarmSubject" href="alarms/"/>
  <ref name="batch" is="obix:op" href="batch/"/>
</obj>
""")

LOBBY_WITHOUT_CONTRACT = xml(f"""
<obj {NS} href="/obix/">
  <ref name="about" href="about/"/>
  <ref name="alarms" href="alarms/"/>
</obj>
""")

LOBBY_NO_ALARMS = xml(f"""
<obj {NS} href="/obix/">
  <ref name="about" is="obix:About" href="about/"/>
  <ref name="watchService" is="obix:WatchService" href="watchService/"/>
</obj>
""")

QUERY_OUT = xml(f"""
<obj {NS} is="obix:AlarmQueryOut">
  <int name="count" val="3"/>
  <list name="data" of="obix:Alarm">
    <obj href="/obix/alarms/a1/" display="Fire Trip"
         is="obix:AckAlarm obix:StatefulAlarm obix:PointAlarm">
      <abstime name="timestamp" val="2026-10-07T08:12:33.123+10:00"/>
      <ref name="source" href="/obix/config/Drivers/J1/points/AHU1/Trip/"
           display="AHU-1 Fire Trip"/>
      <abstime name="normalTimestamp" null="true"/>
      <enum name="ackState" val="unacked" range="/obix/def/AckState/"/>
      <str name="alarmValue" val="true"/>
      <int name="priority" val="1"/>
    </obj>
    <obj href="/obix/alarms/a2/" is="obix:AckAlarm obix:StatefulAlarm">
      <abstime name="timestamp" val="2026-10-06T22:04:00.000+10:00"/>
      <ref name="source" href="/obix/config/Drivers/J1/points/CH1/HiPress/"/>
      <abstime name="normalTimestamp" val="2026-10-07T01:15:00.000+10:00"/>
      <enum name="ackState" val="acked"/>
      <abstime name="ackTimestamp" val="2026-10-07T01:20:00.000+10:00"/>
      <str name="ackUser" val="operator"/>
      <int name="priority" val="40"/>
    </obj>
    <obj href="/obix/alarms/a3/" is="obix:AckAlarm">
      <abstime name="timestamp" val="2026-10-07T07:00:00.000+10:00"/>
      <ref name="source" href="/obix/config/Drivers/J1/points/P1/Fault/"/>
      <enum name="ackState" val="acked"/>
      <int name="priority" val="200"/>
    </obj>
  </list>
</obj>
""")

FEED_EVENTS = xml(f"""
<obj {NS} is="obix:Feed">
  <obj>
    <abstime name="timestamp" val="2026-10-07T08:30:00.000+10:00"/>
    <ref name="source" href="/obix/config/Drivers/J1/points/EF1/Fault/"/>
    <enum name="ackState" val="unacked"/>
  </obj>
</obj>
""")

NESTED = xml(f"""
<obj {NS} is="obix:AlarmQueryOut">
  <obj name="result">
    <list name="data" of="obix:Alarm">
      <obj href="/obix/alarms/b1/" is="obix:PointAlarm">
        <abstime name="timestamp" val="2026-10-07T09:00:00.000+10:00"/>
        <ref name="source" href="/obix/config/Drivers/J1/points/X/Alarm/"/>
      </obj>
    </list>
  </obj>
</obj>
""")

EMPTY = xml(f"""
<obj {NS} is="obix:AlarmQueryOut">
  <int name="count" val="0"/>
  <list name="data" of="obix:Alarm"/>
</obj>
""")


# -- Finding the subject ------------------------------------------------

def test_subject_found_by_contract():
    assert alarms.find_alarm_subject(LOBBY_WITH_CONTRACT) == "alarms/"


def test_subject_found_by_name_when_the_contract_is_missing():
    """Niagara has shipped lobbies whose refs carry no contract."""
    assert alarms.find_alarm_subject(LOBBY_WITHOUT_CONTRACT) == "alarms/"


def test_no_subject_is_none_not_a_guess():
    assert alarms.find_alarm_subject(LOBBY_NO_ALARMS) is None


def test_the_contract_wins_over_a_conventional_name():
    lobby = xml(f"""
    <obj {NS}>
      <ref name="alarms" href="decoy/"/>
      <ref name="theRealOne" is="obix:AlarmSubject" href="svc/alarms/"/>
    </obj>
    """)
    assert alarms.find_alarm_subject(lobby) == "svc/alarms/"


# -- Parsing one record -------------------------------------------------

def records(root, resolve=None):
    return alarms.parse_alarm_list(root, resolve)


def test_every_field_is_read():
    first = records(QUERY_OUT)[0]
    assert first.uri == "/obix/alarms/a1/"
    assert first.timestamp == "2026-10-07T08:12:33.123+10:00"
    assert first.source_name == "AHU-1 Fire Trip"
    assert first.ack_state == "unacked"
    assert first.alarm_value == "true"
    assert first.priority == 1
    assert first.display == "Fire Trip"
    assert "obix:PointAlarm" in first.contracts


def test_a_null_normal_timestamp_means_still_active():
    """Sent as null="true" with no val, which must not read as a time."""
    first = records(QUERY_OUT)[0]
    assert first.normal_timestamp == ""
    assert first.active is True


def test_a_real_normal_timestamp_ends_the_alarm():
    second = records(QUERY_OUT)[1]
    assert second.active is False
    assert second.ack_user == "operator"
    assert second.ack_timestamp == "2026-10-07T01:20:00.000+10:00"


def test_a_record_with_no_normal_timestamp_field_is_active():
    """An AckAlarm without the stateful contract has no such field."""
    third = records(QUERY_OUT)[2]
    assert third.active is True


def test_unacked_is_not_acked():
    first, second, _ = records(QUERY_OUT)
    assert first.acked is False
    assert second.acked is True


@pytest.mark.parametrize("state", ["", "unacked", "unackedAlert", "ackPending", "weird"])
def test_anything_unfamiliar_reads_as_unacknowledged(state):
    """Claiming an alarm was acknowledged when it was not is the costly error."""
    elem = xml(f"""
    <obj {NS} is="obix:AckAlarm">
      <abstime name="timestamp" val="2026-10-07T09:00:00.000+10:00"/>
      <ref name="source" href="/obix/p/"/>
      <enum name="ackState" val="{state}"/>
    </obj>
    """)
    assert alarms.parse_alarm(elem).acked is False


@pytest.mark.parametrize("state", ["acked", "ACKED", " Acked "])
def test_acknowledged_spellings(state):
    elem = xml(f"""
    <obj {NS} is="obix:AckAlarm">
      <abstime name="timestamp" val="2026-10-07T09:00:00.000+10:00"/>
      <ref name="source" href="/obix/p/"/>
      <enum name="ackState" val="{state}"/>
    </obj>
    """)
    assert alarms.parse_alarm(elem).acked is True


def test_a_non_numeric_priority_is_dropped_not_crashed():
    elem = xml(f"""
    <obj {NS} is="obix:Alarm">
      <abstime name="timestamp" val="2026-10-07T09:00:00.000+10:00"/>
      <ref name="source" href="/obix/p/"/>
      <str name="priority" val="high"/>
    </obj>
    """)
    assert alarms.parse_alarm(elem).priority is None


def test_source_is_resolved_back_to_a_stored_point_path():
    """So an alarm can be attributed to the device its point belongs to."""
    resolved = records(QUERY_OUT, lambda h: h.replace("/obix", ""))
    assert resolved[0].source == "/config/Drivers/J1/points/AHU1/Trip/"


def test_source_name_falls_back_to_the_last_path_segment():
    second = records(QUERY_OUT)[1]
    assert second.source_name == "HiPress"


# -- Pulling records out of a response ----------------------------------

def test_a_query_result_yields_every_record():
    assert len(records(QUERY_OUT)) == 3


def test_an_empty_console_yields_nothing():
    assert records(EMPTY) == []


def test_feed_events_without_a_contract_are_still_alarms():
    found = records(FEED_EVENTS)
    assert len(found) == 1
    assert found[0].source.endswith("/EF1/Fault/")


def test_records_nested_one_level_deeper_are_found():
    assert len(records(NESTED)) == 1


def test_the_same_alarm_is_not_reported_twice():
    doubled = xml(f"""
    <obj {NS} is="obix:AlarmQueryOut">
      <list name="data">
        <obj href="/obix/alarms/a1/" is="obix:Alarm">
          <abstime name="timestamp" val="2026-10-07T09:00:00.000+10:00"/>
          <ref name="source" href="/obix/p/"/>
        </obj>
      </list>
      <list name="also">
        <obj href="/obix/alarms/a1/" is="obix:Alarm">
          <abstime name="timestamp" val="2026-10-07T09:00:00.000+10:00"/>
          <ref name="source" href="/obix/p/"/>
        </obj>
      </list>
    </obj>
    """)
    assert len(records(doubled)) == 1


def test_a_watch_response_is_not_mistaken_for_alarms():
    """The parser walks the tree, so it must not claim unrelated objects."""
    watch = xml(f"""
    <obj {NS} is="obix:WatchOut">
      <list name="values">
        <real name="RoomTemp" val="22.5" href="/obix/config/p/RoomTemp/"/>
      </list>
    </obj>
    """)
    assert records(watch) == []


def test_points_carrying_an_alarm_flag_are_not_alarm_records():
    """A point with inAlarm is a point, not an entry in the console."""
    point = xml(f"""
    <obj {NS} is="obix:Point">
      <bool name="inAlarm" val="true"/>
      <real name="out" val="31.0"/>
    </obj>
    """)
    assert records(point) == []


# -- Counts -------------------------------------------------------------

def test_summary_counts_only_active_alarms():
    summary = alarms.summarise(records(QUERY_OUT))
    assert summary["total"] == 3
    assert summary["active"] == 2        # a1 and a3; a2 returned to normal
    assert summary["unacked"] == 1       # a1 only
    assert summary["highest_priority"] == 1


def test_highest_priority_is_the_lowest_number():
    """Niagara counts 1 as the most urgent."""
    summary = alarms.summarise(records(QUERY_OUT))
    assert summary["highest_priority"] == 1


def test_summary_of_nothing_is_zeroes_not_an_error():
    summary = alarms.summarise([])
    assert summary == {
        "total": 0, "active": 0, "unacked": 0, "highest_priority": None,
    }


def test_priority_is_none_when_no_active_alarm_declares_one():
    elem = xml(f"""
    <obj {NS} is="obix:Alarm">
      <abstime name="timestamp" val="2026-10-07T09:00:00.000+10:00"/>
      <ref name="source" href="/obix/p/"/>
    </obj>
    """)
    assert alarms.summarise([alarms.parse_alarm(elem)])["highest_priority"] is None


# -- The query body -----------------------------------------------------

def test_the_filter_asks_for_the_console_not_history():
    """A start or end bound would turn this into a history query."""
    body = alarms.build_filter(50)
    assert 'is="obix:AlarmFilter"' in body
    assert 'name="limit" val="50"' in body
    assert "start" not in body and "end" not in body


def test_the_filter_limit_is_an_integer_in_the_xml():
    assert 'val="200"' in alarms.build_filter(200)


def test_a_record_serialises_for_the_api():
    first = records(QUERY_OUT)[0]
    payload = first.to_dict()
    assert payload["active"] is True
    assert payload["acked"] is False
    assert payload["priority"] == 1
