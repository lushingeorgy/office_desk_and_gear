"""Explicit public representations of activity and upload records."""
import datetime as dt
from pydantic import BaseModel, ConfigDict, field_validator
from server.core.time import utc_to_office


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    created_at: dt.datetime

    @field_validator("created_at")
    @classmethod
    def display_office_time(cls, value):
        return utc_to_office(value)


class NotificationResponse(ActivityResponse):
    id: int
    user_id: int
    text: str
    is_read: bool


class TicketEventResponse(ActivityResponse):
    id: int
    ticket_id: int
    author_id: int
    kind: str
    text: str


class UploadResponse(BaseModel):
    photo_url: str
