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
    planed_at: dt.datetime
    duration: str

    @classmethod
    def from_booking(cls, booking: Any) -> "BookingDTO":
        return cls(
            id=int(booking.id),
            user_id=int(booking.user_id),
            desk_id=int(booking.desk_id),
            planed_at=booking.planed_at,
            duration=format_duration(booking.duration),
        )


__all__ = ["BookingDTO", "format_duration"]
