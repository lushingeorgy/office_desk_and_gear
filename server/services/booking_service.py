from __future__ import annotations

import datetime as dt
from server.core.time import office_now
from typing import Any, Optional

from sqlalchemy import update
from zoneinfo import ZoneInfo
from server.core.config import settings
from server.models.activity import Notification
from server.models.desks import Desk
from server.models.users import User

from server.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from server.dto.booking_dto import BookingDTO, format_duration
from server.models.bookings import Booking
from server.repositories.booking_repository import BookingRepository
from server.repositories.desk_repository import DeskRepository
from server.repositories.user_repository import UserRepository


DEFAULT_START_HOUR = 9
MAX_DURATION_HOURS = 24


def _local_datetime(value: dt.datetime) -> dt.datetime:
    if value.utcoffset() is not None:
        return value.astimezone(ZoneInfo(settings.OFFICE_TIMEZONE)).replace(tzinfo=None)
    return value


class BookingService:
    def __init__(
        self,
        booking_repository: BookingRepository,
        desk_repository: Optional[DeskRepository] = None,
        user_repository: Optional[UserRepository] = None,
    ) -> None:
        self.booking_repository = booking_repository
        self.desk_repository = desk_repository
        self.user_repository = user_repository


    def _ensure_desk(self, desk_id: Optional[int]) -> Any:
        if self.desk_repository is None:
            return None
        if desk_id is None:
            raise ValidationError("Укажите рабочее место")
        desk = self.desk_repository.get_desk_by_id(desk_id)
        if desk is None:
            raise NotFoundError("Рабочее место не найдено")
        return desk

    def _lock_and_check_availability(self, desk_id: int, user_id: int) -> None:
        db = self.booking_repository.session


        db.execute(update(User).where(User.id == user_id).values(id=User.id))
        db.execute(update(Desk).where(Desk.id == desk_id).values(is_free=Desk.is_free))
        desk = self._ensure_desk(desk_id)
        if desk is not None:
            db.refresh(desk)
            if not desk.is_free:
                raise ConflictError("Рабочее место сейчас занято или недоступно. Обновите схему этажа")

    @staticmethod
    def _duration(duration_hours: Any) -> dt.timedelta:
        try:
            hours = int(duration_hours)
        except (TypeError, ValueError):
            raise ValidationError("Длительность брони указана неверно") from None
        if hours <= 0:
            raise ValidationError("Длительность брони должна быть больше нуля")
        if hours > MAX_DURATION_HOURS:
            raise ValidationError(f"Длительность брони не может превышать {MAX_DURATION_HOURS} ч")
        return dt.timedelta(hours=hours)

    def _check_conflicts(
        self,
        desk_id: int,
        user_id: int,
        start: dt.datetime,
        end: dt.datetime,
        exclude_booking_id: Optional[int] = None,
    ) -> None:
        desk_conflict = self.booking_repository.find_overlap(desk_id, start, end, exclude_booking_id)
        if desk_conflict is not None:
            raise ConflictError(
                "Выбранное время уже занято: на это время есть бронь этого рабочего места"
            )
        user_conflict = self.booking_repository.find_user_overlap(user_id, start, end, exclude_booking_id)
        if user_conflict is not None:
            raise ConflictError("У вас уже есть бронь на это время")

    def _attach_to_user(self, user_id: int, booking_id: int) -> None:
        pass  # Foreign-key relationship is the single source of booking membership.

    def _detach_from_user(self, user_id: int, booking_id: int) -> None:
        pass


    def get_booking(self, booking_id: int) -> Optional[BookingDTO]:
        booking = self.booking_repository.get_active_booking_by_id(booking_id)
        return BookingDTO.from_booking(booking) if booking else None

    def get_booking_by_id(self, booking_id: int) -> Optional[BookingDTO]:
        return self.get_booking(booking_id)

    def get_all_bookings(self) -> list[BookingDTO]:
        return [BookingDTO.from_booking(item) for item in self.booking_repository.get_all_bookings()]

    def get_active_booking(self, user_id: int) -> Optional[BookingDTO]:
        booking = self.booking_repository.get_active_booking(user_id)
        return BookingDTO.from_booking(booking) if booking else None

    def get_history(self, user_id: int) -> list[BookingDTO]:
        return [
            BookingDTO.from_booking(item)
            for item in self.booking_repository.get_bookings_by_user_id(user_id)
        ]

    def get_bookings(
        self, user_id: Optional[int] = None
    ) -> list[BookingDTO]:
        if user_id is None:
            return self.get_all_bookings()
        return self.get_history(user_id)

    def get_desk_bookings(self, desk_id: Optional[int], day: Optional[dt.date], owner_id: Optional[int] = None) -> list[BookingDTO]:
        if desk_id is None:
            bookings = self.booking_repository.get_bookings_between(
                dt.datetime.combine(day, dt.time.min) if day else dt.datetime.min,
                dt.datetime.combine(day, dt.time.min) + dt.timedelta(days=1)
                if day
                else dt.datetime.max,
            )
            return [BookingDTO.from_booking(item) for item in bookings if owner_id is None or item.user_id == owner_id]
        bookings = self.booking_repository.get_bookings_by_desk_id(desk_id, day)
        return [BookingDTO.from_booking(item) for item in bookings if owner_id is None or item.user_id == owner_id]

    def availability(self, desk_id: Optional[int], day: Optional[dt.date]):
        bookings = self.get_desk_bookings(desk_id, day)
        return [{"desk_id": item.desk_id, "planed_at": item.planed_at, "duration": item.duration} for item in bookings]


    def create_booking(self, payload: Any, user_id: int) -> BookingDTO:
        desk_id = getattr(payload, "desk_id", None)
        self._ensure_desk(desk_id)
        planed_at = getattr(payload, "planed_at", None)
        if not isinstance(planed_at, dt.datetime):
            raise ValidationError("Укажите дату и время начала брони")
        duration = self._duration(getattr(payload, "duration_hours", None))
        start = _local_datetime(planed_at).replace(microsecond=0)
        end = start + duration
        if end <= office_now():
            raise ValidationError("Нельзя бронировать завершившийся интервал")

        self._lock_and_check_availability(int(desk_id), int(user_id))
        self._check_conflicts(int(desk_id), int(user_id), start, end)

        booking = Booking(
            user_id=int(user_id),
            desk_id=int(desk_id),
            planed_at=start,
            duration=duration,
            created_at=office_now(),
            is_cancelled=False,
        )
        self.booking_repository.session.add(booking)
        self.booking_repository.flush()
        self._attach_to_user(int(user_id), int(booking.id))
        self.booking_repository.commit()
        return BookingDTO.from_booking(self.booking_repository.get_booking_by_id(booking.id))

    def edit_booking(self, booking_id: int, payload: Any, user_id: int) -> BookingDTO:
        booking = self.booking_repository.get_booking_by_id(booking_id)
        if booking is None or booking.is_cancelled:
            raise NotFoundError("Бронь не найдена")
        if booking.user_id != user_id:
            raise ForbiddenError("Можно изменить только собственную бронь")
        start = _local_datetime(payload.planed_at).replace(microsecond=0)
        duration = self._duration(payload.duration_hours)
        if start + duration <= office_now():
            raise ValidationError("Нельзя бронировать завершившийся интервал")
        self._lock_and_check_availability(payload.desk_id, user_id)
        self._check_conflicts(payload.desk_id, user_id, start, start + duration, booking_id)
        booking.desk_id, booking.planed_at, booking.duration = payload.desk_id, start, duration
        self.booking_repository.commit()
        return BookingDTO.from_booking(self.booking_repository.get_booking_by_id(booking.id))

    def create_booking_period(self, payload: Any, user_id: int) -> tuple[list[BookingDTO], list[str]]:
        desk_id = getattr(payload, "desk_id", None)
        self._ensure_desk(desk_id)
        start_date = getattr(payload, "start_date", None)
        end_date = getattr(payload, "end_date", None)
        if not isinstance(start_date, dt.datetime) or not isinstance(end_date, dt.datetime):
            raise ValidationError("Укажите корректный период бронирования")

        start_date = _local_datetime(start_date)
        end_date = _local_datetime(end_date)
        duration = self._duration(getattr(payload, "daily_duration_hours", None))
        first_day = start_date.date()
        last_day = end_date.date()
        if last_day < first_day:
            raise ValidationError("Дата окончания периода раньше даты начала")
        if (last_day - first_day).days > 92:
            raise ValidationError("Период бронирования не может превышать 92 дня")


        start_time = start_date.time()
        if start_time == dt.time(0, 0):
            start_time = dt.time(DEFAULT_START_HOUR, 0)
        if dt.datetime.combine(last_day, start_time) + duration <= office_now():
            raise ValidationError("Нельзя бронировать завершившийся период")

        self._lock_and_check_availability(int(desk_id), int(user_id))
        created: list[Booking] = []
        skipped: list[str] = []
        day = first_day
        while day <= last_day:
            start = dt.datetime.combine(day, start_time)
            end = start + duration
            if end <= office_now():
                skipped.append(day.isoformat())
                day += dt.timedelta(days=1)
                continue
            try:
                self._lock_and_check_availability(int(desk_id), int(user_id))
                self._check_conflicts(int(desk_id), int(user_id), start, end)
            except ConflictError:
                skipped.append(day.isoformat())
                day += dt.timedelta(days=1)
                continue
            booking = Booking(
                user_id=int(user_id),
                desk_id=int(desk_id),
                planed_at=start,
                duration=duration,
                created_at=office_now(),
                is_cancelled=False,
            )
            self.booking_repository.session.add(booking)
            self.booking_repository.flush()
            self._attach_to_user(int(user_id), int(booking.id))
            created.append(booking)
            day += dt.timedelta(days=1)
        self.booking_repository.commit()
        return [BookingDTO.from_booking(item) for item in self.booking_repository.get_bookings_by_ids([item.id for item in created])], skipped


    def cancel_booking(
        self,
        booking_id: int,
        user_id: Optional[int] = None,
        admin: bool = False,
        reason: Optional[str] = None,
    ) -> dt.datetime:
        booking = self.booking_repository.get_booking_by_id(booking_id)
        if booking is None or booking.is_cancelled:
            raise NotFoundError("Бронь не найдена")
        if not admin and user_id is not None and int(booking.user_id) != int(user_id):
            raise ForbiddenError("Можно отменить только собственную бронь")
        reason = (reason or "").strip()
        if admin and not reason:
            raise ValidationError("Укажите причину отмены брони")

        cancelled_at = office_now()
        booking.is_cancelled = True
        booking.cancelled_at = cancelled_at
        booking.cancellation_reason = (reason or "").strip() or None
        if admin:
            self.booking_repository.session.add(Notification(user_id=booking.user_id, text=f"Бронь №{booking.id} отменена администратором: {booking.cancellation_reason}"))
        self._detach_from_user(int(booking.user_id), int(booking.id))
        self.booking_repository.update_booking(booking)
        return cancelled_at

    def delete_booking(self, booking_id: int) -> bool:
        booking = self.booking_repository.get_booking_by_id(booking_id)
        if booking is None:
            return False
        self.cancel_booking(booking_id, admin=True, reason="Служебная отмена")
        return True

    def describe(self, booking: BookingDTO) -> str:
        place = f"рабочее место №{booking.desk_number}" if booking.desk_number is not None else "рабочее место"
        return (
            f"Бронь №{booking.id}: {place}, "
            f"{booking.planed_at:%d.%m.%Y %H:%M}, {format_duration(booking.duration)}"
        )


__all__ = ["BookingService", "DEFAULT_START_HOUR", "MAX_DURATION_HOURS"]
