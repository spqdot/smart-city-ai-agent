from ..data import TransportNetwork


def validate_stop_ids(network: TransportNetwork, stop_ids: list[str]) -> dict:
    """Check that stop IDs exist in the dataset and in the routing graph."""
    results = []
    for stop_id in stop_ids:
        info = network.stop_info(str(stop_id))
        if info is None:
            results.append({"stop_id": str(stop_id), "valid": False,
                            "problem": "unknown_stop_id"})
        elif not info["in_graph"]:
            results.append({**info, "valid": False, "problem": "not_in_graph"})
        else:
            results.append({**info, "valid": True, "problem": None})

    return {
        "status": "ok" if results and all(r["valid"] for r in results) else "invalid",
        "stops": results,
    }
