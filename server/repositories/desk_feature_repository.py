from __future__ import annotations

from typing import List, Optional

from sqlalchemy import select

from server.models.desk_features import DeskFeature
from server.repositories.base_repository import BaseRepository


class DeskFeatureRepository(BaseRepository):
    def get_feature_by_id(self, feature_id: Optional[int]) -> Optional[DeskFeature]:
        return self.get(DeskFeature, feature_id)

    def get_all_features(self) -> List[DeskFeature]:
        stmt = select(DeskFeature).order_by(DeskFeature.id)
        return list(self.session.execute(stmt).scalars().all())

    def create_feature(self, feature: DeskFeature) -> DeskFeature:
        return self.add(feature)

    def update_feature(self, feature: DeskFeature) -> DeskFeature:
        return self.save(feature)

    def delete_feature(self, feature_id: int) -> bool:
        feature = self.get_feature_by_id(feature_id)
        return self.delete(feature) if feature else False


__all__ = ["DeskFeatureRepository"]
