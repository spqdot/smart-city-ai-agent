import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from smartcity.config import load_settings
from smartcity.feeds import expand_calendar, import_feed, validate_gtfs, vehicle_for
from smartcity.modes import (NetworkRegistry, default_registry, modes_for_filter,
                             stop_options)
from smartcity.pipeline import build_pipeline
from smartcity.tools.schedule import find_timetable
from smartcity.tools.search import search_stops

TODAY = date.today()
MONDAY = TODAY + timedelta(days=(7 - TODAY.weekday()) % 7 or 7)
TUESDAY = MONDAY + timedelta(days=1)  # removed by a calendar exception
SATURDAY = MONDAY + timedelta(days=5)
ISO = lambda d: d.isoformat()  # noqa: E731
GTFS = lambda d: d.strftime("%Y%m%d")  # noqa: E731

FILES = {
    "stops.txt": (
        "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
        "ST,Station Alpha,38.70,-9.10,1,\n"
        "ST1,Station Alpha,38.70,-9.10,0,ST\n"
        "ST2,Station Alpha,38.70,-9.10,0,ST\n"
        "BB,Beta,38.71,-9.11,0,\n"
        "CC,Gamma,38.72,-9.12,0,\n"),
    "routes.txt": (
        "route_id,route_short_name,route_long_name,route_type\n"
        "L1,Az,Blue,1\nL2,Vd,Green,1\n"),
    "trips.txt": (
        "route_id,service_id,trip_id,trip_headsign\n"
        "L1,WK,t1,Beta\nL2,WK,t2,Gamma\n"),
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\n"
        "WK,1,1,1,1,1,0,0,20200101,20991231\n"),
    "calendar_dates.txt": f"service_id,date,exception_type\nWK,{GTFS(TUESDAY)},2\n",
    "stop_times.txt": (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "t1,10:00:00,10:00:00,ST1,1\nt1,10:10:00,10:10:00,BB,2\n"
        "t2,10:20:00,10:20:00,ST2,1\nt2,10:30:00,10:30:00,CC,2\n"),
}


def make_feed(tmp_path):
    zip_path = tmp_path / "feed.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, text in FILES.items():
            zf.writestr(name, text)
    return import_feed("metro", zip_path, tmp_path / "metro", log=lambda *_: None)


def test_calendar_applies_weekdays_and_exceptions():
    rows = expand_calendar(
        __import__("pandas").read_csv(__import__("io").StringIO(
            FILES["calendar.txt"]), dtype=str),
        __import__("pandas").read_csv(__import__("io").StringIO(
            FILES["calendar_dates.txt"]), dtype=str),
        TODAY)
    days = set(rows["date"])
    assert GTFS(MONDAY) in days and GTFS(TUESDAY) not in days  # exception removed
    assert GTFS(SATURDAY) not in days


def test_vehicle_types():
    assert vehicle_for("1", "Az", "Azul") == "Metro"
    assert vehicle_for("3", "28E", "Martim Moniz") == "Tram"
    assert vehicle_for("3", "55E", "Funicular Graça") == "Funicular"
    assert vehicle_for("109", "X", "Y") == "Train"
    assert vehicle_for("0", "MTS", "Almada", mode="mts") == "MST light rail"


def test_import_collapses_platforms_into_one_station(tmp_path, monkeypatch):
    out = make_feed(tmp_path)
    registry = default_registry(load_settings({}), modes_dir=tmp_path)
    assert "metro" in registry.modes
    network = registry.get("metro")
    assert "ST" in network.graph and "ST1" not in network.graph
    result = search_stops(network, "Station Alpha")
    assert result["status"] == "ok" and result["candidates"][0]["stop_ids"] == ["ST"]
    assert (out / "timetable.sqlite").exists()


def test_gtfs_validator_checks_core_references(tmp_path):
    feed_path = tmp_path / "feed.zip"
    with zipfile.ZipFile(feed_path, "w") as zf:
        for name, text in FILES.items():
            zf.writestr(name, text.replace("t1,10:00:00,10:00:00,ST1,1",
                                           "missing,10:00:00,10:00:00,ST1,1"))
    import pytest
    with pytest.raises(ValueError, match="unknown trip_id"):
        validate_gtfs(feed_path, mode="metro")


def test_gtfs_validator_allows_zero_based_stop_sequence(tmp_path):
    feed_path = tmp_path / "zero-based.zip"
    with zipfile.ZipFile(feed_path, "w") as zf:
        for name, text in FILES.items():
            zf.writestr(name, text.replace(
                "t1,10:00:00,10:00:00,ST1,1", "t1,10:00:00,10:00:00,ST1,0"))
    assert validate_gtfs(feed_path, mode="metro")["trips"] == 2


def test_checked_in_metro_feed_validates_against_real_counts():
    report = validate_gtfs(Path("data/feeds/metro.zip"), mode="metro")
    assert report == {"stops": 74, "routes": 4, "trips": 5106,
                      "stop_times": 70324, "service_ids": 38}


def test_mst_and_underground_metro_are_separate_choices():
    registry = default_registry(load_settings({}))
    choices = stop_options(registry, "Metro Sul do Tejo (MST)")
    assert {stop["mode"] for stop in choices.values()} == {"mts"}
    assert {stop["name"] for stop in choices.values()} >= {"Pragal", "Cacilhas"}
    metro = stop_options(registry, "Metropolitano de Lisboa")
    assert {stop["mode"] for stop in metro.values()} == {"metro"}
    assert "Pragal" not in {stop["name"] for stop in metro.values()}


def test_mode_filters_and_stop_choices_keep_operator_ids_distinct(tmp_path):
    # Build small independent networks whose stop IDs deliberately overlap.
    import networkx as nx
    import pandas as pd
    from smartcity.data import TransportNetwork

    def make_network(mode, name):
        stops = pd.DataFrame([{"stop_id": "same-id", "stop_name": name,
                               "stop_lat": "38.7", "stop_lon": "-9.1"}])
        graph = nx.MultiDiGraph()
        graph.add_node("same-id", name=name)
        return TransportNetwork(mode, stops, graph)

    registry = NetworkRegistry()
    for mode, label, name in (("bus", "Carris Metropolitana", "Bus stop"),
                               ("train", "CP", "Rail station"),
                               ("fertagus", "Fertagus", "Fertagus station"),
                               ("mts", "Metro Sul do Tejo", "Almada station"),
                               ("metro", "Metro de Lisboa", "Metro station")):
        registry.register_network(make_network(mode, name))
        registry.labels[mode] = label

    assert modes_for_filter("Metro Sul do Tejo (MST)", registry.modes) == ["mts"]
    assert modes_for_filter("Train", registry.modes) == ["fertagus", "train"]
    train_choices = stop_options(registry, "Train")
    assert {stop["mode"] for stop in train_choices.values()} == {"train", "fertagus"}
    metro_choices = stop_options(registry, "Metropolitano de Lisboa")
    assert len(metro_choices) == 1
    assert {stop["mode"] for stop in metro_choices.values()} == {"metro"}
    assert {stop["mode"] for stop in stop_options(registry, "Fertagus").values()} == {
        "fertagus"}
    assert {stop["mode"] for stop in stop_options(registry, "Bus").values()} == {"bus"}
    assert len(stop_options(registry, "Any")) == 5


def test_pipeline_routes_metro_filter_to_metro_agent(tmp_path):
    make_feed(tmp_path)
    registry = default_registry(load_settings({}), modes_dir=tmp_path)
    now = datetime.combine(MONDAY, datetime.min.time()).replace(hour=10, minute=15)
    run = build_pipeline(registry, clock=lambda: now)
    result = run({"mode": "metro", "stop_ids": ["ST"]},
                 {"mode": "metro", "stop_ids": ["CC"]}, "Metro")

    assert result["status"] == "ok"
    assert {option["mode"] for option in result["options"]} == {"metro"}
    assert {agent["agent"] for agent in result["agents"]} == {"Metro de Lisboa"}
    assert result["agents"][0]["status"] == "searched"


def test_metro_journey_with_change_uses_station_and_vehicle(tmp_path):
    make_feed(tmp_path)
    registry = default_registry(load_settings({}), modes_dir=tmp_path)
    network, timetable = registry.get("metro"), registry.timetable("metro")
    result = find_timetable(network, timetable, ["BB"], ["CC"], depart_time="09:00",
                            date_text=ISO(MONDAY))
    # Beta -> Gamma is not directly connected: no departures, but never invented.
    assert result["status"] in {"no_route", "no_departures"}
    ok = find_timetable(network, timetable, ["ST"], ["CC"], depart_time="09:00",
                        date_text=ISO(MONDAY))
    assert ok["status"] == "ok"
    leg = ok["itineraries"][0]["legs"][0]
    assert leg["vehicle"] == "Metro" and leg["departs"] == "10:20"


def test_no_service_on_excluded_date(tmp_path):
    make_feed(tmp_path)
    registry = default_registry(load_settings({}), modes_dir=tmp_path)
    result = find_timetable(registry.get("metro"), registry.timetable("metro"),
                            ["ST"], ["CC"], depart_time="09:00", date_text=ISO(TUESDAY))
    assert result["status"] == "no_service_on_date"
