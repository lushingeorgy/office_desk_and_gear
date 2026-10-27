from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import func, select

from server.models.bookings import Booking
from server.models.desks import Desk
from server.models.reports import Report
from server.repositories.base_repository import BaseRepository


class ReportRepository(BaseRepository):
    def get_report_by_id(self, report_id: Optional[int]) -> Optional[Report]:
        return self.get(Report, report_id)

    def get_all_reports(self) -> List[Report]:
        stmt = select(Report).order_by(Report.generated_at.desc(), Report.id.desc())
        return list(self.session.execute(stmt).scalars().all())

    def get_reports_by_user_id(self, user_id: int) -> List[Report]:
        stmt = (
            select(Report)
            .where(Report.generated_by == int(user_id))
            .order_by(Report.generated_at.desc())
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_reports_with_user(self) -> List[Report]:
        from sqlalchemy.orm import joinedload

        stmt = select(Report).options(joinedload(Report.generated_by_user))
        return list(self.session.execute(stmt).scalars().all())

    def count_desks(self) -> int:
        return int(self.session.execute(select(func.count()).select_from(Desk)).scalar_one())

    def sum_booked_seconds(self, start: dt.datetime, end: dt.datetime) -> int:
        stmt = select(Booking.duration).where(
            Booking.is_cancelled.is_(False),
            Booking.planed_at >= start,
            Booking.planed_at < end,
        )
        total = 0
        for duration in self.session.scalars(stmt):
            duration = duration or dt.timedelta(0)
            total += int(duration.total_seconds())
        return total

    def count_bookings(self, start: dt.datetime, end: dt.datetime) -> int:
        stmt = (
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.is_cancelled.is_(False),
                Booking.planed_at >= start,
                Booking.planed_at < end,
            )
        )
        return int(self.session.execute(stmt).scalar_one())

    def create_report(self, report: Report) -> Report:
        return self.add(report)

    def update_report(self, report: Report) -> Report:
        return self.save(report)

    def delete_report(self, report_id: int) -> bool:
        report = self.get_report_by_id(report_id)
        return self.delete(report) if report else False


__all__ = ["ReportRepository"]
