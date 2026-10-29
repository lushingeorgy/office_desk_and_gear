from __future__ import annotations

import datetime as dt
import logging
import uuid
import jwt
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session

from server.api.pydantic_schemas import (
    AnalyticsResponse,
    BookingCancelRequest,
    BookingCreate,
    BookingPeriodCreate,
    BookingResponse,
    DeskCreate,
    DeskFeatureResponse,
    DeskListResponse,
    DeskResponse,
    DeskUpdate,
    EquipmentAssign,
    EquipmentCreate,
    EquipmentResponse,
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    QrCodeResponse,
    TicketCreate,
    TicketResponse,
    TicketStatusUpdate,
    Token,
    UserCreate,
    UserProfileUpdate,
    UserResponse,
)
from server.core.config import settings
from server.core.rate_limit import auth_rate_limit
from server.core.exceptions import ServiceError
from server.core.security import hash_token_value, decode_access_token, signing_secret
from server.database.base import init_db, seed_db
from server.database.session import get_db, engine
from server.repositories.auth_repository import AuthRepository
from server.services.auth_service import AuthService
from server.models.users import ROLE_IT_ADMIN, ROLE_OFFICE_MANAGER, User
from server.repositories.booking_repository import BookingRepository
from server.repositories.desk_feature_repository import DeskFeatureRepository
from server.repositories.desk_repository import DeskRepository
from server.repositories.equipment_repository import EquipmentRepository
from server.repositories.report_repository import ReportRepository
from server.repositories.ticket_repository import TicketRepository
from server.repositories.user_repository import UserRepository
from server.services.booking_service import BookingService
from server.services.desk_feature_service import DeskFeatureService
from server.services.desk_service import DeskService
from server.services.equipment_service import EquipmentService
from server.services.report_service import ReportService
from server.services.ticket_service import TicketService
from server.services.user_service import UserService
from server.api.pydantic_schemas import EmailVerify, RoleUpdate, TicketCommentCreate, UploadCreate, EquipmentUpdate
from server.repositories.activity_repository import ActivityRepository
from server.services.activity_service import ActivityService
from server.dto.report_dto import ReportDTO
from server.dto.activity_dto import NotificationResponse, TicketEventResponse, UploadResponse


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        signing_secret()
        if settings.ENVIRONMENT == "production" and (not settings.SMTP_HOST or settings.MAIL_MODE != "smtp"):
            raise RuntimeError("Production email verification requires SMTP configuration")
        init_db()
        if settings.SEED_ON_START:
            if settings.ENVIRONMENT == "production":
                raise RuntimeError("Demo seeding is disabled in production")
            seed_db()
        yield
    finally:
        engine.dispose()


app = FastAPI(title="Office Desk & Gear API", version="1.0.0", lifespan=lifespan)
router = APIRouter(prefix="/api/v1")


@router.get("/system/info", tags=["system"])
def system_info():
    return {"service": "office-desk-gear", "api_version": "1.0"}


@app.exception_handler(ServiceError)
async def _service_error_handler(_request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc)},
        headers=exc.headers or None,
    )


def _user_service(db: Session) -> UserService:
    return UserService(UserRepository(db))


def _desk_service(db: Session) -> DeskService:
    return DeskService(DeskRepository(db), DeskFeatureRepository(db))


def _desk_feature_service(db: Session) -> DeskFeatureService:
    return DeskFeatureService(DeskFeatureRepository(db))


def _booking_service(db: Session) -> BookingService:
    return BookingService(BookingRepository(db), DeskRepository(db), UserRepository(db))


def _equipment_service(db: Session) -> EquipmentService:
    return EquipmentService(
        EquipmentRepository(db),
        DeskRepository(db),
        UserRepository(db),
        BookingRepository(db),
    )


def _ticket_service(db: Session) -> TicketService:
    return TicketService(TicketRepository(db), EquipmentRepository(db))


def _report_service(db: Session) -> ReportService:
    return ReportService(ReportRepository(db), TicketRepository(db))


UNAUTHORIZED_MISSING = "Требуется авторизация: токен не передан"
UNAUTHORIZED_BAD_HEADER = "Некорректный заголовок авторизации"
UNAUTHORIZED_BAD_TOKEN = "Недействительный или истёкший токен"
FORBIDDEN_MESSAGE = "Недостаточно прав для выполнения операции"


def get_current_user(
    authorization: Optional[str] = Header(default=None, description="Bearer <идентификатор сессии БД>"),
    db: Session = Depends(get_db),
) -> User:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=UNAUTHORIZED_MISSING,
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, raw_token = authorization.partition(" ")
    if scheme.strip().lower() != "bearer" or not raw_token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=UNAUTHORIZED_BAD_HEADER,
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = decode_access_token(raw_token.strip())
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail=UNAUTHORIZED_BAD_TOKEN) from None
    user = AuthRepository(db).get_session_user(hash_token_value(raw_token.strip()))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=UNAUTHORIZED_BAD_TOKEN
        )
    if claims["sub"] != str(user.id):
        raise HTTPException(status_code=401, detail=UNAUTHORIZED_BAD_TOKEN)
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Подтвердите электронную почту")
    return user


def require_roles(*roles: str):
    allowed = {role for role in roles}

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN_MESSAGE
            )
        return current_user

    return dependency


MANAGE_DESKS = require_roles(ROLE_OFFICE_MANAGER, ROLE_IT_ADMIN)

MANAGE_EQUIPMENT = require_roles(ROLE_IT_ADMIN)

VIEW_REPORTS = require_roles(ROLE_OFFICE_MANAGER, ROLE_IT_ADMIN)


def _parse_feature_ids(raw: Optional[List[str]]) -> List[int]:
    parsed: list[int] = []
    for chunk in raw or []:
        for part in str(chunk).replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                value = int(part)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Параметр features должен содержать целые числа (id характеристик)",
                ) from None
            if value not in parsed:
                parsed.append(value)
    return parsed


@router.post("/auth/register", dependencies=[Depends(auth_rate_limit)], response_model=UserResponse, status_code=201, tags=["auth"])
def register_user(payload: UserCreate, db: Session = Depends(get_db)):
    return _user_service(db).register(payload)


@router.post("/auth/login", dependencies=[Depends(auth_rate_limit)], response_model=Token, tags=["auth"])
def login_user(payload: LoginRequest, db: Session = Depends(get_db)):
    token = AuthService(AuthRepository(db), _user_service(db)).login(payload.email, payload.password)
    return Token(access_token=token, token_type="bearer", expires_at=dt.datetime.fromtimestamp(decode_access_token(token)["exp"], tz=dt.UTC))


@router.post("/auth/logout", status_code=204, response_class=Response, tags=["auth"])
def logout_user(
    authorization: str = Header(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    AuthService(AuthRepository(db), _user_service(db)).logout(authorization.partition(" ")[2].strip())
    return Response(status_code=204)


@router.post("/auth/password-reset/request", dependencies=[Depends(auth_rate_limit)], response_model=MessageResponse, tags=["auth"])
def request_password_reset(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    _user_service(db).issue_reset_code(payload.email)
    return MessageResponse(
        message="Если адрес зарегистрирован, код восстановления отправлен на почту"
    )


@router.post("/auth/password-reset/confirm", dependencies=[Depends(auth_rate_limit)], response_model=MessageResponse, tags=["auth"])
def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    _user_service(db).confirm_reset(payload.email, payload.code, payload.new_password)
    return MessageResponse(message="Пароль успешно изменён")


@router.get("/users/me", response_model=UserResponse, tags=["users"])
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _user_service(db).get_user(current_user.id)


@router.patch("/users/me", response_model=UserResponse, tags=["users"])
def update_me(
    payload: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _user_service(db).update_profile(current_user.id, payload)


@router.get("/desks", response_model=Optional[List[DeskResponse]], tags=["desks"])
def list_desks(
    response: Response,
    current_user: User = Depends(get_current_user),
    search: Optional[str] = Query(default=None, max_length=100),
    q: Optional[str] = Query(default=None, max_length=100),
    sort: str = Query(default="number", pattern="^(number|floor|sockets)$"),
    order: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    start_at: Optional[dt.datetime] = Query(default=None),
    end_at: Optional[dt.datetime] = Query(default=None),
    floor: Optional[int] = Query(default=None, description="Этаж"),
    section: Optional[int] = Query(default=None, description="Секция"),
    sockets: Optional[int] = Query(default=None, description="Минимальное число розеток"),
    features: Optional[List[str]] = Query(
        default=None,
        description="Id характеристик: ?features=1&features=2 или ?features=1,2 (совпадение по всем)",
    ),
    db: Session = Depends(get_db),
):
    from server.services.booking_service import _local_datetime
    if bool(start_at) != bool(end_at) or (start_at and end_at and _local_datetime(start_at) >= _local_datetime(end_at)):
        raise HTTPException(status_code=422, detail="Укажите начало и конец интервала доступности")
    result = _desk_service(db).filter_desks(
        floor=floor, section=section, sockets=sockets, features=_parse_feature_ids(features), search=search or q, sort=sort, order=order, limit=limit, offset=offset, start_at=start_at, end_at=end_at
    )
    if result.partial:
        payload = DeskListResponse(
            items=[DeskResponse.model_validate(desk) for desk in result.desks],
            soft_filter=True,
            message=result.message,
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=payload.model_dump(mode="json"),
            headers={"X-Partial-Match": "true"},
        )
    response.headers["X-Partial-Match"] = "false"
    return result.desks


@router.get("/desk-features", response_model=List[DeskFeatureResponse], tags=["desks"])
def list_desk_features(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _desk_feature_service(db).get_all_desk_features()


@router.get("/desks/{desk_id}", response_model=DeskResponse, tags=["desks"])
def get_desk(desk_id: int, _user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _desk_service(db).get_desk(desk_id)


@router.get("/bookings", response_model=List[BookingResponse], tags=["bookings"])
def list_bookings(
    _user: User = Depends(get_current_user),
    desk_id: Optional[int] = Query(default=None, description="Id стола"),
    date: Optional[dt.date] = Query(default=None, description="Дата (ГГГГ-ММ-ДД)"),
    db: Session = Depends(get_db),
):
    return _booking_service(db).get_desk_bookings(desk_id, date, _user.id if _user.role == "employee" else None)


@router.get("/availability", tags=["bookings"])
def availability(desk_id: Optional[int] = Query(default=None), date: Optional[dt.date] = Query(default=None), _user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _booking_service(db).availability(desk_id, date)


@router.post("/bookings", response_model=BookingResponse, status_code=201, tags=["bookings"])
def create_booking(
    payload: BookingCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _booking_service(db).create_booking(payload, current_user.id)


@router.post("/bookings/period", response_model=List[BookingResponse], status_code=201, tags=["bookings"])
def create_booking_period(
    response: Response,
    payload: BookingPeriodCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    created, skipped = _booking_service(db).create_booking_period(payload, current_user.id)
    if skipped:
        response.headers["X-Skipped-Days"] = ",".join(skipped)
        response.headers["X-Skipped-Count"] = str(len(skipped))
    return created


@router.get("/bookings/active", response_model=Optional[BookingResponse], tags=["bookings"])
def get_active_booking(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _booking_service(db).get_active_booking(current_user.id)


@router.get("/bookings/history", response_model=List[BookingResponse], tags=["bookings"])
def get_booking_history(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _booking_service(db).get_history(current_user.id)


@router.delete("/bookings/{booking_id}", status_code=204, response_class=Response, tags=["bookings"])
def cancel_booking(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    _booking_service(db).cancel_booking(booking_id, user_id=current_user.id, admin=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/bookings/{booking_id}", response_model=BookingResponse, tags=["bookings"])
def edit_booking(booking_id: int, payload: BookingCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _booking_service(db).edit_booking(booking_id, payload, current_user.id)


@router.get("/equipment/my", response_model=List[EquipmentResponse], tags=["equipment"])
def get_my_equipment(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _equipment_service(db).get_my_equipment(current_user.id)


@router.get("/equipment/{equipment_id}", response_model=EquipmentResponse, tags=["equipment"])
def get_equipment(equipment_id: int, _user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _equipment_service(db).get_equipment(equipment_id, _user)


@router.post("/tickets", response_model=TicketResponse, status_code=201, tags=["tickets"])
def create_ticket(
    payload: TicketCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _ticket_service(db).create_ticket(payload, current_user.id)


@router.get("/tickets/my", response_model=List[TicketResponse], tags=["tickets"])
def get_my_tickets(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return _ticket_service(db).get_my_tickets(current_user.id)


@router.post("/admin/desks", response_model=DeskResponse, status_code=201, tags=["admin: desks"])
def admin_create_desk(
    payload: DeskCreate,
    _user: User = Depends(MANAGE_DESKS),
    db: Session = Depends(get_db),
):
    return _desk_service(db).create_desk(payload)


@router.patch("/admin/desks/{desk_id}", response_model=DeskResponse, tags=["admin: desks"])
def admin_update_desk(
    desk_id: int,
    payload: DeskUpdate,
    _user: User = Depends(MANAGE_DESKS),
    db: Session = Depends(get_db),
):
    return _desk_service(db).update_desk(desk_id, payload)


@router.delete(
    "/admin/bookings/{booking_id}",
    status_code=204,
    response_class=Response,
    tags=["admin: bookings"],
)
def admin_cancel_booking(
    booking_id: int,
    payload: Optional[BookingCancelRequest] = Body(default=None),
    _user: User = Depends(MANAGE_DESKS),
    db: Session = Depends(get_db),
) -> Response:
    reason = (payload.reason if payload is not None else "") or ""
    cancelled_at = _booking_service(db).cancel_booking(booking_id, admin=True, reason=reason)
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            "X-Cancelled-By": "admin",
            "X-Cancelled-At": cancelled_at.isoformat(timespec="seconds"),
            "X-Cancel-Reason": quote(reason),
        },
    )


@router.get("/admin/equipment", response_model=List[EquipmentResponse], tags=["admin: equipment"])
def admin_list_equipment(
    _user: User = Depends(MANAGE_EQUIPMENT), db: Session = Depends(get_db)
):
    return _equipment_service(db).get_all_equipment()


@router.get("/admin/tickets", response_model=List[TicketResponse], tags=["admin: tickets"])
def admin_list_tickets(
    _user: User = Depends(MANAGE_EQUIPMENT), db: Session = Depends(get_db)
):
    return _ticket_service(db).get_all_tickets()


@router.post("/admin/equipment", response_model=EquipmentResponse, status_code=201, tags=["admin: equipment"])
def admin_create_equipment(
    payload: EquipmentCreate,
    _user: User = Depends(MANAGE_EQUIPMENT),
    db: Session = Depends(get_db),
):
    return _equipment_service(db).create_equipment(payload)


@router.get(
    "/admin/equipment/{equipment_id}/qr", response_model=QrCodeResponse, tags=["admin: equipment"]
)
def admin_generate_qr(
    equipment_id: int,
    _user: User = Depends(MANAGE_EQUIPMENT),
    db: Session = Depends(get_db),
):
    return _equipment_service(db).generate_qr_code(equipment_id)


@router.patch(
    "/admin/equipment/{equipment_id}/assign",
    response_model=EquipmentResponse,
    tags=["admin: equipment"],
)
def admin_assign_equipment(
    equipment_id: int,
    payload: Optional[EquipmentAssign] = Body(default=None),
    _user: User = Depends(MANAGE_EQUIPMENT),
    db: Session = Depends(get_db),
):
    return _equipment_service(db).assign_equipment(equipment_id, payload)


@router.patch(
    "/admin/tickets/{ticket_id}/status", response_model=TicketResponse, tags=["admin: tickets"]
)
def admin_update_ticket_status(
    ticket_id: int,
    payload: TicketStatusUpdate,
    _user: User = Depends(MANAGE_EQUIPMENT),
    db: Session = Depends(get_db),
):
    return _ticket_service(db).update_status(ticket_id, payload.status.value, _user.id)


@router.get("/admin/analytics", response_model=AnalyticsResponse, tags=["admin: reports"])
def admin_get_analytics(
    start_date: Optional[dt.date] = Query(default=None, description="Начало периода (ГГГГ-ММ-ДД)"),
    end_date: Optional[dt.date] = Query(default=None, description="Конец периода (ГГГГ-ММ-ДД)"),
    _user: User = Depends(VIEW_REPORTS),
    db: Session = Depends(get_db),
):
    return _report_service(db).get_analytics(start_date, end_date)


@router.get("/admin/users", response_model=List[UserResponse], tags=["admin: users"])
def admin_list_users(_user: User = Depends(VIEW_REPORTS), db: Session = Depends(get_db)):
    return _user_service(db).get_all_users()


@router.post("/auth/verify-email", dependencies=[Depends(auth_rate_limit)], response_model=MessageResponse, tags=["auth"])
def verify_email(payload: EmailVerify, db: Session = Depends(get_db)):
    _user_service(db).verify_email(payload.email, payload.code)
    return MessageResponse(message="Почта подтверждена. Войдите в приложение")


@router.post("/auth/resend-verification", dependencies=[Depends(auth_rate_limit)], response_model=MessageResponse, tags=["auth"])
def resend_verification(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    _user_service(db).resend_verification(payload.email)
    return MessageResponse(message="Если подтверждение требуется, код отправлен на почту")


@router.patch("/admin/users/{user_id}/role", response_model=UserResponse, tags=["admin: users"])
def set_user_role(user_id: int, payload: RoleUpdate, _user: User = Depends(require_roles(ROLE_IT_ADMIN)), db: Session = Depends(get_db)):
    return _user_service(db).set_role(user_id, payload.role)


@router.patch("/admin/equipment/{equipment_id}", response_model=EquipmentResponse, tags=["admin: equipment"])
def edit_equipment(equipment_id: int, payload: EquipmentUpdate, _user: User = Depends(MANAGE_EQUIPMENT), db: Session = Depends(get_db)):
    return _equipment_service(db).update_equipment(equipment_id, payload)


@router.delete("/admin/equipment/{equipment_id}", status_code=204, tags=["admin: equipment"])
def delete_equipment(equipment_id: int, _user: User = Depends(MANAGE_EQUIPMENT), db: Session = Depends(get_db)):
    _equipment_service(db).delete_equipment(equipment_id)
    return Response(status_code=204)


@router.delete("/admin/desks/{desk_id}", status_code=204, tags=["admin: desks"])
def delete_desk(desk_id: int, _user: User = Depends(MANAGE_DESKS), db: Session = Depends(get_db)):
    _desk_service(db).delete_desk(desk_id)
    return Response(status_code=204)


@router.get("/notifications", response_model=List[NotificationResponse], tags=["notifications"])
def notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ActivityService(ActivityRepository(db)).notifications(user)


@router.post("/notifications/{notification_id}/read", status_code=204, tags=["notifications"])
def read_notification(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ActivityService(ActivityRepository(db)).read_notification(notification_id, user)
    return Response(status_code=204)


@router.get("/tickets/{ticket_id}/events", response_model=List[TicketEventResponse], tags=["tickets"])
def ticket_events(ticket_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ActivityService(ActivityRepository(db)).events(ticket_id, user)


@router.get("/tickets/{ticket_id}", response_model=TicketResponse, tags=["tickets"])
def ticket_detail(ticket_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ActivityService(ActivityRepository(db))._ticket(ticket_id, user)
    return _ticket_service(db).get_ticket(ticket_id)


@router.post("/tickets/{ticket_id}/comments", response_model=TicketEventResponse, status_code=201, tags=["tickets"])
def ticket_comment(ticket_id: int, payload: TicketCommentCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ActivityService(ActivityRepository(db)).comment(ticket_id, user, payload.text)


@router.post("/uploads", response_model=UploadResponse, status_code=201, tags=["uploads"])
def upload_image(payload: UploadCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return ActivityService(ActivityRepository(db)).upload_image(user, payload.image_base64)


@router.get("/uploads/{identifier}", tags=["uploads"])
def get_image(identifier: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return FileResponse(ActivityService(ActivityRepository(db)).image_path(identifier, user), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/admin/reports", response_model=ReportDTO, status_code=201, tags=["admin: reports"])
def create_report(start_date: Optional[dt.date] = Query(default=None), end_date: Optional[dt.date] = Query(default=None), user: User = Depends(VIEW_REPORTS), db: Session = Depends(get_db)):
    return _report_service(db).save_report(user.id, start_date, end_date)


@router.get("/admin/reports", response_model=List[ReportDTO], tags=["admin: reports"])
def list_reports(_user: User = Depends(VIEW_REPORTS), db: Session = Depends(get_db)):
    return _report_service(db).list_reports()


@router.get("/admin/reports/{report_id}/export", tags=["admin: reports"])
def export_report(report_id: int, _user: User = Depends(VIEW_REPORTS), db: Session = Depends(get_db)):
    report = _report_service(db).get_report(report_id)
    return JSONResponse(content=report.model_dump(mode="json"), headers={"Content-Disposition": f'attachment; filename="office-report-{report_id}.json"'})


app.include_router(router)

__all__ = ["app", "router", "get_current_user", "require_roles"]
