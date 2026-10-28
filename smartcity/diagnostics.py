"""Read-only status checks for locally indexed operator feeds."""

import sqlite3
from datetime import date

from .timetable import lisbon_now


def feed_status(mode, network, timetable, today: date | None = None) -> dict:
    """Summarize whether a mode has an intact, current local timetable index."""
    today = today or lisbon_now().date()
    result = {
        "mode": mode,
        "operator": network.mode if network else mode,
        "status": "missing",
        "stops": 0,
        "routes": 0,
        "trips": 0,
        "stop_times": 0,
        "calendar_start": None,
        "calendar_end": None,
        "last_refresh": "Unknown (no refresh timestamp stored)",
        "reason": "Local network files or timetable index are missing.",
    }
    if network is not None:
        result["stops"] = len(network.stops)
        result["routes"] = len(network.route_names)
    if timetable is None or not timetable.available:
        return result
    source_status = getattr(timetable, "source_status", None)
    if isinstance(source_status, dict):
        result.update(
            status="available",
            trips=sum(len(entry["data"]["data"]["times"])
                      for entry in timetable._snapshot["schedules"].values()),
            stop_times=sum(len(entry["data"]["data"]["times"])
                           * len(entry["data"]["data"]["stations"])
                           for entry in timetable._snapshot["schedules"].values()),
            calendar_start="seasonal",
            calendar_end="weekday/Saturday/Sunday-holiday",
            last_refresh=source_status.get("retrieved_at", "Unknown"),
            reason=(source_status.get("calendar_model", "") + " "
                    + "Operational alerts are not included."),
        )
        return result
    try:
        conn = sqlite3.connect(f"file:{timetable.path.resolve().as_posix()}?mode=ro", uri=True)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"trips", "stop_times", "calendar"}
        if not required.issubset(tables):
            conn.close()
            result.update(status="invalid", reason="Timetable database is missing required tables.")
            return result
        result["trips"] = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        result["stop_times"] = conn.execute("SELECT COUNT(*) FROM stop_times").fetchone()[0]
        span = conn.execute("SELECT MIN(date), MAX(date) FROM calendar").fetchone()
        result["calendar_start"], result["calendar_end"] = span
        conn.close()
        if not result["stops"] or not result["routes"] or not result["trips"] or not result["stop_times"]:
            result.update(status="invalid", reason="One or more required feed tables are empty.")
        elif not span[1]:
            result.update(status="invalid", reason="No active service dates are indexed.")
        elif span[1] < today.strftime("%Y%m%d"):
            result.update(status="outdated", reason=f"Service calendar ended on {span[1]}.")
        else:
            result.update(status="available", reason="Feed files and timetable index are readable.")
    except (sqlite3.Error, OSError) as exc:
        result.update(status="invalid", reason=f"Cannot read timetable index: {type(exc).__name__}: {exc}")
    return result
