"""Read-only access to a SQLite index of scheduled GTFS times (see build_timetable.py)."""

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

DAY = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    trip_idx INTEGER PRIMARY KEY,
    route_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    headsign TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stop_times (
    trip_idx INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    stop_id TEXT NOT NULL,
    arr INTEGER NOT NULL,
    dep INTEGER NOT NULL,
    PRIMARY KEY (trip_idx, seq)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS calendar (
    date TEXT NOT NULL,
    service_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_stop_dep ON stop_times (stop_id, dep);
CREATE INDEX IF NOT EXISTS idx_calendar ON calendar (date, service_id);
CREATE INDEX IF NOT EXISTS idx_trips_service ON trips (service_id);
"""


def lisbon_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Lisbon"))
    except Exception:
        return datetime.now()


def format_time(seconds: int) -> str:
    """HH:MM from seconds since local midnight; marks times after midnight."""
    hours, minutes = (seconds // 3600) % 24, (seconds % 3600) // 60
    label = f"{hours:02d}:{minutes:02d}"
    days = seconds // DAY
    if days >= 1:
        label += f" (+{days} day)"
    return label


class Timetable:
    def __init__(self, path):
        self.path = Path(path)
        self._conn = None
        self._services: dict[str, bool] = {}

    @property
    def available(self) -> bool:
        return self.path.exists()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            uri = f"{self.path.resolve().as_uri()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        return self._conn

    def has_service_on(self, day: date) -> bool:
        key = day.strftime("%Y%m%d")
        if key not in self._services:
            row = self._db().execute(
                "SELECT 1 FROM calendar WHERE date = ? LIMIT 1", (key,)).fetchone()
            self._services[key] = row is not None
        return self._services[key]

    def date_range(self) -> tuple[str, str] | None:
        row = self._db().execute("SELECT MIN(date), MAX(date) FROM calendar").fetchone()
        return (row[0], row[1]) if row and row[0] else None

    def service_days(self, base_day: date) -> list[date]:
        """Service dates a window starting on base_day can touch (yesterday's
        service runs past midnight, tomorrow's starts just after)."""
        return [base_day + timedelta(days=o) for o in (-1, 0, 1)
                if self.has_service_on(base_day + timedelta(days=o))]

    def departures_between(self, from_stops: list[str], to_stops: list[str],
                           base_day: date, start: int, end: int) -> list[dict]:
        """Every direct scheduled trip boarding at any from_stop within [start, end].

        The trip must call at one of to_stops later in its stop sequence and
        its service must run on the date it belongs to. Times are seconds since
        midnight of base_day (GTFS times over 24h run into the next day).
        Each result carries the real stop sequence of the trip between the two.
        """
        if not from_stops or not to_stops:
            return []
        found = {}
        marks_from = ",".join("?" * len(from_stops))
        marks_to = ",".join("?" * len(to_stops))
        for offset in (-1, 0, 1):
            day = base_day + timedelta(days=offset)
            low, high = start - offset * DAY, end - offset * DAY
            if high < 0:
                continue
            sql = (
                "SELECT a.trip_idx, t.route_id, t.headsign, a.seq, a.stop_id, a.dep, "
                "b.seq, b.stop_id, b.arr "
                "FROM stop_times a "
                "JOIN trips t ON t.trip_idx = a.trip_idx "
                "JOIN calendar c ON c.service_id = t.service_id AND c.date = ? "
                "JOIN stop_times b ON b.trip_idx = a.trip_idx AND b.seq > a.seq "
                f"AND b.stop_id IN ({marks_to}) "
                f"WHERE a.stop_id IN ({marks_from}) AND a.dep BETWEEN ? AND ? "
                "ORDER BY a.dep, b.seq"
            )
            params = [day.strftime("%Y%m%d"), *to_stops, *from_stops, max(low, 0), high]
            for (trip_idx, route, headsign, a_seq, a_stop, dep,
                 b_seq, b_stop, arr) in self._db().execute(sql, params):
                key = (offset, trip_idx)
                # Keep the earliest boarding, then the earliest alighting stop.
                if key in found and (found[key]["board_seq"], found[key]["alight_seq"]) <= (a_seq, b_seq):
                    continue
                found[key] = {
                    "trip_idx": trip_idx, "route_id": route, "headsign": headsign,
                    "board_seq": a_seq, "board_stop": a_stop,
                    "alight_seq": b_seq, "alight_stop": b_stop,
                    "dep": dep + offset * DAY, "arr": arr + offset * DAY,
                    "offset": offset, "service_date": day.isoformat(),
                }
        trips = [t for t in found.values() if start <= t["dep"] <= end]
        trips.sort(key=lambda t: (t["dep"], t["arr"], t["route_id"]))
        for trip in trips:
            trip["stops"] = self.stop_sequence(
                trip["trip_idx"], trip["board_seq"], trip["alight_seq"], trip["offset"])
        return trips

    def stop_sequence(self, trip_idx: int, first_seq: int, last_seq: int,
                      offset: int = 0) -> list[dict]:
        rows = self._db().execute(
            "SELECT stop_id, arr, dep FROM stop_times "
            "WHERE trip_idx = ? AND seq BETWEEN ? AND ? ORDER BY seq",
            (trip_idx, first_seq, last_seq)).fetchall()
        return [{"stop_id": s, "arr": a + offset * DAY, "dep": d + offset * DAY}
                for s, a, d in rows]

    def departures_from(self, stop_ids: list[str], base_day: date,
                        start: int, end: int, limit_per_stop: int = 8) -> list[dict]:
        """Scheduled trips boarding at these stops, with their real downstream calls."""
        found = {}
        for stop_id in dict.fromkeys(map(str, stop_ids)):
            for offset in (-1, 0, 1):
                day = base_day + timedelta(days=offset)
                low, high = start - offset * DAY, end - offset * DAY
                if high < 0:
                    continue
                rows = self._db().execute(
                    "SELECT a.trip_idx, t.route_id, t.headsign, a.seq, a.stop_id, a.dep "
                    "FROM stop_times a JOIN trips t ON t.trip_idx = a.trip_idx "
                    "JOIN calendar c ON c.service_id = t.service_id AND c.date = ? "
                    "WHERE a.stop_id = ? AND a.dep BETWEEN ? AND ? "
                    "ORDER BY a.dep LIMIT ?",
                    (day.strftime("%Y%m%d"), stop_id, max(0, low), high,
                     limit_per_stop),
                ).fetchall()
                for trip_idx, route, headsign, seq, board, dep in rows:
                    key = (offset, trip_idx, seq)
                    if key in found:
                        continue
                    found[key] = {
                        "trip_idx": trip_idx, "route_id": route,
                        "headsign": headsign, "board_stop": board,
                        "board_seq": seq, "dep": dep + offset * DAY,
                        "service_date": day.isoformat(),
                        "stops": self.stop_sequence(trip_idx, seq, 2**31 - 1, offset),
                    }
        return sorted(found.values(), key=lambda trip: (trip["dep"], trip["route_id"]))

    def upcoming(self, from_stop: str, to_stop: str, base_day: date,
                 start: int, end: int, route_id: str | None = None,
                 limit: int = 5) -> list[dict]:
        """Direct trips from one stop to a later stop on the same trip.

        Times are seconds since midnight of base_day. Trips from the previous
        service day running past midnight, and from the next day, are included.
        """
        found = {}
        for offset in (-1, 0, 1):
            day = base_day + timedelta(days=offset)
            low, high = start - offset * DAY, end - offset * DAY
            if high < 0:
                continue
            sql = (
                "SELECT t.route_id, t.headsign, a.dep, b.arr, a.trip_idx "
                "FROM stop_times a "
                "JOIN trips t ON t.trip_idx = a.trip_idx "
                "JOIN calendar c ON c.service_id = t.service_id AND c.date = ? "
                "JOIN stop_times b ON b.trip_idx = a.trip_idx AND b.seq > a.seq "
                "AND b.stop_id = ? "
                "WHERE a.stop_id = ? AND a.dep BETWEEN ? AND ? "
            )
            params = [day.strftime("%Y%m%d"), to_stop, from_stop, max(low, 0), high]
            if route_id:
                sql += "AND t.route_id = ? "
                params.append(route_id)
            sql += "ORDER BY a.dep LIMIT ?"
            params.append(limit * 3)
            for route, headsign, dep, arr, trip_idx in self._db().execute(sql, params):
                key = (offset, trip_idx)
                if key not in found:
                    found[key] = {
                        "route_id": route, "headsign": headsign,
                        "dep": dep + offset * DAY, "arr": arr + offset * DAY,
                    }
        trips = sorted(found.values(), key=lambda t: (t["dep"], t["arr"]))
        return [t for t in trips if t["dep"] >= start][:limit]
