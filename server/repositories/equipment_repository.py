from __future__ import annotations

from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from server.models.equipment import Equipment
from server.repositories.base_repository import BaseRepository


class EquipmentRepository(BaseRepository):
    def get_equipment_by_id(self, equipment_id: Optional[int]) -> Optional[Equipment]:
        return self.get(Equipment, equipment_id)

    def get_equipment_by_serial(self, serial: Optional[str]) -> Optional[Equipment]:
        if not serial:
            return None
        stmt = select(Equipment).where(Equipment.socket_serial == str(serial).strip())
        return self.session.execute(stmt).scalars().first()

    def get_all_equipment(self) -> List[Equipment]:
        stmt = select(Equipment).order_by(Equipment.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_equipment_by_user_id(self, user_id: int) -> List[Equipment]:
        stmt = (
            select(Equipment)
            .where(Equipment.assigned_user_id == int(user_id))
            .order_by(Equipment.id)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_equipment_by_desk_id(self, desk_id: int) -> List[Equipment]:
        stmt = select(Equipment).where(Equipment.desk_id == int(desk_id)).order_by(Equipment.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_equipment_by_desk_ids(self, desk_ids: Iterable[int]) -> List[Equipment]:
        ids = [int(value) for value in desk_ids]
        if not ids:
            return []
        stmt = select(Equipment).where(Equipment.desk_id.in_(ids)).order_by(Equipment.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_equipment_with_desk_and_user(self) -> List[Equipment]:
        stmt = (
            select(Equipment)
            .options(joinedload(Equipment.desk), joinedload(Equipment.assigned_user))
            .order_by(Equipment.id)
        )
        return list(self.session.execute(stmt).scalars().all())

    def create_equipment(self, equipment: Equipment) -> Equipment:
        return self.add(equipment)

    def update_equipment(self, equipment: Equipment) -> Equipment:
        return self.save(equipment)

    def delete_equipment(self, equipment_id: int) -> bool:
        equipment = self.get_equipment_by_id(equipment_id)
        return self.delete(equipment) if equipment else False


__all__ = ["EquipmentRepository"]
