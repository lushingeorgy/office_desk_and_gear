from __future__ import annotations

import datetime as dt
from server.core.time import office_now
from typing import Any, Optional

from server.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from server.dto.ticket_dto import TicketDTO
from server.models.tickets import (
    STATUS_CLOSED,
    STATUS_NEW,
    TICKET_STATUSES,
    TICKET_TRANSITIONS,
    Ticket,
)
from server.repositories.equipment_repository import EquipmentRepository
from server.repositories.ticket_repository import TicketRepository
from server.models.activity import Notification, TicketEvent


class TicketService:
    def __init__(
        self,
        ticket_repository: TicketRepository,
        equipment_repository: Optional[EquipmentRepository] = None,
    ) -> None:
        self.ticket_repository = ticket_repository
        self.equipment_repository = equipment_repository


    def get_ticket(self, ticket_id: int) -> TicketDTO:
        ticket = self.ticket_repository.get_ticket_by_id(ticket_id)
        if ticket is None:
            raise NotFoundError("Тикет не найден")
        return TicketDTO.from_ticket(ticket)

    def get_ticket_by_id(self, ticket_id: int) -> Optional[TicketDTO]:
        ticket = self.ticket_repository.get_ticket_by_id(ticket_id)
        return TicketDTO.from_ticket(ticket) if ticket else None

    def get_all_tickets(self) -> list[TicketDTO]:
        return [TicketDTO.from_ticket(item) for item in self.ticket_repository.get_all_tickets()]

    def get_my_tickets(self, user_id: int) -> list[TicketDTO]:
        return [
            TicketDTO.from_ticket(item)
            for item in self.ticket_repository.get_tickets_by_user_id(user_id)
        ]

    def get_tickets(self, user_id: Optional[int] = None) -> list[TicketDTO]:
        return self.get_my_tickets(user_id) if user_id is not None else self.get_all_tickets()


    def create_ticket(self, payload: Any, user_id: int) -> TicketDTO:
        equipment_id = getattr(payload, "equipment_id", None)
        if equipment_id is None:
            raise ValidationError("Укажите оборудование")

        if self.equipment_repository is not None:
            equipment = self.equipment_repository.get_equipment_by_id(equipment_id)
            if equipment is None:
                raise NotFoundError("Единица техники не найдена")
            from server.repositories.user_repository import UserRepository
            from server.repositories.booking_repository import BookingRepository
            user = UserRepository(self.ticket_repository.session).get_user_by_id(user_id)
            if user is not None and user.role != "it_admin":
                if equipment.assigned_user_id is not None and equipment.assigned_user_id != user_id:
                    raise ForbiddenError("Техника закреплена за другим сотрудником")
                desk_ids = BookingRepository(self.ticket_repository.session).get_desk_ids_with_active_booking(user_id)
                if equipment.assigned_user_id != user_id and equipment.desk_id not in desk_ids:
                    raise ForbiddenError("Создать обращение можно для своего оборудования или техники на текущем рабочем месте")

        description = str(getattr(payload, "description", "") or "").strip()
        if not description:
            raise ValidationError("Опишите проблему")

        photo_url = getattr(payload, "photo_url", None)
        photo_url = str(photo_url).strip() if photo_url not in (None, "") else None
        from server.repositories.activity_repository import ActivityRepository
        ActivityRepository(self.ticket_repository.session).validate_photo(photo_url, user_id)

        ticket = Ticket(
            author_id=int(user_id),
            equipment_id=int(equipment_id),
            description=description,
            photo_url=photo_url,
            status=STATUS_NEW,
            created_at=office_now(),
        )
        self.ticket_repository.session.add(ticket)
        self.ticket_repository.flush()
        self.ticket_repository.session.add(TicketEvent(ticket_id=ticket.id, author_id=user_id, kind="status", text=STATUS_NEW))
        from server.repositories.user_repository import UserRepository
        for administrator in UserRepository(self.ticket_repository.session).get_users_by_role("it_admin"):
            self.ticket_repository.session.add(Notification(user_id=administrator.id, text=f"Новое обращение №{ticket.id}: оборудование №{equipment_id}"))
        self.ticket_repository.commit()
        return TicketDTO.from_ticket(ticket)


    def update_status(self, ticket_id: int, status: str, author_id: Optional[int] = None) -> TicketDTO:
        ticket = self.ticket_repository.get_ticket_by_id(ticket_id)
        if ticket is None:
            raise NotFoundError("Тикет не найден")

        target = (status or "").strip().lower()
        if target in {"", "none"}:
            raise ValidationError("Укажите новый статус тикета")
        if target not in TICKET_STATUSES:
            raise ValidationError("Недопустимый статус тикета")

        current = (ticket.status or STATUS_NEW).strip().lower() or STATUS_NEW
        allowed = TICKET_TRANSITIONS.get(current, set(TICKET_STATUSES))
        if target not in allowed:
            raise ConflictError(
                f"Из статуса «{current}» нельзя перейти в «{target}»"
            )

        ticket.status = target
        self.ticket_repository.session.add(TicketEvent(ticket_id=ticket.id, author_id=author_id or ticket.author_id, kind="status", text=target))
        self.ticket_repository.session.add(Notification(user_id=ticket.author_id, text=f"Обращение №{ticket.id}: статус изменён на {target}"))
        ticket = self.ticket_repository.update_ticket(ticket)
        return TicketDTO.from_ticket(ticket)

    def update_ticket(self, ticket_id: int, payload: Any) -> TicketDTO:
        status = getattr(payload, "status", None)
        if hasattr(status, "value"):
            status = status.value
        return self.update_status(ticket_id, str(status))

    def delete_ticket(self, ticket_id: int) -> bool:
        ticket = self.ticket_repository.get_ticket_by_id(ticket_id)
        if ticket is None:
            return False
        return self.ticket_repository.delete_ticket(ticket_id)

    @staticmethod
    def is_final(status: str) -> bool:
        return (status or "").strip().lower() == STATUS_CLOSED


__all__ = ["TicketService"]
