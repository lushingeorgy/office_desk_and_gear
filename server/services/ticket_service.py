from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from server.core.exceptions import ConflictError, NotFoundError, ValidationError
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

        description = str(getattr(payload, "description", "") or "").strip()
        if not description:
            raise ValidationError("Опишите проблему")

        photo_url = getattr(payload, "photo_url", None)
        photo_url = str(photo_url).strip() if photo_url not in (None, "") else None

        ticket = Ticket(
            author_id=int(user_id),
            equipment_id=int(equipment_id),
            description=description,
            photo_url=photo_url,
            status=STATUS_NEW,
            created_at=dt.datetime.now(),
        )
        ticket = self.ticket_repository.create_ticket(ticket)
        return TicketDTO.from_ticket(ticket)


    def update_status(self, ticket_id: int, status: str) -> TicketDTO:
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
