from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReportDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_type: str
    generated_at: str
    data: dict = Field(default_factory=dict)
    generated_by: int | None = None
    status: str = "ready"

    @classmethod
    def from_report(cls, report: Any) -> "ReportDTO":
        return cls(
            id=int(report.id),
            report_type=report.report_type or "",
            generated_at=str(report.generated_at),
            data=report.data or {},
            generated_by=report.generated_by,
            status=report.status or "ready",
        )


class EquipmentIssueDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    equipment_type: str
    ticket_count: int


class AnalyticsDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period: str
    occupancy_rate: float
    total_bookings: int
    most_frequent_issues: list[EquipmentIssueDTO] = Field(default_factory=list)


__all__ = ["ReportDTO", "AnalyticsDTO", "EquipmentIssueDTO"]
