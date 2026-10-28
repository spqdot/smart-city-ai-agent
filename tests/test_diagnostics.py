import sqlite3
from datetime import date

from smartcity.diagnostics import feed_status
from smartcity.timetable import INDEXES, SCHEMA, Timetable


class Network:
    mode = "train"
    stops = [1, 2]
    route_names = {"r": {}}


def test_status_reports_missing_and_invalid_feeds(tmp_path):
    missing = feed_status("train", Network(), Timetable(tmp_path / "missing.sqlite"),
                          today=date(2026, 10, 27))
    assert missing["status"] == "missing"

    path = tmp_path / "broken.sqlite"
    sqlite3.connect(path).close()
    invalid = feed_status("train", Network(), Timetable(path), today=date(2026, 10, 27))
    assert invalid["status"] == "invalid"


def test_status_reports_outdated_and_current_calendars(tmp_path):
    path = tmp_path / "timetable.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executescript(INDEXES)
    conn.execute("INSERT INTO trips VALUES (1, 'r', 's', '')")
    conn.execute("INSERT INTO stop_times VALUES (1, 1, 'a', 0, 0)")
    conn.execute("INSERT INTO calendar VALUES ('20261026', 's')")
    conn.commit()
    conn.close()
    status = feed_status("train", Network(), Timetable(path), today=date(2026, 10, 27))
    assert status["status"] == "outdated"
    assert status["trips"] == 1 and status["stop_times"] == 1

    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO calendar VALUES ('20261028', 's')")
    conn.commit()
    conn.close()
    current = feed_status("train", Network(), Timetable(path), today=date(2026, 10, 27))
    assert current["status"] == "available"
