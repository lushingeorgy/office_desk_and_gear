from __future__ import annotations

import datetime as dt
from server.core.time import office_now
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from server.models.bookings import Booking
from server.models.desks import Desk
from server.repositories.base_repository import BaseRepository


def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time.min)
    return start, start + dt.timedelta(days=1)


class BookingRepository(BaseRepository):
    @staticmethod
    def _display_query():
        return select(Booking).options(
            joinedload(Booking.desk).load_only(Desk.number, Desk.floor, Desk.section).raiseload("*")
        ).execution_options(populate_existing=True)

    def get_booking_by_id(self, booking_id: Optional[int]) -> Optional[Booking]:
        return self.session.scalar(self._display_query().where(Booking.id == booking_id))

    def get_bookings_by_ids(self, booking_ids) -> List[Booking]:
        return list(self.session.scalars(self._display_query().where(Booking.id.in_(booking_ids)).order_by(Booking.id)))

    def get_active_booking_by_id(self, booking_id: Optional[int]) -> Optional[Booking]:
        booking = self.get_booking_by_id(booking_id)
        if booking is None or booking.is_cancelled:
            return None
        return booking

    def get_all_bookings(self) -> List[Booking]:
        stmt = (
            self._display_query()
            .where(Booking.is_cancelled.is_(False))
            .order_by(Booking.planed_at.desc(), Booking.id.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_bookings_by_user_id(self, user_id: int) -> List[Booking]:
        stmt = (
            self._display_query()
            .where(Booking.user_id == int(user_id))
            .order_by(Booking.planed_at.desc(), Booking.id.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_bookings_by_desk_id(
        self,
        desk_id: int,
        day: Optional[dt.date] = None,
        include_past: bool = True,
    ) -> List[Booking]:
        stmt = self._display_query().where(
            Booking.desk_id == int(desk_id), Booking.is_cancelled.is_(False)
        )
        if day is not None:
            start, end = _day_bounds(day)
            stmt = stmt.where(Booking.planed_at < end, Booking.planed_at + Booking.duration > start)
        elif not include_past:
            stmt = stmt.where(Booking.planed_at >= office_now())
        stmt = stmt.order_by(Booking.planed_at, Booking.id)
        return list(self.session.execute(stmt).scalars().all())

    def find_overlap(
        self,
        desk_id: int,
        start: dt.datetime,
        end: dt.datetime,
        exclude_booking_id: Optional[int] = None,
    ) -> Optional[Booking]:
        stmt = (
            select(Booking)
            .where(
                Booking.desk_id == int(desk_id),
                Booking.is_cancelled.is_(False),
                Booking.planed_at < end,
            )
            .order_by(Booking.planed_at)
        )
        if exclude_booking_id is not None:
            stmt = stmt.where(Booking.id != int(exclude_booking_id))
        for booking in self.session.execute(stmt).scalars().all():
            if booking.ends_at > start:
                return booking
        return None

    def find_user_overlap(
        self,
        user_id: int,
        start: dt.datetime,
        end: dt.datetime,
        exclude_booking_id: Optional[int] = None,
    ) -> Optional[Booking]:
        stmt = (
            select(Booking)
            .where(
                Booking.user_id == int(user_id),
                Booking.is_cancelled.is_(False),
                Booking.planed_at < end,
            )
            .order_by(Booking.planed_at)
        )
        if exclude_booking_id is not None:
            stmt = stmt.where(Booking.id != exclude_booking_id)
        for booking in self.session.execute(stmt).scalars().all():
            if booking.ends_at > start:
                return booking
        return None

    def get_active_booking(self, user_id: int, now: Optional[dt.datetime] = None) -> Optional[Booking]:
        moment = now or office_now()
        stmt = (
            self._display_query()
            .where(
                Booking.user_id == int(user_id),
                Booking.is_cancelled.is_(False),
                Booking.planed_at <= moment,
            )
            .order_by(Booking.planed_at.desc())
        )
        for booking in self.session.execute(stmt).scalars().all():
            if booking.ends_at > moment:
                return booking
        return None

    def get_bookings_between(
        self, start: dt.datetime, end: dt.datetime
    ) -> List[Booking]:
        stmt = (
            self._display_query()
            .where(
                Booking.is_cancelled.is_(False),
                Booking.planed_at < end,
                Booking.planed_at + Booking.duration > start,
            )
            .order_by(Booking.planed_at)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_desk_ids_with_active_booking(self, user_id: int, now: Optional[dt.datetime] = None) -> List[int]:
        moment = now or office_now()
        stmt = select(Booking).where(
            Booking.user_id == int(user_id),
            Booking.is_cancelled.is_(False),
            Booking.planed_at <= moment,
        )
        desk_ids: list[int] = []
        for booking in self.session.execute(stmt).scalars().all():
            if booking.ends_at >= moment and booking.desk_id not in desk_ids:
                desk_ids.append(int(booking.desk_id))
        return desk_ids

    def occupied_desk_ids(self, now: Optional[dt.datetime] = None) -> set[int]:
        moment = now or office_now()
        bookings = self.session.execute(select(Booking).where(
            Booking.is_cancelled.is_(False), Booking.planed_at <= moment,
        )).scalars().all()
        return {int(b.desk_id) for b in bookings if b.ends_at > moment}

    def occupied_between(self, start: dt.datetime, end: dt.datetime) -> set[int]:
        bookings = self.session.scalars(select(Booking).where(Booking.is_cancelled.is_(False), Booking.planed_at < end))
        return {b.desk_id for b in bookings if b.ends_at > start}

    def create_booking(self, booking: Booking) -> Booking:
        return self.add(booking)

    def update_booking(self, booking: Booking) -> Booking:
        return self.save(booking)

    def delete_booking(self, booking_id: int) -> bool:
        booking = self.get_booking_by_id(booking_id)
        return self.delete(booking) if booking else False


__all__ = ["BookingRepository"]
