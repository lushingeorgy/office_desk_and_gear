from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import Date, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models import Base

ROLE_EMPLOYEE = "employee"
ROLE_IT_ADMIN = "it_admin"
ROLE_OFFICE_MANAGER = "office_manager"
ROLES = (ROLE_EMPLOYEE, ROLE_IT_ADMIN, ROLE_OFFICE_MANAGER)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_title: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt.date] = mapped_column(Date, nullable=False, default=dt.date.today)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=ROLE_EMPLOYEE)
    current_bookings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    bookings = relationship("Booking", lazy="raise",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    equipment = relationship("Equipment", lazy="raise",
        back_populates="assigned_user",
        foreign_keys="Equipment.assigned_user_id",
    )
    tickets = relationship("Ticket", lazy="raise", back_populates="author")
    reports = relationship("Report", lazy="raise", back_populates="generated_by_user")

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role!r}>"

    @property
    def booking_ids(self) -> list[int]:
        return [int(value) for value in (self.current_bookings or [])]


def coerce_role(role: Optional[str]) -> str:
    candidate = (role or "").strip().lower()
    return candidate if candidate in ROLES else ROLE_EMPLOYEE


__all__ = ["User", "ROLES", "ROLE_EMPLOYEE", "ROLE_IT_ADMIN", "ROLE_OFFICE_MANAGER", "coerce_role"]
