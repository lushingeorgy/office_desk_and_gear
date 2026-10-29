from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy.exc import IntegrityError

from server.core.exceptions import ConflictError, NotFoundError, ValidationError
from server.dto.desk_dto import DeskDTO
from server.dto.desk_feature_dto import DeskFeatureDTO
from server.models.desks import Desk
from server.repositories.desk_feature_repository import DeskFeatureRepository
from server.repositories.desk_repository import DeskRepository
from server.repositories.booking_repository import BookingRepository

PARTIAL_MATCH_MESSAGE = (
    "Строгий фильтр не дал результатов: показаны столы без учёта числа розеток"
)


@dataclass
class DeskListResult:
    desks: list[DeskDTO] = field(default_factory=list)
    partial: bool = False
    message: Optional[str] = None


class DeskService:
    def __init__(
        self,
        desk_repository: DeskRepository,
        desk_feature_repository: Optional[DeskFeatureRepository] = None,
    ) -> None:
        self.desk_repository = desk_repository
        self.desk_feature_repository = desk_feature_repository


    def get_desk(self, desk_id: int) -> DeskDTO:
        desk = self.desk_repository.get_desk_by_id(desk_id)
        if desk is None:
            raise NotFoundError("Рабочее место не найдено")
        return DeskDTO.from_desk(desk)

    def get_desk_by_id(self, desk_id: int) -> DeskDTO:
        return self.get_desk(desk_id)

    def get_desks(self) -> list[DeskDTO]:
        return [DeskDTO.from_desk(desk) for desk in self.desk_repository.get_all_desks()]

    def get_all_desks(self) -> list[DeskDTO]:
        return self.get_desks()

    def _available_desks(self, desks, start_at=None, end_at=None) -> list[DeskDTO]:
        from server.services.booking_service import _local_datetime
        repository = BookingRepository(self.desk_repository.session)
        occupied = repository.occupied_between(_local_datetime(start_at), _local_datetime(end_at)) if start_at and end_at else repository.occupied_desk_ids()
        result = []
        for desk in desks:
            item = DeskDTO.from_desk(desk)
            item.is_free = item.is_free and item.id not in occupied
            result.append(item)
        return result

    def filter_desks(
        self,
        floor: Optional[int] = None,
        section: Optional[int] = None,
        sockets: Optional[int] = None,
        features: Optional[Iterable[int]] = None,
        search: Optional[str] = None, sort: str = "number", order: str = "asc", limit: int = 100, offset: int = 0,
        start_at=None, end_at=None,
    ) -> DeskListResult:
        requested = [int(value) for value in (features or [])]
        found = self.desk_repository.get_desks_filtered(
            floor=floor, section=section, sockets=sockets, features=requested, search=search, sort=sort, order=order, limit=limit, offset=offset
        )
        if found:
            return DeskListResult(desks=self._available_desks(found, start_at, end_at))

        if sockets is not None:
            softened = self.desk_repository.get_desks_filtered(
                floor=floor, section=section, sockets=None, features=requested, search=search, sort=sort, order=order, limit=limit, offset=offset
            )
            if softened:
                return DeskListResult(
                    desks=self._available_desks(softened, start_at, end_at),
                    partial=True,
                    message=PARTIAL_MATCH_MESSAGE,
                )
        return DeskListResult(desks=[])

    def get_desk_features(self) -> list[DeskFeatureDTO]:
        if self.desk_feature_repository is None:
            return []
        return [
            DeskFeatureDTO.from_feature(feature)
            for feature in self.desk_feature_repository.get_all_features()
        ]


    def create_desk(self, payload: Any) -> DeskDTO:
        floor = getattr(payload, "floor", None)
        section = getattr(payload, "section", None)
        number = getattr(payload, "number", None)
        if number is None:
            raise ValidationError("Укажите номер рабочего места")

        if self.desk_repository.get_desk_by_floor_and_number(floor, number) is not None:
            raise ConflictError(
                f"Рабочее место №{number} уже существует на этом этаже"
            )

        desk = Desk(
            floor=floor,
            section=section,
            number=int(number),
            is_free=bool(getattr(payload, "is_free", True)),
            sockets=getattr(payload, "sockets", None),
        )
        self.desk_repository.set_features(desk, getattr(payload, "features", None) or [])
        try:
            desk = self.desk_repository.create_desk(desk)
        except IntegrityError as error:
            self.desk_repository.rollback()
            raise ConflictError(
                f"Рабочее место №{number} уже существует на этом этаже"
            ) from error
        return DeskDTO.from_desk(desk)

    def update_desk(self, desk_id: int, payload: Any) -> DeskDTO:
        desk = self.desk_repository.get_desk_by_id(desk_id)
        if desk is None:
            raise NotFoundError("Рабочее место не найдено")

        data = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else dict(payload)
        data.pop("id", None)

        new_number = data.get("number", desk.number)
        new_floor = data.get("floor", desk.floor)
        if ("number" in data or "floor" in data) and (
            int(new_number) != int(desk.number) or new_floor != desk.floor
        ):
            twin = self.desk_repository.get_desk_by_floor_and_number(new_floor, new_number)
            if twin is not None and int(twin.id) != int(desk.id):
                raise ConflictError(
                    f"Рабочее место №{new_number} уже существует на этом этаже"
                )

        for field_name in ("floor", "section", "number", "is_free", "sockets"):
            if field_name in data:
                value = data[field_name]
                if field_name == "number":
                    value = int(value)
                if field_name == "is_free":
                    value = bool(value)
                setattr(desk, field_name, value)
        if "features" in data:
            self.desk_repository.set_features(desk, data["features"] or [])

        try:
            desk = self.desk_repository.update_desk(desk)
        except IntegrityError as error:
            self.desk_repository.rollback()
            raise ConflictError("Стол с такими параметрами уже существует") from error
        return DeskDTO.from_desk(desk)

    def delete_desk(self, desk_id: int) -> bool:
        desk = self.desk_repository.get_desk_by_id(desk_id)
        if desk is None:
            raise NotFoundError("Рабочее место не найдено")
        from sqlalchemy import select, exists
        from server.models.bookings import Booking
        from server.models.equipment import Equipment
        db = self.desk_repository.session
        if db.scalar(select(exists().where(Booking.desk_id == desk_id))) or db.scalar(select(exists().where(Equipment.desk_id == desk_id))):
            raise ConflictError("Нельзя удалить стол с историей броней или закреплённым оборудованием. Сделайте его недоступным")
        return self.desk_repository.delete_desk(desk_id)


__all__ = ["DeskService", "DeskListResult", "PARTIAL_MATCH_MESSAGE"]
