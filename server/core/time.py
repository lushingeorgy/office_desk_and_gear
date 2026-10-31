import datetime as dt
from zoneinfo import ZoneInfo
from server.core.config import settings


def office_now() -> dt.datetime:
    """Naive office wall time for the existing PostgreSQL timestamp contract."""
    return dt.datetime.now(ZoneInfo(settings.OFFICE_TIMEZONE)).replace(tzinfo=None)


def utc_to_office(value: dt.datetime) -> dt.datetime:
    """Render UTC audit storage using the same office-time API contract as bookings."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(ZoneInfo(settings.OFFICE_TIMEZONE)).replace(tzinfo=None)
