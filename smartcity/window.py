"""The fixed search window: now (Europe/Lisbon) to two hours later."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

LISBON = ZoneInfo("Europe/Lisbon")
WINDOW_MINUTES = 120


@dataclass(frozen=True)
class SearchWindow:
    start: datetime
    end: datetime
    base_day: date
    start_seconds: int  # seconds since local midnight of base_day
    end_seconds: int  # may exceed 86400 when the window crosses midnight

    @property
    def label(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M} (Lisbon time, {self.start:%Y-%m-%d})"


def current_window(now: datetime | None = None) -> SearchWindow:
    """Window from `now` to `now` + two hours in Europe/Lisbon.

    `now` is for tests; a naive value is taken as Lisbon local time.
    """
    if now is None:
        now = datetime.now(LISBON)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=LISBON)
    else:
        now = now.astimezone(LISBON)
    start_seconds = now.hour * 3600 + now.minute * 60 + now.second
    return SearchWindow(
        start=now,
        end=now + timedelta(minutes=WINDOW_MINUTES),
        base_day=now.date(),
        start_seconds=start_seconds,
        end_seconds=start_seconds + WINDOW_MINUTES * 60,
    )
