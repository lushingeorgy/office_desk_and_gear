from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class TicketDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author_id: int
    equipment_id: int
    equipment_type: Optional[str] = None
    description: str
    photo_url: Optional[str] = None
    status: str = "new"
    created_at: dt.datetime

    @classmethod
    def from_ticket(cls, ticket: Any) -> "TicketDTO":
        created = ticket.created_at or dt.datetime.now()
        return cls(
            id=int(ticket.id),
            author_id=int(ticket.author_id),
            equipment_id=int(ticket.equipment_id),
            equipment_type=ticket.__dict__["equipment"].type if ticket.__dict__.get("equipment") else None,
            description=ticket.description or "",
            photo_url=ticket.photo_url,
            status=ticket.status or "new",
            created_at=created,
        )


__all__ = ["TicketDTO"]
