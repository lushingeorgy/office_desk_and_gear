from __future__ import annotations

from sqlalchemy import inspect, select, text

from server.core.security import hash_password
from server.database.session import SessionLocal, engine
from server.models import Base
from server.models.auth import AuthSession, PasswordResetCode, EmailVerificationCode
from server.models.activity import Notification, TicketEvent, Upload
from server.models.bookings import Booking
from server.models.desk_features import DeskFeature
from server.models.desks import Desk
from server.models.equipment import Equipment, STATUS_ASSIGNED, STATUS_IN_STOCK
from server.models.reports import Report
from server.models.tickets import Ticket
from server.models.users import ROLE_EMPLOYEE, ROLE_IT_ADMIN, ROLE_OFFICE_MANAGER, User


def init_db() -> None:
    """Startup checks only; schema mutations run through Alembic before startup."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from server.core.config import PROJECT_ROOT
    configuration = Config(str(PROJECT_ROOT / "alembic.ini"))
    expected = ScriptDirectory.from_config(configuration).get_current_head()
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        if revision != expected:
            raise RuntimeError("Database migrations are required: run alembic upgrade head")


def seed_db() -> None:
    init_db()
    with SessionLocal() as db:
        users = [
            ("Сотрудник", "employee@company.ru", ROLE_EMPLOYEE),
            ("IT Администратор", "it@company.ru", ROLE_IT_ADMIN),
            ("Офис Менеджер", "office@company.ru", ROLE_OFFICE_MANAGER),
        ]
        by_email: dict[str, User] = {}
        for full_name, email, role in users:
            user = db.execute(select(User).where(User.email == email)).scalars().first()
            if user is None:
                user = User(full_name=full_name, email=email,
                            password_hash=hash_password("password123"), role=role, is_verified=True)
                db.add(user)
                db.flush()
            by_email[email] = user

        for feature_id, name, description in [
            (1, "Дополнительный монитор", "Стол оборудован вторым монитором"),
            (2, "USB Type-C", "Док-станция или питание через USB Type-C"),
            (3, "Тихая зона", "Рабочее место в зоне пониженного шума"),
            (4, "Регулируемый стол", "Электрическая регулировка высоты"),
        ]:
            if db.get(DeskFeature, feature_id) is None:
                db.add(DeskFeature(id=feature_id, name=name, description=description))

        if db.execute(select(Desk)).scalars().first() is None:
            db.flush()
            for floor, section, number, available, sockets, ids in [(1,1,101,True,4,[1,2]), (1,1,102,True,2,[2,3]), (2,1,201,True,4,[1,4]), (2,2,202,False,2,[3])]:
                desk = Desk(floor=floor, section=section, number=number, is_free=available, sockets=sockets)
                desk.feature_records = [db.get(DeskFeature, value) for value in ids]
                db.add(desk)
            db.flush()

        employee = by_email["employee@company.ru"]
        first_desk = db.execute(select(Desk).order_by(Desk.id)).scalars().first()
        if db.execute(select(Equipment)).scalars().first() is None:
            db.add_all([
                Equipment(type="Ноутбук", socket_serial="ODG-LAPTOP-001",
                          socket_needed=True, assigned_user_id=employee.id,
                          status=STATUS_ASSIGNED),
                Equipment(type="Монитор", socket_serial="ODG-MONITOR-001",
                          socket_needed=True, desk_id=first_desk.id if first_desk else None,
                          status=STATUS_ASSIGNED if first_desk else STATUS_IN_STOCK),
            ])
        db.commit()


__all__ = ["Base", "init_db", "seed_db"]
