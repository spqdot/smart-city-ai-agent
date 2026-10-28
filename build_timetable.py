import argparse
import sqlite3
import time
from pathlib import Path

import pandas as pd

from smartcity.timetable import INDEXES, SCHEMA

DATA_DIR = Path("data/raw")
DEFAULT_OUTPUT = Path("data/processed/timetable.sqlite")


def seconds(series: pd.Series) -> pd.Series:
    """GTFS HH:MM:SS (hours may exceed 23) to seconds since midnight."""
    parts = series.str.strip().str.split(":", expand=True).astype("int64")
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build a SQLite index of scheduled times from the GTFS files.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--chunk-size", type=int, default=500_000)
    return parser.parse_args()


def main():
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = args.output.with_suffix(".building")
    temp_path.unlink(missing_ok=True)
    started = time.time()

    trips = pd.read_csv(
        args.data_dir / "trips.txt",
        usecols=["trip_id", "route_id", "service_id", "trip_headsign"],
        dtype=str, encoding="utf-8",
    ).fillna("")
    trips["trip_idx"] = range(len(trips))
    trip_index = trips.set_index("trip_id")["trip_idx"]

    calendar = pd.read_csv(args.data_dir / "calendar_dates.txt", dtype=str)
    calendar = calendar[calendar["exception_type"] == "1"][["date", "service_id"]]

    conn = sqlite3.connect(temp_path)
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO trips VALUES (?, ?, ?, ?)",
        trips[["trip_idx", "route_id", "service_id", "trip_headsign"]]
        .itertuples(index=False, name=None))
    conn.executemany("INSERT INTO calendar VALUES (?, ?)",
                     calendar.itertuples(index=False, name=None))

    total = 0
    reader = pd.read_csv(
        args.data_dir / "stop_times.txt",
        usecols=["trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"],
        dtype=str, chunksize=args.chunk_size,
    )
    for chunk in reader:
        chunk = chunk.dropna()
        frame = pd.DataFrame({
            "trip_idx": chunk["trip_id"].map(trip_index),
            "seq": pd.to_numeric(chunk["stop_sequence"], errors="coerce"),
            "stop_id": chunk["stop_id"],
            "arr": seconds(chunk["arrival_time"]),
            "dep": seconds(chunk["departure_time"]),
        }).dropna()
        frame[["trip_idx", "seq"]] = frame[["trip_idx", "seq"]].astype("int64")
        conn.executemany("INSERT OR IGNORE INTO stop_times VALUES (?, ?, ?, ?, ?)",
                         frame.itertuples(index=False, name=None))
        total += len(frame)
        print(f"  {total:,} stop times loaded ({time.time() - started:.0f}s)", flush=True)

    feed = pd.read_csv(args.data_dir / "feed_info.txt", dtype=str).iloc[0]
    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", [
        ("feed_version", feed.get("feed_version", "")),
        ("feed_start_date", feed.get("feed_start_date", "")),
        ("feed_end_date", feed.get("feed_end_date", "")),
    ])
    print("Creating indexes...", flush=True)
    conn.executescript(INDEXES)
    conn.commit()
    conn.close()

    args.output.unlink(missing_ok=True)
    temp_path.rename(args.output)
    print(f"Saved {total:,} stop times to {args.output} "
          f"({args.output.stat().st_size / 1e6:.0f} MB, {time.time() - started:.0f}s)")


if __name__ == "__main__":
    main()
