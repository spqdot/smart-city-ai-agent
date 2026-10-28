
from pathlib import Path

import networkx as nx

GRAPH_PATH = Path("data/processed/transport_graph.graphml")


def main():
    if not GRAPH_PATH.exists():
        print(f"Graph file not found: {GRAPH_PATH}")
        return

    graph = nx.read_graphml(GRAPH_PATH, node_type=str)

    print(f"Transport graph loaded: {graph.number_of_nodes():,} stops")
    print(f"Connections: {graph.number_of_edges():,}")

    start = input("\nEnter your starting stop ID: ").strip()
    destination = input("Enter your destination stop ID: ").strip()

    for stop_id in (start, destination):
        if stop_id not in graph:
            print(f"Stop ID not found in the current sample: {stop_id}")
            return

    try:
        path = nx.shortest_path(
            graph,
            source=start,
            target=destination,
        )

        print("\nRoute found:")
        for index, stop_id in enumerate(path, start=1):
            stop_name = graph.nodes[stop_id].get("name", "Unknown")
            print(f"{index}. {stop_name} [{stop_id}]")

        print(f"\nStops visited: {len(path)}")

    except nx.NetworkXNoPath:
        print("No directed route found between these stops in the current graph.")


if __name__ == "__main__":
    main()
