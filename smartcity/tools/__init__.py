import json

from langchain_core.tools import StructuredTool

from ..modes import NetworkRegistry
from .routing import find_route
from .search import search_stops
from .validate import validate_stop_ids


def _unknown_mode(registry: NetworkRegistry, mode: str) -> str:
    return json.dumps({"status": "unknown_mode", "mode": mode,
                       "available_modes": registry.modes})


def _mode_help(registry: NetworkRegistry) -> str:
    return "; ".join(f"'{m}' = {registry.labels.get(m, m)}" for m in registry.modes)


def build_tools(registry: NetworkRegistry, geocoder=None,
                timetable=None, clock=None) -> list[StructuredTool]:
    """LangChain tools bound to a registry. Each returns a JSON string."""
    from ..journey_service import search_journey

    # Stop IDs that tools have produced; find_route and find_departures refuse anything else.
    surfaced: set[tuple[str, str]] = set()

    def search_stops_tool(query: str, municipality: str | None = None,
                          mode: str = "bus") -> str:
        if mode == "any":
            candidates, searched = [], []
            for candidate_mode in registry.modes:
                network = registry.get(candidate_mode)
                found = search_stops(network, query, municipality, geocoder=geocoder)
                searched.append(candidate_mode)
                for candidate in found["candidates"]:
                    candidate = {**candidate, "mode": candidate_mode,
                                 "operator": registry.labels.get(candidate_mode, candidate_mode)}
                    candidates.append(candidate)
                    surfaced.update((candidate_mode, str(stop_id))
                                    for stop_id in candidate["stop_ids"])
            status = "not_found" if not candidates else ("ok" if len(candidates) == 1 else "ambiguous")
            result = {"status": status, "query": query, "candidates": candidates,
                      "total_candidates": len(candidates), "searched_modes": searched,
                      "message": ("No matching stop found in loaded modes." if not candidates
                                  else "Choose the matching operator and municipality." if status == "ambiguous"
                                  else "One matching stop candidate.")}
            return json.dumps(result, ensure_ascii=False)
        network = registry.get(mode)
        if network is None:
            return _unknown_mode(registry, mode)
        result = search_stops(network, query, municipality, geocoder=geocoder)
        for candidate in result["candidates"]:
            surfaced.update((mode, str(stop_id)) for stop_id in candidate["stop_ids"])
        return json.dumps(result, ensure_ascii=False)

    def validate_stop_ids_tool(stop_ids: list[str], mode: str = "bus") -> str:
        network = registry.get(mode)
        if network is None:
            return _unknown_mode(registry, mode)
        result = validate_stop_ids(network, stop_ids)
        surfaced.update((mode, str(s["stop_id"]))
                        for s in result["stops"] if s["valid"])
        return json.dumps(result, ensure_ascii=False)

    def find_route_tool(origin_stop_ids: list[str],
                        destination_stop_ids: list[str],
                        mode: str = "bus") -> str:
        network = registry.get(mode)
        if network is None:
            return _unknown_mode(registry, mode)
        allowed_ids = {stop_id for surfaced_mode, stop_id in surfaced
                       if surfaced_mode == mode}
        result = find_route(network, origin_stop_ids, destination_stop_ids,
                            allowed_ids=allowed_ids)
        return json.dumps(result, ensure_ascii=False)

    def find_departures_tool(origin_stop_ids: list[str],
                             destination_stop_ids: list[str],
                             mode: str = "bus") -> str:
        network = registry.get(mode)
        if network is None:
            return _unknown_mode(registry, mode)
        unknown = [s for s in [*origin_stop_ids, *destination_stop_ids]
                   if (mode, str(s)) not in surfaced]
        if unknown:
            return json.dumps({
                "status": "unverified_stop_ids", "stop_ids": unknown[:10],
                "message": "Use only stop IDs returned by search_stops."})
        transport_filter = {"bus": "Carris Metropolitana Bus",
                            "train": "CP / Fertagus Train",
                            "fertagus": "CP / Fertagus Train",
                            "metro": "Metropolitano de Lisboa",
                            "mts": "Metro Sul do Tejo (MST)",
                            "carris": "Carris Lisboa Tram"}.get(mode)
        if transport_filter is None:
            return _unknown_mode(registry, mode)
        result = search_journey(
            registry,
            {"mode": mode, "stop_ids": origin_stop_ids},
            {"mode": mode, "stop_ids": destination_stop_ids},
            transport_filter=transport_filter, clock=clock)
        return json.dumps(result, ensure_ascii=False)

    def find_mst_departures_tool(origin_stop_ids: list[str],
                                 destination_stop_ids: list[str]) -> str:
        mode = "mts"
        if registry.get(mode) is None:
            return _unknown_mode(registry, mode)
        unknown = [s for s in [*origin_stop_ids, *destination_stop_ids]
                   if (mode, str(s)) not in surfaced]
        if unknown:
            return json.dumps({"status": "unverified_stop_ids", "stop_ids": unknown[:10],
                               "message": "Use only MST stop IDs returned by search_stops."})
        result = search_journey(registry,
            {"mode": mode, "stop_ids": origin_stop_ids},
            {"mode": mode, "stop_ids": destination_stop_ids},
            transport_filter="Metro Sul do Tejo (MST)", clock=clock)
        return json.dumps(result, ensure_ascii=False)

    def find_any_departures_tool(origin_stop_ids: list[str], origin_mode: str,
                                 destination_stop_ids: list[str], destination_mode: str) -> str:
        pairs = [(origin_mode, origin_stop_ids), (destination_mode, destination_stop_ids)]
        unknown = [stop_id for mode, ids in pairs for stop_id in ids
                   if (mode, str(stop_id)) not in surfaced]
        if unknown:
            return json.dumps({"status": "unverified_stop_ids", "stop_ids": unknown[:10],
                               "message": "Use stop IDs returned by search_stops(mode='any')."})
        origin_network = registry.get(origin_mode)
        destination_network = registry.get(destination_mode)
        if origin_network is None or destination_network is None:
            return _unknown_mode(registry, origin_mode if origin_network is None else destination_mode)
        result = search_journey(
            registry,
            {"mode": origin_mode, "stop_ids": origin_stop_ids,
             "name": origin_network.stop_info(origin_stop_ids[0])["name"]},
            {"mode": destination_mode, "stop_ids": destination_stop_ids,
             "name": destination_network.stop_info(destination_stop_ids[0])["name"]},
            transport_filter="Any available transport", clock=clock)
        return json.dumps(result, ensure_ascii=False)

    return [
        StructuredTool.from_function(
            find_departures_tool, name="find_departures",
            description=(
                "Find scheduled journeys departing in the NEXT TWO HOURS (Lisbon "
                "time, fixed; there is no way to change the window, date or time) "
                "between two places in the mode of the selected stop IDs: bus means "
                "Carris Metropolitana, train means CP/Fertagus, metro means the "
                "underground Metropolitano de Lisboa, mts means Metro Sul do Tejo, "
                "carris means Carris Lisboa buses/trams. Pass mode and stop_ids from "
                "search_stops. Returns "
                "options with line, departure time, stop sequence, direct journeys first "
                "(only direct journeys when any exist; otherwise at most one change), "
                "transfer info "
                "and arrival/duration when known, or a clear no-journey message. "
                "Never invent anything not in the result. Modes: "
                + _mode_help(registry) + "."),
        ),
        StructuredTool.from_function(
            find_mst_departures_tool, name="find_mst_departures",
            description=("Search only the Metro Sul do Tejo surface light rail timetable. "
                         "Use MST stop IDs from search_stops(mode='mts'); this never "
                         "falls back to Metro de Lisboa or buses. Search window is the "
                         "next two hours in Europe/Lisbon. Prefer direct departures; "
                         "return one-change journeys only when no direct journey exists. "
                         "Never invent service."),
        ),
        StructuredTool.from_function(
            find_any_departures_tool, name="find_any_departures",
            description=("Search any loaded operator for direct journeys, with scheduled "
                         "two-leg journeys across nearby interchanges as fallback only "
                         "when no direct departure exists. Pass origin and "
                         "destination stop IDs and their mode IDs exactly as returned by "
                         "search_stops(mode='any'). Never invent an interchange or service."),
        ),
        StructuredTool.from_function(
            search_stops_tool, name="search_stops",
            description=(
                "Resolve a place to stops: stop names, towns/municipalities "
                "(Lisbon, Almada), or landmarks (Oriente station, a mall) via "
                "map lookup, using the nearest stops. Returns candidates with "
                "stop_ids. status 'ambiguous' means the user must choose; "
                "'ok' means use it without asking. Optional municipality filter. "
                "mode selects the network (same ids as find_departures); use mode='any' "
                "to search every loaded operator and retain each candidate's mode: "
                + _mode_help(registry) + "."),
        ),
        StructuredTool.from_function(
            validate_stop_ids_tool, name="validate_stop_ids",
            description=("Check stop IDs exist in the dataset and in the routing "
                         "graph."),
        ),
        StructuredTool.from_function(
            find_route_tool, name="find_route",
            description=(
                "Compute the route between origin and destination stop_id lists "
                "taken from search_stops results. Never pass invented IDs."),
        ),
    ]
