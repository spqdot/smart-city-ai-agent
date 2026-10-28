from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from smartcity.config import load_settings
from smartcity.feeds import validate_gtfs
from smartcity.modes import (default_registry, match_stop_options,
                             municipality_area_option, stop_options)
from smartcity.mst_timetable import (MSTimetable, day_type_for, season_for,
                                     validate_snapshot)
from smartcity.pipeline import _deduplicate_options, build_pipeline
from smartcity.journey_service import search_journey

RAW = Path("data/raw/metro_sul_tejo")


def test_mst_archive_and_official_schedule_snapshot_are_validated():
    report = validate_gtfs(RAW / "mst_gtfs_archive.zip", mode="mts")
    assert report == {"stops": 19, "routes": 3, "trips": 2067,
                      "stop_times": 23590, "service_ids": 5}
    snapshot = __import__("json").loads((RAW / "mst_schedule.json").read_text(encoding="utf-8"))
    counts = validate_snapshot(snapshot)
    assert counts["directed_lines"] == 6
    assert counts["day_season_tables"] == 36
    assert counts["scheduled_start_times"] > 0


def test_mst_filter_resolves_only_pragal_and_cacilhas_on_mst():
    registry = default_registry(load_settings({}))
    choices = stop_options(registry, "Metro Sul do Tejo (MST)")
    assert {item["mode"] for item in choices.values()} == {"mts"}
    origin = next(item for item in choices.values() if item["name"] == "Pragal")
    destination = next(item for item in choices.values() if item["name"] == "Cacilhas")
    assert origin["stop_ids"] == ["16"]
    assert destination["stop_ids"] == ["1"]

    wrong_mode = build_pipeline(registry, clock=lambda: datetime(
        2026, 10, 27, 14, 0, tzinfo=ZoneInfo("Europe/Lisbon")))
    result = wrong_mode(
        {"mode": "bus", "stop_ids": ["320003"], "lat": origin["lat"], "lon": origin["lon"]},
        destination, "Metro Sul do Tejo (MST)")
    assert result["status"] == "not_searched"
    assert not any(item["status"] == "searched" for item in result["agents"])


def test_mst_stop_search_uses_only_official_mst_stops_and_normalizes_query():
    registry = default_registry(load_settings({}))
    choices = stop_options(registry, "Metro Sul do Tejo (MST)")
    assert {value["mode"] for value in choices.values()} == {"mts"}
    for query, stop_id in (("Pragal", "16"), ("Cacilhas", "1"),
                           ("Universidade", "20"), ("  pRaGaL  ", "16")):
        matches = match_stop_options(choices, query)
        assert len(matches) == 1
        assert choices[matches[0]]["stop_id"] == stop_id
    assert not any("Oeiras" in title or value["mode"] == "bus"
                   for title, value in choices.items())


def test_stop_search_suggests_close_spelling_without_changing_official_name():
    choices = {
        "Laranjeiro — Bus": {"name": "Laranjeiro", "mode": "bus", "stop_id": "42"},
        "Cacilhas — MST": {"name": "Cacilhas", "mode": "mts", "stop_id": "1"},
    }
    matches = match_stop_options(choices, "Laranjerio")
    assert matches == ["Laranjeiro — Bus"]
    assert choices[matches[0]]["name"] == "Laranjeiro"


def test_stop_search_suggests_connector_variant_in_area_name():
    choices = {
        "Costa da Caparica â€” Bus": {
            "name": "Costa da Caparica", "mode": "bus", "stop_id": "123",
        },
    }

    matches = match_stop_options(choices, "Costa de Caparica")

    assert matches == ["Costa da Caparica â€” Bus"]
    assert choices[matches[0]]["name"] == "Costa da Caparica"


def test_lisbon_query_resolves_to_tagged_municipality_area_not_name_substrings():
    registry = default_registry(load_settings({}))
    area = municipality_area_option(registry, "  Lisbon  ")
    assert area is not None
    assert area["name"] == "Lisboa"
    assert set(area["stop_ids_by_mode"]) == {"bus"}
    assert area["area_stop_counts"]["bus"] > 1
    assert "170841" not in area["stop_ids_by_mode"]["bus"]
    assert "170842" not in area["stop_ids_by_mode"]["bus"]
    assert area["lat"] and area["lon"]


def test_lisbon_area_includes_untagged_fertagus_stations_from_nearby_municipality_data():
    registry = default_registry(load_settings({}))
    area = municipality_area_option(registry, "Lisbon", target_modes={"fertagus"})

    assert area is not None
    assert area["area_stop_counts"] == {"fertagus": 4}
    stations = registry.get("fertagus").stops
    names = set(stations[stations["stop_id"].isin(
        area["stop_ids_by_mode"]["fertagus"])] ["stop_name"])
    assert names == {"Roma-Areeiro", "Entrecampos", "Sete-Rios", "Campolide"}


def test_pragal_entrecampos_suppresses_detour_when_next_direct_arrives_as_soon(monkeypatch):
    from smartcity import pipeline as pipeline_module
    from smartcity import window as window_module

    monkeypatch.setattr(pipeline_module, "WINDOW_MINUTES", 15)
    monkeypatch.setattr(window_module, "WINDOW_MINUTES", 15)
    registry = default_registry(load_settings({}))
    choices = stop_options(registry, "Fertagus")
    origin = next(stop for stop in choices.values() if stop["name"] == "Pragal")
    destination = next(stop for stop in choices.values()
                       if stop["name"] == "Entrecampos")
    result = search_journey(
        registry, origin, destination, "Fertagus + connections",
        clock=lambda: datetime(2026, 10, 28, 11, 10,
                               tzinfo=ZoneInfo("Europe/Lisbon")))

    fertagus_report = next(report for report in result["agents"]
                           if report["mode"] == "fertagus")
    assert result["options"] == []
    assert fertagus_report["dominated_transfers_suppressed"] > 0
    assert fertagus_report["next_direct_outside_window"] == {
        "departs": "11:29", "arrives": "11:44"}
    assert "outside this departure window" in result["message"]


def test_bus_3022_transfer_uses_its_later_pragal_station_call():
    registry = default_registry(load_settings({}))
    bus_choices = stop_options(registry, "Bus")
    fertagus_choices = stop_options(registry, "Fertagus")
    origin = next(stop for stop in bus_choices.values()
                  if stop["stop_id"] == "320359")
    destination = next(stop for stop in fertagus_choices.values()
                       if stop["stop_id"] == "FTStop15")

    result = search_journey(
        registry, origin, destination, "Any available transport",
        clock=lambda: datetime(2026, 10, 28, 13, 20,
                               tzinfo=ZoneInfo("Europe/Lisbon")))

    route_3022 = next(option for option in result["options"]
                      if option["legs"][0]["line"] == "3022")
    assert route_3022["legs"][0]["alight_stop_id"] == "327999"
    assert route_3022["legs"][0]["arrives"] == "13:32"
    assert route_3022["transfer_info"].startswith("1 change at PRAGAL")


def test_pragal_cacilhas_schedule_in_next_two_hours_lisbon_time():
    registry = default_registry(load_settings({}))
    choices = stop_options(registry, "Metro Sul do Tejo (MST)")
    origin = next(item for item in choices.values() if item["name"] == "Pragal")
    destination = next(item for item in choices.values() if item["name"] == "Cacilhas")
    run = build_pipeline(registry, clock=lambda: datetime(
        2026, 10, 27, 14, 0, tzinfo=ZoneInfo("Europe/Lisbon")))
    result = run(origin, destination, "Metro Sul do Tejo (MST)")
    assert result["status"] == "ok"
    direct = [option for option in result["options"] if option["transfers"] == 0]
    assert len(direct) >= 4
    assert all("14:00" <= option["departs"] <= "16:00" for option in direct)
    assert all(option["transfers"] == 0 and len(option["legs"]) == 1
               for option in direct)
    assert all(option["legs"][0]["board_stop_id"] == "16"
               and option["legs"][0]["alight_stop_id"] == "1" for option in direct)
    assert all(option["mode"] == "mts" and option["vehicle"] == "MST light rail"
               for option in result["options"])
    assert result["agents"][0]["data_source"]["operational_alerts_included"] is False
    assert any("disruption alerts" in note for note in result["notes"])


def test_monte_de_caparica_to_entrecampos_can_use_mst_then_fertagus():
    registry = default_registry(load_settings({}))
    choices = stop_options(registry, "Any available transport")
    origin = next(item for item in choices.values()
                  if item["name"] == "Monte de Caparica" and item["mode"] == "mts")
    destination = next(item for item in choices.values()
                       if item["name"] == "Entrecampos" and item["mode"] == "fertagus")
    result = search_journey(
        registry, origin, destination, "Any available transport",
        clock=lambda: datetime(2026, 10, 28, 11, 0,
                               tzinfo=ZoneInfo("Europe/Lisbon")))

    assert result["window"]["minutes"] == 120
    assert any([leg["mode"] for leg in option["legs"]] == ["mts", "fertagus"]
               and option["legs"][0]["alight"] == "Pragal"
               and option["legs"][1]["alight"] == "Entrecampos"
               for option in result["options"])


def test_chat_and_planner_share_the_same_deterministic_search():
    registry = default_registry(load_settings({}))
    choices = stop_options(registry, "Metro Sul do Tejo (MST)")
    origin = next(item for item in choices.values() if item["name"] == "Pragal")
    destination = next(item for item in choices.values() if item["name"] == "Cacilhas")
    clock = lambda: datetime(2026, 10, 27, 14, 0, tzinfo=ZoneInfo("Europe/Lisbon"))
    result = search_journey(registry, origin, destination,
                            "Metro Sul do Tejo (MST)", clock=clock)
    ui_result = build_pipeline(registry, clock=clock)(
        origin, destination, "Metro Sul do Tejo (MST)")
    assert result["request"]["origin"]["id"] == "16"
    assert result["request"]["destination"]["id"] == "1"
    assert result["operators_searched"] == [{"mode": "mts", "operator": "Metro Sul do Tejo (MST)"}]
    assert result["options"] == ui_result["options"]
    assert all(option["transfers"] == max(0, len(option["legs"]) - 1)
               for option in result["options"])


def test_mst_season_day_type_and_missing_feed_are_explicit(tmp_path):
    assert season_for(date(2026, 8, 1)) == 1
    assert season_for(date(2026, 10, 27)) == 2
    assert day_type_for(date(2026, 10, 27)) == 1
    assert day_type_for(date(2026, 10, 31)) == 2
    assert day_type_for(date(2026, 11, 1)) == 3
    assert day_type_for(date(2026, 6, 29)) == 3
    unavailable = MSTimetable(tmp_path / "missing.json", tmp_path / "stops.txt")
    assert not unavailable.available
    assert "import_mst.py" in unavailable.source_status["reason"]

    source_registry = default_registry(load_settings({}))
    source_network = source_registry.get("mts")
    from smartcity.modes import NetworkRegistry
    registry = NetworkRegistry()
    registry.register_network(source_network, unavailable)
    choices = stop_options(registry, "Metro Sul do Tejo (MST)")
    origin = next(item for item in choices.values() if item["name"] == "Pragal")
    destination = next(item for item in choices.values() if item["name"] == "Cacilhas")
    result = build_pipeline(registry, clock=lambda: datetime(
        2026, 10, 27, 14, 0, tzinfo=ZoneInfo("Europe/Lisbon")))(
            origin, destination, "Metro Sul do Tejo (MST)")
    assert result["status"] == "data_missing"
    assert "import_mst.py" in result["message"]


def test_mst_no_departure_window_and_duplicate_journey_removal(tmp_path):
    registry = default_registry(load_settings({}))
    choices = stop_options(registry, "Metro Sul do Tejo (MST)")
    origin = next(item for item in choices.values() if item["name"] == "Pragal")
    destination = next(item for item in choices.values() if item["name"] == "Cacilhas")
    run = build_pipeline(registry, clock=lambda: datetime(
        2026, 10, 27, 2, 0, tzinfo=ZoneInfo("Europe/Lisbon")))
    result = run(origin, destination, "Metro Sul do Tejo (MST)")
    assert result["status"] == "no_journey"
    assert result["options"] == []
    assert result["agents"][0]["status"] == "searched"

    journey = {"mode": "mts", "route_id": "mts:6", "board_stop_id": "16",
               "alight_stop_id": "1", "departs": "14:04", "arrives": "14:17"}
    later = {**journey, "departs": "14:14", "arrives": "14:27"}
    assert _deduplicate_options([journey.copy(), journey.copy(), later]) == [journey, later]
