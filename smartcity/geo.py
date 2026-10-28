import json
import math
import urllib.parse
import urllib.request

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "smart-city-ai-agent/0.1 (local learning project)"


def nominatim_geocoder(query: str, timeout: float = 6.0) -> dict | None:
    """Free OpenStreetMap geocoding, limited to the Lisbon area. Sends only the place text."""
    params = urllib.parse.urlencode({
        "q": query, "format": "json", "limit": 1,
        "viewbox": "-9.55,39.05,-8.5,38.35", "bounded": 1,
    })
    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            results = json.load(response)
    except (OSError, ValueError):
        return None
    if not results:
        return None
    best = results[0]
    return {"lat": float(best["lat"]), "lon": float(best["lon"]),
            "label": best.get("display_name", query)}


def make_geocoder(name: str):
    if name == "nominatim":
        return nominatim_geocoder
    return None


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance in km; works with scalars or numpy arrays."""
    import numpy as np

    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def nearest_graph_stops(network, lat: float, lon: float,
                        count: int = 10, max_km: float = 2.0):
    stops = network.stops[network.stops["in_graph"]].dropna(subset=["lat", "lon"])
    if stops.empty:
        return []
    distances = haversine_km(lat, lon, stops["lat"].to_numpy(), stops["lon"].to_numpy())
    stops = stops.assign(distance_km=distances).sort_values("distance_km")
    stops = stops[stops["distance_km"] <= max_km].head(count)
    return list(stops.itertuples(index=False))


def candidate_from_nearest(label: str, rows) -> dict:
    return {
        "name": f"Near {label}",
        "municipality": rows[0].municipality_name,
        "stop_ids": [r.stop_id for r in rows],
        "in_graph": True,
        "match": "nearest_stops",
        "nearest_stop": rows[0].stop_name,
        "distance_km": round(float(rows[0].distance_km), 2),
    }
