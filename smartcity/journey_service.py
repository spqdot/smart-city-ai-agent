"""Shared deterministic journey search used by Streamlit and agent tools."""

from datetime import datetime
from typing import Callable

from .modes import NetworkRegistry
from .pipeline import build_pipeline


def search_journey(registry: NetworkRegistry, origin: dict, destination: dict,
                   transport_filter: str,
                   clock: Callable[[], datetime] | None = None) -> dict:
    """Run the one LangGraph schedule search for both the planner and chat."""
    result = build_pipeline(registry, clock)(origin, destination,
                                             transport_filter=transport_filter)
    def place_details(place):
        mode = place.get("mode")
        stop_ids = [str(item) for item in place.get("stop_ids", [])]
        name = place.get("name", "")
        network = registry.get(mode) if mode else None
        if not name and network and stop_ids:
            info = network.stop_info(stop_ids[0])
            name = info["name"] if info else ""
        return {"id": stop_ids[0] if stop_ids else None, "ids": stop_ids,
                "name": name, "selected_mode": mode}

    result["request"] = {
        "origin": place_details(origin),
        "destination": place_details(destination),
        "transport_filter": transport_filter,
        "transport_mode": transport_filter,
    }
    result["operators_searched"] = [
        {"mode": report.get("mode"), "operator": report.get("agent")}
        for report in result.get("agents", [])
        if report.get("status") in {"searched", "ok"}
    ]
    result["data_sources"] = [
        {"mode": report.get("mode"), **report["data_source"]}
        for report in result.get("agents", []) if report.get("data_source")
    ]
    result["validation_status"] = {
        source.get("mode"): source.get("status") for source in result["data_sources"]
    }
    return result
