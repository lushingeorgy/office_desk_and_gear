import datetime as dt
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

import requests
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.core.config import Settings, settings
from server.core.exceptions import BadRequestError, ConflictError
from server.core.security import hash_token_value, verify_password
from server.database.base import Base
from server.database.session import engine, get_db
from server.models.auth import AuthSession, utc_now
from server.models.bookings import Booking
from server.models.desks import Desk
from server.models.users import User
from server.repositories.auth_repository import AuthRepository
from server.repositories.booking_repository import BookingRepository
from server.repositories.desk_repository import DeskRepository
from server.repositories.user_repository import UserRepository
from server.services.auth_service import AuthService
from server.services.booking_service import BookingService
from server.services.user_service import UserService


class ConfigurationTests(unittest.TestCase):
    def test_other_database_backends_are_rejected(self):
        with patch.dict(os.environ, DATABASE_URL="sqlite:///:memory:"):
            with self.assertRaisesRegex(ValueError, "только PostgreSQL"):
                Settings()

    def test_postgresql_uses_psycopg(self):
        with patch.dict(os.environ, DATABASE_URL="postgresql://postgres@localhost/office_desk_gear"):
            self.assertEqual(Settings().DATABASE_URL.drivername, "postgresql+psycopg")
        self.assertEqual(engine.dialect.name, "postgresql")


class PostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "odg_test_" + uuid.uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.addClassCleanup(cls.drop_schema)
        cls.test_engine = create_engine(settings.DATABASE_URL, connect_args={
            "connect_timeout": 5, "options": f"-c search_path={cls.schema}",
        })
        with cls.test_engine.connect() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == cls.schema
        Base.metadata.create_all(cls.test_engine)
        cls.sessions = sessionmaker(bind=cls.test_engine, autoflush=False, expire_on_commit=False)

    @classmethod
    def drop_schema(cls):
        if hasattr(cls, "test_engine"):
            cls.test_engine.dispose()
        assert cls.schema.startswith("odg_test_") and len(cls.schema) == 41
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        engine.dispose()

    def setUp(self):
        self.db = self.sessions()
        self.addCleanup(self.db.close)
        assert self.db.scalar(text("SELECT current_schema()")) == self.schema
        for table in reversed(Base.metadata.sorted_tables):
            self.db.execute(table.delete())
        self.db.commit()
        self.users = UserService(UserRepository(self.db))
        self.user = self.users.register(SimpleNamespace(
            full_name="Тестовый сотрудник", job_title="Сотрудник",
            email="test@company.ru", password="password123", role="employee",
        ))

    def test_registration_and_postgresql_types(self):
        self.db.close()
        with self.sessions() as db:
            user = db.get(User, self.user.id)
            self.assertEqual(user.job_title, "Сотрудник")
            self.assertTrue(verify_password("password123", user.password_hash))
            self.assertEqual(user.current_bookings, [])
            self.assertEqual(list(db.scalars(select(AuthSession))), [])
        columns = {item["name"]: str(item["type"]) for item in inspect(self.test_engine).get_columns("desks")}
        self.assertEqual(columns["features"], "JSONB")
        indexes = inspect(self.test_engine).get_indexes("desks")
        self.assertTrue(any(item["name"] == "ix_desks_features" and item["dialect_options"].get("postgresql_using") == "gin" for item in indexes))
        with self.assertRaises(BadRequestError):
            self.users.register(SimpleNamespace(full_name="Дубль", email="TEST@company.ru", password="password123"))

    def test_authorization_expiry_logout_and_reset(self):
        repository = AuthRepository(self.db)
        auth = AuthService(repository, self.users)
        token = auth.login("test@company.ru", "password123")
        self.assertEqual(repository.get_session_user(hash_token_value(token)).id, self.user.id)
        self.db.get(AuthSession, hash_token_value(token)).expires_at = utc_now() - dt.timedelta(seconds=1)
        self.db.commit()
        self.assertIsNone(repository.get_session_user(hash_token_value(token)))
        token = auth.login("test@company.ru", "password123")
        auth.logout(token)
        self.assertIsNone(repository.get_session_user(hash_token_value(token)))
        token = auth.login("test@company.ru", "password123")
        with patch("builtins.print"):
            code = self.users.issue_reset_code("test@company.ru")
        self.db.close()
        with self.sessions() as db:
            service = UserService(UserRepository(db))
            service.confirm_reset("test@company.ru", code, "new-password")
            self.assertIsNone(AuthRepository(db).get_session_user(hash_token_value(token)))
            with self.assertRaises(BadRequestError):
                service.confirm_reset("test@company.ru", code, "another-password")

    def test_request_session_rollback_and_pool_return(self):
        self.db.close()
        with patch("server.database.session.SessionLocal", self.sessions):
            dependency = get_db()
            db = next(dependency)
            db.get(User, self.user.id).full_name = "Не должно сохраниться"
            db.flush()
            self.assertEqual(self.test_engine.pool.checkedout(), 1)
            with self.assertRaises(ValueError):
                dependency.throw(ValueError("ошибка запроса"))
        self.assertEqual(self.test_engine.pool.checkedout(), 0)
        with self.sessions() as db:
            self.assertEqual(db.get(User, self.user.id).full_name, "Тестовый сотрудник")

    def test_booking_commit_conflict_and_rollback(self):
        desk = Desk(floor=1, number=1, is_free=True, features=[1, 2])
        self.db.add(desk)
        self.db.commit()
        service = BookingService(BookingRepository(self.db), DeskRepository(self.db), UserRepository(self.db))
        payload = SimpleNamespace(desk_id=desk.id, planed_at=dt.datetime.now() + dt.timedelta(days=1), duration_hours=2)
        booking = service.create_booking(payload, self.user.id)
        self.assertIn(booking.id, self.db.get(User, self.user.id).current_bookings)
        with self.assertRaises(ConflictError):
            service.create_booking(payload, self.user.id)
        self.db.rollback()
        service.cancel_booking(booking.id, self.user.id)
        self.assertEqual(self.db.get(User, self.user.id).current_bookings, [])
        with patch.object(service, "_attach_to_user", side_effect=ValueError("ошибка после flush")):
            with self.assertRaises(ValueError):
                service.create_booking(payload, self.user.id)
        self.db.rollback()
        self.assertEqual(len(list(self.db.scalars(select(Booking)))), 1)

    def test_http_api_on_postgresql(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = dict(os.environ, PGOPTIONS=f"-c search_path={self.schema}", HOST="127.0.0.1", PORT=str(port), ODG_SEED="0", PYTHONIOENCODING="utf-8")
        with tempfile.TemporaryFile(dir=ROOT / "tests") as log:
            process = subprocess.Popen([sys.executable, "-u", "-m", "server.main"], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                base = f"http://127.0.0.1:{port}"
                for _ in range(100):
                    if process.poll() is not None:
                        log.seek(0)
                        self.fail(log.read().decode("utf-8", "replace"))
                    try:
                        if requests.get(base + "/health", timeout=1).status_code == 200:
                            break
                    except requests.RequestException:
                        pass
                    time.sleep(0.1)
                else:
                    self.fail("Тестовый API не запустился")
                with requests.Session() as http:
                    def call(method, path, body=None, token=None, expected=200):
                        headers = {"Authorization": f"Bearer {token}"} if token else {}
                        response = http.request(method, base + "/api/v1" + path, json=body, headers=headers, timeout=5)
                        self.assertEqual(response.status_code, expected, f"{method} {path}: {response.text}")
                        return response.json() if response.content else None

                    tokens = {}
                    for role in ("employee", "office_manager", "it_admin"):
                        email = role + "@http.test.ru"
                        call("POST", "/auth/register", {"full_name": role, "job_title": role, "email": email, "password": "password123", "role": role})
                        tokens[role] = call("POST", "/auth/login", {"email": email, "password": "password123"})["access_token"]
                    employee, manager, admin = (tokens[role] for role in ("employee", "office_manager", "it_admin"))
                    profile = call("GET", "/users/me", token=employee)
                    self.assertNotIn("password_hash", profile)
                    call("GET", "/users/me", expected=401)
                    call("GET", "/admin/users", token=employee, expected=403)
                    call("GET", "/admin/users", token=manager)
                    desk = call("POST", "/admin/desks", {"floor": 1, "section": 1, "number": 10, "sockets": 4, "features": [1]}, manager)
                    call("PATCH", f"/admin/desks/{desk['id']}", {"sockets": 5}, manager)
                    self.assertEqual(call("GET", "/desks")[0]["features"], [1])
                    call("GET", "/desk-features")
                    start = (dt.datetime.now() + dt.timedelta(days=1)).replace(microsecond=0)
                    payload = {"desk_id": desk["id"], "planed_at": start.isoformat(), "duration_hours": 2}
                    booking = call("POST", "/bookings", payload, employee)
                    call("POST", "/bookings", payload, employee, expected=409)
                    call("GET", "/bookings/history", token=employee)
                    call("GET", "/bookings/active", token=employee)
                    call("DELETE", f"/bookings/{booking['id']}", token=employee, expected=204)
                    period = {"desk_id": desk["id"], "start_date": start.isoformat(), "end_date": (start + dt.timedelta(days=1)).isoformat(), "daily_duration_hours": 2}
                    self.assertEqual(len(call("POST", "/bookings/period", period, employee)), 2)
                    gear = call("POST", "/admin/equipment", {"type": "Монитор", "socket_serial": "PG-TEST-001", "socket_needed": True}, admin)
                    call("PATCH", f"/admin/equipment/{gear['id']}/assign", {"assigned_user_id": profile["id"]}, admin)
                    call("GET", "/equipment/my", token=employee)
                    self.assertTrue(call("GET", f"/admin/equipment/{gear['id']}/qr", token=admin)["qr_code_base64"])
                    ticket = call("POST", "/tickets", {"equipment_id": gear["id"], "description": "Тест PostgreSQL"}, employee)
                    call("PATCH", f"/admin/tickets/{ticket['id']}/status", {"status": "in_progress"}, admin)
                    call("GET", "/tickets/my", token=employee)
                    call("GET", "/admin/tickets", token=admin)
                    call("GET", "/admin/equipment", token=admin)
                    call("GET", "/admin/analytics", token=manager)
                    call("POST", "/auth/logout", token=employee, expected=204)
                    call("GET", "/users/me", token=employee, expected=401)
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
