"""Build map layers for a route returned by the find_route tool."""

LEG_COLORS = [
    [214, 39, 40], [31, 119, 180], [44, 160, 44], [255, 127, 14], [148, 103, 189],
]


def route_map_data(route: dict) -> dict | None:
    """Convert a find_route result into path and stop records for drawing."""
    paths, stops = [], []
    for index, leg in enumerate(route.get("legs", [])):
        points = [p for p in leg.get("points", []) if p.get("lat") is not None]
        if len(points) < 2:
            continue
        color = LEG_COLORS[index % len(LEG_COLORS)]
        label = f"{leg.get('vehicle', 'Bus')} {leg['bus_number']}: {leg.get('bus_description', '')}"
        paths.append({"path": [[p["lon"], p["lat"]] for p in points],
                      "color": color, "label": label})
        for position, point in enumerate(points):
            is_end = position in (0, len(points) - 1)
            stops.append({
                "lon": point["lon"], "lat": point["lat"], "color": color,
                "radius": 70 if is_end else 25,
                "label": f"{point['name']} ({label})",
            })
    if not paths:
        return None
    all_points = [p for path in paths for p in path["path"]]
    return {
        "paths": paths,
        "stops": stops,
        "center": (sum(p[1] for p in all_points) / len(all_points),
                   sum(p[0] for p in all_points) / len(all_points)),
        "bounds": (min(p[0] for p in all_points), min(p[1] for p in all_points),
                   max(p[0] for p in all_points), max(p[1] for p in all_points)),
    }


def zoom_for(bounds) -> float:
    """Rough zoom level so that the whole route fits the view."""
    import math

    span = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 0.005)
    return max(8.0, min(15.0, math.log2(360 / span) - 1.2))


def journey_map_data(origin: dict, destination: dict, options: list[dict], registry) -> dict:
    """Map selected places and, when available, the first scheduled itinerary."""
    places = []
    for label, place in (("Origin", origin), ("Destination", destination)):
        if place.get("lat") is not None and place.get("lon") is not None:
            places.append({"name": place.get("name", label), "lat": float(place["lat"]),
                           "lon": float(place["lon"]), "kind": label})

    paths = []
    if len(places) == 2 and options:
        option = options[0]
        points = []
        for stop in option.get("stops", []):
            point = _scheduled_point(registry, option.get("mode"), stop.get("stop_id"),
                                     stop.get("name"))
            if point and (not points or (point["lon"], point["lat"]) !=
                          (points[-1]["lon"], points[-1]["lat"])):
                points.append(point)
        if option.get("legs"):
            points = []
            for leg in option["legs"]:
                for key in ("board_stop_id", "alight_stop_id"):
                    point = _scheduled_point(registry, leg.get("mode", option.get("mode")), leg.get(key),
                                             leg.get("board" if key == "board_stop_id" else "alight"))
                    if point and (not points or (point["lon"], point["lat"]) !=
                                  (points[-1]["lon"], points[-1]["lat"])):
                        points.append(point)
        # Include selected endpoints while preserving actual scheduled intermediate calls.
        if points:
            points = [places[0], *[p for p in points if
                      (p["lon"], p["lat"]) not in {(places[0]["lon"], places[0]["lat"]),
                                                   (places[1]["lon"], places[1]["lat"])}], places[1]]
            paths.append({"path": [[p["lon"], p["lat"]] for p in points],
                          "color": LEG_COLORS[0], "label": "Scheduled itinerary"})
    if len(places) == 2 and not paths:
        # Keep the map useful when the timetable has no matching journey. This
        # connector is deliberately labeled as orientation only, not as transit.
        paths.append({"path": [[p["lon"], p["lat"]] for p in places],
                      "color": [110, 110, 110],
                      "label": "Selected places only; not a scheduled route"})

    all_places = list(places)
    for path in paths:
        all_places.extend({"lon": x, "lat": y} for x, y in path["path"])
    if not all_places:
        return {"paths": [], "stops": [], "center": (38.72, -9.14),
                "bounds": (-9.5, 38.4, -8.8, 39.0)}
    lons = [p["lon"] for p in all_places]
    lats = [p["lat"] for p in all_places]
    return {"paths": paths, "stops": [
                {"lon": p["lon"], "lat": p["lat"], "color":
                 [214, 39, 40] if p.get("kind") == "Origin" else [31, 119, 180],
                 "radius": 110, "label": f"{p.get('kind', 'Scheduled stop')}: {p.get('name', '')}"}
                for p in places],
            "center": (sum(lats) / len(lats), sum(lons) / len(lons)),
            "bounds": (min(lons), min(lats), max(lons), max(lats))}


def _scheduled_point(registry, mode, stop_id, name):
    if mode and stop_id is not None:
        network = registry.get(mode)
        row = network._stop_index.get(str(stop_id)) if network else None
        if row and row.get("lat") == row.get("lat") and row.get("lon") == row.get("lon"):
            return {"lat": float(row["lat"]), "lon": float(row["lon"]), "name": name or str(stop_id)}
    return None


def build_journey_deck(origin: dict, destination: dict, options: list[dict], registry):
    """Create a map of selected endpoints and the best timetable result, if any."""
    import pydeck as pdk

    data = journey_map_data(origin, destination, options, registry)
    return pdk.Deck(
        map_style="light",
        initial_view_state=pdk.ViewState(latitude=data["center"][0],
            longitude=data["center"][1], zoom=zoom_for(data["bounds"])),
        layers=[
            pdk.Layer("PathLayer", data["paths"], get_path="path", get_color="color",
                      get_width=6, width_min_pixels=4, pickable=True),
            pdk.Layer("ScatterplotLayer", data["stops"], get_position=["lon", "lat"],
                      get_fill_color="color", get_line_color=[255, 255, 255],
                      get_radius="radius", radius_min_pixels=5, stroked=True,
                      line_width_min_pixels=1, pickable=True),
        ], tooltip={"text": "{label}"})


def build_deck(route: dict):
    """A pydeck map of the route, or None if no coordinates are available."""
    import pydeck as pdk

    data = route_map_data(route)
    if data is None:
        return None
    lat, lon = data["center"]
    return pdk.Deck(
        map_style="light",
        initial_view_state=pdk.ViewState(
            latitude=lat, longitude=lon, zoom=zoom_for(data["bounds"])),
        layers=[
            pdk.Layer("PathLayer", data["paths"], get_path="path",
                      get_color="color", get_width=6, width_min_pixels=4,
                      pickable=True),
            pdk.Layer("ScatterplotLayer", data["stops"], get_position=["lon", "lat"],
                      get_fill_color="color", get_line_color=[255, 255, 255],
                      get_radius="radius", radius_min_pixels=4, stroked=True,
                      line_width_min_pixels=1, pickable=True),
        ],
        tooltip={"text": "{label}"},
    )
