"""Import a GTFS zip into a transport mode: stops, routes, full graph and timetable.

Each mode lives in data/modes/<mode>/ with stops.txt, routes.txt,
graph.graphml and timetable.sqlite, so new operators need no code changes.
"""

import json
import sqlite3
import urllib.request
import zipfile
from datetime import date, timedelta
from pathlib import Path

import networkx as nx
import pandas as pd

from .timetable import INDEXES, SCHEMA, lisbon_now

MODES_DIR = Path(__file__).resolve().parents[1] / "data" / "modes"

FEEDS = {
    "carris": {
        "url": "https://gateway.carris.pt/gateway/gtfs/api/v2.11/GTFS",
        "label": "Carris Lisboa (city buses, trams, funiculars)",
    },
    "metro": {
        "url": "https://www.metrolisboa.pt/google_transit/googleTransit.zip",
        "dataset": "https://dados.gov.pt/pt/datasets/informacao-sobre-transportes-publicos-da-cidade-de-lisboa-metropolitano-de-lisboa",
        "label": "Metro de Lisboa",
    },
    "mts": {
        # The publisher URL currently redirects to a 404 page; use its archived
        # official GTFS snapshot from the public MobilityDatabase feed archive.
        "url": "https://files.mobilitydatabase.org/mdb-2408/mdb-2408-202503311930/mdb-2408-202503311930.zip",
        "label": "Metro Sul do Tejo (Almada light rail)",
    },
    "train": {
        "url": "https://publico.cp.pt/gtfs/gtfs.zip",
        "label": "CP trains (Comboios de Portugal)",
    },
    "fertagus": {
        "url": "https://www.fertagus.pt/GTFSTMLzip/Fertagus_GTFS.zip",
        "label": "Fertagus (Lisbon–Setúbal rail)",
    },
}

ROUTE_TYPE_VEHICLE = {0: "Tram", 1: "Metro", 2: "Train", 3: "Bus", 4: "Ferry",
                      5: "Tram", 6: "Gondola", 7: "Funicular"}
CARRIS_TRAMS = {"12E", "15E", "18E", "24E", "25E", "28E"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]
CALENDAR_PAST_DAYS = 7
CALENDAR_FUTURE_DAYS = 400


def download(mode: str, target: Path) -> Path:
    feed = FEEDS[mode]
    url = feed.get("url")
    if not url:
        with urllib.request.urlopen(feed["dataset"], timeout=60) as response:
            url = json.load(response)["resources"][0]["url"]
    target.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, target)
    return target


def vehicle_for(route_type: str, short_name: str, long_name: str,
                mode: str | None = None) -> str:
    try:
        kind = int(route_type)
    except (TypeError, ValueError):
        kind = 3
    if mode == "mts" and kind in {0, 5}:
        vehicle = "MST light rail"
    elif kind in ROUTE_TYPE_VEHICLE:
        vehicle = ROUTE_TYPE_VEHICLE[kind]
    elif 100 <= kind < 200:
        vehicle = "Train"
    else:
        vehicle = "Bus"
    if vehicle == "Bus":
        if short_name in CARRIS_TRAMS:
            return "Tram"
        if any(word in long_name.lower() for word in ("ascensor", "elevador", "funicular")):
            return "Funicular"
    return vehicle


def _read(zf: zipfile.ZipFile, name: str, **kwargs) -> pd.DataFrame | None:
    if name not in zf.namelist():
        return None
    return pd.read_csv(zf.open(name), dtype=str, encoding="utf-8",
                       encoding_errors="replace", **kwargs)


def _seconds(series: pd.Series) -> pd.Series:
    parts = series.str.strip().str.split(":", expand=True).astype("int64")
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def validate_gtfs(zip_path: Path, mode: str | None = None,
                  chunk_size: int = 500_000) -> dict:
    """Validate GTFS references and schedule fields before importing a feed."""
    required = {
        "stops.txt": {"stop_id", "stop_name", "stop_lat", "stop_lon"},
        "routes.txt": {"route_id", "route_type"},
        "trips.txt": {"trip_id", "route_id", "service_id"},
        "stop_times.txt": {"trip_id", "stop_id", "stop_sequence",
                           "arrival_time", "departure_time"},
    }
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        missing = set(required) - names
        if missing:
            raise ValueError(f"GTFS is missing required file(s): {', '.join(sorted(missing))}")
        if not ({"calendar.txt", "calendar_dates.txt"} & names):
            raise ValueError("GTFS must include calendar.txt or calendar_dates.txt")
        for name, columns in required.items():
            header = set(pd.read_csv(zf.open(name), nrows=0).columns)
            absent = columns - header
            if absent:
                raise ValueError(f"{name} is missing required column(s): "
                                 f"{', '.join(sorted(absent))}")

        stops = _read(zf, "stops.txt").fillna("")
        routes = _read(zf, "routes.txt").fillna("")
        trips = _read(zf, "trips.txt", usecols=["trip_id", "route_id", "service_id"]).fillna("")
        calendar = _read(zf, "calendar.txt")
        exceptions = _read(zf, "calendar_dates.txt")

        def unique_ids(frame, column, filename):
            ids = frame[column].astype(str)
            if ids.eq("").any() or ids.duplicated().any():
                raise ValueError(f"{filename} has empty or duplicate {column} values")
            return set(ids)

        stop_ids = unique_ids(stops, "stop_id", "stops.txt")
        route_ids = unique_ids(routes, "route_id", "routes.txt")
        trip_ids = unique_ids(trips, "trip_id", "trips.txt")
        if trips[["route_id", "service_id"]].eq("").any().any():
            raise ValueError("trips.txt has an empty route_id or service_id")
        if not set(trips["route_id"]).issubset(route_ids):
            raise ValueError("trips.txt references an unknown route_id")
        if mode == "metro" and set(routes["route_type"].astype(str)) != {"1"}:
            raise ValueError("Metro feed contains a non-Metro route_type")

        calendar_services = set()
        if calendar is not None:
            cal_required = {"service_id", *WEEKDAYS, "start_date", "end_date"}
            absent = cal_required - set(calendar.columns)
            if absent:
                raise ValueError("calendar.txt is missing required column(s): "
                                 f"{', '.join(sorted(absent))}")
            calendar_services.update(calendar["service_id"].dropna().astype(str))
            for row in calendar.itertuples(index=False):
                try:
                    first, last = _parse(row.start_date), _parse(row.end_date)
                except (TypeError, ValueError):
                    raise ValueError("calendar.txt has an invalid service date") from None
                if first > last:
                    raise ValueError("calendar.txt has a start_date after end_date")
        if exceptions is not None:
            exception_required = {"service_id", "date", "exception_type"}
            absent = exception_required - set(exceptions.columns)
            if absent:
                raise ValueError("calendar_dates.txt is missing required column(s): "
                                 f"{', '.join(sorted(absent))}")
            calendar_services.update(exceptions["service_id"].dropna().astype(str))
            for row in exceptions.itertuples(index=False):
                try:
                    _parse(row.date)
                except (TypeError, ValueError):
                    raise ValueError("calendar_dates.txt has an invalid date") from None
                if str(row.exception_type) not in {"1", "2"}:
                    raise ValueError("calendar_dates.txt has an invalid exception_type")
        missing_services = set(trips["service_id"]) - calendar_services
        if missing_services:
            raise ValueError("trips.txt references a service_id absent from calendar files")

        valid_time = __import__("re").compile(r"^\d{1,3}:[0-5]\d:[0-5]\d$")
        stop_time_count = 0
        for chunk in pd.read_csv(zf.open("stop_times.txt"), dtype=str,
                                 chunksize=chunk_size, usecols=list(required["stop_times.txt"])):
            if chunk[list(required["stop_times.txt"])].isna().any().any():
                raise ValueError("stop_times.txt has missing required timetable values")
            if not set(chunk["trip_id"]).issubset(trip_ids):
                raise ValueError("stop_times.txt references an unknown trip_id")
            if not set(chunk["stop_id"]).issubset(stop_ids):
                raise ValueError("stop_times.txt references an unknown stop_id")
            if not chunk["arrival_time"].map(lambda value: bool(valid_time.fullmatch(value))).all() \
                    or not chunk["departure_time"].map(
                        lambda value: bool(valid_time.fullmatch(value))).all():
                raise ValueError("stop_times.txt contains an invalid timetable time")
            sequence = pd.to_numeric(chunk["stop_sequence"], errors="coerce")
            # GTFS stop_sequence need only increase within a trip; feeds may start at 0.
            if sequence.isna().any() or (sequence < 0).any():
                raise ValueError("stop_times.txt contains an invalid stop_sequence")
            stop_time_count += len(chunk)

    if not stop_time_count:
        raise ValueError("stop_times.txt contains no timetable rows")
    return {"stops": len(stop_ids), "routes": len(route_ids), "trips": len(trip_ids),
            "stop_times": stop_time_count, "service_ids": len(calendar_services)}


def expand_calendar(calendar, calendar_dates, today: date) -> pd.DataFrame:
    """(date, service_id) rows from calendar.txt plus calendar_dates exceptions."""
    first = today - timedelta(days=CALENDAR_PAST_DAYS)
    last = today + timedelta(days=CALENDAR_FUTURE_DAYS)
    rows: set[tuple[str, str]] = set()
    if calendar is not None:
        for row in calendar.itertuples(index=False):
            start = max(_parse(row.start_date), first)
            end = min(_parse(row.end_date), last)
            day = start
            while day <= end:
                if getattr(row, WEEKDAYS[day.weekday()]) == "1":
                    rows.add((day.strftime("%Y%m%d"), row.service_id))
                day += timedelta(days=1)
    if calendar_dates is not None:
        for row in calendar_dates.itertuples(index=False):
            key = (row.date, row.service_id)
            if row.exception_type == "1":
                rows.add(key)
            elif row.exception_type == "2":
                rows.discard(key)
    return pd.DataFrame(sorted(rows), columns=["date", "service_id"])


def _parse(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def import_feed(mode: str, zip_path: Path, out_dir: Path | None = None,
                chunk_size: int = 500_000, log=print) -> Path:
    validate_gtfs(zip_path, mode=mode, chunk_size=chunk_size)
    out_dir = out_dir or MODES_DIR / mode
    out_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        stops = _read(zf, "stops.txt").fillna("")
        routes = _read(zf, "routes.txt").fillna("")
        trips = _read(zf, "trips.txt", usecols=["trip_id", "route_id", "service_id",
                                                 *(["trip_headsign"] if _has(zf, "trips.txt", "trip_headsign") else [])]).fillna("")
        if "trip_headsign" not in trips:
            trips["trip_headsign"] = ""
        calendar = _read(zf, "calendar.txt")
        calendar_dates = _read(zf, "calendar_dates.txt")

        # Platforms collapse to their parent station so lines meet at one stop.
        parent = {}
        if "parent_station" in stops:
            parent = {r.stop_id: r.parent_station for r in stops.itertuples()
                      if r.parent_station}
        station = lambda s: s.map(lambda x: parent.get(x, x))  # noqa: E731

        trips["trip_idx"] = range(len(trips))
        trip_index = trips.set_index("trip_id")["trip_idx"]
        route_of = trips.set_index("trip_idx")["route_id"]

        db_path = out_dir / "timetable.building"
        db_path.unlink(missing_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA synchronous = OFF")
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO trips VALUES (?, ?, ?, ?)",
            trips[["trip_idx", "route_id", "service_id", "trip_headsign"]]
            .itertuples(index=False, name=None))
        cal = expand_calendar(calendar, calendar_dates, lisbon_now().date())
        conn.executemany("INSERT INTO calendar VALUES (?, ?)",
                         cal.itertuples(index=False, name=None))

        edges, used, total, carry = [], set(), 0, None
        reader = pd.read_csv(
            zf.open("stop_times.txt"), dtype=str, chunksize=chunk_size,
            usecols=["trip_id", "stop_id", "stop_sequence",
                     "arrival_time", "departure_time"])
        for chunk in reader:
            chunk = chunk.dropna()
            frame = pd.DataFrame({
                "trip_idx": chunk["trip_id"].map(trip_index),
                "seq": pd.to_numeric(chunk["stop_sequence"], errors="coerce"),
                "stop_id": station(chunk["stop_id"]),
                "arr": _seconds(chunk["arrival_time"]),
                "dep": _seconds(chunk["departure_time"]),
            }).dropna()
            frame[["trip_idx", "seq"]] = frame[["trip_idx", "seq"]].astype("int64")
            conn.executemany(
                "INSERT OR IGNORE INTO stop_times VALUES (?, ?, ?, ?, ?)",
                frame.itertuples(index=False, name=None))
            total += len(frame)
            used.update(frame["stop_id"].unique())

            ordered = frame.sort_values(["trip_idx", "seq"])
            if carry is not None:
                ordered = pd.concat([carry, ordered], ignore_index=True)
            carry = ordered.iloc[[-1]]
            nxt = ordered.shift(-1)
            same = ordered["trip_idx"] == nxt["trip_idx"]
            pairs = pd.DataFrame({
                "u": ordered["stop_id"][same], "v": nxt["stop_id"][same],
                "trip_idx": ordered["trip_idx"][same]})
            pairs = pairs[pairs["u"] != pairs["v"]]
            pairs["route_id"] = pairs["trip_idx"].map(route_of)
            edges.append(pairs.drop_duplicates(["u", "v", "route_id"]))
            log(f"  {total:,} stop times")

        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", [
            ("mode", mode), ("feed", str(zip_path.name))])
        conn.executescript(INDEXES)
        conn.commit()
        conn.close()
        target = out_dir / "timetable.sqlite"
        target.unlink(missing_ok=True)
        db_path.rename(target)

    all_edges = pd.concat(edges).drop_duplicates(["u", "v", "route_id"])
    trip_id_of = trips.set_index("trip_idx")["trip_id"]

    stations = stops.copy()
    stations["stop_id"] = station(stations["stop_id"])
    stations = stations[stations["stop_id"].isin(used)].drop_duplicates("stop_id")
    # A parent station row (if any) names the stop better than a platform row.
    parents = stops[stops["stop_id"].isin(set(parent.values()))].set_index("stop_id")
    for column in ("stop_name", "stop_lat", "stop_lon"):
        stations[column] = [
            parents.at[s, column] if s in parents.index else v
            for s, v in zip(stations["stop_id"], stations[column])]
    stations[["stop_id", "stop_name", "stop_lat", "stop_lon"]].to_csv(
        out_dir / "stops.txt", index=False, encoding="utf-8")

    graph = nx.MultiDiGraph()
    for row in stations.itertuples():
        graph.add_node(row.stop_id, name=row.stop_name,
                       latitude=str(row.stop_lat), longitude=str(row.stop_lon))
    for row in all_edges.itertuples():
        if row.u in graph and row.v in graph:
            graph.add_edge(row.u, row.v, route_id=row.route_id,
                           trip_id=trip_id_of[row.trip_idx])
    nx.write_graphml(graph, out_dir / "graph.graphml")

    routes["vehicle"] = [
        vehicle_for(r.get("route_type", "3"), r.get("route_short_name", ""),
                    r.get("route_long_name", ""), mode=mode)
        for r in routes.to_dict("records")]
    routes.to_csv(out_dir / "routes.txt", index=False, encoding="utf-8")
    log(f"{mode}: {graph.number_of_nodes():,} stops, "
        f"{graph.number_of_edges():,} edges, {total:,} stop times -> {out_dir}")
    return out_dir


def _has(zf: zipfile.ZipFile, name: str, column: str) -> bool:
    header = pd.read_csv(zf.open(name), nrows=0)
    return column in header.columns
