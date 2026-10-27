from __future__ import annotations

from server.dto.booking_dto import BookingDTO, format_duration
from server.dto.desk_dto import DeskDTO
from server.dto.desk_feature_dto import DeskFeatureDTO
from server.dto.equipment_dto import EquipmentDTO
from server.dto.report_dto import AnalyticsDTO, EquipmentIssueDTO, ReportDTO
from server.dto.ticket_dto import TicketDTO
from server.dto.user_dto import UserDTO

__all__ = [
    "UserDTO",
    "DeskDTO",
    "DeskFeatureDTO",
    "BookingDTO",
    "EquipmentDTO",
    "TicketDTO",
    "ReportDTO",
    "AnalyticsDTO",
    "EquipmentIssueDTO",
    "format_duration",
]
