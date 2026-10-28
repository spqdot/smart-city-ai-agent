from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from smartcity.agent import build_agent, run_turn
from smartcity.modes import NetworkRegistry
from smartcity.pipeline import build_pipeline
from smartcity.window import current_window

from .conftest import ScriptedChatModel, tool_call
from .test_schedule import timetable  # noqa: F401  (fixture)
from langchain_core.messages import AIMessage


def at(day, hour, minute, second=0):
    return datetime(2026, 10, day, hour, minute, second)


def make(network, timetable, now):
    reg = NetworkRegistry()
    reg.register_network(network, timetable)
    return build_pipeline(reg, clock=lambda: now), reg


def ends(origin="1", destination="4"):
    return ({"mode": "bus", "stop_ids": [origin]},
            {"mode": "bus", "stop_ids": [destination]})


# --- window -----------------------------------------------------------------

def test_window_is_two_hours_in_lisbon():
    utc = datetime(2026, 7, 1, 9, 0, tzinfo=ZoneInfo("UTC"))  # 10:00 Lisbon (WEST)
    w = current_window(utc)
    assert (w.start.hour, w.start_seconds, w.end_seconds) == (10, 36000, 43200)
    assert (w.end - w.start).total_seconds() == 7200


def test_window_crossing_midnight_exceeds_24h():
    w = current_window(at(26, 23, 50))
    assert w.end_seconds > 86400 and w.base_day.day == 26


# --- pipeline ---------------------------------------------------------------

def test_departures_inside_two_hour_window_with_edges_inclusive(network, timetable):
    run, _ = make(network, timetable, at(26, 9, 55))
    result = run(*ends())
    assert result["status"] == "ok"
    times = [o["departs"] for o in result["options"]]
    assert times == ["10:00", "10:10", "10:30"]
    assert result["window"]["minutes"] == 120
    first = result["options"][0]
    assert first["arrives"] == "10:20" and first["duration_minutes"] == 20
    assert first["transfers"] == 0
    assert first["transfer_info"] == "Direct · 0 changes."
    assert len(first["legs"]) == 1
    assert first["legs"][0]["board_stop_id"] == "1"
    assert first["legs"][0]["alight_stop_id"] == "4"
    assert [s["name"] for s in first["stops"]][0] and len(first["stops"]) == 2


def test_no_journey_message_and_no_proof_claim(network, timetable):
    run, _ = make(network, timetable, at(26, 12, 0))
    result = run(*ends())
    assert result["status"] == "no_journey" and result["options"] == []
    assert "No timetable-validated journey was found" in result["message"]
    assert "does not prove" in result["message"]


def test_after_midnight_trip_from_previous_service_day(network, timetable):
    run, _ = make(network, timetable, at(26, 0, 20))
    result = run(*ends())
    assert result["status"] == "ok"
    assert [o["departs"] for o in result["options"]] == ["00:30"]  # trip 7 (24:30 of 25th)
    assert result["options"][0]["service_date"] == "2026-10-25"


def test_no_service_calendar_for_date(network, timetable):
    run, _ = make(network, timetable, datetime(2030, 1, 1, 10, 0))
    result = run(*ends())
    assert result["status"] == "no_service"
    assert any("calendar" in s["reason"] for s in result["skipped"])


def test_invalid_inputs(network, timetable):
    run, _ = make(network, timetable, at(26, 9, 55))
    assert run({}, {"mode": "bus", "stop_ids": ["4"]})["status"] == "invalid_input"
    same = run(*ends("4", "4"))
    assert same["status"] == "invalid_input" and "same" in same["message"]


def test_mode_without_timetable_is_skipped(network):
    run, _ = make(network, None, at(26, 9, 55))
    result = run(*ends())
    assert result["status"] == "data_missing"
    assert any("timetable" in s["reason"].lower() for s in result["skipped"])


def test_far_destination_skips_mode(network, timetable):
    run, _ = make(network, timetable, at(26, 9, 55))
    result = run({"mode": "bus", "stop_ids": ["1"]}, {"lat": 41.0, "lon": -8.0})
    assert result["status"] == "not_searched" and result["skipped"]


def test_graph_path_is_only_a_hint(network, timetable):
    run, _ = make(network, timetable, at(26, 12, 0))
    result = run(*ends())
    assert "graph_hint" in result
    assert result["options"] == []


def test_pipeline_returns_transfer_only_when_each_scheduled_leg_connects(network, timetable):
    run, _ = make(network, timetable, at(26, 8, 50))
    result = run(*ends("2", "4"))

    assert result["status"] == "ok"
    option = result["options"][0]
    assert option["transfers"] == 1
    assert [leg["departs"] for leg in option["legs"]] == ["09:00", "09:14"]
    assert option["legs"][0]["alight"] == option["legs"][1]["board"]
    assert option["legs"][0]["board_stop_id"] == "2"
    assert option["legs"][0]["alight_stop_id"] == "8"
    assert option["legs"][0]["alight_stop_id"] == option["legs"][1]["board_stop_id"]
    assert option["legs"][1]["alight_stop_id"] == "4"
    assert option["arrives"] == "09:40"
    assert result["agents"][0]["count"] == 1
    assert result["options"] == [option]


def test_direct_service_suppresses_transfer_options(network, timetable):
    import sqlite3

    with sqlite3.connect(timetable.path) as conn:
        conn.execute("INSERT INTO trips VALUES (9, 'R10', 'S1', 'Direct Almada')")
        conn.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", [
            (9, 1, "2", 9 * 3600 + 5 * 60, 9 * 3600 + 5 * 60),
            (9, 2, "4", 9 * 3600 + 25 * 60, 9 * 3600 + 25 * 60),
        ])
    run, _ = make(network, timetable, at(26, 8, 50))
    result = run(*ends("2", "4"))
    assert result["status"] == "ok"
    assert [option["transfers"] for option in result["options"]] == [0]
    direct = result["options"][0]
    assert direct["departs"] == "09:05"
    assert direct["legs"][0]["board_stop_id"] == "2"
    assert direct["legs"][0]["alight_stop_id"] == "4"


def test_two_change_journey_is_excluded(network, timetable):
    import sqlite3

    with sqlite3.connect(timetable.path) as conn:
        conn.execute("DELETE FROM stop_times WHERE trip_idx IN (5, 6)")
        conn.execute("DELETE FROM trips WHERE trip_idx IN (5, 6)")
        conn.executemany("INSERT INTO trips VALUES (?, ?, ?, ?)", [
            (9, "R4", "S1", "Transfer via stop 9"),
            (10, "R5", "S1", "Final leg"),
        ])
        conn.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", [
            (9, 1, "8", 9 * 3600 + 14 * 60, 9 * 3600 + 14 * 60),
            (9, 2, "9", 9 * 3600 + 20 * 60, 9 * 3600 + 20 * 60),
            (10, 1, "9", 9 * 3600 + 24 * 60, 9 * 3600 + 24 * 60),
            (10, 2, "4", 9 * 3600 + 40 * 60, 9 * 3600 + 40 * 60),
        ])
    run, _ = make(network, timetable, at(26, 8, 50))
    result = run(*ends("2", "4"))
    assert result["options"] == []
    assert result["status"] == "no_journey"


def test_two_hour_window_includes_boundary_and_excludes_after_it(network, timetable):
    import sqlite3

    with sqlite3.connect(timetable.path) as conn:
        conn.executemany("INSERT INTO trips VALUES (?, ?, ?, ?)", [
            (9, "R10", "S1", "Twenty minute departure"),
            (10, "R11", "S1", "Eleven fifty-nine departure"),
            (11, "R10", "S1", "Two hour boundary departure"),
            (12, "R11", "S1", "After boundary departure"),
        ])
        conn.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", [
            (9, 1, "1", 10 * 3600 + 20 * 60, 10 * 3600 + 20 * 60),
            (9, 2, "4", 10 * 3600 + 30 * 60, 10 * 3600 + 30 * 60),
            (10, 1, "1", 11 * 3600 + 59 * 60, 11 * 3600 + 59 * 60),
            (10, 2, "4", 12 * 3600 + 9 * 60, 12 * 3600 + 9 * 60),
            (11, 1, "1", 12 * 3600, 12 * 3600),
            (11, 2, "4", 12 * 3600 + 10 * 60, 12 * 3600 + 10 * 60),
            (12, 1, "1", 12 * 3600 + 1 * 60, 12 * 3600 + 1 * 60),
            (12, 2, "4", 12 * 3600 + 11 * 60, 12 * 3600 + 11 * 60),
        ])
    run, _ = make(network, timetable, at(26, 10, 0))
    result = run(*ends("1", "4"))
    departures = [item["departs"] for item in result["options"]]
    assert "10:20" in departures
    assert "11:59" in departures
    assert "12:00" in departures  # inclusive two-hour boundary
    assert "12:01" not in departures
    assert result["window"]["minutes"] == 120


def test_transfer_search_skips_missing_timetable_arrival(network, timetable, monkeypatch):
    from smartcity.pipeline import _transfer_options
    from smartcity.specialists import Specialist

    trip = {
        "dep": 10 * 3600,
        "route_id": "R1",
        "headsign": "Almada",
        "line_number": "R1",
        "stops": [
            {"stop_id": "1", "dep": 10 * 3600},
            {"stop_id": "8", "arr": None},
        ],
    }
    monkeypatch.setattr(
        timetable, "departures_from", lambda *args, **kwargs: [trip])
    window = current_window(at(26, 10, 0))
    assert _transfer_options(
        network, timetable, Specialist("bus", "Bus", "bus"),
        {"origin_ids": ["1"], "destination_ids": ["4"]}, window, "Bus") == []


def test_dedup_collapses_same_journey_from_nearby_origin_stops():
    from smartcity.pipeline import _deduplicate_options

    first = {
        "mode": "bus+fertagus", "departs": "12:01", "arrives": "12:47",
        "legs": [
            {"mode": "bus", "route_id": "3601", "board_stop_id": "a",
             "alight_stop_id": "pragal", "departs": "12:01", "arrives": "12:05"},
            {"mode": "fertagus", "route_id": "IC", "board_stop_id": "pragal",
             "alight_stop_id": "entrecampos", "departs": "12:34", "arrives": "12:47"},
        ],
    }
    nearby_stop_variant = {
        **first,
        "legs": [{**first["legs"][0], "board_stop_id": "b"}, first["legs"][1]],
    }

    assert _deduplicate_options([first, nearby_stop_variant]) == [first]


# --- agent ------------------------------------------------------------------

def test_agent_tool_output_reaches_ui(network, timetable):
    pipeline_registry = NetworkRegistry()
    pipeline_registry.register_network(network, timetable)
    agent = build_agent(ScriptedChatModel(responses=[
        tool_call("search_stops", {"query": "Pragal Portagem"}, "a"),
        tool_call("search_stops", {"query": "Almada Cristo Rei"}, "b"),
        tool_call("find_departures", {
            "origin_stop_ids": ["1"],
            "destination_stop_ids": ["4"]}, "c"),
        AIMessage(content="Bus R1 leaves at 10:00."),
    ]), pipeline_registry, timetable=timetable, clock=lambda: at(26, 9, 55))
    turn = run_turn(agent, "p1", "Pragal to Almada")
    assert len(turn.departures) == 1
    assert turn.departures[0]["options"][0]["departs"] == "10:00"
    assert "10:00" in turn.reply and "10:30" in turn.reply


def test_agent_rejects_unsurfaced_stop_ids(network, timetable):
    reg = NetworkRegistry()
    reg.register_network(network, timetable)
    agent = build_agent(ScriptedChatModel(responses=[
        tool_call("find_departures", {
            "origin_stop_ids": ["1"],
            "destination_stop_ids": ["4"]}, "c"),
        AIMessage(content="Please tell me the stops."),
    ]), reg, timetable=timetable, clock=lambda: at(26, 9, 55))
    turn = run_turn(agent, "p2", "go")
    assert turn.departures == []


# --- specialist agents ------------------------------------------------------

def test_specialists_report_and_missing_feed_listed(network, timetable):
    run, _ = make(network, timetable, at(26, 9, 55))
    result = run(*ends())
    assert [a["agent"] for a in result["agents"]] == ["Bus (Carris Metropolitana)"]
    assert result["agents"][0]["count"] == 3
    assert result["options"][0]["agent"] == "Bus (Carris Metropolitana)"
    assert not any(s["mode"] == "ferry" for s in result["skipped"])


def test_tram_split_from_carris_bus(network, timetable):
    from smartcity.specialists import specialists_for
    active, _ = specialists_for(["carris"], {"carris": "Carris Lisboa"})
    assert [s.key for s in active] == ["carris_bus", "tram"]
    tram, bus = active[1], active[0]
    assert tram.accepts("Tram") and tram.accepts("Funicular") and not tram.accepts("Bus")
    assert bus.accepts("Bus") and not bus.accepts("Tram")


def test_transport_filter_searches_requested_operator_only(network, timetable):
    reg = NetworkRegistry()
    reg.register_network(network, timetable)
    train_network = network.__class__(mode="train", stops=network.stops,
                                      graph=network.graph,
                                      route_names={r: {"number": r, "vehicle": "Train"}
                                                   for r in ("R1", "R9", "R2", "R3")})
    reg.register_network(train_network, timetable,)
    run = build_pipeline(reg, clock=lambda: at(26, 9, 55))

    origin, destination = ends()
    # The public callable accepts the origin, destination, and selected filter.
    trains = run(origin, destination, "Train")
    buses = run(origin, destination, "Bus")

    assert trains["options"] and {o["mode"] for o in trains["options"]} == {"train"}
    assert all(o["vehicle"] == "Train" for o in trains["options"])
    assert buses["options"] and {o["mode"] for o in buses["options"]} == {"bus"}
    same_feed_id = run({"mode": "bus", "stop_ids": ["1"]},
                       {"mode": "train", "stop_ids": ["1"]}, "Any")
    assert same_feed_id["status"] != "invalid_input"


def test_any_filter_does_not_match_same_name_in_faraway_municipality(network, timetable):
    import networkx as nx
    import pandas as pd
    from smartcity.data import TransportNetwork

    reg = NetworkRegistry()
    reg.register_network(network, timetable)
    # A second Cacilhas is deliberately in a different municipality, far from
    # the explicitly selected Carris Metropolitana stop.
    other_stops = pd.DataFrame([{
        "stop_id": "oeiras-cacilhas", "stop_name": "Cacilhas",
        "stop_lat": "38.70", "stop_lon": "-9.30",
    }])
    other_graph = nx.MultiDiGraph()
    other_graph.add_node("oeiras-cacilhas", name="Cacilhas")
    other = TransportNetwork("mts", other_stops, other_graph)
    reg.register_network(other, timetable)
    run = build_pipeline(reg, clock=lambda: at(26, 9, 55))
    result = run(
        {"mode": "bus", "stop_ids": ["1"], "name": "Alpha",
         "lat": 38.70, "lon": -9.10},
        {"mode": "bus", "stop_ids": ["4"], "name": "Cacilhas",
         "lat": 38.72, "lon": -9.12},
        "Any available transport")
    mts = next(item for item in result["agents"] if item["mode"] == "mts")
    assert mts["status"] == "not_searched"


def test_any_filter_prefers_direct_services_over_transfer_options(network, timetable):
    reg = NetworkRegistry()
    reg.register_network(network, timetable)
    train = network.__class__(
        mode="train", stops=network.stops, graph=network.graph,
        route_names={route: {"number": route, "vehicle": "Train"}
                     for route in ("R1", "R9", "R2", "R3")})
    reg.register_network(train, timetable)
    run = build_pipeline(reg, clock=lambda: at(26, 8, 50))
    result = run(
        {"mode": "bus", "stop_ids": ["2"]},
        {"mode": "bus", "stop_ids": ["4"]},
        "Any available transport")
    cross = [option for option in result["options"]
             if len(option.get("legs", [])) == 2
             and option["legs"][0]["mode"] != option["legs"][1]["mode"]]
    # The two-hour window now includes a direct train; do not show an earlier
    # transfer option that takes longer than that direct service.
    assert result["options"]
    assert all(option["transfers"] == 0 for option in result["options"])
    assert cross == []


def test_mode_with_connections_requires_selected_mode_in_every_journey(network, timetable):
    reg = NetworkRegistry()
    reg.register_network(network, timetable)
    train = network.__class__(
        mode="train", stops=network.stops, graph=network.graph,
        route_names={route: {"number": route, "vehicle": "Train"}
                     for route in ("R1", "R9", "R2", "R3")})
    reg.register_network(train, timetable)
    run = build_pipeline(reg, clock=lambda: at(26, 8, 50))
    result = run(
        {"mode": "bus", "stop_ids": ["2"]},
        {"mode": "bus", "stop_ids": ["4"]},
        "CP Train + connections")

    assert result["options"]
    assert all("train" in {leg.get("mode") for leg in option.get("legs", [])}
               or option.get("mode") == "train" for option in result["options"])
    assert {report["mode"] for report in result["agents"]} == {"bus", "train"}


def test_any_chat_tools_resolve_operator_specific_ids_and_search_them(network, timetable):
    import json
    from smartcity.tools import build_tools

    reg = NetworkRegistry()
    reg.register_network(network, timetable)
    train = network.__class__(mode="train", stops=network.stops, graph=network.graph,
                              route_names={r: {"number": r, "vehicle": "Train"}
                                           for r in ("R1", "R9", "R2", "R3")})
    reg.register_network(train, timetable)
    tools = {tool.name: tool for tool in build_tools(
        reg, clock=lambda: at(26, 8, 50))}
    origin = json.loads(tools["search_stops"].invoke({"query": "Pragal", "mode": "any"}))
    destination = json.loads(tools["search_stops"].invoke(
        {"query": "ALMADA CRISTO REI", "mode": "any"}))
    assert {item["mode"] for item in origin["candidates"]} == {"bus", "train"}
    assert {item["mode"] for item in destination["candidates"]} == {"bus", "train"}
    result = json.loads(tools["find_any_departures"].invoke({
        "origin_mode": "bus", "origin_stop_ids": ["2"],
        "destination_mode": "train", "destination_stop_ids": ["4"],
    }))
    assert any(len(option.get("legs", [])) == 2 for option in result["options"])


def test_carris_tram_filter_does_not_return_its_bus_routes(network, timetable):
    reg = NetworkRegistry()
    car = network.__class__(mode="carris", stops=network.stops,
                            graph=network.graph,
                            route_names={r: {"number": r, "vehicle": "Tram"}
                                         for r in ("R1", "R9", "R2", "R3")})
    reg.register_network(car, timetable)
    reg.labels["carris"] = "Carris Lisboa"
    run = build_pipeline(reg, clock=lambda: at(26, 9, 55))
    result = run({"lat": 38.660, "lon": -9.160},
                 {"lat": 38.670, "lon": -9.150}, transport_filter="Tram")
    assert result["options"]
    assert {o["vehicle"] for o in result["options"]} == {"Tram"}
    assert {o["agent"] for o in result["options"]} == {"Tram & funicular (Carris Lisboa)"}


def test_unknown_future_mode_gets_generic_specialist():
    from smartcity.specialists import specialists_for
    active, missing = specialists_for(["hovercraft"], {"hovercraft": "Hovercraft"})
    assert [s.key for s in active] == ["hovercraft"] and active[0].accepts("Anything")
    assert "ferry" in [s.key for s in missing]


def test_failing_specialist_does_not_hide_others(network, timetable, monkeypatch):
    reg = NetworkRegistry()
    reg.register_network(network, timetable)
    other = type("T", (), {"available": True,
                           "service_days": lambda self, d: (_ for _ in ()).throw(RuntimeError())})()
    reg.register_network(network.__class__(mode="metro", stops=network.stops,
                                           graph=network.graph), other)
    run = build_pipeline(reg, clock=lambda: at(26, 9, 55))
    result = run(*ends())
    statuses = {a["agent"]: a["status"] for a in result["agents"]}
    assert statuses["Bus (Carris Metropolitana)"] == "searched"
    assert statuses["Metro de Lisboa"] == "search_failed"
    assert len(result["options"]) == 3
