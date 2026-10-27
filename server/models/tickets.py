from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models import Base

STATUS_NEW = "new"
STATUS_IN_PROGRESS = "in_progress"
STATUS_RESOLVED = "resolved"
STATUS_CLOSED = "closed"
TICKET_STATUSES = (STATUS_NEW, STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_CLOSED)


TICKET_TRANSITIONS: dict[str, set[str]] = {
    STATUS_NEW: {STATUS_NEW, STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_CLOSED},
    STATUS_IN_PROGRESS: {STATUS_IN_PROGRESS, STATUS_RESOLVED, STATUS_CLOSED, STATUS_NEW},
    STATUS_RESOLVED: {STATUS_RESOLVED, STATUS_CLOSED, STATUS_IN_PROGRESS},
    STATUS_CLOSED: {STATUS_CLOSED, STATUS_RESOLVED},
}


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    equipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("equipment.id"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    photo_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_NEW)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, default=dt.datetime.now
    )

    author = relationship("User", lazy="raise", back_populates="tickets")
    equipment = relationship("Equipment", lazy="raise", back_populates="tickets")

    def __repr__(self) -> str:
        return f"<Ticket id={self.id} equipment={self.equipment_id} status={self.status!r}>"


__all__ = [
    "Ticket",
    "TICKET_STATUSES",
    "TICKET_TRANSITIONS",
    "STATUS_NEW",
    "STATUS_IN_PROGRESS",
    "STATUS_RESOLVED",
    "STATUS_CLOSED",
]
