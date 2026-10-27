from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from server.models import Base


class DeskFeature(Base):
    __tablename__ = "desk_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")

    def __repr__(self) -> str:
        return f"<DeskFeature id={self.id} name={self.name!r}>"


__all__ = ["DeskFeature"]
