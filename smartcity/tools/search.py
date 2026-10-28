from ..data import PLACE_ALIASES, TransportNetwork, normalize
from ..geo import candidate_from_nearest, haversine_km, nearest_graph_stops

MAX_IDS_PER_PLACE = 40
FILLER_WORDS = {"city", "centre", "center", "centro", "downtown", "of", "the", "de", "do"}
AREA_COLUMNS = (
    ("municipality", "norm_municipality", "municipality_name"),
    ("locality", "norm_locality", "locality_name"),
    ("parish", "norm_parish", "parish_name"),
)


def _result(status, query, candidates, message, **extra):
    return {"status": status, "query": query, "candidates": candidates,
            "total_candidates": len(candidates), "message": message, **extra}


def _group_by_place(matches, norm_query):
    """One candidate per municipality: stops in the same town are one place."""
    groups = {}
    for row in matches.itertuples(index=False):
        key = row.norm_municipality
        group = groups.setdefault(key, {
            "names": set(), "municipality": row.municipality_name,
            "locality": row.locality_name, "stop_ids": [], "exact": False,
        })
        group["names"].add(row.stop_name)
        group["stop_ids"].append(row.stop_id)
        group["exact"] = group["exact"] or row.norm_name == norm_query
    return list(groups.values())


def _to_candidate(group, query):
    names = sorted(group["names"])
    if len(names) == 1:
        name = names[0]
    else:
        place = group["municipality"]
        name = f"{query.strip().title()} stops ({place})"
    return {
        "name": name,
        "municipality": group["municipality"],
        "stop_names": names[:5],
        "stop_ids": group["stop_ids"][:MAX_IDS_PER_PLACE],
        "in_graph": True,
        "match": "stop_name",
    }


def _area_candidates(network, norm_query, limit, exact=False):
    """Match a town, parish or municipality (e.g. 'Lisbon' -> Lisboa).

    Uses the stops closest to the middle of the area.
    """
    tokens = [PLACE_ALIASES.get(t, t) for t in norm_query.split()
              if t not in FILLER_WORDS]
    if not tokens:
        return []
    stops = network.stops
    usable = stops[stops["in_graph"]].dropna(subset=["lat", "lon"])

    for label, norm_column, name_column in AREA_COLUMNS:
        if exact and label != "municipality":
            continue
        matched = usable[usable[norm_column].apply(
            lambda v: v == norm_query if exact else all(t in v for t in tokens))]
        if matched.empty:
            continue
        candidates = []
        for _, area in list(matched.groupby(norm_column))[:limit]:
            lat, lon = area["lat"].median(), area["lon"].median()
            distance = haversine_km(lat, lon, area["lat"].to_numpy(),
                                    area["lon"].to_numpy())
            nearest = list(area.assign(distance_km=distance)
                           .sort_values("distance_km").head(10)
                           .itertuples(index=False))
            candidate = candidate_from_nearest(
                f"{area.iloc[0][name_column]} centre", nearest)
            candidate["match"] = f"{label}_centre"
            candidates.append(candidate)
        return candidates
    return []


def search_stops(
    network: TransportNetwork,
    query: str,
    municipality: str | None = None,
    limit: int = 10,
    geocoder=None,
) -> dict:
    """Resolve a place name to stop candidates.

    Order: stop names, then town/parish/municipality names, then (optionally)
    a geocoded landmark with the nearest stops. Stops in one neighbourhood are
    returned as a single place, so the user is only asked when places differ.
    """
    norm_query = normalize(query)
    if not norm_query:
        return _result("not_found", query, [], "Empty search text.")
    norm_query = PLACE_ALIASES.get(norm_query, norm_query)

    stops = network.stops
    if not municipality and norm_query in set(stops["norm_municipality"]):
        candidates = _area_candidates(network, norm_query, 1, exact=True)
        if candidates:
            return _result("ok", query, candidates,
                           "Matched a municipality; stops closest to its centre are used.")

    tokens = norm_query.split()
    mask = stops["norm_name"].apply(lambda name: all(t in name for t in tokens))
    if municipality:
        mask &= stops["norm_municipality"].str.contains(
            normalize(municipality), regex=False)
    in_graph = stops[mask & stops["in_graph"]]

    if not in_graph.empty:
        groups = _group_by_place(in_graph, norm_query)
        groups.sort(key=lambda g: (not g["exact"], -len(g["stop_ids"])))
        exact = [g for g in groups if g["exact"]]
        chosen = exact if len(exact) == 1 else groups
        candidates = [_to_candidate(g, query) for g in chosen[:limit]]
        status = "ok" if len(candidates) == 1 else "ambiguous"
        message = ("One matching place." if status == "ok" else
                   "Several different places match. Ask the user which one they mean.")
        return _result(status, query, candidates, message,
                       excluded_not_in_graph=int((mask & ~stops["in_graph"]).sum()))

    if not municipality:
        candidates = _area_candidates(network, norm_query, limit)
        if candidates:
            status = "ok" if len(candidates) == 1 else "ambiguous"
            return _result(status, query, candidates,
                           "Matched an area; the stops closest to its centre are used.")

    if geocoder is not None:
        place = geocoder(query)
        if place:
            rows = nearest_graph_stops(network, place["lat"], place["lon"])
            if rows:
                candidate = candidate_from_nearest(query.strip().title(), rows)
                candidate["geocoded_as"] = place.get("label", query)
                return _result(
                    "ok", query, [candidate],
                    "Place found on the map; the nearest stops in the graph are used.")
            return _result(
                "not_in_graph", query, [],
                "The place was found on the map but no graph stop is within 2 km. "
                + network.sample_note)

    excluded = int(mask.sum())
    if excluded:
        return _result("not_in_graph", query, [],
                       f"{excluded} stop(s) match, but none are in the sampled "
                       "transport graph. " + network.sample_note)
    return _result("not_found", query, [],
                   "No stop, town or place matches this search.")
