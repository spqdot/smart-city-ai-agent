"""Refresh the local Metro Sul do Tejo timetable snapshot from its public API."""

from pathlib import Path

from smartcity.feeds import download, import_feed, validate_gtfs
from smartcity.mst_timetable import fetch_snapshot, validate_snapshot

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw" / "metro_sul_tejo"
ARCHIVE = RAW / "mst_gtfs_archive.zip"
SNAPSHOT = RAW / "mst_schedule.json"

if not ARCHIVE.exists():
    download("mts", ARCHIVE)
print("Archived GTFS validation:", validate_gtfs(ARCHIVE, mode="mts"))
print("Imported MST stations and network:", import_feed("mts", ARCHIVE))
snapshot = fetch_snapshot(SNAPSHOT)
print("Official MTS timetable validation:", validate_snapshot(snapshot))
print("Saved local schedule snapshot:", SNAPSHOT)
