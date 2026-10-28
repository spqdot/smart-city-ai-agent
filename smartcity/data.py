import re
import unicodedata
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import networkx as nx
import pandas as pd

SAMPLE_NOTE = (
    "The transport graph is built from a SAMPLE of GTFS trips, so it is "
    "incomplete. A missing route here does not prove that no real service exists."
)

FULL_NOTE = (
    "The graph for this mode is built from the full published timetable, so a "
    "missing route means no service links those stops in this feed. Other "
    "operators or modes are not covered."
)

STOP_COLUMNS = [
    "stop_id", "stop_name", "stop_lat", "stop_lon", "municipality_name",
    "district_name", "locality_name", "parish_name",
]

# Common English spellings mapped to the names used in the dataset.
PLACE_ALIASES = {"lisbon": "lisboa", "oporto": "porto"}


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation so names can be compared reliably."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^0-9a-zA-Z]+", " ", text.casefold())
    return text.strip()


def prepare_stops(stops: pd.DataFrame) -> pd.DataFrame:
    stops = stops.copy()
    for column in STOP_COLUMNS:
        if column not in stops.columns:
            stops[column] = ""
    stops = stops.fillna("")
    stops["stop_id"] = stops["stop_id"].astype(str)
    stops["lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
    stops["lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
    stops["norm_name"] = stops["stop_name"].map(normalize)
    stops["norm_municipality"] = stops["municipality_name"].map(normalize)
    stops["norm_locality"] = stops["locality_name"].map(normalize)
    stops["norm_parish"] = stops["parish_name"].map(normalize)
    return stops.drop_duplicates(subset=["stop_id"]).reset_index(drop=True)


@dataclass
class TransportNetwork:
    """Stops plus a directed graph for one transport mode (bus, train, ...)."""

    mode: str
    stops: pd.DataFrame
    graph: nx.MultiDiGraph
    sample_note: str = SAMPLE_NOTE
    route_names: dict = field(default_factory=dict)
    _stop_index: dict = field(init=False, repr=False)

    def __post_init__(self):
        self.stops = prepare_stops(self.stops)
        self.stops["in_graph"] = self.stops["stop_id"].isin(set(self.graph.nodes))
        self._stop_index = self.stops.set_index("stop_id").to_dict("index")

    def stop_info(self, stop_id: str) -> dict | None:
        row = self._stop_index.get(str(stop_id))
        if row is None:
            return None
        return {
            "stop_id": str(stop_id),
            "name": row["stop_name"],
            "municipality": row["municipality_name"],
            "in_graph": str(stop_id) in self.graph,
        }

    @cached_property
    def _components(self) -> dict:
        mapping = {}
        for index, nodes in enumerate(nx.weakly_connected_components(self.graph)):
            for node in nodes:
                mapping[node] = index
        return mapping

    def same_component(self, a: str, b: str) -> bool:
        return self._components.get(a) == self._components.get(b)


def load_network(stops_path: Path, graph_path: Path, mode: str = "bus",
                routes_path: Path | None = None, label: str | None = None) -> TransportNetwork:
    stops = pd.read_csv(
        stops_path,
        usecols=lambda c: c in STOP_COLUMNS,
        dtype=str,
        encoding="utf-8",
        encoding_errors="replace",
    )
    graph = nx.MultiDiGraph(nx.read_graphml(graph_path, node_type=str))
    return TransportNetwork(
        mode=mode, stops=stops, graph=graph,
        route_names=load_route_names(routes_path or stops_path.parent / "routes.txt"),
        **({"sample_note": FULL_NOTE} if mode != "bus" else {}),
    )


def load_route_names(routes_path: Path) -> dict:
    """route_id -> passenger-facing line number, description and vehicle type."""
    if not routes_path.exists():
        return {}
    routes = pd.read_csv(routes_path, dtype=str, encoding="utf-8",
                         encoding_errors="replace").fillna("")
    vehicles = routes["vehicle"] if "vehicle" in routes else ["Bus"] * len(routes)
    return {
        row.route_id: {"number": row.route_short_name, "description": row.route_long_name,
                       "vehicle": vehicle or "Bus"}
        for row, vehicle in zip(routes.itertuples(index=False), vehicles)
    }

