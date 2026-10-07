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


# -- Resolving the query operation --------------------------------------
#
# A station answered every alarm query with "Cannot find lobby agent for
# obix:alarmQuery". Niagara advertises the op as a bare "~alarmQuery/",
# relative to the alarm subject; treating that as root-relative posts to
# /obix/~alarmQuery/, which is nowhere.

SUBJECT = xml(f"""
<obj {NS} href="/obix/config/Services/AlarmService" is="obix:AlarmSubject">
  <ref name="status" href="status/"/>
  <ref name="defaultAlarmClass" href="defaultAlarmClass/"
       is="/obix/def/alarm:AlarmClass obix:AlarmSubject"/>
  <int name="count" val="0"/>
  <op name="query" href="~alarmQuery/"/>
  <feed name="feed" href="~alarmFeed/"/>
</obj>
""")


def op_href(subject_path, href):
    """What the client resolves an operation href to."""
    if href.startswith(("http://", "https://", "/")):
        return href
    return subject_path.rstrip("/") + "/" + href.lstrip("./")


def test_a_relative_op_resolves_against_its_own_object():
    subject = "/config/Services/AlarmService"
    query = next(c for c in SUBJECT if c.get("name") == "query")
    assert op_href(subject, query.get("href")) == (
        "/config/Services/AlarmService/~alarmQuery/"
    )


def test_a_root_relative_op_is_left_alone():
    assert op_href("/config/Services/AlarmService", "/obix/alarms/query/") == (
        "/obix/alarms/query/"
    )


def test_the_subject_advertises_a_query_operation():
    assert any(c.get("name") == "query" for c in SUBJECT)


def test_an_alarm_class_is_itself_a_subject():
    """Each alarm class can be queried separately; the service covers all."""
    classes = [
        c.get("name") for c in SUBJECT
        if "obix:AlarmSubject" in (c.get("is") or "")
    ]
    assert "defaultAlarmClass" in classes


# -- Niagara's real record shape ----------------------------------------
#
# Taken verbatim from a station. It carries no <ref name="source"> at all:
# the source is a "Station:PointName" string, the ack fields are absent,
# and the transition is stated in fromState/toState. Reading only the
# spec's shape gave 25 alarms with an empty source — they parsed cleanly
# and were useless.

NIAGARA_QUERY = xml(f"""
<obj {NS} is="obix:AlarmQueryOut">
 <list name="data" of="obix:Alarm">
  <obj href="/obix/alarm/3320d258" display=""
       is="obix:Alarm obix:AckAlarm obix:PointAlarm obix:StatefulAlarm">
   <op name="ack" href="/obix/alarm/3320d258/ack"
       in="obix:AlarmAckIn" out="obix:AlarmAckOut"/>
   <bool name="alarmValue" val="true"/>
   <abstime name="normalTimestamp" val="2026-10-05T13:27:07.031+11:00"/>
   <abstime name="timestamp" val="2026-10-05T13:21:33.805+11:00"/>
   <int name="priority" val="255"/>
   <str name="alarmClass" val="defaultAlarmClass"/>
   <str name="presentValue" val="OK"/>
   <str name="fromState" val="offnormal"/>
   <str name="toState" val="normal"/>
   <str name="offnormalValue" val="FAULT"/>
   <str name="msgText" val=""/>
   <str name="sourceName" val="SiteJace1:PAC_3_5_Flt"/>
   <str name="sourceStation" val="SiteES"/>
  </obj>
  <obj href="/obix/alarm/99beef" is="obix:Alarm obix:PointAlarm">
   <abstime name="timestamp" val="2026-10-07T06:00:00.000+11:00"/>
   <int name="priority" val="10"/>
   <str name="alarmClass" val="Critical"/>
   <str name="fromState" val="normal"/>
   <str name="toState" val="offnormal"/>
   <str name="offnormalValue" val="HIGH"/>
   <str name="msgText" val="Tank 1 level high"/>
   <str name="sourceName" val="SiteJace2:DCW_Tank1_High_Alm"/>
   <str name="sourceStation" val="SiteES"/>
  </obj>
 </list>
</obj>
""")


def test_the_source_comes_from_the_name_when_there_is_no_ref():
    """The bug: 25 alarms parsed with an empty source and no way to place
    any of them on a device."""
    first = records(NIAGARA_QUERY)[0]
    assert first.source_name == "PAC_3_5_Flt"
    assert first.source_station == "SiteJace1"


def test_the_reporting_station_is_kept_apart_from_the_points_station():
    """sourceStation is the station holding the alarm database — on a
    Supervisor that is not the station the point is on. Placing an alarm
    on a device needs the latter, and the two routinely differ."""
    first = records(NIAGARA_QUERY)[0]
    assert first.source_station == "SiteJace1"   # where the point lives
    assert first.reported_by == "SiteES"         # where the alarm is stored


def test_the_reporting_station_is_the_fallback_when_the_name_has_no_colon():
    elem = xml(f"""
    <obj {NS} is="obix:Alarm">
      <abstime name="timestamp" val="2026-10-07T09:00:00Z"/>
      <str name="sourceName" val="JustAPoint"/>
      <str name="sourceStation" val="SiteES"/>
    </obj>
    """)
    parsed = alarms.parse_alarm(elem)
    assert parsed.source_name == "JustAPoint"
    assert parsed.source_station == "SiteES"


@pytest.mark.parametrize("raw,expected", [
    ("SiteJace1:PAC_3_5_Flt", ("SiteJace1", "PAC_3_5_Flt")),
    ("PAC_3_5_Flt", ("", "PAC_3_5_Flt")),
    ("Jace:Folder:Point", ("Jace", "Folder:Point")),
    ("", ("", "")),
    ("  Jace:Pt  ", ("Jace", "Pt")),
])
def test_source_names_split_on_the_first_colon_only(raw, expected):
    assert alarms.split_source_name(raw) == expected


def test_the_transition_decides_whether_an_alarm_is_over():
    """toState is the station saying it outright, which beats inferring it."""
    done, live = records(NIAGARA_QUERY)
    assert done.active is False
    assert live.active is True


def test_an_alarm_with_no_normal_timestamp_is_active_by_its_state():
    """The second record has no normalTimestamp at all, so the old rule
    would have called it active for the right reason by accident. This
    checks the state is what decides."""
    live = records(NIAGARA_QUERY)[1]
    assert live.to_state == "offnormal"
    assert live.normal_timestamp == ""
    assert live.active is True


def test_a_state_of_normal_ends_an_alarm_even_with_no_timestamp():
    elem = xml(f"""
    <obj {NS} is="obix:Alarm">
      <abstime name="timestamp" val="2026-10-07T09:00:00Z"/>
      <str name="sourceName" val="J1:Pt"/>
      <str name="toState" val="normal"/>
    </obj>
    """)
    assert alarms.parse_alarm(elem).active is False


def test_the_offnormal_value_is_kept_not_the_bare_boolean():
    """alarmValue is just "it alarmed"; offnormalValue says what it read."""
    first = records(NIAGARA_QUERY)[0]
    assert first.alarm_value == "FAULT"
    assert first.present_value == "OK"


def test_the_operators_message_is_carried():
    live = records(NIAGARA_QUERY)[1]
    assert live.message == "Tank 1 level high"
    assert live.alarm_class == "Critical"


def test_a_record_with_no_ack_fields_reads_as_unacknowledged():
    """Niagara sends no ackState on these. Claiming they were acknowledged
    would be the one error here with operational consequences."""
    assert all(not r.acked for r in records(NIAGARA_QUERY))


def test_niagara_records_are_recognised_as_alarms_at_all():
    """They carry no <ref name="source">, which the first version required."""
    assert len(records(NIAGARA_QUERY)) == 2


def test_the_summary_counts_the_live_one_only():
    summary = alarms.summarise(records(NIAGARA_QUERY))
    assert summary["total"] == 2
    assert summary["active"] == 1
    assert summary["unacked"] == 1
    assert summary["highest_priority"] == 10


def test_the_spec_shape_still_parses():
    """A station that does send a source ref must not regress."""
    first = records(QUERY_OUT)[0]
    assert first.source.endswith("/AHU1/Trip/")
    assert first.source_name == "AHU-1 Fire Trip"
