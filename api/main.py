"""FastAPI service used by the Vercel journey planner frontend."""

from functools import lru_cache
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from smartcity.journey_service import search_journey
from smartcity.map_view import journey_map_data
from smartcity.modes import default_registry, match_stop_options, municipality_area_option, stop_options


registry = default_registry(load_settings())
app = FastAPI(title="Lisbon Journey Planner API", version="1.0.0")

allowed_origins = [origin.strip() for origin in os.getenv(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class PlaceSelection(BaseModel):
    name: str
    mode: str | None = None
    stop_id: str | None = None
    stop_ids: list[str] = Field(default_factory=list)
    stop_ids_by_mode: dict[str, list[str]] | None = None
    area: bool = False
    lat: float | None = None
    lon: float | None = None
    municipality: str | None = None
    area_stop_counts: dict[str, int] | None = None


class JourneyRequest(BaseModel):
    origin: PlaceSelection
    destination: PlaceSelection


@lru_cache(maxsize=1)
def _all_stop_choices():
    return stop_options(registry, "Any available transport")


def _public_stop(stop: dict, label: str | None = None) -> dict:
    return {**stop, "label": label or stop.get("name", "Stop")}


def _suggestions(query: str, limit: int) -> list[dict]:
    if not query.strip():
        return []
    area = municipality_area_option(registry, query, target_modes=set(registry.modes))
    if area:
        count = sum(area.get("area_stop_counts", {}).values())
        modes = ", ".join(area.get("area_stop_counts", {}))
        label = f"{area['name']} area · {count} stops · {modes}"
        return [_public_stop(area, label)]

    choices = _all_stop_choices()
    matches = match_stop_options(choices, query, limit=limit)
    results = []
    for title in matches:
        stop = choices[title]
        label = f"{stop['name']} · {stop['operator']}"
        if label in {item["label"] for item in results}:
            label += f" · {stop['stop_id']}"
        results.append(_public_stop(stop, label))
    return results


def _validate_place(place: PlaceSelection) -> dict:
    selected = place.dict()
    if place.area:
        mapped = place.stop_ids_by_mode or {}
        valid_ids = {}
        for mode, stop_ids in mapped.items():
            network = registry.get(mode)
            if network is None:
                continue
            valid_ids[mode] = [str(stop_id) for stop_id in stop_ids
                               if network.stop_info(str(stop_id)) is not None]
        if not any(valid_ids.values()):
            raise HTTPException(status_code=422, detail=f"No loaded stops for {place.name}.")
        selected["stop_ids_by_mode"] = valid_ids
        return selected

    if not place.mode or not place.stop_id:
        raise HTTPException(status_code=422, detail="Choose a stop from the suggestions.")
    network = registry.get(place.mode)
    info = network.stop_info(place.stop_id) if network else None
    if info is None:
        raise HTTPException(status_code=422, detail="That stop is not in the selected operator feed.")
    selected["stop_ids"] = [place.stop_id]
    selected["name"] = info["name"]
    selected["lat"] = info.get("lat", selected["lat"])
    selected["lon"] = info.get("lon", selected["lon"])
    return selected


@app.get("/api/health")
def health():
    """Keep process health separate from timetable readiness for hosting checks."""
    return {"status": "ok", "service": "lisbon-journey-planner-api",
            "loaded_modes": registry.modes}


@app.get("/api/stops")
def stops(q: str = Query(default="", max_length=120),
         limit: int = Query(default=12, ge=1, le=30)):
    try:
        return {"query": q, "suggestions": _suggestions(q, limit)}
    except (FileNotFoundError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"Stop data is unavailable. Configure the runtime data bundle: {error}",
        ) from error


@app.post("/api/journeys")
def journeys(request: JourneyRequest):
    origin = _validate_place(request.origin)
    destination = _validate_place(request.destination)
    if origin.get("name") == destination.get("name") and origin.get("mode") == destination.get("mode"):
        raise HTTPException(status_code=422, detail="Choose two different stops.")
    try:
        result = search_journey(registry, origin, destination, "Any available transport")
        result["maps"] = [journey_map_data(origin, destination, [option], registry)
                          for option in result.get("options", [])]
        return result
    except (FileNotFoundError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=503,
            detail=f"Timetable data is unavailable or invalid: {error}",
        ) from error
