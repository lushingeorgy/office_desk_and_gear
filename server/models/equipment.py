from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models import Base

STATUS_IN_STOCK = "in_stock"
STATUS_ASSIGNED = "assigned"
STATUS_IN_REPAIR = "in_repair"
STATUS_WRITTEN_OFF = "written_off"
EQUIPMENT_STATUSES = (STATUS_IN_STOCK, STATUS_ASSIGNED, STATUS_IN_REPAIR, STATUS_WRITTEN_OFF)


class Equipment(Base):
    __tablename__ = "equipment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    desk_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("desks.id"), nullable=True, index=True
    )
    assigned_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    socket_serial: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    socket_needed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=STATUS_IN_STOCK)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, default=dt.datetime.now
    )

    desk = relationship("Desk", lazy="raise",
        back_populates="equipment",
        foreign_keys="Equipment.desk_id",
    )
    assigned_user = relationship("User", lazy="raise",
        back_populates="equipment",
        foreign_keys="Equipment.assigned_user_id",
    )
    tickets = relationship("Ticket", lazy="raise", back_populates="equipment")

    def __repr__(self) -> str:
        return f"<Equipment id={self.id} type={self.type!r} status={self.status!r}>"


__all__ = [
    "Equipment",
    "EQUIPMENT_STATUSES",
    "STATUS_IN_STOCK",
    "STATUS_ASSIGNED",
    "STATUS_IN_REPAIR",
    "STATUS_WRITTEN_OFF",
]
