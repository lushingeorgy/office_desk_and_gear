from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from server.models.equipment import Equipment
from server.models.tickets import Ticket
from server.repositories.base_repository import BaseRepository


class TicketRepository(BaseRepository):
    def get_ticket_by_id(self, ticket_id: Optional[int]) -> Optional[Ticket]:
        return self.session.get(Ticket, ticket_id, options=[joinedload(Ticket.equipment)])

    def get_all_tickets(self) -> List[Ticket]:
        stmt = select(Ticket).options(joinedload(Ticket.equipment)).order_by(Ticket.created_at.desc(), Ticket.id.desc())
        return list(self.session.execute(stmt).scalars().all())

    def get_tickets_by_user_id(self, user_id: int) -> List[Ticket]:
        stmt = (
            select(Ticket)
            .options(joinedload(Ticket.equipment))
            .where(Ticket.author_id == int(user_id))
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_tickets_by_equipment_id(self, equipment_id: int) -> List[Ticket]:
        stmt = (
            select(Ticket)
            .where(Ticket.equipment_id == int(equipment_id))
            .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def count_by_equipment_type(
        self, start: Optional[dt.datetime] = None, end: Optional[dt.datetime] = None
    ) -> List[tuple[str, int]]:
        count_column = func.count(Ticket.id)
        stmt = select(Equipment.type, count_column).join(
            Ticket, Ticket.equipment_id == Equipment.id
        )
        if start is not None:
            stmt = stmt.where(Ticket.created_at >= start)
        if end is not None:
            stmt = stmt.where(Ticket.created_at < end)
        stmt = stmt.group_by(Equipment.type).order_by(count_column.desc(), Equipment.type)
        return [(row[0] or "—", int(row[1])) for row in self.session.execute(stmt).all()]

    def create_ticket(self, ticket: Ticket) -> Ticket:
        return self.add(ticket)

    def update_ticket(self, ticket: Ticket) -> Ticket:
        return self.save(ticket)

    def delete_ticket(self, ticket_id: int) -> bool:
        ticket = self.get_ticket_by_id(ticket_id)
        return self.delete(ticket) if ticket else False


__all__ = ["TicketRepository"]
