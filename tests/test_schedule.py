import json
import sqlite3
from datetime import date, datetime

import pytest

from smartcity.agent import build_agent, run_turn
from smartcity.timetable import INDEXES, SCHEMA, Timetable, format_time
from smartcity.tools import build_tools
from smartcity.tools.schedule import find_timetable

from .conftest import ScriptedChatModel, tool_call
from langchain_core.messages import AIMessage

DAY = date(2026, 10, 26)


def hms(h, m=0):
    return h * 3600 + m * 60


@pytest.fixture
def timetable(tmp_path):
    """Trips on the fixture graph: R1 1->4, R9 1->4 (other line), R2 2->8, R3 8->4."""
    path = tmp_path / "timetable.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO trips VALUES (?, ?, ?, ?)", [
        (1, "R1", "S1", "Almada"), (2, "R1", "S1", "Almada"),
        (3, "R9", "S1", "Cacilhas"),
        (4, "R2", "S1", "Pragal"), (5, "R3", "S1", "Almada"),
        (6, "R3", "S1", "Almada"),
        (7, "R1", "S0", "Almada"),  # yesterday's service, runs past midnight
        (8, "R1", "S1", "Almada"),  # today's service, runs past midnight
    ])
    stop_times = [
        (1, 1, "1", hms(10), hms(10)), (1, 2, "4", hms(10, 20), hms(10, 20)),
        (2, 1, "1", hms(10, 30), hms(10, 30)), (2, 2, "4", hms(10, 50), hms(10, 50)),
        (3, 1, "1", hms(10, 10), hms(10, 10)), (3, 2, "4", hms(10, 25), hms(10, 25)),
        (4, 1, "2", hms(9), hms(9)), (4, 2, "8", hms(9, 10), hms(9, 10)),
        (5, 1, "8", hms(9, 11), hms(9, 11)), (5, 2, "4", hms(9, 30), hms(9, 30)),
        (6, 1, "8", hms(9, 14), hms(9, 14)), (6, 2, "4", hms(9, 40), hms(9, 40)),
        (7, 1, "1", hms(24, 30), hms(24, 30)), (7, 2, "4", hms(24, 50), hms(24, 50)),
        (8, 1, "1", hms(24, 30), hms(24, 30)), (8, 2, "4", hms(24, 50), hms(24, 50)),
    ]
    conn.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", stop_times)
    conn.executemany("INSERT INTO calendar VALUES (?, ?)", [
        ("20261026", "S1"), ("20261025", "S0"),
    ])
    conn.executescript(INDEXES)
    conn.commit()
    conn.close()
    return Timetable(path)


NOW = datetime(2026, 10, 26, 9, 55)


def run(network, timetable, origins, destinations, **kwargs):
    return find_timetable(network, timetable, origins, destinations,
                          now=NOW, **kwargs)


def test_next_departures_and_arrivals(network, timetable):
    result = run(network, timetable, ["1"], ["4"])
    assert result["status"] == "ok"
    first = result["itineraries"][0]
    assert (first["departs"], first["arrives"], first["duration_minutes"]) == (
        "10:00", "10:20", 20)
    assert [i["departs"] for i in result["itineraries"]][:3] == ["10:00", "10:10", "10:30"]


def test_itineraries_use_any_bus_serving_the_stops(network, timetable):
    result = run(network, timetable, ["1"], ["4"])
    buses = [i["legs"][0]["headsign"] for i in result["itineraries"]]
    assert buses == ["Almada", "Cacilhas", "Almada"]  # R9 included, not only graph route R1


def test_other_buses_within_window(network, timetable):
    result = run(network, timetable, ["1"], ["4"], window_minutes=60)
    buses = result["other_options"][0]["buses"]
    assert [b["departs"] for b in buses] == ["10:00", "10:10", "10:30"]
    narrow = run(network, timetable, ["1"], ["4"], window_minutes=15)
    assert [b["departs"] for b in narrow["other_options"][0]["buses"]] == ["10:00", "10:10"]


def test_transfer_waits_for_next_connection(network, timetable):
    result = run(network, timetable, ["2"], ["4"], depart_time="08:50")
    itinerary = result["itineraries"][0]
    assert [leg["departs"] for leg in itinerary["legs"]] == ["09:00", "09:14"]
    assert itinerary["arrives"] == "09:40"  # the 09:11 bus is missed (buffer)


def test_after_midnight_trip_from_previous_service_day(network, timetable):
    result = run(network, timetable, ["1"], ["4"], depart_time="00:10")
    assert result["itineraries"][0]["departs"] == "00:30"


def test_after_midnight_trip_is_marked_next_day(network, timetable):
    result = run(network, timetable, ["1"], ["4"], depart_time="23:00")
    assert result["status"] == "ok"
    assert result["itineraries"][0]["departs"] == "00:30 (+1 day)"


def test_no_service_on_date(network, timetable):
    result = run(network, timetable, ["1"], ["4"], date_text="2030-01-01")
    assert result["status"] == "no_service_on_date"
    assert result["route"]["status"] == "ok"
    assert "2026" in result["message"]


def test_no_departures_after_last_trip(network, timetable):
    result = run(network, timetable, ["2"], ["4"], depart_time="15:00")
    assert result["status"] == "no_departures"
    assert result["route"]["status"] == "ok"


def test_invalid_time(network, timetable):
    assert run(network, timetable, ["1"], ["4"], depart_time="25:99")["status"] == "invalid_time"
    assert run(network, timetable, ["1"], ["4"], date_text="tomorrow")["status"] == "invalid_time"


def test_missing_timetable_file_is_explained(network, tmp_path):
    missing = Timetable(tmp_path / "nope.sqlite")
    result = run(network, missing, ["1"], ["4"])
    assert result["status"] == "timetable_unavailable"
    assert "build_timetable.py" in result["message"]
    assert result["route"]["status"] == "ok"


def test_no_route_passes_through(network, timetable):
    assert run(network, timetable, ["4"], ["1"])["status"] == "no_route"


def test_format_time():
    assert format_time(hms(9, 5)) == "09:05"
    assert format_time(hms(25, 10)) == "01:10 (+1 day)"
