from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class UserDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    job_title: str = ""
    email: str
    role: str
    created_at: dt.date
    current_bookings: list[int] = Field(default_factory=list)

    @classmethod
    def from_user(cls, user: Any) -> "UserDTO":
        created = getattr(user, "created_at", None)
        if isinstance(created, dt.datetime):
            created = created.date()
        return cls(
            id=int(user.id),
            full_name=user.full_name or "",
            job_title=user.job_title or "",
            email=user.email or "",
            role=user.role or "employee",
            created_at=created or dt.date.today(),
            current_bookings=[int(value) for value in (user.current_bookings or [])],
        )


__all__ = ["UserDTO"]
