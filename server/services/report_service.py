from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from server.core.config import settings
from server.core.exceptions import ValidationError
from server.dto.report_dto import AnalyticsDTO, EquipmentIssueDTO
from server.repositories.report_repository import ReportRepository
from server.repositories.ticket_repository import TicketRepository

SECONDS_IN_HOUR = 3600.0


class ReportService:
    def __init__(
        self,
        report_repository: ReportRepository,
        ticket_repository: Optional[TicketRepository] = None,
    ) -> None:
        self.report_repository = report_repository
        self.ticket_repository = ticket_repository


    @staticmethod
    def resolve_period(
        start_date: Optional[Any] = None, end_date: Optional[Any] = None
    ) -> tuple[dt.date, dt.date]:
        today = dt.date.today()
        default_start = today - dt.timedelta(days=settings.ANALYTICS_DEFAULT_DAYS)

        start = ReportService._to_date(start_date) or default_start
        end = ReportService._to_date(end_date) or today
        if end < start:
            raise ValidationError("Дата окончания периода раньше даты начала")
        return start, end

    @staticmethod
    def _to_date(value: Optional[Any]) -> Optional[dt.date]:
        if value is None or value == "":
            return None
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        try:
            return dt.date.fromisoformat(str(value).strip()[:10])
        except ValueError:
            raise ValidationError("Дата указана в неверном формате (ожидается ГГГГ-ММ-ДД)") from None


    def get_analytics(
        self,
        start_date: Optional[Any] = None,
        end_date: Optional[Any] = None,
    ) -> AnalyticsDTO:
        start, end = self.resolve_period(start_date, end_date)
        start_dt = dt.datetime.combine(start, dt.time.min)
        end_dt = dt.datetime.combine(end, dt.time.min) + dt.timedelta(days=1)
        days = (end - start).days + 1

        total_bookings = self.report_repository.count_bookings(start_dt, end_dt)
        booked_seconds = self.report_repository.sum_booked_seconds(start_dt, end_dt)
        desk_count = self.report_repository.count_desks()
        available_hours = desk_count * days * max(int(settings.WORKDAY_HOURS), 1)
        booked_hours = booked_seconds / SECONDS_IN_HOUR
        occupancy_rate = 0.0
        if available_hours > 0:
            occupancy_rate = min(booked_hours / available_hours, 1.0)

        issues: list[EquipmentIssueDTO] = []
        if self.ticket_repository is not None:
            issues = [
                EquipmentIssueDTO(equipment_type=type_name, ticket_count=count)
                for type_name, count in self.ticket_repository.count_by_equipment_type(
                    start_dt, end_dt
                )
            ]

        return AnalyticsDTO(
            period=f"{start.isoformat()} .. {end.isoformat()}",
            occupancy_rate=round(occupancy_rate, 4),
            total_bookings=int(total_bookings),
            most_frequent_issues=issues,
        )

    def build_report(self, start_date: Optional[Any] = None, end_date: Optional[Any] = None) -> dict:
        analytics = self.get_analytics(start_date, end_date)
        return analytics.model_dump(mode="json")


__all__ = ["ReportService", "SECONDS_IN_HOUR"]
