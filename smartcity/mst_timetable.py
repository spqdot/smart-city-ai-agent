"""Offline reader and importer for Metro Sul do Tejo's published timetable API."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from .data import normalize
from .timetable import DAY
from .window import LISBON

MTS_API = "https://www.mts.pt/mts-timetable-api/"
SEASONS = {1: "summer", 2: "winter"}
DAY_TYPES = {1: "weekday", 2: "saturday", 3: "sunday_holiday"}
API_STATION_ALIASES = {"monte caparica": "monte de caparica",
                       "sao joao baptista": "s joao baptista"}
API_NAME_REPAIRS = {"s�o jo�o baptista": "são joão baptista",
                    "ant�nio gede�o": "antónio gedeão"}


def _easter_sunday(year: int) -> date:
    """Gregorian Easter, used only to classify MTS's Sunday/holiday timetable."""
    a, b = year % 19, year // 100
    c, d, e = year % 100, year // 100 % 4, year % 100 % 4
    f, g = (b + 8) // 25, (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _portugal_holidays(year: int) -> set[date]:
    easter = _easter_sunday(year)
    fixed = {(1, 1), (4, 25), (5, 1), (6, 10), (8, 15), (10, 5),
             (11, 1), (12, 1), (12, 8), (12, 25)}
    holidays = {date(year, month, day) for month, day in fixed}
    holidays.update({easter - timedelta(days=2), easter, easter + timedelta(days=60)})
    holidays.add(date(year, 6, 24))  # Almada municipal holiday
    holidays.add(date(year, 6, 29))  # Seixal municipal holiday
    return holidays


def season_for(day: date) -> int:
    return 1 if (day.month, day.day) >= (7, 15) and (day.month, day.day) <= (9, 7) else 2


def day_type_for(day: date) -> int:
    if day.weekday() == 5:
        return 2
    if day.weekday() == 6 or day in _portugal_holidays(day.year):
        return 3
    return 1


def validate_snapshot(snapshot: dict) -> dict:
    """Validate schedule API structure before it is accepted as local data."""
    lines = snapshot.get("lines", [])
    schedules = snapshot.get("schedules", {})
    if len(lines) != 6:
        raise ValueError(f"MTS schedule must contain 6 directed lines, found {len(lines)}")
    if len(schedules) != 36:
        raise ValueError(f"MTS schedule must contain 36 day/season combinations, found {len(schedules)}")
    calls = departures = 0
    for key, entry in schedules.items():
        response = entry.get("data", {})
        data = response.get("data", {})
        if response.get("status") != 0 or not data.get("stations") or not data.get("times"):
            raise ValueError(f"Invalid or empty MTS timetable response: {key}")
        if str(data.get("station_id")) != str(data["stations"][0].get("id")):
            raise ValueError(f"MTS timetable origin does not match first stop: {key}")
        for station in data["stations"]:
            if not str(station.get("id", "")) or not str(station.get("time_diff", "")).isdigit():
                raise ValueError(f"Invalid MTS station/time offset in {key}")
        for item in data["times"]:
            try:
                start = int(item["start_time"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"Invalid MTS departure time in {key}") from None
            if start < 0:
                raise ValueError(f"Negative MTS departure time in {key}")
            departures += 1
        calls += len(data["stations"])
    return {"directed_lines": len(lines), "day_season_tables": len(schedules),
            "station_calls_per_table": calls, "scheduled_start_times": departures}


def fetch_snapshot(output: Path) -> dict:
    """Download the MTS public schedule API and write its raw, structured responses."""
    import requests

    session = requests.Session()
    session.headers["User-Agent"] = "SmartCityAgent/1.0 (local public transport schedule importer)"
    lines_response = session.get(MTS_API + "lines", timeout=30)
    lines_response.raise_for_status()
    lines_payload = lines_response.json()
    if lines_payload.get("status") != 0:
        raise ValueError("MTS API did not return its line list successfully")
    lines = lines_payload.get("data", [])
    schedules = {}
    station_lists = {}
    for line in lines:
        line_id = int(line["id"])
        response = session.post(MTS_API + "stations", data={"line_id": line_id}, timeout=30)
        response.raise_for_status()
        stations = response.json()
        if not stations:
            raise ValueError(f"MTS line {line_id} has no stations")
        station_lists[str(line_id)] = stations
        for season in SEASONS:
            for day_type in DAY_TYPES:
                key = f"{line_id}:{season}:{day_type}"
                response = session.post(MTS_API + "search", data={
                    "line": line_id, "stations": stations[0]["id"],
                    "season": season, "day_type": day_type,
                }, timeout=30)
                response.raise_for_status()
                schedules[key] = {"line_id": line_id, "season": season,
                                  "day_type": day_type, "data": response.json()}
    snapshot = {
        "source": "Metro Transportes do Sul public timetable API",
        "api_url": MTS_API,
        "network_feed_url": "https://mts.pt/imt/MTS-20240129.zip",
        "retrieved_at": datetime.now(LISBON).isoformat(),
        "calendar_model": "MTS provides seasonal weekday/Saturday/Sunday-holiday tables; "
                          "date-specific exceptions are not exposed by this API.",
        "operational_alerts_included": False,
        "lines": lines, "stations_by_line": station_lists, "schedules": schedules,
    }
    validate_snapshot(snapshot)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


class MSTimetable:
    """Expose cached MTS seasonal timetable tables through the timetable interface."""

    def __init__(self, path: Path, stops_path: Path):
        self.path = Path(path)
        self.stops_path = Path(stops_path)
        self._snapshot = None
        self._stop_id_by_name = {}
        self.last_error = ""
        if self.path.exists() and self.stops_path.exists():
            try:
                self._snapshot = json.loads(self.path.read_text(encoding="utf-8"))
                validate_snapshot(self._snapshot)
                stops = pd.read_csv(self.stops_path, dtype=str, encoding="utf-8",
                                    encoding_errors="replace").fillna("")
                self._stop_id_by_name = {normalize(row.stop_name): str(row.stop_id)
                                         for row in stops.itertuples()}
                self._stop_id_by_name.update({"monte caparica": self._stop_id_by_name.get(
                    "monte de caparica", "")})
                self._check_station_mapping()
            except Exception as error:
                self.last_error = f"MST timetable snapshot is invalid: {type(error).__name__}: {error}"
                self._snapshot = None

    @property
    def available(self) -> bool:
        return self._snapshot is not None

    @property
    def source_status(self) -> dict:
        if not self.available:
            return {"source": "MTS public timetable API", "status": "unavailable",
                    "reason": self.last_error or "Run import_mst.py to cache the schedule."}
        return {"source": self._snapshot["source"], "status": "validated_local_snapshot",
                "retrieved_at": self._snapshot["retrieved_at"],
                "calendar_model": self._snapshot["calendar_model"],
                "operational_alerts_included": False}

    def _check_station_mapping(self):
        missing = set()
        for entry in self._snapshot["schedules"].values():
            for station in entry["data"]["data"]["stations"]:
                raw_name = station["name"].casefold()
                name = normalize(API_NAME_REPAIRS.get(raw_name, station["name"]))
                name = API_STATION_ALIASES.get(name, name)
                if name not in self._stop_id_by_name:
                    missing.add(station["name"])
        if missing:
            raise ValueError("MTS timetable station(s) do not map to GTFS IDs: "
                             + ", ".join(sorted(missing)))

    def date_range(self):
        return None

    def service_days(self, base_day: date) -> list[date]:
        if not self.available:
            return []
        return [base_day + timedelta(days=offset) for offset in (-1, 0, 1)]

    def has_service_on(self, day: date) -> bool:
        return self.available

    def _trips(self, service_day: date) -> list[dict]:
        if not self.available:
            return []
        season, day_type = season_for(service_day), day_type_for(service_day)
        trips = []
        for line in self._snapshot["lines"]:
            line_id = int(line["id"])
            entry = self._snapshot["schedules"][f"{line_id}:{season}:{day_type}"]
            data = entry["data"]["data"]
            stations = data["stations"]
            route_number = (line.get("name", "").split("LINHA ")[-1].split(" ")[0]
                            if "LINHA " in line.get("name", "") else "")
            # MTS exposes each direction as a separate line ID. Keep that identity
            # so the outbound and return patterns cannot collapse into one route.
            route_id = f"mts:{line_id}"
            headsign = (line.get("name", "").split(" / ")[-1]
                        if " / " in line.get("name", "") else line.get("name", ""))
            previous, day_offset = None, 0
            for time_item in data["times"]:
                start = int(time_item["start_time"])
                if previous is not None and previous - start > 12 * 3600:
                    day_offset += DAY
                elif previous is not None and start < previous:
                    raise ValueError(f"MTS times are not in service-day order for line {line_id}")
                previous = start
                seconds = start + day_offset
                calls, elapsed = [], 0
                for index, station in enumerate(stations):
                    if index:
                        elapsed += int(station["time_diff"]) * 60
                    raw_name = station["name"].casefold()
                    api_name = normalize(API_NAME_REPAIRS.get(raw_name, station["name"]))
                    if api_name not in self._stop_id_by_name:
                        api_name = API_STATION_ALIASES.get(api_name, api_name)
                    stop_id = self._stop_id_by_name[api_name]
                    at = seconds + elapsed
                    calls.append({"stop_id": stop_id, "arr": at, "dep": at})
                trip_id = f"mts:{line_id}:{service_day.isoformat()}:{seconds}"
                trips.append({"trip_id": trip_id, "route_id": route_id,
                              "line_number": route_number,
                              "line_description": line.get("name", ""),
                              "headsign": headsign, "service_date": service_day.isoformat(),
                              "stops": calls})
        return trips

    @staticmethod
    def _service_times(base_day: date, start: int, end: int):
        for offset in (-1, 0, 1):
            day = base_day + timedelta(days=offset)
            yield day, start - offset * DAY, end - offset * DAY

    def departures_between(self, from_stops: list[str], to_stops: list[str],
                           base_day: date, start: int, end: int) -> list[dict]:
        destinations = {str(x) for x in to_stops}
        found = {}
        for day, low, high in self._service_times(base_day, start, end):
            for trip in self._trips(day):
                calls = trip["stops"]
                for origin_index, call in enumerate(calls[:-1]):
                    if call["stop_id"] not in {str(x) for x in from_stops}:
                        continue
                    if not low <= call["dep"] <= high:
                        continue
                    for dest_index in range(origin_index + 1, len(calls)):
                        if calls[dest_index]["stop_id"] in destinations:
                            key = (trip["route_id"], call["stop_id"],
                                   calls[dest_index]["stop_id"], call["dep"], day)
                            found[key] = {**trip, "dep": call["dep"],
                                          "arr": calls[dest_index]["arr"],
                                          "board_stop": call["stop_id"],
                                          "alight_stop": calls[dest_index]["stop_id"],
                                          "stops": calls[origin_index:dest_index + 1]}
                            break
        return sorted(found.values(), key=lambda t: (t["dep"], t["arr"]))

    def departures_from(self, stop_ids: list[str], base_day: date, start: int, end: int,
                        limit_per_stop: int = 8) -> list[dict]:
        origins = {str(x) for x in stop_ids}
        found = {}
        for day, low, high in self._service_times(base_day, start, end):
            for trip in self._trips(day):
                calls = trip["stops"]
                for index, call in enumerate(calls[:-1]):
                    if call["stop_id"] not in origins or not low <= call["dep"] <= high:
                        continue
                    key = (trip["route_id"], call["stop_id"], call["dep"], day)
                    found[key] = {**trip, "dep": call["dep"], "arr": calls[-1]["arr"],
                                  "board_stop": call["stop_id"],
                                  "alight_stop": calls[-1]["stop_id"],
                                  "stops": calls[index:]}
        rows = sorted(found.values(), key=lambda t: (t["dep"], t["arr"]))
        counts, result = {}, []
        for trip in rows:
            stop_id = trip["board_stop"]
            if counts.get(stop_id, 0) >= limit_per_stop:
                continue
            counts[stop_id] = counts.get(stop_id, 0) + 1
            result.append(trip)
        return result

