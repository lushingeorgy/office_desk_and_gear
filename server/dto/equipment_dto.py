from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class EquipmentDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    desk_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    type: str
    socket_serial: Optional[str] = None
    socket_needed: bool = False
    status: str = "in_stock"

    @classmethod
    def from_equipment(cls, item: Any) -> "EquipmentDTO":
        return cls(
            id=int(item.id),
            desk_id=item.desk_id,
            assigned_user_id=item.assigned_user_id,
            type=item.type or "",
            socket_serial=item.socket_serial,
            socket_needed=bool(item.socket_needed),
            status=item.status or "in_stock",
        )


__all__ = ["EquipmentDTO"]
