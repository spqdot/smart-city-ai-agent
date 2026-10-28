"""Specialist transport agents: one per vehicle family, all deterministic.

A specialist owns a mode (a data folder / timetable) and optionally a vehicle
filter, so trams can be split out of the Carris Lisboa feed. An unknown loaded
mode without a dedicated specialist gets a generic one.
"""

from dataclasses import dataclass

TRAM_VEHICLES = frozenset({"Tram", "Funicular"})


@dataclass(frozen=True)
class Specialist:
    key: str
    label: str
    mode: str
    include: frozenset | None = None  # keep only these vehicles
    exclude: frozenset | None = None  # drop these vehicles

    def accepts(self, vehicle: str) -> bool:
        if self.include is not None and vehicle not in self.include:
            return False
        return not (self.exclude and vehicle in self.exclude)


SPECIALISTS = [
    Specialist("bus", "Bus (Carris Metropolitana)", "bus"),
    Specialist("carris_bus", "Bus (Carris Lisboa)", "carris", exclude=TRAM_VEHICLES),
    Specialist("tram", "Tram & funicular (Carris Lisboa)", "carris", include=TRAM_VEHICLES),
    Specialist("metro", "Metro de Lisboa", "metro"),
    Specialist("mts", "Metro Sul do Tejo (MST)", "mts", include=frozenset({"MST light rail"})),
    Specialist("train", "Train (CP)", "train"),
    Specialist("fertagus", "Train (Fertagus)", "fertagus"),
    Specialist("ferry", "Ferry", "ferry"),
]


def specialists_for(modes: list[str],
                    labels: dict[str, str]) -> tuple[list[Specialist], list[Specialist]]:
    """Return (specialists with a loaded feed, specialists whose feed is missing)."""
    active = [s for s in SPECIALISTS if s.mode in modes]
    covered = {s.mode for s in active}
    active += [Specialist(m, labels.get(m, m), m) for m in modes if m not in covered]
    missing = [s for s in SPECIALISTS if s.mode not in modes]
    return active, missing

