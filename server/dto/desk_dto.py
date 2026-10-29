from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class DeskDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    floor: Optional[int] = None
    section: Optional[int] = None
    number: int
    is_free: bool = True
    is_operational: bool = True
    sockets: Optional[int] = None
    features: list[int] = Field(default_factory=list)

    @classmethod
    def from_desk(cls, desk: Any) -> "DeskDTO":
        return cls(
            id=int(desk.id),
            floor=desk.floor,
            section=desk.section,
            number=int(desk.number),
            is_free=bool(desk.is_free),
            is_operational=bool(desk.is_free),
            sockets=desk.sockets,
            features=[int(value) for value in (desk.features or [])],
        )


__all__ = ["DeskDTO"]
