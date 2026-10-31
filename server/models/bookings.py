from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Interval, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models import Base


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (CheckConstraint("duration > interval '0 seconds' AND duration <= interval '24 hours'", name="ck_bookings_duration"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    desk_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("desks.id"), nullable=False, index=True
    )
    planed_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, index=True)
    duration: Mapped[dt.timedelta] = mapped_column(Interval, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, default=dt.datetime.now
    )
    is_cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    cancelled_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, nullable=True)

    user = relationship("User", lazy="raise", back_populates="bookings")
    desk = relationship("Desk", lazy="raise", back_populates="bookings")

    def __repr__(self) -> str:
        return f"<Booking id={self.id} desk={self.desk_id} at={self.planed_at}>"

    @property
    def ends_at(self) -> dt.datetime:
        return self.planed_at + (self.duration or dt.timedelta(0))


__all__ = ["Booking"]
