from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class EquipmentDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    desk_id: Optional[int] = None
    desk_number: Optional[int] = None
    desk_floor: Optional[int] = None
    desk_section: Optional[int] = None
    assigned_user_id: Optional[int] = None
    assigned_user_name: Optional[str] = None
    type: str
    model: str = ""
    inventory_number: Optional[str] = None
    socket_serial: Optional[str] = None
    socket_needed: bool = False
    status: str = "in_stock"

    @classmethod
    def from_equipment(cls, item: Any) -> "EquipmentDTO":
        desk = getattr(item, "desk", None)
        user = getattr(item, "assigned_user", None)
        return cls(
            id=int(item.id),
            desk_id=item.desk_id,
            desk_number=desk.number if desk is not None else None,
            desk_floor=desk.floor if desk is not None else None,
            desk_section=desk.section if desk is not None else None,
            assigned_user_id=item.assigned_user_id,
            assigned_user_name=user.full_name if user is not None else None,
            type=item.type or "",
            model=item.model,
            inventory_number=item.inventory_number,
            socket_serial=item.socket_serial,
            socket_needed=bool(item.socket_needed),
            status=item.status or "in_stock",
        )


__all__ = ["EquipmentDTO"]
