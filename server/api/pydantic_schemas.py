from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, List, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from server.dto.booking_dto import BookingDTO
from server.dto.desk_dto import DeskDTO
from server.dto.desk_feature_dto import DeskFeatureDTO
from server.dto.equipment_dto import EquipmentDTO
from server.dto.report_dto import AnalyticsDTO, EquipmentIssueDTO
from server.dto.ticket_dto import TicketDTO
from server.dto.user_dto import UserDTO

__all__ = [
    "UserCreate",
    "LoginRequest",
    "Token",
    "MessageResponse",
    "PasswordResetRequest",
    "PasswordResetConfirm",
    "UserProfileUpdate",
    "UserResponse",
    "DeskCreate",
    "DeskUpdate",
    "DeskResponse",
    "DeskFeatureResponse",
    "DeskListResponse",
    "BookingCreate",
    "BookingPeriodCreate",
    "BookingResponse",
    "BookingCancelRequest",
    "EquipmentCreate",
    "EquipmentAssign",
    "EquipmentResponse",
    "TicketCreate",
    "TicketStatus",
    "TicketStatusUpdate",
    "TicketResponse",
    "QrCodeResponse",
    "AnalyticsResponse",
    "EquipmentIssueResponse",
]


class UserCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    full_name: str = Field(min_length=1, max_length=255)
    job_title: str = Field(default="", max_length=255)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=6, max_length=1024)
    role: Optional[str] = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str
    password: str


class Token(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    access_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message: str


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str


class PasswordResetConfirm(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str
    code: str
    new_password: str


class UserProfileUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    full_name: Optional[str] = None
    job_title: Optional[str] = Field(default=None, max_length=255)
    email: Optional[str] = None


class UserResponse(UserDTO):
    model_config = ConfigDict(from_attributes=True)


class DeskCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    floor: Optional[int] = None
    section: Optional[int] = None
    number: int
    is_free: bool = True
    sockets: Optional[int] = None
    features: List[int] = Field(default_factory=list)


class DeskUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    floor: Optional[int] = None
    section: Optional[int] = None
    number: Optional[int] = None
    is_free: Optional[bool] = None
    sockets: Optional[int] = None
    features: Optional[List[int]] = None


class DeskResponse(DeskDTO):
    model_config = ConfigDict(from_attributes=True)


class DeskFeatureResponse(DeskFeatureDTO):
    model_config = ConfigDict(from_attributes=True)


class DeskListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: List[DeskResponse] = Field(default_factory=list)
    soft_filter: bool = True
    message: Optional[str] = None


class BookingCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    desk_id: int
    planed_at: datetime
    duration_hours: Annotated[int, Field(gt=0, le=24)]


class BookingPeriodCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    desk_id: int
    start_date: datetime
    end_date: datetime
    daily_duration_hours: Annotated[int, Field(gt=0, le=24)]


class BookingResponse(BookingDTO):
    model_config = ConfigDict(from_attributes=True)


class BookingCancelRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reason: str


class EquipmentCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    desk_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    type: str
    socket_serial: Optional[str] = None
    socket_needed: bool = False
    status: Optional[str] = None


class EquipmentAssign(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    assigned_user_id: Optional[int] = None
    desk_id: Optional[int] = None


class EquipmentResponse(EquipmentDTO):
    model_config = ConfigDict(from_attributes=True)


class TicketCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    equipment_id: int = Field(
        validation_alias=AliasChoices("equipment_id", "epuipment_id"),
        serialization_alias="equipment_id",
    )
    description: str
    photo_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("photo_url", "photho_url"),
        serialization_alias="photo_url",
    )


class TicketStatus(str, Enum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketStatusUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: TicketStatus


class TicketResponse(TicketDTO):
    model_config = ConfigDict(from_attributes=True)


class QrCodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    equipment_id: int
    qr_code_url: str
    qr_code_base64: str


class EquipmentIssueResponse(EquipmentIssueDTO):
    model_config = ConfigDict(from_attributes=True)


class AnalyticsResponse(AnalyticsDTO):
    model_config = ConfigDict(from_attributes=True)
