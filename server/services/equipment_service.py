from __future__ import annotations

import base64
import io
from typing import Any, Optional

import qrcode

from sqlalchemy.exc import IntegrityError

from server.core.config import settings
from server.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from server.dto.equipment_dto import EquipmentDTO
from server.models.equipment import (
    Equipment,
    EQUIPMENT_STATUSES,
    STATUS_ASSIGNED,
    STATUS_IN_STOCK,
)
from server.repositories.booking_repository import BookingRepository
from server.repositories.desk_repository import DeskRepository
from server.repositories.equipment_repository import EquipmentRepository
from server.repositories.user_repository import UserRepository
from server.models.activity import Notification

class EquipmentService:
    def __init__(
        self,
        equipment_repository: EquipmentRepository,
        desk_repository: Optional[DeskRepository] = None,
        user_repository: Optional[UserRepository] = None,
        booking_repository: Optional[BookingRepository] = None,
    ) -> None:
        self.equipment_repository = equipment_repository
        self.desk_repository = desk_repository
        self.user_repository = user_repository
        self.booking_repository = booking_repository


    def get_equipment(self, equipment_id: int, viewer: Any = None) -> EquipmentDTO:
        item = self.equipment_repository.get_equipment_by_id(equipment_id)
        if item is None:
            raise NotFoundError("Единица техники не найдена")
        if viewer is not None and viewer.role == "employee" and item.assigned_user_id is not None and item.assigned_user_id != viewer.id:
            raise ForbiddenError("Техника закреплена за другим сотрудником")
        return EquipmentDTO.from_equipment(item)

    def get_equipment_by_id(self, equipment_id: int) -> EquipmentDTO:
        return self.get_equipment(equipment_id)

    def get_all_equipment(self) -> list[EquipmentDTO]:
        return [
            EquipmentDTO.from_equipment(item)
            for item in self.equipment_repository.get_all_equipment()
        ]

    def get_equipment_by_serial(self, serial: str) -> Optional[EquipmentDTO]:
        item = self.equipment_repository.get_equipment_by_serial(serial)
        return EquipmentDTO.from_equipment(item) if item else None

    def get_my_equipment(self, user_id: int) -> list[EquipmentDTO]:
        items: dict[int, Equipment] = {}
        for item in self.equipment_repository.get_equipment_by_user_id(user_id):
            items[int(item.id)] = item

        if self.booking_repository is not None:
            desk_ids = self.booking_repository.get_desk_ids_with_active_booking(user_id)
            for item in self.equipment_repository.get_equipment_by_desk_ids(desk_ids):
                items.setdefault(int(item.id), item)

        ordered = sorted(items.values(), key=lambda item: int(item.id))
        return [EquipmentDTO.from_equipment(item) for item in ordered]


    def create_equipment(self, payload: Any) -> EquipmentDTO:
        type_name = str(getattr(payload, "type", "") or "").strip()
        if not type_name:
            raise ValidationError("Укажите тип техники")

        serial = getattr(payload, "socket_serial", None)
        serial = str(serial).strip() if serial not in (None, "") else None
        if serial and self.equipment_repository.get_equipment_by_serial(serial) is not None:
            raise ConflictError(f"Техника с серийным номером {serial} уже зарегистрирована")

        desk_id = getattr(payload, "desk_id", None)
        if desk_id is not None and self.desk_repository is not None:
            if self.desk_repository.get_desk_by_id(desk_id) is None:
                raise NotFoundError("Рабочее место не найдено")

        assigned_user_id = getattr(payload, "assigned_user_id", None)
        if assigned_user_id is not None and self.user_repository is not None:
            if self.user_repository.get_user_by_id(assigned_user_id) is None:
                raise NotFoundError("Сотрудник не найден")

        status = getattr(payload, "status", None) or (
            STATUS_ASSIGNED if assigned_user_id is not None or desk_id is not None else STATUS_IN_STOCK
        )
        if status not in EQUIPMENT_STATUSES:
            raise ValidationError("Недопустимый статус техники")
        if status == STATUS_IN_STOCK and (assigned_user_id is not None or desk_id is not None):
            raise ValidationError("Закреплённая техника не может иметь статус «на складе»")

        item = Equipment(
            desk_id=desk_id,
            assigned_user_id=assigned_user_id,
            type=type_name,
            model=getattr(payload, "model", "") or "",
            inventory_number=getattr(payload, "inventory_number", None),
            socket_serial=serial,
            socket_needed=bool(getattr(payload, "socket_needed", False)),
            status=status,
        )
        try:
            item = self.equipment_repository.create_equipment(item)
        except IntegrityError as error:
            self.equipment_repository.rollback()
            raise ConflictError(f"Техника с серийным номером {serial} уже зарегистрирована") from error
        return EquipmentDTO.from_equipment(item)

    def assign_equipment(self, equipment_id: int, payload: Any) -> EquipmentDTO:
        item = self.equipment_repository.get_equipment_by_id(equipment_id)
        if item is None:
            raise NotFoundError("Единица техники не найдена")

        data = payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else dict(payload or {})

        assigned_user_id = data.get("assigned_user_id", item.assigned_user_id)
        desk_id = data.get("desk_id", item.desk_id) if "desk_id" in data else item.desk_id
        if item.status in {"in_repair", "written_off"} and (assigned_user_id is not None or desk_id is not None):
            raise ConflictError("Нельзя выдавать технику в ремонте или списанную технику")

        if assigned_user_id is not None and self.user_repository is not None:
            if self.user_repository.get_user_by_id(assigned_user_id) is None:
                raise NotFoundError("Сотрудник не найден")
        if desk_id is not None and self.desk_repository is not None:
            if self.desk_repository.get_desk_by_id(desk_id) is None:
                raise NotFoundError("Рабочее место не найдено")

        item.assigned_user_id = assigned_user_id
        item.desk_id = desk_id
        item.status = STATUS_ASSIGNED if assigned_user_id is not None or desk_id is not None else STATUS_IN_STOCK
        if assigned_user_id is not None:
            self.equipment_repository.session.add(Notification(user_id=assigned_user_id, text=f"Вам назначено оборудование: {item.type} (№{item.id})"))
        return EquipmentDTO.from_equipment(self.equipment_repository.update_equipment(item))

    def update_equipment(self, equipment_id: int, payload: Any) -> EquipmentDTO:
        item = self.equipment_repository.get_equipment_by_id(equipment_id)
        if item is None:
            raise NotFoundError("Единица техники не найдена")
        data = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") else dict(payload)
        data.pop("id", None)
        for field in ("model", "inventory_number"):
            if field in data:
                setattr(item, field, data[field])
        if "type" in data:
            item.type = str(data["type"]).strip()
            if not item.type:
                raise ValidationError("Укажите тип техники")
        if "socket_serial" in data:
            serial = data["socket_serial"]
            twin = self.equipment_repository.get_equipment_by_serial(serial)
            if twin is not None and int(twin.id) != int(item.id):
                raise ConflictError(f"Техника с серийным номером {serial} уже зарегистрирована")
            item.socket_serial = serial
        if "socket_needed" in data:
            item.socket_needed = bool(data["socket_needed"])
        if "status" in data and data["status"] in EQUIPMENT_STATUSES:
            item.status = data["status"]
        if "desk_id" in data or "assigned_user_id" in data:
            if "assigned_user_id" in data:
                item.assigned_user_id = data["assigned_user_id"]
            if "desk_id" in data:
                item.desk_id = data["desk_id"]
            item.status = STATUS_ASSIGNED if item.assigned_user_id is not None else STATUS_IN_STOCK
        return EquipmentDTO.from_equipment(self.equipment_repository.update_equipment(item))

    def delete_equipment(self, equipment_id: int) -> bool:
        item = self.equipment_repository.get_equipment_by_id(equipment_id)
        if item is None:
            raise NotFoundError("Единица техники не найдена")
        from sqlalchemy import select, exists
        from server.models.tickets import Ticket
        if self.equipment_repository.session.scalar(select(exists().where(Ticket.equipment_id == equipment_id))):
            raise ConflictError("Нельзя удалить технику с историей обращений. Используйте статус списания")
        return self.equipment_repository.delete_equipment(equipment_id)


    @staticmethod
    def build_qr_payload(equipment_id: int) -> str:
        return f"{settings.QR_BASE_URL}/{int(equipment_id)}"

    def generate_qr_code(self, equipment_id: int) -> dict:
        self.get_equipment(equipment_id)
        url = self.build_qr_payload(equipment_id)
        return {
            "equipment_id": int(equipment_id),
            "qr_code_url": url,
            "qr_code_base64": self._render_qr_base64(url),
        }

    @staticmethod
    def _render_qr_base64(text: str) -> str:
        try:
            code = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=4,
            )
            code.add_data(text)
            code.make(fit=True)
            image = code.make_image(fill_color="black", back_color="white")
        except Exception:
            return ""

        buffer = io.BytesIO()
        try:
            image.save(buffer, format="PNG")
        except Exception:
            buffer = io.BytesIO()
            image.save(buffer)
        return base64.b64encode(buffer.getvalue()).decode("ascii")


__all__ = ["EquipmentService"]
