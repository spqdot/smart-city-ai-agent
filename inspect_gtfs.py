
from pathlib import Path
import pandas as pd

DATA_DIR = Path("data/raw")

def inspect_file(filename, columns=None, chunksize=100_000):
    path = DATA_DIR / filename

    if not path.exists():
        print(f"Missing file: {path}")
        return

    total_rows = 0

    for chunk in pd.read_csv(
        path,
        usecols=columns,
        dtype=str,
        chunksize=chunksize,
        low_memory=False,
    ):
        total_rows += len(chunk)

    print(f"{filename}: {total_rows:,} rows")

    
def check_trip_ids():
    trips_path = DATA_DIR / "trips.txt"
    stop_times_path = DATA_DIR / "stop_times.txt"

    trip_ids = set(
        pd.read_csv(
            trips_path,
            usecols=["trip_id"],
            dtype=str,
        )["trip_id"]
    )

    matched = 0
    unmatched = 0

    for chunk in pd.read_csv(
        stop_times_path,
        usecols=["trip_id"],
        dtype=str,
        chunksize=100_000,
    ):
        valid = chunk["trip_id"].isin(trip_ids)
        matched += int(valid.sum())
        unmatched += int((~valid).sum())

    print(f"Matched stop-time rows: {matched:,}")
    print(f"Unmatched stop-time rows: {unmatched:,}")


    
def check_stop_ids():
    stops_path = DATA_DIR / "stops.txt"
    stop_times_path = DATA_DIR / "stop_times.txt"

    stop_ids = set(
        pd.read_csv(
            stops_path,
            usecols=["stop_id"],
            dtype=str,
        )["stop_id"].dropna()
    )

    matched = 0
    unmatched = 0

    for chunk in pd.read_csv(
        stop_times_path,
        usecols=["stop_id"],
        dtype=str,
        chunksize=100_000,
    ):
        valid = chunk["stop_id"].isin(stop_ids)
        matched += int(valid.sum())
        unmatched += int((~valid).sum())

    print(f"Matched stop-time rows: {matched:,}")
    print(f"Unmatched stop-time rows: {unmatched:,}")




if __name__ == "__main__":
    inspect_file(
        "stops.txt",
        columns=["stop_id", "stop_name", "stop_lat", "stop_lon"],
    )
    inspect_file(
        "routes.txt",
        columns=["route_id", "route_short_name", "route_long_name"],
    )
    inspect_file(
        "trips.txt",
        columns=["trip_id", "route_id", "service_id"],
    )
    inspect_file(
        "stop_times.txt",
        columns=["trip_id", "stop_id", "stop_sequence"],
    )
    check_trip_ids()
    check_stop_ids()
