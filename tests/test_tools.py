import json

from smartcity.tools import build_tools
from smartcity.tools.routing import find_route
from smartcity.tools.search import search_stops
from smartcity.tools.validate import validate_stop_ids


def test_search_single_match_is_accent_insensitive(network):
    result = search_stops(network, "cacilhas")
    assert result["status"] == "ok"
    assert result["candidates"][0]["stop_ids"] == ["5"]


def test_search_groups_stops_in_same_neighbourhood(network):
    result = search_stops(network, "Pragal Torrado Silva estacao")
    assert result["status"] == "ok"
    assert sorted(result["candidates"][0]["stop_ids"]) == ["2", "3"]


def test_search_ambiguous_across_different_places(network):
    result = search_stops(network, "Pragal")
    assert result["status"] == "ambiguous"
    assert result["total_candidates"] == 2
    assert {c["municipality"] for c in result["candidates"]} == {"Almada", "Seixal"}


def test_search_municipality_filter_removes_ambiguity(network):
    result = search_stops(network, "Pragal", municipality="Almada")
    assert result["status"] == "ok"
    assert sorted(result["candidates"][0]["stop_ids"]) == ["1", "2", "3"]


def test_search_area_name_uses_centre_stops(network):
    result = search_stops(network, "Seixal city centre")
    assert result["status"] == "ok"
    assert result["candidates"][0]["match"] == "municipality_centre"
    assert "9" in result["candidates"][0]["stop_ids"]


def test_search_exact_municipality_name_is_not_ambiguous(network):
    result = search_stops(network, "Seixal")
    assert result["status"] == "ok"
    assert result["candidates"][0]["match"] == "municipality_centre"


def test_search_landmark_uses_geocoder_and_nearest_stops(network):
    fake = lambda query: {"lat": 38.6705, "lon": -9.1505, "label": "Fake Mall"}
    result = search_stops(network, "Fake Mall", geocoder=fake)
    candidate = result["candidates"][0]
    assert result["status"] == "ok"
    assert candidate["match"] == "nearest_stops"
    assert candidate["stop_ids"][0] == "4"
    assert candidate["distance_km"] < 0.2


def test_search_landmark_too_far_from_graph(network):
    far = lambda query: {"lat": 41.15, "lon": -8.61, "label": "Porto"}
    result = search_stops(network, "Porto Mall", geocoder=far)
    assert result["status"] == "not_in_graph"


def test_search_landmark_unknown_to_geocoder(network):
    assert search_stops(network, "Atlantis", geocoder=lambda q: None)["status"] == "not_found"


def test_search_not_found(network):
    assert search_stops(network, "Atlantis")["status"] == "not_found"


def test_search_stop_outside_graph(network):
    result = search_stops(network, "Fora da Amostra")
    assert result["status"] == "not_in_graph"
    assert "sample" in result["message"].lower()


def test_validate_stop_ids(network):
    result = validate_stop_ids(network, ["1", "7", "999"])
    problems = {s["stop_id"]: s["problem"] for s in result["stops"]}
    assert result["status"] == "invalid"
    assert problems == {"1": None, "7": "not_in_graph", "999": "unknown_stop_id"}
    assert validate_stop_ids(network, ["1", "4"])["status"] == "ok"


def test_find_route_with_transfer(network):
    result = find_route(network, ["2"], ["4"])
    assert result["status"] == "ok"
    assert result["board_at"]["stop_id"] == "2"
    assert result["alight_at"]["stop_id"] == "4"
    assert result["stops_travelled"] == 2
    assert result["num_transfers"] == 1
    assert [leg["route_id"] for leg in result["legs"]] == ["R2", "R3"]


def test_find_route_picks_best_of_several_ids(network):
    result = find_route(network, ["1", "2"], ["4"])
    assert result["board_at"]["stop_id"] == "1"
    assert result["num_transfers"] == 0


def test_find_route_prefers_fewer_transfers_and_uses_bus_numbers(network):
    # Direct on one bus is 3 stops; via x it is 2 stops but needs a transfer.
    graph = network.graph
    for stop in "abcdx":
        graph.add_node(stop, name=stop.upper())
    graph.add_edge("a", "b", route_id="R10")
    graph.add_edge("b", "c", route_id="R10")
    graph.add_edge("c", "d", route_id="R10")
    graph.add_edge("a", "x", route_id="R20")
    graph.add_edge("x", "d", route_id="R30")
    network.route_names = {"R10": {"number": "3012", "description": "Pragal - Cacilhas"}}

    result = find_route(network, ["a"], ["d"])
    assert result["num_transfers"] == 0
    assert [leg["bus_number"] for leg in result["legs"]] == ["3012"]
    assert result["legs"][0]["bus_description"] == "Pragal - Cacilhas"
    assert result["stops_travelled"] == 3


def test_find_route_wrong_direction_is_explained(network):
    result = find_route(network, ["4"], ["1"])
    assert result["status"] == "no_route"
    assert result["reason"] == "no_directed_path"
    assert "sample" in result["message"].lower()


def test_find_route_disconnected_component(network):
    result = find_route(network, ["1"], ["6"])
    assert result["status"] == "no_route"
    assert result["reason"] == "disconnected_in_sample"


def test_find_route_invalid_and_same_stop(network):
    assert find_route(network, ["1"], ["7"])["status"] == "invalid_stops"
    assert find_route(network, ["1"], ["1"])["status"] == "same_stop"


def test_find_route_rejects_unverified_ids(network):
    result = find_route(network, ["1"], ["4"], allowed_ids={"1"})
    assert result["status"] == "unverified_stop_ids"
    assert result["stop_ids"] == ["4"]


def test_langchain_tools_enforce_search_first(registry):
    tools = {t.name: t for t in build_tools(registry)}
    blocked = json.loads(tools["find_route"].invoke(
        {"origin_stop_ids": ["1"], "destination_stop_ids": ["4"]}))
    assert blocked["status"] == "unverified_stop_ids"

    tools["validate_stop_ids"].invoke({"stop_ids": ["1", "4"]})
    allowed = json.loads(tools["find_route"].invoke(
        {"origin_stop_ids": ["1"], "destination_stop_ids": ["4"]}))
    assert allowed["status"] == "ok"


def test_unknown_mode(registry):
    tools = {t.name: t for t in build_tools(registry)}
    result = json.loads(tools["search_stops"].invoke({"query": "x", "mode": "ferry"}))
    assert result["status"] == "unknown_mode"
