
import argparse
from pathlib import Path

import networkx as nx
import pandas as pd

DATA_DIR = Path("data/raw")
OUTPUT_DIR = Path("data/processed")

SAMPLE_TRIPS = 1_000


def parse_args():
    parser = argparse.ArgumentParser(description="Build the sampled transport graph.")
    parser.add_argument("--sample-trips", type=int, default=SAMPLE_TRIPS,
                        help=f"Number of random trips to sample (default {SAMPLE_TRIPS}).")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default 42).")
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR / "transport_graph.graphml",
                        help="Output GraphML path.")
    return parser.parse_args()


def main():
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Load a manageable sample of trips.
    all_trips = pd.read_csv(
        DATA_DIR / "trips.txt",
        usecols=["trip_id", "route_id"],
        dtype=str,
    )

    trips = all_trips.sample(
        n=min(args.sample_trips, len(all_trips)),
        random_state=args.seed,
    ).reset_index(drop=True)

    trip_ids = set(trips["trip_id"])

    # Read only stop-time records for the sampled trips.
    stop_times = pd.read_csv(
        DATA_DIR / "stop_times.txt",
        usecols=["trip_id", "stop_id", "stop_sequence"],
        dtype={"trip_id": str, "stop_id": str},
        chunksize=100_000,
    )

    sampled_parts = []
    for chunk in stop_times:
        part = chunk[chunk["trip_id"].isin(trip_ids)]
        if not part.empty:
            sampled_parts.append(part)

    if not sampled_parts:
        print("No stop-time records found for the sampled trips.")
        return

    sampled_times = pd.concat(sampled_parts, ignore_index=True)
    sampled_times["stop_sequence"] = pd.to_numeric(
        sampled_times["stop_sequence"], errors="coerce"
    )
    sampled_times = sampled_times.dropna(subset=["stop_sequence"])

    # Add stop names and coordinates.
    stops = pd.read_csv(
        DATA_DIR / "stops.txt",
        usecols=["stop_id", "stop_name", "stop_lat", "stop_lon"],
        dtype=str,
    ).drop_duplicates(subset=["stop_id"])

    sampled_times = sampled_times.merge(
        stops, on="stop_id", how="left"
    )
    sampled_times = sampled_times.merge(
        trips, on="trip_id", how="left"
    )

    graph = nx.MultiDiGraph()

    for row in sampled_times.itertuples(index=False):
        graph.add_node(
            row.stop_id,
            name=row.stop_name,
            latitude=row.stop_lat,
            longitude=row.stop_lon,
        )

    # Connect consecutive stops in each trip.
    sampled_times = sampled_times.sort_values(
        ["trip_id", "stop_sequence"]
    )

    for trip_id, group in sampled_times.groupby("trip_id", sort=False):
        records = list(group.itertuples(index=False))

        for current, following in zip(records, records[1:]):
            if current.stop_id != following.stop_id:
                graph.add_edge(
                    current.stop_id,
                    following.stop_id,
                    trip_id=trip_id,
                    route_id=current.route_id,
                )

    output_path = args.output
    nx.write_graphml(graph, output_path)
    # Analyze network connectivity.
    undirected_graph = graph.to_undirected()

    components = list(nx.connected_components(undirected_graph))
    largest_component = max(components, key=len) if components else set()

    print(f"Connected components: {len(components):,}")
    print(f"Largest component: {len(largest_component):,} stops")
    print(
        "Stops in largest component: "
        f"{len(largest_component) / graph.number_of_nodes() * 100:.1f}%"
        if graph.number_of_nodes()
        else "No stops in graph"
    )

    print(f"Sampled trips: {len(trips):,}")
    print(f"Graph nodes (stops): {graph.number_of_nodes():,}")
    print(f"Graph edges (connections): {graph.number_of_edges():,}")
    print(f"Saved graph to: {output_path}")
    print(f"Unique sampled trips with stop records: {sampled_times['trip_id'].nunique():,}")
    print(f"Unique sampled stops in stop_times: {sampled_times['stop_id'].nunique():,}")


if __name__ == "__main__":
    main()
