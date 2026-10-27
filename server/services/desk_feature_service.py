from __future__ import annotations

from typing import Any, Optional

from server.core.exceptions import ConflictError, NotFoundError
from server.dto.desk_feature_dto import DeskFeatureDTO
from server.models.desk_features import DeskFeature
from server.repositories.desk_feature_repository import DeskFeatureRepository


class DeskFeatureService:
    def __init__(self, desk_feature_repository: DeskFeatureRepository) -> None:
        self.desk_feature_repository = desk_feature_repository

    def get_desk_feature(self, feature_id: int) -> DeskFeatureDTO:
        feature = self.desk_feature_repository.get_feature_by_id(feature_id)
        if feature is None:
            raise NotFoundError("Характеристика не найдена")
        return DeskFeatureDTO.from_feature(feature)

    def get_desk_feature_by_id(self, feature_id: int) -> DeskFeatureDTO:
        return self.get_desk_feature(feature_id)

    def get_all_desk_features(self) -> list[DeskFeatureDTO]:
        return [
            DeskFeatureDTO.from_feature(feature)
            for feature in self.desk_feature_repository.get_all_features()
        ]


    get_desk_features = get_all_desk_features

    def create_desk_feature(self, payload: Any) -> DeskFeatureDTO:
        name = str(getattr(payload, "name", "") or "").strip()
        if not name:
            raise ConflictError("Укажите название характеристики")
        feature = DeskFeature(
            name=name, description=str(getattr(payload, "description", "") or "")
        )
        return DeskFeatureDTO.from_feature(self.desk_feature_repository.create_feature(feature))

    def update_desk_feature(self, feature_id: int, payload: Any) -> DeskFeatureDTO:
        feature = self.desk_feature_repository.get_feature_by_id(feature_id)
        if feature is None:
            raise NotFoundError("Характеристика не найдена")
        data = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else dict(payload)
        if "name" in data:
            feature.name = str(data["name"])
        if "description" in data:
            feature.description = str(data["description"])
        return DeskFeatureDTO.from_feature(self.desk_feature_repository.update_feature(feature))

    def delete_desk_feature(self, feature_id: int) -> bool:
        feature = self.desk_feature_repository.get_feature_by_id(feature_id)
        if feature is None:
            raise NotFoundError("Характеристика не найдена")
        return self.desk_feature_repository.delete_feature(feature_id)


__all__ = ["DeskFeatureService"]
