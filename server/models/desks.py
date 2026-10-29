from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Integer, Table, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.models import Base

desk_feature_links = Table("desk_feature_links", Base.metadata,
    Column("desk_id", ForeignKey("desks.id", ondelete="CASCADE"), primary_key=True),
    Column("feature_id", ForeignKey("desk_features.id", ondelete="RESTRICT"), primary_key=True, index=True))


class Desk(Base):
    __tablename__ = "desks"
    __table_args__ = (
        UniqueConstraint("floor", "number", name="uq_desks_floor_number"),
        CheckConstraint("number > 0", name="ck_desks_number"),
        CheckConstraint("sockets IS NULL OR sockets >= 0", name="ck_desks_sockets"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    floor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    section: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sockets: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    feature_records = relationship("DeskFeature", secondary=desk_feature_links, lazy="selectin")

    @property
    def features(self) -> list[int]:
        return sorted(feature.id for feature in self.feature_records)

    bookings = relationship("Booking", lazy="raise", back_populates="desk", passive_deletes=True)
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
