from pathlib import Path

import networkx as nx
import pandas as pd
import streamlit as st

STOPS_PATH = Path("data/raw/stops.txt")
GRAPH_PATH = Path("data/processed/transport_graph.graphml")


def load_stops():
    return pd.read_csv(
        STOPS_PATH,
        usecols=[
            "stop_id",
            "stop_name",
            "municipality_name",
            "district_name",
        ],
        dtype=str,
    ).fillna("")


def find_stop(stops, label):
    while True:
        query = input(f"\nSearch for {label} stop: ").strip()

        if not query:
            print("Please enter a search term.")
            continue

        
        # Search stop names and municipality names.
        name_matches = stops["stop_name"].str.contains(
            query, case=False, regex=False
        )

        municipality_matches = stops["municipality_name"].str.contains(
            query, case=False, regex=False
        )

        matches = stops[name_matches | municipality_matches].copy()

        # Show stops in the matching municipality first.
        matches["_priority"] = (
            matches["municipality_name"].str.casefold()
            != query.casefold()
        ).astype(int)

        matches = matches.sort_values(
            by=["_priority", "stop_name"]
        ).drop(columns="_priority")
        
        # Display at most 15 results.
        matches = matches.head(15)


        if matches.empty:
            print("No matching stops found. Try another search.")
            continue

        print(f"\nFound {len(matches)} matches:")
        for index, row in enumerate(matches.itertuples(index=False), start=1):
            print(
                f"{index}. {row.stop_name} | "
                f"{row.municipality_name} | "
                f"{row.district_name} | ID: {row.stop_id}"
            )

        choice = input(
            "\nEnter the number of your preferred stop "
            "(or press Enter to search again): "
        ).strip()

        if not choice:
            continue

        if choice.isdigit():
            # Accept a result number, such as 1, 2, or 3.
            if 1 <= int(choice) <= len(matches):
                return matches.iloc[int(choice) - 1]["stop_id"]

            # Also accept an exact stop ID.
            matching_ids = matches[matches["stop_id"] == choice]
            if not matching_ids.empty:
                return matching_ids.iloc[0]["stop_id"]

        print("Invalid selection. Enter a result number or a matching stop ID.")
        
        if choice.isdigit() and 1 <= int(choice) <= len(matches):
            return matches.iloc[int(choice) - 1]["stop_id"]

        print("Invalid selection. Please try again.")


def main():
    if not STOPS_PATH.exists() or not GRAPH_PATH.exists():
        print("Required data files are missing. Check data/raw and data/processed.")
        return

    stops = load_stops()
    graph = nx.read_graphml(GRAPH_PATH, node_type=str)

    print("Lisbon Metropolitan Area — Journey Planner")
    print(f"Available graph stops: {graph.number_of_nodes():,}")

    origin = find_stop(stops, "origin")
    destination = find_stop(stops, "destination")

    if origin not in graph:
        print("The selected origin is not in the current sampled graph.")
        return

    if destination not in graph:
        print("The selected destination is not in the current sampled graph.")
        return

    try:
        path = nx.shortest_path(
            graph,
            source=origin,
            target=destination,
        )

        
        print("\nRoute found:")

        for index, stop_id in enumerate(path, start=1):
            node = graph.nodes[stop_id]
            stop_name = node.get("name", "Unknown")
            print(f"{index}. {stop_name} [{stop_id}]")

            if index < len(path):
                next_stop_id = path[index]

                # Retrieve the edge connecting these two stops.
                edge_data = graph.get_edge_data(stop_id, next_stop_id)

                if edge_data:
                    # A MultiDiGraph can have multiple edges between stops.
                    edge = next(iter(edge_data.values()))
                    route_id = edge.get("route_id", "Unknown")

                    next_name = graph.nodes[next_stop_id].get(
                        "name", "Unknown"
                    )

                    print(
                        f"   └── Bus route ID: {route_id} "
                        f"→ {next_name}"
                    )

        print(f"\nStops in route: {len(path)}")


    except nx.NetworkXNoPath:
        print("No directed route found in the current graph sample.")


if __name__ == "__main__":
    main()
