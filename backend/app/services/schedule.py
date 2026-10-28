"""IST sending window. IST is UTC+5:30 with no daylight saving, so a fixed
offset is exact and independent of the server's own time zone."""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def now_ist(ts: float | None = None) -> datetime:
    return datetime.fromtimestamp(ts, IST) if ts is not None else datetime.now(IST)


def in_window(campaign: dict, ts: float | None = None) -> bool:
    if not campaign.get("ist_window", True):
        return True
    d = now_ist(ts)
    hour = d.hour + d.minute / 60
    return d.weekday() in campaign.get("send_days", [0, 1, 2, 3, 4, 5]) and campaign["window_start_hour"] <= hour < campaign["window_end_hour"]


def next_window_open(campaign: dict, ts: float | None = None) -> datetime | None:
    """The next moment the window opens (or now, if it's open)."""
    if in_window(campaign, ts):
        return now_ist(ts)
    days = campaign.get("send_days") or []
    if not days:
        return None
    d = now_ist(ts)
    for offset in range(0, 8):
        day = (d + timedelta(days=offset)).replace(hour=int(campaign["window_start_hour"]), minute=0, second=0, microsecond=0)
        if day.weekday() in days and day > d:
            return day
    return None


def start_of_ist_day(ts: float | None = None) -> float:
    d = now_ist(ts)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
