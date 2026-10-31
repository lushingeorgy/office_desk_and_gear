from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict


def format_duration(value: Any) -> str:
    if value is None:
        return "00:00:00"
    if isinstance(value, dt.timedelta):
        total_seconds = int(value.total_seconds())
    elif isinstance(value, (int, float)):
        total_seconds = int(float(value) * 3600)
    else:
        text = str(value)
        parts = text.split(":")
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return "00:00:00"
        while len(numbers) < 3:
            numbers.append(0)
        total_seconds = numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    total_seconds = max(total_seconds, 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class BookingDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    desk_id: int
    desk_number: int | None = None
    desk_floor: int | None = None
    desk_section: int | None = None
    planed_at: dt.datetime
    duration: str
    is_cancelled: bool = False
    cancellation_reason: str | None = None
    cancelled_at: dt.datetime | None = None

    @classmethod
    def from_booking(cls, booking: Any) -> "BookingDTO":
        desk = getattr(booking, "__dict__", {}).get("desk")
        return cls(
            id=int(booking.id),
            user_id=int(booking.user_id),
            desk_id=int(booking.desk_id),
            desk_number=desk.number if desk is not None else None,
            desk_floor=desk.floor if desk is not None else None,
            desk_section=desk.section if desk is not None else None,
            planed_at=booking.planed_at,
            duration=format_duration(booking.duration),
            is_cancelled=booking.is_cancelled,
            cancellation_reason=booking.cancellation_reason,
            cancelled_at=booking.cancelled_at,
        )


__all__ = ["BookingDTO", "format_duration"]
