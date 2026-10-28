"""LangGraph pipeline for "what departs in the next two hours".

Supervisor nodes: validate_input -> resolve_stops -> dispatch (LangGraph Send, one
parallel `specialist` run per vehicle family: bus, tram, metro, train, ...)
-> prepare_results (merge, sort, messages). Every node is deterministic Python; the LLM (in the chat
agent) only turns free text into the stop IDs that search_stops returned.

The NetworkX graph is used to find stops and to give an informational hint
when nothing is found. It is built from a sample of trips, so a graph path is
never treated as proof that a scheduled service exists: every reported journey
is an actual trip in the GTFS timetable on a valid service date.
"""

import operator
from datetime import datetime
import math
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .geo import haversine_km
from .data import normalize
from .modes import (MODE_FILTERS, REQUIRED_MODE_FILTERS, NetworkRegistry,
                    modes_for_filter)
from .specialists import specialists_for
from .timetable import format_time
from .tools.routing import find_route
from .window import WINDOW_MINUTES, current_window

MAX_WALK_KM = 1.5
MAX_INTERCHANGE_KM = 0.4
NEAREST_COUNT = 8
MAX_RIDE_SECONDS = 6 * 3600  # longer than this, a time pair is not trusted
TRANSFER_BUFFER_SECONDS = 2 * 60
MAX_TRANSFER_WAIT_SECONDS = 90 * 60
DIRECT_LOOKAHEAD_SECONDS = 2 * 60 * 60


def _schedule_seconds(value):
    """Return a finite timetable time, or None for a missing/invalid time."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return int(seconds) if math.isfinite(seconds) else None


class PipelineState(TypedDict, total=False):
    origin: dict
    destination: dict
    transport_filter: str
    window: object
    errors: list
    resolved: dict
    endpoint_modes: dict
    # Specialists run in parallel; these lists are merged with operator.add.
    skipped: Annotated[list, operator.add]
    options: Annotated[list, operator.add]
    reports: Annotated[list, operator.add]
    result: dict


class SpecialistInput(TypedDict):
    key: str
    window: object
    ends: dict


def _centre(network, stop_ids):
    points = []
    for stop_id in stop_ids:
        row = network._stop_index.get(str(stop_id))
        if row and row["lat"] == row["lat"] and row["lon"] == row["lon"]:
            points.append((row["lat"], row["lon"]))
    if not points:
        return None
    # Use a real stop near the middle: same-named stops can be far apart.
    mid_lat = sorted(p[0] for p in points)[len(points) // 2]
    mid_lon = sorted(p[1] for p in points)[len(points) // 2]
    return min(points, key=lambda p: (p[0] - mid_lat) ** 2 + (p[1] - mid_lon) ** 2)


def _nearest(network, point):
    # All stops count: the timetable, not the sampled graph, decides service.
    stops = network.stops.dropna(subset=["lat", "lon"])
    if stops.empty or point is None:
        return [], 0.0
    distance = haversine_km(point[0], point[1], stops["lat"].to_numpy(),
                            stops["lon"].to_numpy())
    near = stops.assign(distance_km=distance)
    near = near[near["distance_km"] <= MAX_WALK_KM]
    near = near.sort_values("distance_km").head(NEAREST_COUNT)
    if near.empty:
        return [], 0.0
    return list(near["stop_id"]), float(near["distance_km"].iloc[0])


def _match_other_network(network, place, point):
    """Match an explicit stop across operators only at the same named/local stop."""
    stops = network.stops.dropna(subset=["lat", "lon"])
    if stops.empty or point is None:
        return [], 0.0
    distance = haversine_km(point[0], point[1], stops["lat"].to_numpy(),
                            stops["lon"].to_numpy())
    near = stops.assign(distance_km=distance)
    near = near[near["distance_km"] <= MAX_INTERCHANGE_KM]
    place_name = normalize(place.get("name", ""))
    exact = near[near["norm_name"] == place_name]
    candidates = exact if not exact.empty else near
    candidates = candidates.sort_values("distance_km").head(NEAREST_COUNT)
    if candidates.empty:
        return [], 0.0
    return list(candidates["stop_id"].astype(str)), float(candidates["distance_km"].iloc[0])


def _plain(value) -> str:
    return " ".join(str(value).split())


def _deduplicate_options(options: list[dict]) -> list[dict]:
    """Drop duplicate journeys that differ only by nearby boarding-stop aliases."""
    unique, result = set(), []
    for option in options:
        legs = option.get("legs") or [{
            "mode": option.get("mode"), "route_id": option.get("route_id"),
            "board_stop_id": option.get("board_stop_id", option.get("board", "")),
            "alight_stop_id": option.get("alight_stop_id", option.get("alight", "")),
            "departs": option.get("departs"), "arrives": option.get("arrives"),
        }]
        if len(legs) > 1:
            # The same scheduled first and second trips can be connected at
            # multiple nearby calls on the first trip. Treat those as one
            # itinerary; the caller orders candidates by walk distance first,
            # so the closest/actual interchange is retained.
            first, last = legs[0], legs[-1]
            identity = (
                (first.get("mode", option.get("mode")), first.get("route_id"),
                 first.get("departs")),
                (last.get("mode", option.get("mode")), last.get("route_id"),
                 last.get("departs"), last.get("arrives"),
                 last.get("alight_stop_id", last.get("alight", ""))),
            )
        else:
            identity = tuple((leg.get("mode", option.get("mode")), leg.get("route_id"),
                              leg.get("alight_stop_id", leg.get("alight", "")),
                              leg.get("departs"), leg.get("arrives")) for leg in legs)
        if identity not in unique:
            unique.add(identity)
            result.append(option)
    return result


def _journey_changes(option: dict) -> int:
    """Count changes from the ordered scheduled legs, not labels or stops."""
    legs = option.get("legs")
    if legs:
        return max(0, len(legs) - 1)
    return max(0, int(option.get("transfers", 0)))


def _cross_operator_options(registry, endpoint_modes, window,
                            preferred_origin=None, preferred_destination=None):
    """Compose two scheduled legs from an origin mode to a destination mode."""
    results, searched_modes = [], set()
    modes = list(endpoint_modes)
    modes.sort(key=lambda mode: (mode != preferred_origin, mode))
    destination_modes = sorted(
        endpoint_modes, key=lambda mode: (mode != preferred_destination, mode))
    for first_mode in modes:
        first_ends = endpoint_modes[first_mode]
        if not first_ends.get("origin_ids"):
            continue
        first_network = registry.get(first_mode)
        first_table = registry.timetable(first_mode)
        if first_table is None or not first_table.available:
            continue
        first_label = registry.labels.get(first_mode, first_mode)
        try:
            starts = first_table.departures_from(
                first_ends["origin_ids"], window.base_day,
                window.start_seconds, window.end_seconds, limit_per_stop=5)
            searched_modes.add(first_mode)
        except Exception:
            continue
        for second_mode in destination_modes:
            second_ends = endpoint_modes[second_mode]
            if second_mode == first_mode or not second_ends.get("destination_ids"):
                continue
            second_network = registry.get(second_mode)
            second_table = registry.timetable(second_mode)
            if second_table is None or not second_table.available:
                continue
            second_label = registry.labels.get(second_mode, second_mode)
            stops = second_network.stops.dropna(subset=["lat", "lon"])
            if stops.empty:
                continue
            for first_trip in starts:
                pair_result_start = len(results)
                first_departure = _schedule_seconds(first_trip.get("dep"))
                if first_departure is None:
                    continue
                first_calls = first_trip["stops"]
                for index, interchange in enumerate(first_calls[1:], start=1):
                    interchange_arrival = _schedule_seconds(interchange.get("arr"))
                    if interchange_arrival is None:
                        continue
                    first_stop = first_network._stop_index.get(str(interchange["stop_id"]))
                    if not first_stop or first_stop["lat"] != first_stop["lat"]:
                        continue
                    distances = haversine_km(
                        first_stop["lat"], first_stop["lon"],
                        stops["lat"].to_numpy(), stops["lon"].to_numpy())
                    candidates = stops.assign(distance_km=distances)
                    candidates = candidates[candidates["distance_km"] <= MAX_INTERCHANGE_KM]
                    candidates = candidates.sort_values("distance_km").head(3)
                    candidate_rows = list(candidates.itertuples(index=False))
                    if not candidate_rows:
                        continue
                    walk_by_stop = {
                        str(row.stop_id): (int(row.distance_km / 4.0 * 3600),
                                           round(row.distance_km * 1000))
                        for row in candidate_rows
                    }
                    buffer_seconds = max(
                        TRANSFER_BUFFER_SECONDS,
                        max(walk for walk, _ in walk_by_stop.values()))
                    board_after = interchange_arrival + buffer_seconds
                    try:
                        onward = second_table.departures_between(
                            list(walk_by_stop), second_ends["destination_ids"],
                            window.base_day, board_after,
                            board_after + MAX_TRANSFER_WAIT_SECONDS)
                        searched_modes.add(second_mode)
                    except Exception:
                        continue
                    for second_trip in onward:
                        walk_seconds, walk_m = walk_by_stop.get(
                            str(second_trip.get("board_stop")),
                            (buffer_seconds, round(buffer_seconds * 4.0 / 3.6)))
                        if walk_seconds > buffer_seconds:
                            continue
                        second_departure = _schedule_seconds(second_trip.get("dep"))
                        second_arrival = _schedule_seconds(second_trip.get("arr"))
                        if second_departure is None or second_arrival is None:
                            continue
                        first_info = first_network.route_names.get(
                            first_trip["route_id"], {})
                        second_info = second_network.route_names.get(
                            second_trip["route_id"], {})
                        first_vehicle = ("MST light rail" if first_mode == "mts"
                                         else first_info.get("vehicle", "Bus"))
                        second_vehicle = ("MST light rail" if second_mode == "mts"
                                          else second_info.get("vehicle", "Bus"))
                        first_leg = {
                            "mode": first_mode, "operator": first_label,
                            "vehicle": first_vehicle,
                            "line": first_trip.get("line_number") or
                                   first_info.get("number") or first_trip["route_id"],
                            "route_id": first_trip["route_id"],
                            "headsign": first_trip["headsign"],
                            "departs": format_time(first_departure),
                            "arrives": format_time(interchange_arrival),
                            "board": _stop_name(first_network, first_trip["board_stop"]),
                            "alight": _stop_name(first_network, interchange["stop_id"]),
                            "board_stop_id": str(first_trip["board_stop"]),
                            "alight_stop_id": str(interchange["stop_id"]),
                        }
                        second_leg = {
                            "mode": second_mode, "operator": second_label,
                            "vehicle": second_vehicle,
                            "line": second_trip.get("line_number") or
                                   second_info.get("number") or second_trip["route_id"],
                            "route_id": second_trip["route_id"],
                            "headsign": second_trip["headsign"],
                            "departs": format_time(second_departure),
                            "arrives": format_time(second_arrival),
                            "board": _stop_name(second_network, second_trip["board_stop"]),
                            "alight": _stop_name(second_network, second_trip["alight_stop"]),
                            "board_stop_id": str(second_trip["board_stop"]),
                            "alight_stop_id": str(second_trip["alight_stop"]),
                        }
                        results.append({
                            "mode": f"{first_mode}+{second_mode}",
                            "agent": f"{first_label} + {second_label}",
                            "label": f"{first_label} + {second_label}",
                            "vehicle": "Multiple modes",
                            "line": f"{first_leg['line']} -> {second_leg['line']}",
                            "route_id": f"{first_leg['route_id']} -> {second_leg['route_id']}",
                            "headsign": second_leg["headsign"],
                            "service_date": second_trip["service_date"],
                            "departs": first_leg["departs"],
                            "arrives": second_leg["arrives"],
                            "duration_minutes": (second_arrival - first_departure) // 60,
                            "board": first_leg["board"], "alight": second_leg["alight"],
                            "board_stop_id": first_leg["board_stop_id"],
                            "alight_stop_id": second_leg["alight_stop_id"],
                            "stops": [], "legs": [first_leg, second_leg],
                            "transfers": 1,
                            "transfer_info": (f"1 change at {first_leg['alight']} / "
                                              f"{second_leg['board']}; about {walk_m} m "
                                              f"walk and a scheduled "
                                              f"{max(TRANSFER_BUFFER_SECONDS, walk_seconds) // 60} min buffer."),
                            "walk_from_km": first_ends.get("walk_from_km", 0.0),
                            "walk_to_km": second_ends.get("walk_to_km", 0.0),
                            "_dep": first_departure, "_arr": second_arrival,
                            "_transfer_walk_m": walk_m,
                        })
                # Finish checking this scheduled trip's downstream calls so a
                # later exact station interchange can replace an earlier walk.
                # Then cap work for this mode pair as before.
                if len(results) - pair_result_start >= 3:
                    break
    results.sort(key=lambda item: (item["_dep"], item["_arr"],
                                  item.get("_transfer_walk_m", 0)))
    unique = _deduplicate_options(results)
    by_mode_pair = {}
    for option in sorted(unique, key=lambda item: (
            item["_dep"], item["_arr"], item.get("_transfer_walk_m", 0))):
        by_mode_pair.setdefault(option["mode"], []).append(option)
    balanced = [option for pair_options in by_mode_pair.values()
                for option in pair_options[:3]]
    return sorted(balanced, key=lambda option: (option["_dep"], option["_arr"]))[:20], searched_modes


def build_pipeline(registry: NetworkRegistry,
                   clock: Callable[[], datetime] | None = None):
    """Compile the pipeline. `clock` returns the current time (tests inject one)."""

    def validate_input(state: PipelineState):
        errors = []
        for key in ("origin", "destination"):
            place = state.get(key) or {}
            area_ids = any(place.get("stop_ids_by_mode", {}).values())
            if not (place.get("stop_ids") or area_ids
                    or ("lat" in place and "lon" in place)):
                errors.append(f"Missing {key}: choose a {key} stop.")
        if not errors:
            a, b = state["origin"], state["destination"]
            if (a.get("mode", "bus") == b.get("mode", "bus")
                    and a.get("stop_ids") and a.get("stop_ids") == b.get("stop_ids")):
                errors.append("Origin and destination are the same stop.")
        return {"errors": errors, "window": current_window(clock() if clock else None)}

    def resolve_stops(state: PipelineState):
        transport_filter = state.get("transport_filter", "Any available transport")
        selected_modes = set(modes_for_filter(transport_filter, registry.modes))
        points = {}
        for key in ("origin", "destination"):
            place = state[key]
            if "lat" in place:
                points[key] = (place["lat"], place["lon"])
            else:
                network = registry.get(place.get("mode", "bus"))
                points[key] = _centre(network, place["stop_ids"]) if network else None
        resolved, endpoint_modes, skipped, reports = {}, {}, [], []
        for mode in registry.modes:
            if mode not in selected_modes:
                continue
            network = registry.get(mode)
            label = registry.labels.get(mode, mode)
            ends = {}
            for key in ("origin", "destination"):
                place = state[key]
                if "stop_ids_by_mode" in place:
                    ids = [str(stop_id) for stop_id in
                           place["stop_ids_by_mode"].get(mode, [])
                           if str(stop_id) in network._stop_index]
                    ends[key] = (ids, 0.0)
                    continue
                given = [s for s in place.get("stop_ids", [])
                         if place.get("mode", "bus") == mode and s in network._stop_index]
                allowed = MODE_FILTERS.get(transport_filter)
                wrong_network = (allowed == {mode} and place.get("mode") not in (None, mode)
                                 and bool(place.get("stop_ids")))
                explicit_multimode_match = (
                    bool(place.get("stop_ids")) and place.get("mode") != mode
                    and (allowed is None or (allowed is not None and len(allowed) > 1)))
                ends[key] = ((given, 0.0) if given else
                             ([], 0.0) if wrong_network else
                             _match_other_network(network, place, points[key])
                             if explicit_multimode_match else
                             _nearest(network, points[key]))
            endpoint_modes[mode] = {
                "origin_ids": ends["origin"][0],
                "destination_ids": ends["destination"][0],
                "walk_from_km": round(ends["origin"][1], 2),
                "walk_to_km": round(ends["destination"][1], 2),
            }
            if not ends["origin"][0] or not ends["destination"][0]:
                missing_end = "starting point" if not ends["origin"][0] else "destination"
                reason = f"No {label} stop close enough to the {missing_end}."
                skipped.append({"mode": mode, "label": label, "reason": reason})
                reports.append({"agent": label, "mode": mode,
                                "status": "not_searched", "count": 0,
                                "reason": reason})
                continue
            resolved[mode] = {
                "origin_ids": ends["origin"][0], "destination_ids": ends["destination"][0],
                "walk_from_km": round(ends["origin"][1], 2),
                "walk_to_km": round(ends["destination"][1], 2)}
        return {"resolved": resolved, "endpoint_modes": endpoint_modes,
                "skipped": skipped, "reports": reports}

    specialists, missing = specialists_for(registry.modes, registry.labels)
    by_key = {s.key: s for s in specialists}

    def dispatch(state: PipelineState):
        """Supervisor: fan out to one specialist per vehicle family with stops."""
        transport_filter = state.get("transport_filter", "Any available transport")
        selected_modes = set(modes_for_filter(transport_filter, registry.modes))
        selected_keys = {"Tram": {"tram"}, "Carris Lisboa Tram": {"tram"}}.get(transport_filter)
        sends = [Send("specialist", {"key": s.key, "window": state["window"],
                                     "ends": state["resolved"][s.mode]})
                 for s in specialists if s.mode in selected_modes
                 and (selected_keys is None or s.key in selected_keys)
                 and s.mode in state["resolved"]]
        return sends or "prepare_results"

    def specialist(state: SpecialistInput):
        spec, window, ends = by_key[state["key"]], state["window"], state["ends"]
        network = registry.get(spec.mode)
        timetable = registry.timetable(spec.mode)

        def skip(reason, status="skipped"):
            return {"skipped": [{"mode": spec.mode, "label": spec.label, "reason": reason}],
                    "reports": [{"agent": spec.label, "mode": spec.mode,
                                 "status": status,
                                 "count": 0, "reason": reason}]}

        try:
            if timetable is None or not timetable.available:
                setup = ("No local MTS schedule snapshot; run import_mst.py."
                         if spec.mode == "mts" else
                         "No timetable index for this mode (run build_timetable.py / import_feed.py).")
                return skip(setup, "data_missing")
            if not timetable.service_days(window.base_day):
                span = timetable.date_range()
                return skip(
                    f"No service-calendar entries around {window.base_day.isoformat()}"
                    + (f" (timetable covers {span[0]}–{span[1]})." if span else "."), "no_service")
            trips = timetable.departures_between(
                ends["origin_ids"], ends["destination_ids"], window.base_day,
                window.start_seconds, window.end_seconds)
            options = [_option(registry, network, spec, trip, ends) for trip in trips]
            options = [o for o in options if spec.accepts(o["vehicle"])]
            next_direct = None
            suppressed_transfers = 0
            if network is not None:
                transfer_options = _transfer_options(
                    network, timetable, spec, ends, window,
                    registry.labels.get(spec.mode, spec.mode))
                # A transfer should not send someone away from the destination
                # when a direct train just beyond the departure window arrives
                # at the same time (or earlier).
                future_directs = timetable.departures_between(
                    ends["origin_ids"], ends["destination_ids"], window.base_day,
                    window.end_seconds + 1,
                    window.end_seconds + DIRECT_LOOKAHEAD_SECONDS)
                future_directs = [trip for trip in future_directs
                                  if spec.accepts(
                                      "MST light rail" if spec.mode == "mts" else
                                      network.route_names.get(trip["route_id"], {})
                                      .get("vehicle", "Bus"))]
                if future_directs:
                    next_direct = min(future_directs,
                                      key=lambda trip: (trip["dep"], trip["arr"]))
                    retained = [option for option in transfer_options
                                if option["_arr"] < next_direct["arr"]]
                    suppressed_transfers = len(transfer_options) - len(retained)
                    transfer_options = retained
                options.extend(transfer_options)
            origin_trips = timetable.departures_from(
                ends["origin_ids"], window.base_day, window.start_seconds,
                window.end_seconds, limit_per_stop=8)
        except Exception as error:  # one failing specialist must not hide the others
            return skip(f"Search failed: {type(error).__name__}: {error}",
                        "search_failed")
        origin_departures = sum(
            1 for trip in origin_trips
            if spec.accepts("MST light rail" if spec.mode == "mts" else
                            network.route_names.get(trip["route_id"], {}).get("vehicle", "Bus")))
        return {"options": options,
                "reports": [{"agent": spec.label, "mode": spec.mode,
                             "status": "searched",
                             "count": len(options), "origin_departures": origin_departures,
                             "dominated_transfers_suppressed": suppressed_transfers,
                             "next_direct_outside_window": ({
                                 "departs": format_time(next_direct["dep"]),
                                 "arrives": format_time(next_direct["arr"]),
                                 "arrival_seconds": next_direct["arr"],
                             } if next_direct and suppressed_transfers else None),
                             "reason": ""}]}

    def prepare_results(state: PipelineState):
        window = state.get("window")
        errors = state.get("errors") or []
        info = {"minutes": WINDOW_MINUTES, "label": window.label if window else None,
                "start": window.start.isoformat() if window else None,
                "end": window.end.isoformat() if window else None}
        if errors:
            return {"result": {"status": "invalid_input", "window": info,
                               "options": [], "skipped": [], "agents": [],
                               "message": " ".join(errors)}}
        all_options = list(state.get("options", []))
        transport_filter = state.get("transport_filter", "Any available transport")
        selected_modes = modes_for_filter(transport_filter, registry.modes)
        cross_searched_modes = set()
        if len(selected_modes) > 1 and state.get("endpoint_modes"):
            cross_options, cross_searched_modes = _cross_operator_options(
                registry, state["endpoint_modes"], window,
                preferred_origin=state.get("origin", {}).get("mode"),
                preferred_destination=state.get("destination", {}).get("mode"))
            all_options.extend(cross_options)
            for report in state.get("reports", []):
                if report.get("mode") in cross_searched_modes and report.get("status") == "not_searched":
                    report["status"] = "searched"
                    report["reason"] = "Searched in a timetable-validated inter-operator journey."
        required_mode = REQUIRED_MODE_FILTERS.get(transport_filter)
        if required_mode:
            all_options = [option for option in all_options
                           if required_mode in {
                               leg.get("mode") for leg in option.get("legs", [])
                           } or option.get("mode") == required_mode]
        next_direct_arrivals = {
            report["mode"]: report["next_direct_outside_window"]["arrival_seconds"]
            for report in state.get("reports", [])
            if report.get("next_direct_outside_window")
        }
        retained_options, dominated_count = [], 0
        for option in all_options:
            leg_modes = {leg.get("mode") for leg in option.get("legs", [])}
            option_modes = leg_modes or {option.get("mode")}
            dominated = (option.get("transfers", 0) > 0 and any(
                mode in next_direct_arrivals
                and option.get("_arr", float("inf")) >= next_direct_arrivals[mode]
                for mode in option_modes))
            if dominated:
                dominated_count += 1
            else:
                retained_options.append(option)
        all_options = retained_options
        for option in all_options:
            option["transfers"] = _journey_changes(option)
            if option["transfers"] == 0:
                option["transfer_info"] = "Direct · 0 changes."
            elif option["transfers"] == 1 and not option.get("transfer_info"):
                option["transfer_info"] = "1 change."
        ordered = _deduplicate_options(all_options)
        direct = [option for option in ordered if option["transfers"] == 0]
        selected = direct if direct else [
            option for option in ordered if option["transfers"] == 1]
        ordered = sorted(selected, key=lambda o: (o["_dep"], o["_arr"]))[:20]
        options = [{k: v for k, v in o.items() if not k.startswith("_")} for o in ordered]
        skipped, seen = [], set()
        selected_modes = set(modes_for_filter(
            state.get("transport_filter", "Any available transport"), registry.modes))
        for item in [*(item for item in state.get("skipped", [])
                       if item.get("mode") not in cross_searched_modes),
                     *({"mode": s.mode, "label": s.label,
                        "reason": f"No {s.label} feed is loaded."} for s in missing
                       if s.mode in selected_modes)]:
            key = (item["mode"], item["reason"])
            if key not in seen:
                seen.add(key)
                skipped.append(item)
        agents = sorted(state.get("reports", []), key=lambda r: r["agent"])
        for report in agents:
            next_direct = report.get("next_direct_outside_window")
            if next_direct:
                next_direct.pop("arrival_seconds", None)
            timetable = registry.timetable(report.get("mode", ""))
            source = getattr(timetable, "source_status", None)
            if isinstance(source, dict):
                report["data_source"] = source
            elif timetable is not None:
                date_range = getattr(timetable, "date_range", None)
                span = date_range() if timetable.available and callable(date_range) else None
                report["data_source"] = {
                    "source": "local GTFS timetable index",
                    "status": "available" if timetable.available else "unavailable",
                    "path": str(getattr(timetable, "path", "")),
                    "service_date_range": span,
                }
        origin_counts = [(r["agent"], r.get("origin_departures", 0))
                         for r in agents if r["status"] in {"ok", "searched"}]
        total_origin_departures = sum(count for _, count in origin_counts)
        search_errors = [r for r in agents if r["status"] == "search_failed"]
        searched_agents = [r for r in agents if r["status"] in {"ok", "searched"}]
        missing_data = [r for r in agents if r["status"] == "data_missing"]
        no_service = [r for r in agents if r["status"] == "no_service"]
        notes = ["Scheduled times from the published timetable, not live arrivals. "
                 "Transfer options require scheduled legs, a nearby mapped interchange "
                 "(within 400 m), and enough time for the walk and boarding buffer.",
                 "The bus graph is built from a sample of 1,000 trips; it is not a "
                 "complete network and a graph path does not prove a service."]
        if "ferry" not in registry.modes:
            notes.append("No local ferry timetable feed is loaded, so ferry journeys are not included.")
        if dominated_count or any(report.get("dominated_transfers_suppressed", 0)
                                  for report in agents):
            notes.append("A transfer was omitted because a direct scheduled service just "
                         "outside this departure window arrives no later.")
        if any(report.get("mode") == "mts" and report.get("status") == "searched"
               for report in agents):
            notes.append("MST times come from the published seasonal timetable snapshot. "
                         "It has no date-specific service exceptions or live disruption "
                         "alerts; check MTS notices before travelling.")
        if options:
            return {"result": {"status": "ok", "window": info, "options": options,
                               "skipped": skipped, "agents": agents, "notes": notes,
                               "message": f"{len(options)} scheduled departure(s) "
                                          f"between {window.start:%H:%M} and {window.end:%H:%M}."}}
        if search_errors and not searched_agents:
            status = "search_failed"
            message = ("Operator search failed: " + "; ".join(
                r["reason"] for r in search_errors))
        elif search_errors or ((missing_data or no_service) and searched_agents):
            status = "partial_failure"
            issues = [*search_errors, *missing_data, *no_service]
            message = ("No journey was found across the available results; "
                       "some operator coverage was unavailable: "
                       + "; ".join(r["reason"] for r in issues))
        elif missing_data:
            status = "data_missing"
            message = "No timetable search ran because the selected feed is missing: " \
                      + "; ".join(r["reason"] for r in missing_data)
        elif no_service:
            status = "no_service"
            message = "No timetable search ran because the selected feed has no active " \
                      "service dates for this window: " + "; ".join(
                          r["reason"] for r in no_service)
        elif not searched_agents:
            status = "not_searched"
            message = ("No operator search ran because no stops could be resolved for "
                       "the selected mode and places. See the status details below.")
        else:
            status = "no_journey"
            next_direct = next((report["next_direct_outside_window"] for report in agents
                                if report.get("next_direct_outside_window")), None)
            message = (
                f"No timetable-validated journey was found between {window.start:%H:%M} "
                f"and {window.end:%H:%M} (Lisbon time). "
                + (f"{total_origin_departures} scheduled departure(s) leave nearby "
                   "origin stops, but none connect to the selected destination with "
                   "a feasible timetable transfer. " if total_origin_departures else
                   "No scheduled departures leave nearby origin stops in this window. ")
                + (f"The next direct service leaves at {next_direct['departs']} and "
                   f"arrives at {next_direct['arrives']}, outside this departure window. "
                   if next_direct else "")
                + "This does not prove that no service exists; results depend on the "
                  "loaded timetables and interchange coverage.")
        return {"result": {
            "status": status, "window": info, "options": [], "skipped": skipped,
            "agents": agents, "notes": notes, "graph_hint": _graph_hint(registry, state),
            "message": message}}

    def after_input(state: PipelineState):
        return "prepare_results" if state.get("errors") else "resolve_stops"

    graph = StateGraph(PipelineState)
    for node in (validate_input, resolve_stops, specialist, prepare_results):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "validate_input")
    graph.add_conditional_edges("validate_input", after_input)
    graph.add_conditional_edges("resolve_stops", dispatch)
    graph.add_edge("specialist", "prepare_results")
    graph.add_edge("prepare_results", END)
    compiled = graph.compile()

    def run(origin: dict, destination: dict,
            transport_filter: str = "Any") -> dict:
        return compiled.invoke({"origin": origin, "destination": destination,
                                "transport_filter": transport_filter})["result"]

    return run


def _stop_name(network, stop_id):
    info = network.stop_info(stop_id)
    return _plain(info["name"]) if info else str(stop_id)


def _option(registry, network, spec, trip, ends):
    mode = spec.mode
    info = network.route_names.get(trip["route_id"], {})
    if mode == "mts":
        info = {"number": trip.get("line_number", "3"),
                "description": trip.get("line_description", "Metro Sul do Tejo")}
    ride = trip["arr"] - trip["dep"]
    reliable = 0 < ride <= MAX_RIDE_SECONDS
    stops = [{"name": _stop_name(network, s["stop_id"]), "stop_id": s["stop_id"],
              "time": format_time(s["arr"] if i else s["dep"])}
             for i, s in enumerate(trip["stops"])]
    leg = {
        "mode": mode, "operator": spec.label,
        "vehicle": "MST light rail" if mode == "mts" else info.get("vehicle", "Bus"),
        "line": info.get("number") or trip["route_id"],
        "route_id": trip["route_id"], "headsign": trip["headsign"],
        "departs": format_time(trip["dep"]),
        "arrives": format_time(trip["arr"]) if reliable else None,
        "board": _stop_name(network, trip["board_stop"]),
        "alight": _stop_name(network, trip["alight_stop"]),
        "board_stop_id": str(trip["board_stop"]),
        "alight_stop_id": str(trip["alight_stop"]),
    }
    return {
        "mode": mode,
        "agent": spec.label,
        "label": registry.labels.get(mode, mode),
        "vehicle": "MST light rail" if mode == "mts" else info.get("vehicle", "Bus"),
            "line": info.get("number") or trip["route_id"],
        "line_description": info.get("description", ""),
        "route_id": trip["route_id"],
        "headsign": trip["headsign"],
        "service_date": trip["service_date"],
        "departs": format_time(trip["dep"]),
        "arrives": format_time(trip["arr"]) if reliable else None,
        "duration_minutes": ride // 60 if reliable else None,
        "board": _stop_name(network, trip["board_stop"]),
        "alight": _stop_name(network, trip["alight_stop"]),
        "board_stop_id": str(trip["board_stop"]),
        "alight_stop_id": str(trip["alight_stop"]),
        "stops": stops, "legs": [leg],
        "transfers": 0,
        "transfer_info": "Direct · 0 changes.",
        "walk_from_km": ends["walk_from_km"],
        "walk_to_km": ends["walk_to_km"],
        "_dep": trip["dep"], "_arr": trip["arr"],
    }


def _transfer_options(network, timetable, spec, ends, window, operator_label):
    """Search schedule-derived legs, using graph stops only as interchange candidates."""
    destination_ids = set(ends["destination_ids"])
    graph_stops = set(network.graph.nodes)
    # A state is (current stop, arrival time, scheduled legs, visited stops).
    frontier = [(str(stop), None, [], {str(stop)}) for stop in ends["origin_ids"]]
    results, seen = [], set()

    for transfers_so_far in range(2):  # a direct leg plus at most one change
        best_next = {}
        for stop_id, arrival, chain, visited in frontier:
            arrival = _schedule_seconds(arrival)
            start = (window.start_seconds if arrival is None else
                     arrival + TRANSFER_BUFFER_SECONDS)
            end = (window.end_seconds if arrival is None else
                   arrival + MAX_TRANSFER_WAIT_SECONDS)
            departures = timetable.departures_from(
                [stop_id], window.base_day, start, end, limit_per_stop=50)
            for trip in departures:
                if (spec.mode == "mts" and any(
                        previous_leg.get("line_number") == trip.get("line_number")
                        for previous_leg in chain)):
                    continue  # avoid an artificial reversal on the same MST line
                sequence = trip["stops"]
                for index in range(1, len(sequence)):
                    call = sequence[index]
                    call_arrival = _schedule_seconds(call.get("arr"))
                    trip_departure = _schedule_seconds(trip.get("dep"))
                    if call_arrival is None or trip_departure is None:
                        continue
                    alight_id = str(call["stop_id"])
                    if alight_id == stop_id or alight_id in visited:
                        continue
                    leg = {
                        "route_id": trip["route_id"], "headsign": trip["headsign"],
                        "line_number": trip.get("line_number"),
                        "line_description": trip.get("line_description", ""),
                        "dep": trip_departure, "arr": call_arrival,
                        "board_stop": stop_id, "alight_stop": alight_id,
                    }
                    full_chain = [*chain, leg]
                    if alight_id in destination_ids:
                        if len(full_chain) < 2:
                            continue  # direct service is already returned first
                        key = tuple((l["route_id"], l["dep"], l["board_stop"],
                                     l["alight_stop"]) for l in full_chain)
                        if key not in seen:
                            seen.add(key)
                            option = _transfer_option(
                                network, spec, operator_label, ends, full_chain)
                            if option is not None:
                                results.append(option)
                    elif transfers_so_far < 1 and alight_id in graph_stops:
                        state = (alight_id, call_arrival, full_chain,
                                 visited | {alight_id})
                        previous = best_next.get(alight_id)
                        if previous is None or call_arrival < previous[1]:
                            best_next[alight_id] = state
        frontier = sorted(best_next.values(), key=lambda s: s[1])[:60]
        if not frontier:
            break
    return sorted(results, key=lambda o: (o["_dep"], o["_arr"]))[:5]


def _transfer_option(network, spec, operator_label, ends, chain):
    views = []
    for leg in chain:
        info = network.route_names.get(leg["route_id"], {})
        views.append({
            "vehicle": "MST light rail" if spec.mode == "mts" else info.get("vehicle", "Bus"),
            "line": leg.get("line_number") or info.get("number") or leg["route_id"],
            "line_description": leg.get("line_description", info.get("description", "")),
            "route_id": leg["route_id"], "headsign": leg["headsign"],
            "departs": format_time(leg["dep"]), "arrives": format_time(leg["arr"]),
            "board": _stop_name(network, leg["board_stop"]),
            "alight": _stop_name(network, leg["alight_stop"]),
            "board_stop_id": leg["board_stop"],
            "alight_stop_id": leg["alight_stop"],
            "mode": spec.mode,
        })
    if not all(spec.accepts(view["vehicle"]) for view in views):
        return None
    first, last = chain[0], chain[-1]
    return {
        "mode": spec.mode, "agent": spec.label, "label": operator_label,
        "vehicle": views[0]["vehicle"],
        "line": " → ".join(str(view["line"]) for view in views),
        "line_description": " · ".join(view["line_description"] for view in views
                                         if view["line_description"]),
        "route_id": " → ".join(view["route_id"] for view in views),
        "headsign": views[-1]["headsign"],
        "departs": format_time(first["dep"]), "arrives": format_time(last["arr"]),
        "duration_minutes": (last["arr"] - first["dep"]) // 60,
        "board": views[0]["board"], "alight": views[-1]["alight"],
        "stops": [], "legs": views, "transfers": max(0, len(views) - 1),
        "transfer_info": (f"{len(views) - 1} change(s); every leg and wait is scheduled."
                          if len(views) > 1 else "Direct · 0 changes."),
        "walk_from_km": ends["walk_from_km"], "walk_to_km": ends["walk_to_km"],
        "_dep": first["dep"], "_arr": last["arr"],
    }


def _graph_hint(registry, state):
    """Informational only: does the sampled graph link the stops with changes?"""
    hints = []
    for mode, ends in (state.get("resolved") or {}).items():
        route = find_route(registry.get(mode), ends["origin_ids"],
                           ends["destination_ids"])
        if route["status"] == "ok" and route["legs"]:
            lines = ", ".join(sorted({str(l["bus_number"]) for l in route["legs"]}))
            hints.append(
                f"{registry.labels.get(mode, mode)}: the graph links these places "
                f"through line(s) {lines}"
                + (f" with {route['num_transfers']} change(s)" if route["num_transfers"] else "")
                + ", but no timetable-validated journey was found in the window.")
    return hints
