from smartcity.map_view import build_deck, journey_map_data, route_map_data
from smartcity.tools.routing import find_route


def test_route_includes_coordinates_for_each_leg(network):
    for node, (lat, lon) in {"2": (38.661, -9.161), "8": (38.665, -9.155),
                             "4": (38.670, -9.150)}.items():
        network.graph.nodes[node]["latitude"] = str(lat)
        network.graph.nodes[node]["longitude"] = str(lon)

    route = find_route(network, ["2"], ["4"])
    first_leg = route["legs"][0]
    assert first_leg["points"][0]["lat"] == 38.661
    assert len(route_map_data(route)["paths"]) == len(route["legs"])


def test_map_data_skips_routes_without_coordinates(network):
    route = find_route(network, ["2"], ["4"])
    assert route_map_data(route) is None
    assert build_deck(route) is None


def test_deck_is_built_when_coordinates_exist(network):
    for node in ("1", "4"):
        network.graph.nodes[node]["latitude"] = "38.66"
        network.graph.nodes[node]["longitude"] = "-9.15"
    network.graph.nodes["4"]["latitude"] = "38.67"
    deck = build_deck(find_route(network, ["1"], ["4"]))
    assert deck is not None and len(deck.layers) == 2


def test_journey_map_marks_places_and_draws_scheduled_stops(network):
    class Registry:
        def get(self, mode):
            return network

    network._stop_index["2"] = {"lat": 38.661, "lon": -9.161}
    network._stop_index["8"] = {"lat": 38.665, "lon": -9.155}
    origin = {"name": "Origin", "lat": 38.661, "lon": -9.161}
    destination = {"name": "Destination", "lat": 38.665, "lon": -9.155}
    option = {"mode": "bus", "stops": [
        {"stop_id": "2", "name": "Origin"},
        {"stop_id": "8", "name": "Destination"}]}

    data = journey_map_data(origin, destination, [option], Registry())

    assert [stop["label"] for stop in data["stops"]] == [
        "Origin: Origin", "Destination: Destination"]
    assert len(data["paths"]) == 1
    assert data["paths"][0]["path"] == [[-9.161, 38.661], [-9.155, 38.665]]


def test_journey_map_without_results_draws_labeled_orientation_connector():
    data = journey_map_data({"name": "A", "lat": 38.7, "lon": -9.2},
                            {"name": "B", "lat": 38.8, "lon": -9.1}, [], None)
    assert data["paths"][0]["label"] == "Selected places only; not a scheduled route"
    assert data["paths"][0]["path"] == [[-9.2, 38.7], [-9.1, 38.8]]
    assert len(data["stops"]) == 2


def test_transfer_journey_map_uses_stop_ids_from_each_leg(network):
    class Registry:
        def get(self, mode):
            return network

    for stop_id, lat, lon in (("2", 38.661, -9.161), ("8", 38.665, -9.155),
                              ("4", 38.670, -9.150)):
        network._stop_index[stop_id] = {"lat": lat, "lon": lon}
    option = {"mode": "bus", "legs": [
        {"board_stop_id": "2", "alight_stop_id": "8", "board": "A", "alight": "B"},
        {"board_stop_id": "8", "alight_stop_id": "4", "board": "B", "alight": "C"},
    ]}
    data = journey_map_data({"name": "A", "lat": 38.661, "lon": -9.161},
                            {"name": "C", "lat": 38.670, "lon": -9.150},
                            [option], Registry())
    assert len(data["paths"]) == 1
    assert data["paths"][0]["path"] == [
        [-9.161, 38.661], [-9.155, 38.665], [-9.150, 38.670]]
