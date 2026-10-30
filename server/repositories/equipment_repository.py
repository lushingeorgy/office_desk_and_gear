from __future__ import annotations

from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from server.models.desks import Desk
from server.models.equipment import Equipment
from server.models.users import User
from server.repositories.base_repository import BaseRepository


class EquipmentRepository(BaseRepository):
    @staticmethod
    def _display_query():
        return select(Equipment).options(
            joinedload(Equipment.desk).load_only(Desk.number, Desk.floor, Desk.section).raiseload("*"),
            joinedload(Equipment.assigned_user).load_only(User.full_name).raiseload("*"),
        )

    def get_equipment_by_id(self, equipment_id: Optional[int]) -> Optional[Equipment]:
        if equipment_id is None:
            return None
        statement = self._display_query().where(Equipment.id == equipment_id).execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def desk_exists(self, desk_id: int) -> bool:
        return self.session.scalar(select(Desk.id).where(Desk.id == desk_id)) is not None

    def user_exists(self, user_id: int) -> bool:
        return self.session.scalar(select(User.id).where(User.id == user_id)) is not None

    def get_equipment_by_serial(self, serial: Optional[str]) -> Optional[Equipment]:
        if not serial:
            return None
        stmt = self._display_query().where(Equipment.socket_serial == str(serial).strip())
        return self.session.execute(stmt).scalars().first()

    def get_all_equipment(self) -> List[Equipment]:
        stmt = self._display_query().order_by(Equipment.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_equipment_by_user_id(self, user_id: int) -> List[Equipment]:
        stmt = (
            self._display_query()
            .where(Equipment.assigned_user_id == int(user_id))
            .order_by(Equipment.id)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_equipment_by_desk_id(self, desk_id: int) -> List[Equipment]:
        stmt = self._display_query().where(Equipment.desk_id == int(desk_id)).order_by(Equipment.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_equipment_by_desk_ids(self, desk_ids: Iterable[int]) -> List[Equipment]:
        ids = [int(value) for value in desk_ids]
        if not ids:
            return []
        stmt = self._display_query().where(Equipment.desk_id.in_(ids)).order_by(Equipment.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_equipment_with_desk_and_user(self) -> List[Equipment]:
        stmt = self._display_query().order_by(Equipment.id)
        return list(self.session.execute(stmt).scalars().all())

    def create_equipment(self, equipment: Equipment) -> Equipment:
        self.add(equipment)
        return self.get_equipment_by_id(equipment.id)

    def update_equipment(self, equipment: Equipment) -> Equipment:
        self.save(equipment)
        return self.get_equipment_by_id(equipment.id)

    def delete_equipment(self, equipment_id: int) -> bool:
        equipment = self.get_equipment_by_id(equipment_id)
        return self.delete(equipment) if equipment else False


__all__ = ["EquipmentRepository"]
