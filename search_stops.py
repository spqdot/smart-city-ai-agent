
from pathlib import Path

import pandas as pd

STOPS_PATH = Path("data/raw/stops.txt")


def main():
    if not STOPS_PATH.exists():
        print(f"Stops file not found: {STOPS_PATH}")
        return

    stops = pd.read_csv(
        STOPS_PATH,
        usecols=[
            "stop_id",
            "stop_name",
            "stop_lat",
            "stop_lon",
            "municipality_name",
            "district_name",
        ],
        dtype=str,
    ).fillna("")

    query = input("Enter a stop name or part of a name: ").strip()

    if not query:
        print("Please enter a search term.")
        return

    matches = stops[
        stops["stop_name"].str.contains(query, case=False, regex=False)
    ]

    if matches.empty:
        print("No matching stops found.")
        return

    print(f"\nFound {len(matches):,} matching stops. Showing up to 20:\n")

    for row in matches.head(20).itertuples(index=False):
        print(f"Stop ID: {row.stop_id}")
        print(f"Name: {row.stop_name}")
        print(f"Municipality: {row.municipality_name or 'Unknown'}")
        print(f"District: {row.district_name or 'Unknown'}")
        print(f"Coordinates: ({row.stop_lat}, {row.stop_lon})")
        print("-" * 45)


if __name__ == "__main__":
    main()
