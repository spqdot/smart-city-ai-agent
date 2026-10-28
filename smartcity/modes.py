from pathlib import Path
from typing import Callable
from difflib import SequenceMatcher

from .config import Settings
from .data import PLACE_ALIASES, TransportNetwork, load_network, normalize
from .feeds import FEEDS, MODES_DIR
from .geo import haversine_km
from .timetable import Timetable


MODE_FILTERS = {
    "Carris Metropolitana Bus": {"bus"},
    "CP / Fertagus Train": {"train", "fertagus"},
    "Metro Sul do Tejo (MST)": {"mts"},
    "Metropolitano de Lisboa": {"metro"},
    "Carris Lisboa Tram": {"carris"},
    "Any available transport": None,
    "Bus": {"bus"},
    "Train": {"train", "fertagus"},
    "CP Train": {"train"},
    "Fertagus": {"fertagus"},
    "Metro": {"metro"},
    "MST": {"mts"},
    "Tram": {"carris"},
    "Any": None,
    # Dashboard searches that require at least one leg on the selected mode,
    # while still allowing timetable-validated inter-operator connections.
    "Bus + connections": None,
    "CP Train + connections": None,
    "Fertagus + connections": None,
    "Metro Sul do Tejo (MST) + connections": None,
    "Metropolitano de Lisboa + connections": None,
    "Carris Lisboa Tram + connections": None,
    # Keep older tool callers working while the Streamlit UI uses "Any".
    "Any available transport": None,
}

_SEARCH_CONNECTORS = {"a", "as", "da", "das", "de", "do", "dos", "e", "o", "os"}

REQUIRED_MODE_FILTERS = {
    "Bus + connections": "bus",
    "CP Train + connections": "train",
    "Fertagus + connections": "fertagus",
    "Metro Sul do Tejo (MST) + connections": "mts",
    "Metropolitano de Lisboa + connections": "metro",
    "Carris Lisboa Tram + connections": "carris",
}


def modes_for_filter(transport_filter: str, available_modes: list[str]) -> list[str]:
    """Return loaded mode IDs allowed by a user-facing mode selection."""
    if transport_filter not in MODE_FILTERS:
        return []
    allowed = MODE_FILTERS.get(transport_filter)
    if allowed is None:
        return list(available_modes)
    return [mode for mode in available_modes if mode in allowed]


def stop_options(registry: "NetworkRegistry", transport_filter: str) -> dict[str, dict]:
    """Build unique, mode-scoped stop choices for the Streamlit selectors."""
    choices = {}
    for mode in modes_for_filter(transport_filter, registry.modes):
        network = registry.get(mode)
        if network is None:
            continue
        operator = registry.labels.get(mode, mode)
        for row in network.stops.itertuples(index=False):
            if row.lat != row.lat or row.lon != row.lon:
                continue
            stop_id = str(row.stop_id)
            name = str(row.stop_name)
            title = f"{name} — {operator} [{mode}:{stop_id}]"
            municipality = getattr(row, "municipality_name", "")
            if municipality:
                title += f" · {municipality}"
            choices[title] = {
                "operator": operator, "mode": mode, "stop_id": stop_id,
                "stop_ids": [stop_id], "name": name,
                "lat": float(row.lat), "lon": float(row.lon),
            }
    return choices


def match_stop_options(choices: dict[str, dict], query: str, limit: int = 100) -> list[str]:
    """Match official names exactly first, then offer close spelling suggestions."""
    needle = normalize(query)
    if not needle:
        return []
    matches = [(title, stop) for title, stop in choices.items()
               if needle in normalize(stop.get("name", title))]
    if matches:
        # Put an exact station-name match before nearby variants such as
        # "Cacilhas (C Morais)".
        matches.sort(key=lambda item: (
            normalize(item[1].get("name", item[0])) != needle,
            normalize(item[1].get("name", item[0])),
        ))
        return [title for title, _ in matches[:limit]]

    # A short input is too ambiguous for useful typo correction. For longer
    # input, compare its words against stop-name words to suggest likely typos.
    query_words = [word for word in needle.split()
                   if len(word) >= 3 and word not in _SEARCH_CONNECTORS]
    if not query_words:
        return []
    suggestions = []
    for title, stop in choices.items():
        name_words = normalize(stop.get("name", title)).split()
        scores = []
        for query_word in query_words:
            best = max((SequenceMatcher(None, query_word, name_word,
                                        autojunk=False).ratio()
                        for name_word in name_words), default=0.0)
            scores.append(best)
        if scores and min(scores) >= 0.76:
            suggestions.append((sum(scores) / len(scores), title,
                                normalize(stop.get("name", title))))
    suggestions.sort(key=lambda item: (-item[0], item[2], item[1]))
    return [title for _, title, _ in suggestions[:min(limit, 10)]]


def municipality_area_option(registry: "NetworkRegistry", query: str,
                             target_modes: set[str] | None = None) -> dict | None:
    """Resolve an exact municipality to tagged stops and nearby untagged stations.

    Some operator feeds omit municipality fields. For those, infer the area from
    the nearest stop in an operator feed that does carry municipality metadata,
    and only include a stop when that tagged reference is within 750 m. When
    ``target_modes`` is omitted, use only directly tagged operator data.
    """
    normalized = normalize(query)
    normalized = PLACE_ALIASES.get(normalized, normalized)
    if not normalized:
        return None

    import pandas as pd

    stop_ids_by_mode, coordinate_rows, official_name = {}, [], None
    tagged_areas = []
    for mode in registry.modes:
        network = registry.get(mode)
        stops = network.stops
        if "norm_municipality" not in stops or "municipality_name" not in stops:
            continue
        tagged = stops[(stops["norm_municipality"].fillna("") != "")
                       & stops["lat"].notna() & stops["lon"].notna()]
        if not tagged.empty:
            tagged_areas.append(tagged[["lat", "lon", "norm_municipality",
                                       "municipality_name"]])
        area = tagged[tagged["norm_municipality"] == normalized]
        if area.empty:
            continue
        if target_modes is None or mode in target_modes:
            stop_ids_by_mode[mode] = list(area["stop_id"].astype(str))
            coordinate_rows.append(area[["lat", "lon"]])
        if official_name is None:
            official_name = str(area.iloc[0]["municipality_name"])

    anchors = pd.concat(tagged_areas, ignore_index=True) if tagged_areas else None
    if target_modes and anchors is not None and not anchors.empty:
        for mode in target_modes:
            if mode not in registry.modes:
                continue
            network = registry.get(mode)
            stops = network.stops
            if "norm_municipality" not in stops:
                continue
            untagged = stops[(stops["norm_municipality"].fillna("") == "")
                             & stops["lat"].notna() & stops["lon"].notna()]
            if untagged.empty:
                continue
            anchor_lat = anchors["lat"].to_numpy(dtype=float)
            anchor_lon = anchors["lon"].to_numpy(dtype=float)
            anchor_municipality = anchors["norm_municipality"].astype(str).to_numpy()
            ids = []
            for row in untagged.itertuples(index=False):
                distances = haversine_km(row.lat, row.lon, anchor_lat, anchor_lon)
                nearest = int(distances.argmin())
                if distances[nearest] <= 0.75 and anchor_municipality[nearest] == normalized:
                    ids.append(str(row.stop_id))
            if ids:
                stop_ids_by_mode[mode] = ids
                coordinate_rows.append(
                    untagged[untagged["stop_id"].astype(str).isin(ids)][["lat", "lon"]])

    if not stop_ids_by_mode:
        return None

    points = pd.concat(coordinate_rows, ignore_index=True)
    return {
        "name": official_name,
        "area": True,
        "stop_ids": [],
        "stop_ids_by_mode": stop_ids_by_mode,
        "area_stop_counts": {mode: len(ids) for mode, ids in stop_ids_by_mode.items()},
        "lat": float(points["lat"].median()),
        "lon": float(points["lon"].median()),
    }


class NetworkRegistry:
    """Holds one network (and optional timetable) per transport mode.

    Adding a mode means registering another loader; the tools and the agent
    graph do not change.
    """

    def __init__(self):
        self._loaders: dict[str, Callable[[], TransportNetwork]] = {}
        self._loaded: dict[str, TransportNetwork] = {}
        self._timetables: dict[str, object] = {}
        self.labels: dict[str, str] = {}

    def register(self, mode: str, loader: Callable[[], TransportNetwork],
                 timetable: Timetable | None = None, label: str | None = None) -> None:
        self._loaders[mode] = loader
        self._loaded.pop(mode, None)
        if timetable is not None:
            self._timetables[mode] = timetable
        self.labels[mode] = label or mode

    def register_network(self, network: TransportNetwork,
                         timetable: Timetable | None = None) -> None:
        self._loaded[network.mode] = network
        self._loaders[network.mode] = lambda: network
        if timetable is not None:
            self._timetables[network.mode] = timetable
        self.labels.setdefault(network.mode, network.mode)

    @property
    def modes(self) -> list[str]:
        return sorted(self._loaders)

    def timetable(self, mode: str) -> Timetable | None:
        return self._timetables.get(mode)

    def get(self, mode: str) -> TransportNetwork | None:
        if mode not in self._loaders:
            return None
        if mode not in self._loaded:
            self._loaded[mode] = self._loaders[mode]()
        return self._loaded[mode]


def default_registry(settings: Settings, modes_dir: Path = MODES_DIR) -> NetworkRegistry:
    registry = NetworkRegistry()
    registry.register(
        "bus", lambda: load_network(settings.stops_path, settings.graph_path, "bus"),
        timetable=Timetable(settings.timetable_path),
        label="Carris Metropolitana buses (Lisbon metropolitan area)",
    )
    # Every folder produced by import_feed.py becomes a mode.
    for folder in sorted(modes_dir.glob("*")) if modes_dir.exists() else []:
        stops, graph = folder / "stops.txt", folder / "graph.graphml"
        if not (stops.exists() and graph.exists()):
            continue
        if folder.name == "mts":
            from .mst_timetable import MSTimetable
            timetable = MSTimetable(
                MODES_DIR.parent / "raw" / "metro_sul_tejo" / "mst_schedule.json", stops)
        else:
            timetable = Timetable(folder / "timetable.sqlite")
        registry.register(
            folder.name,
            lambda s=stops, g=graph, r=folder / "routes.txt", m=folder.name:
                load_network(s, g, m, r),
            timetable=timetable,
            label=FEEDS.get(folder.name, {}).get("label", folder.name),
        )
    return registry
