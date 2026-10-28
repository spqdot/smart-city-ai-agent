from datetime import date

from ..data import TransportNetwork
from ..timetable import DAY, Timetable, format_time, lisbon_now
from .routing import find_route

TRANSFER_BUFFER = 120  # seconds to change buses
SEARCH_HORIZON = DAY
MAX_TRANSFER_WAIT = 90 * 60  # longest sensible wait when changing vehicle
MAX_OPTIONS = 3


def _parse_request(depart_time, date_text, now):
    day = date.fromisoformat(date_text) if date_text else now.date()
    if depart_time:
        hours, minutes = (int(p) for p in depart_time.split(":")[:2])
        if not (0 <= hours < 24 and 0 <= minutes < 60):
            raise ValueError("time out of range")
        start = hours * 3600 + minutes * 60
    else:
        start = now.hour * 3600 + now.minute * 60
    return day, start


def _leg_view(network, leg, trip):
    info = network.route_names.get(trip["route_id"], {})
    return {
        "bus_number": info.get("number") or trip["route_id"],
        "vehicle": info.get("vehicle", "Bus"),
        "bus_description": info.get("description", ""),
        "headsign": trip["headsign"],
        "from_name": leg["from_name"],
        "to_name": leg["to_name"],
        "departs": format_time(trip["dep"]),
        "arrives": format_time(trip["arr"]),
        "ride_minutes": max(0, (trip["arr"] - trip["dep"]) // 60),
    }


def _plan_itineraries(network, timetable, legs, day, start):
    first = legs[0]
    # The graph only fixes the stops; any bus serving them in the timetable counts.
    candidates = timetable.upcoming(
        first["from_stop_id"], first["to_stop_id"], day, start,
        start + SEARCH_HORIZON, None, limit=MAX_OPTIONS)

    itineraries = []
    for trip in candidates:
        chain, views, arrival = [trip], [_leg_view(network, first, trip)], trip["arr"]
        for leg in legs[1:]:
            options = timetable.upcoming(
                leg["from_stop_id"], leg["to_stop_id"], day,
                arrival + TRANSFER_BUFFER, arrival + MAX_TRANSFER_WAIT,
                None, limit=1)
            if not options:
                chain = None
                break
            chain.append(options[0])
            views.append(_leg_view(network, leg, options[0]))
            arrival = options[0]["arr"]
        if chain:
            itineraries.append({
                "departs": format_time(chain[0]["dep"]),
                "arrives": format_time(arrival),
                "duration_minutes": (arrival - chain[0]["dep"]) // 60,
                "legs": views,
                "_chain": chain,
            })
    return itineraries


def _other_options(network, timetable, legs, day, start, window, itineraries):
    """Any bus running directly between each leg's stops within the window."""
    results = []
    for index, leg in enumerate(legs):
        leg_start = start
        if itineraries:
            previous = itineraries[0]["_chain"][index - 1] if index else None
            leg_start = previous["arr"] + TRANSFER_BUFFER if previous else start
        trips = timetable.upcoming(
            leg["from_stop_id"], leg["to_stop_id"], day,
            leg_start, leg_start + window * 60, None, limit=10)
        results.append({
            "leg": index + 1,
            "from_name": leg["from_name"],
            "to_name": leg["to_name"],
            "window_minutes": window,
            "buses": [_leg_view(network, leg, t) for t in trips],
        })
    return results


def _direct_itineraries(network, timetable, origins, destinations, day, start):
    """Earliest direct buses between any origin and destination stop."""
    names = network.graph.nodes
    found = []
    for o in origins[:20]:
        for d in destinations[:20]:
            for trip in timetable.upcoming(o, d, day, start, start + SEARCH_HORIZON,
                                           None, limit=MAX_OPTIONS):
                found.append((trip["dep"], o, d, trip))
    found.sort(key=lambda item: item[0])
    itineraries, seen = [], set()
    for _, o, d, trip in found:
        key = (trip["dep"], trip["route_id"])
        if key in seen:
            continue
        seen.add(key)
        leg = {"from_name": names[o].get("name", "") if o in names else "",
               "to_name": names[d].get("name", "") if d in names else ""}
        itineraries.append({
            "departs": format_time(trip["dep"]),
            "arrives": format_time(trip["arr"]),
            "duration_minutes": (trip["arr"] - trip["dep"]) // 60,
            "legs": [_leg_view(network, leg, trip)],
        })
        if len(itineraries) == MAX_OPTIONS:
            break
    return itineraries


def find_timetable(
    network: TransportNetwork,
    timetable: Timetable | None,
    origin_stop_ids: list[str],
    destination_stop_ids: list[str],
    allowed_ids: set[str] | None = None,
    depart_time: str | None = None,
    date_text: str | None = None,
    window_minutes: int = 60,
    now=None,
) -> dict:
    """Route plus scheduled departure and arrival times, from the GTFS timetable."""
    route = find_route(network, origin_stop_ids, destination_stop_ids, allowed_ids)
    if route["status"] != "ok":
        return route

    if timetable is None or not timetable.available:
        return {"status": "timetable_unavailable", "route": route,
                "message": ("No timetable index found. Run "
                            "`python build_timetable.py` once to create it.")}

    try:
        day, start = _parse_request(depart_time, date_text, now or lisbon_now())
    except ValueError:
        return {"status": "invalid_time", "route": route,
                "message": "Use depart_time as HH:MM and date as YYYY-MM-DD."}

    window = max(10, min(int(window_minutes or 60), 480))
    if not timetable.has_service_on(day):
        span = timetable.date_range()
        detail = f" The timetable covers {span[0]} to {span[1]}." if span else ""
        return {"status": "no_service_on_date", "route": route,
                "message": f"No scheduled service data for {day.isoformat()}.{detail}"}

    legs = route["legs"]
    itineraries = _plan_itineraries(network, timetable, legs, day, start)
    others = _other_options(network, timetable, legs, day, start, window, itineraries)
    for itinerary in itineraries:
        itinerary.pop("_chain")

    base = {
        "route": route,
        "date": day.isoformat(),
        "depart_after": format_time(start),
        "note": ("Scheduled times from the published timetable, not live "
                 "arrivals; delays are not reflected."),
    }
    if not itineraries:
        direct = _direct_itineraries(
            network, timetable, origin_stop_ids, destination_stop_ids, day, start)
        if direct:
            return {**base, "status": "ok", "itineraries": direct,
                    "other_options": others,
                    "note": base["note"] + (" These are direct buses found in the "
                                            "timetable; the graph route above was "
                                            "not served by a single real trip.")}
        return {**base, "status": "no_departures", "other_options": others,
                "message": ("No matching departures in the next 24 hours for this "
                            "route in the timetable.")}
    return {**base, "status": "ok", "itineraries": itineraries,
            "other_options": others}
