import heapq
from itertools import count

from ..data import TransportNetwork

MAX_IDS = 60
TRANSFER_PENALTY = 5  # one transfer is treated like riding 5 extra stops


def _best_path(network: TransportNetwork, origins, destinations):
    """Cheapest ride as a list of (stop, route) states, penalising transfers.

    Dijkstra over (stop, route) states, so staying on one bus is preferred
    over hopping between routes. The start state has route None.
    """
    graph = network.graph
    targets = set(destinations)
    tie = count()
    heap = [(0, next(tie), (o, None), None) for o in origins]
    heapq.heapify(heap)
    parent = {}
    settled = set()

    while heap:
        cost, _, state, previous = heapq.heappop(heap)
        if state in settled:
            continue
        settled.add(state)
        parent[state] = previous
        stop, route = state
        if stop in targets and route is not None:
            path = []
            while state is not None:
                path.append(state)
                state = parent[state]
            return list(reversed(path))
        for nxt, edges in graph[stop].items():
            for route_id in {d.get("route_id", "unknown") for d in edges.values()}:
                extra = 0 if route in (None, route_id) else TRANSFER_PENALTY
                new_state = (nxt, route_id)
                if new_state not in settled:
                    heapq.heappush(
                        heap, (cost + 1 + extra, next(tie), new_state, state))
    return None


def _point(network: TransportNetwork, stop_id: str) -> dict:
    node = network.graph.nodes[stop_id]
    try:
        lat, lon = float(node.get("latitude")), float(node.get("longitude"))
    except (TypeError, ValueError):
        lat = lon = None
    return {"stop_id": stop_id, "name": node.get("name", ""), "lat": lat, "lon": lon}


def _legs(network: TransportNetwork, path) -> list[dict]:
    """Group consecutive states on the same route into legs."""
    legs = []
    previous_stop = path[0][0]
    for stop, route in path[1:]:
        if legs and legs[-1]["route_id"] == route:
            legs[-1]["to_stop_id"] = stop
            legs[-1]["stops_travelled"] += 1
            legs[-1]["points"].append(_point(network, stop))
        else:
            legs.append({"route_id": route, "from_stop_id": previous_stop,
                         "to_stop_id": stop, "stops_travelled": 1,
                         "points": [_point(network, previous_stop), _point(network, stop)]})
        previous_stop = stop
    for leg in legs:
        info = network.route_names.get(leg["route_id"], {})
        leg["bus_number"] = info.get("number") or leg["route_id"]
        leg["vehicle"] = info.get("vehicle", "Bus")
        leg["bus_description"] = info.get("description", "")
        leg["from_name"] = network.graph.nodes[leg["from_stop_id"]].get("name", "")
        leg["to_name"] = network.graph.nodes[leg["to_stop_id"]].get("name", "")
    return legs


def find_route(
    network: TransportNetwork,
    origin_stop_ids: list[str],
    destination_stop_ids: list[str],
    allowed_ids: set[str] | None = None,
) -> dict:
    """Route between two groups of stop IDs, preferring few transfers."""
    origins = [str(s) for s in origin_stop_ids][:MAX_IDS]
    destinations = [str(s) for s in destination_stop_ids][:MAX_IDS]
    base = {"mode": network.mode, "sample_note": network.sample_note}

    unknown = [s for s in origins + destinations
               if allowed_ids is not None and s not in allowed_ids]
    if unknown:
        return {**base, "status": "unverified_stop_ids", "stop_ids": unknown,
                "message": ("These IDs did not come from search_stops or "
                            "validate_stop_ids. Search for the stops first.")}

    invalid = [s for s in origins + destinations if s not in network.graph]
    if not origins or not destinations or invalid:
        return {**base, "status": "invalid_stops", "stop_ids": invalid,
                "reason": "stop_not_in_graph",
                "message": "Some stop IDs are missing from the graph. "
                           + network.sample_note}

    if set(origins) & set(destinations):
        return {**base, "status": "same_stop",
                "message": "Origin and destination are the same stop."}

    path = _best_path(network, origins, destinations)

    if path is None:
        connected = any(network.same_component(o, d)
                        for o in origins for d in destinations)
        reason = "no_directed_path" if connected else "disconnected_in_sample"
        detail = (
            "The stops are linked in the graph, but not in this travel direction."
            if connected else
            "The stops belong to separate parts of the graph with no "
            "connection between them."
        )
        return {**base, "status": "no_route", "reason": reason,
                "message": f"No route found. {detail} {network.sample_note}"}

    legs = _legs(network, path)
    start, end = legs[0]["from_stop_id"], legs[-1]["to_stop_id"]
    return {
        **base,
        "status": "ok",
        "board_at": {"stop_id": start, "name": network.graph.nodes[start].get("name", "")},
        "alight_at": {"stop_id": end, "name": network.graph.nodes[end].get("name", "")},
        "legs": legs,
        "stops_travelled": sum(leg["stops_travelled"] for leg in legs),
        "num_transfers": len(legs) - 1,
        "note": ("Route prefers fewer transfers and uses Carris Metropolitana "
                 "bus numbers. It starts at the connected stop in your origin "
                 "area, which may differ from the exact stop you named. Use "
                 "find_timetable for scheduled times."),
    }
