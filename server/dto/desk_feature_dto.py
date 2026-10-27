from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class DeskFeatureDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str = ""

    @classmethod
    def from_feature(cls, feature: Any) -> "DeskFeatureDTO":
        return cls(
            id=int(feature.id),
            name=feature.name or "",
            description=feature.description or "",
        )


__all__ = ["DeskFeatureDTO"]
