from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import selectinload

from server.models.desks import Desk
from server.models.desk_features import DeskFeature
from server.repositories.base_repository import BaseRepository


class DeskRepository(BaseRepository):
    def get_desk_by_id(self, desk_id: Optional[int]) -> Optional[Desk]:
        return self.get(Desk, desk_id)

    def get_desk_by_floor_and_number(
        self, floor: Optional[int], number: Optional[int]
    ) -> Optional[Desk]:
        if number is None:
            return None
        stmt = select(Desk).where(Desk.number == int(number))
        if floor is None:
            stmt = stmt.where(Desk.floor.is_(None))
        else:
            stmt = stmt.where(Desk.floor == int(floor))
        return self.session.execute(stmt).scalars().first()

    def get_all_desks(self) -> List[Desk]:
        stmt = select(Desk).order_by(Desk.floor, Desk.section, Desk.number, Desk.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_desks_filtered(
        self,
        floor: Optional[int] = None,
        section: Optional[int] = None,
        sockets: Optional[int] = None,
        features: Optional[Iterable[int]] = None,
        search: Optional[str] = None,
        sort: str = "number", order: str = "asc", limit: int = 100, offset: int = 0,
    ) -> List[Desk]:
        stmt = select(Desk).options(selectinload(Desk.feature_records))
        if floor is not None:
            stmt = stmt.where(Desk.floor == int(floor))
        if section is not None:
            stmt = stmt.where(Desk.section == int(section))
        if sockets is not None:
            stmt = stmt.where(Desk.sockets.is_not(None), Desk.sockets >= int(sockets))
        for feature_id in features or []:
            stmt = stmt.where(Desk.feature_records.any(DeskFeature.id == int(feature_id)))
        if search:
            stmt = stmt.where(cast(Desk.number, String).ilike("%" + search + "%"))
        column = {"number": Desk.number, "floor": Desk.floor, "sockets": Desk.sockets}.get(sort, Desk.number)
        stmt = stmt.order_by(column.desc() if order == "desc" else column.asc(), Desk.id).limit(limit).offset(offset)
        return list(self.session.scalars(stmt))

    def set_features(self, desk: Desk, feature_ids: Iterable[int]):
        from server.core.exceptions import ValidationError
        ids = set(int(value) for value in feature_ids)
        records = list(self.session.scalars(select(DeskFeature).where(DeskFeature.id.in_(ids)))) if ids else []
        if len(records) != len(ids):
            raise ValidationError("Неизвестная характеристика рабочего места")
        desk.feature_records = records

    def get_desks_by_feature_id(self, feature_id: int) -> List[Desk]:
        return self.get_desks_filtered(features=[feature_id])

    def create_desk(self, desk: Desk) -> Desk:
        return self.add(desk)

    def update_desk(self, desk: Desk) -> Desk:
        return self.save(desk)

    def delete_desk(self, desk_id: int) -> bool:
        desk = self.get_desk_by_id(desk_id)
        return self.delete(desk) if desk else False


__all__ = ["DeskRepository"]
