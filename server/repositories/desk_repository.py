from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from sqlalchemy import select

from server.models.desks import Desk
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
    ) -> List[Desk]:
        stmt = select(Desk)
        if floor is not None:
            stmt = stmt.where(Desk.floor == int(floor))
        if section is not None:
            stmt = stmt.where(Desk.section == int(section))
        if sockets is not None:
            stmt = stmt.where(Desk.sockets.is_not(None), Desk.sockets >= int(sockets))
        stmt = stmt.order_by(Desk.floor, Desk.section, Desk.number, Desk.id)

        desks = list(self.session.execute(stmt).scalars().all())
        wanted: Sequence[int] = [int(value) for value in (features or [])]
        if not wanted:
            return desks
        return [
            desk
            for desk in desks
            if all(feature in [int(v) for v in (desk.features or [])] for feature in wanted)
        ]

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
