from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models import Base


class Desk(Base):
    __tablename__ = "desks"
    __table_args__ = (
        UniqueConstraint("floor", "number", name="uq_desks_floor_number"),
        Index("ix_desks_features", "features", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    floor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sockets: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    features: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    bookings = relationship("Booking", lazy="raise", back_populates="desk", cascade="all, delete-orphan")
    equipment = relationship("Equipment", lazy="raise",
        back_populates="desk",
        foreign_keys="Equipment.desk_id",
    )

    def __repr__(self) -> str:
        return f"<Desk id={self.id} floor={self.floor} number={self.number}>"

    @property
    def feature_ids(self) -> list[int]:
        return [int(value) for value in (self.features or [])]


__all__ = ["Desk"]
