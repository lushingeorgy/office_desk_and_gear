from __future__ import annotations

from server.repositories.base_repository import BaseRepository
from server.repositories.booking_repository import BookingRepository
from server.repositories.desk_feature_repository import DeskFeatureRepository
from server.repositories.desk_repository import DeskRepository
from server.repositories.equipment_repository import EquipmentRepository
from server.repositories.report_repository import ReportRepository
from server.repositories.ticket_repository import TicketRepository
from server.repositories.user_repository import UserRepository

__all__ = [
    "BaseRepository",
    "UserRepository",
    "DeskRepository",
    "DeskFeatureRepository",
    "BookingRepository",
    "EquipmentRepository",
    "TicketRepository",
    "ReportRepository",
]
