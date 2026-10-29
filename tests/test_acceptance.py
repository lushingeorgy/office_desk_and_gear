"""Real PostgreSQL acceptance checks. Run: python tests/test_acceptance.py.

The launcher creates a unique schema and runs tests in a fresh interpreter so
PGOPTIONS is set before importing any application engine. No production rows
are used. A configured, unavailable PostgreSQL is a failure, never a skip.
"""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PREFIX = "/api/v1"
PASSWORD = "Acceptance-password-2026"


def run_isolated() -> int:
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    load_dotenv(ROOT / ".env")
    schema = "acceptance_" + uuid.uuid4().hex
    database_url = make_url(os.getenv("DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/office_desk_gear"))
    if database_url.get_backend_name() != "postgresql":
        raise RuntimeError("Acceptance checks require PostgreSQL")
    control = create_engine(database_url.set(drivername="postgresql+psycopg"), connect_args={"connect_timeout": 5})
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        data_dir = ROOT / "tests" / "artifacts" / schema
        data_dir.mkdir(parents=True, exist_ok=True)
        environment = dict(
            os.environ,
            PGOPTIONS=f"-csearch_path={schema}",
            ODG_TEST_SCHEMA=schema,
            ODG_SEED="0",
            APP_ENV="test",
            ENVIRONMENT="test",
            JWT_SECRET=secrets.token_urlsafe(48),
            REQUIRE_EMAIL_VERIFICATION="1",
            MAIL_MODE="outbox",
            DATA_DIR=str(data_dir),
            PYTHONIOENCODING="utf-8",
        )
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"], cwd=ROOT, env=environment)
        return result.returncode
    finally:
        if created:
            # Only the exact schema created by this launcher may be removed.
            if re.fullmatch(r"acceptance_[0-9a-f]{32}", schema) is None:
                raise RuntimeError("Unsafe test schema name")
            with control.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            print("Removed isolated acceptance schema", flush=True)
        control.dispose()


@unittest.skipUnless(os.getenv("ODG_TEST_SCHEMA"), "The isolated launcher runs PostgreSQL cases in a subprocess")
class PostgreSQLAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = os.getenv("ODG_TEST_SCHEMA", "")
        if re.fullmatch(r"acceptance_[0-9a-f]{32}", schema) is None:
            raise RuntimeError("Run python tests/test_acceptance.py to create an isolated PostgreSQL schema")

        from alembic import command
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from fastapi.testclient import TestClient
        from sqlalchemy import inspect, text
        from server.core.config import settings
        from server.database.session import engine, SessionLocal

        cls.engine, cls.SessionLocal = engine, SessionLocal
        cls.addClassCleanup(engine.dispose)
        cls.settings = settings
        configuration = Config(str(ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(configuration)
        revisions = list(reversed(list(script.walk_revisions())))
        if len(revisions) < 2:
            raise AssertionError("Case 3 requires an initial and a subsequent migration")
        command.upgrade(configuration, revisions[0].revision)
        with engine.begin() as connection:
            actual = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if actual != revisions[0].revision:
                raise AssertionError("Initial migration did not set its version")
            if "users" not in inspect(connection).get_table_names():
                raise AssertionError("Initial migration did not create users")
            # The next migration must preserve an existing installation's data.
            connection.execute(text("INSERT INTO desk_features(id,name,description) VALUES (900000,'Legacy monitor','Migrated fixture')"))
            connection.execute(text("INSERT INTO desks(id,floor,section,number,is_free,sockets,features) VALUES (900000,9,1,900000,true,2,'[900000]'::jsonb)"))
        command.upgrade(configuration, "head")
        command.upgrade(configuration, "head")  # Running deployment twice is safe.
        with engine.connect() as connection:
            if connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() != script.get_current_head():
                raise AssertionError("Second migration did not reach head")
            migrated = connection.execute(text("SELECT feature_id FROM desk_feature_links WHERE desk_id=:desk_id"), {"desk_id": 900000}).scalar_one()
            if migrated != 900000:
                raise AssertionError("Second migration lost legacy desk feature data")

        from server.main import app
        cls.app = app
        cls.client = TestClient(app, raise_server_exceptions=False)
        cls.client.__enter__()
        cls.addClassCleanup(cls.client.__exit__, None, None, None)
        cls.users = {}
        cls.tokens = {}
        for role in ("employee", "other", "it_admin", "office_manager"):
            user, token = cls.register_user(role)
            if role in {"it_admin", "office_manager"}:
                from server.models.users import User
                with SessionLocal.begin() as session:
                    session.get(User, user["id"]).role = role
            cls.users[role] = user
            cls.tokens[role] = token

    @classmethod
    def register_user(cls, label: str):
        email = f"{label}-{uuid.uuid4().hex}@company.ru"
        payload = {"full_name": "Acceptance " + label, "email": email, "password": PASSWORD, "confirm_password": PASSWORD}
        response = cls.client.post(PREFIX + "/auth/register", json=payload)
        if response.status_code != 201:
            raise AssertionError(f"Registration expected 201, got {response.status_code}: {response.text[:500]}")
        user = response.json()
        matching = []
        for path in (cls.settings.DATA_DIR / "outbox").glob("*.json"):
            content = json.loads(path.read_text(encoding="utf-8"))
            if content.get("email") == email:
                matching.append((path.stat().st_mtime_ns, content))
        if not matching:
            raise AssertionError("Registration did not produce a verification message in the test outbox")
        code = max(matching, key=lambda item: item[0])[1]["code"]
        response = cls.client.post(PREFIX + "/auth/verify-email", json={"email": email, "code": code})
        if response.status_code != 200:
            raise AssertionError(f"Email verification failed: {response.status_code}: {response.text[:500]}")
        response = cls.client.post(PREFIX + "/auth/login", json={"email": email, "password": PASSWORD})
        if response.status_code != 200:
            raise AssertionError(f"Login failed: {response.status_code}: {response.text[:500]}")
        return user, response.json()["access_token"]

    def request(self, method, path, expected=200, *, role="employee", token=None, payload=None, **kwargs):
        headers = {} if role is None and token is None else {"Authorization": "Bearer " + (token or self.tokens[role])}
        response = self.client.request(method, PREFIX + path, headers=headers, json=payload, **kwargs)
        self.assertEqual(expected, response.status_code, f"{method} {path}: {response.text[:700]}")
        if response.content:
            return response.json()
        return None

    def create_desk(self, **changes):
        payload = {"floor": 7, "section": 1, "number": int(uuid.uuid4().hex[:7], 16), "sockets": 4, "features": []}
        payload.update(changes)
        return self.request("POST", "/admin/desks", 201, role="office_manager", payload=payload)

    def create_equipment(self, **changes):
        suffix = uuid.uuid4().hex
        payload = {"type": "Монитор", "model": "Acceptance display", "socket_serial": "SN-" + suffix, "inventory_number": "INV-" + suffix}
        payload.update(changes)
        return self.request("POST", "/admin/equipment", 201, role="it_admin", payload=payload)

    @staticmethod
    def items(data):
        return data.get("items", []) if isinstance(data, dict) else data

    def test_01_migrations_tables_relationships_and_constraints(self):
        from sqlalchemy import inspect
        inspector = inspect(self.engine)
        tables = inspector.get_table_names()
        self.assertGreaterEqual(len([name for name in tables if name != "alembic_version"]), 7)
        joins = []
        for name in tables:
            references = {fk["referred_table"] for fk in inspector.get_foreign_keys(name)}
            if {"desks", "desk_features"}.issubset(references):
                joins.append(name)
        self.assertTrue(joins, "A real N:M desk/features junction table is required")
        booking_refs = {fk["referred_table"] for fk in inspector.get_foreign_keys("bookings")}
        self.assertTrue({"users", "desks"}.issubset(booking_refs))
        self.assertTrue(inspector.get_check_constraints("desks"), "Desk validity must be checked inside PostgreSQL")
        self.assertTrue(inspector.get_check_constraints("bookings"), "Booking validity must be checked inside PostgreSQL")
        self.assertTrue(inspector.get_check_constraints("users"), "Roles must be restricted inside PostgreSQL")

    def test_02_database_rejects_invalid_values_without_api(self):
        from sqlalchemy import insert
        from sqlalchemy.exc import IntegrityError
        from server.models.desks import Desk
        from server.models.users import User
        with self.SessionLocal() as session:
            with self.assertRaises(IntegrityError):
                session.execute(insert(Desk).values(floor=7, section=1, number=981001, sockets=-1, is_free=True))
                session.commit()
            session.rollback()
            original = session.get(User, self.users["employee"]["id"])
            original.role = "superuser"
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            self.assertEqual("employee", session.get(User, self.users["employee"]["id"]).role)

    def test_03_registration_validation_duplicate_and_no_self_elevation(self):
        existing = self.users["employee"]["email"]
        self.request("POST", "/auth/register", 409, role=None, payload={"full_name": "Duplicate", "email": existing, "password": PASSWORD, "confirm_password": PASSWORD})
        self.request("POST", "/auth/register", 422, role=None, payload={"full_name": " ", "email": "invalid", "password": "123", "confirm_password": "other"})
        self.request("POST", "/auth/register", 422, role=None, payload={"full_name": "Mismatch", "email": f"mismatch-{uuid.uuid4().hex}@company.ru", "password": PASSWORD, "confirm_password": PASSWORD + "other"})
        response = self.client.post(PREFIX + "/auth/register", json={"full_name": "Attacker", "email": f"attacker-{uuid.uuid4().hex}@company.ru", "password": PASSWORD, "confirm_password": PASSWORD, "role": "it_admin"})
        self.assertIn(response.status_code, {201, 403, 422})
        if response.status_code == 201:
            self.assertEqual("employee", response.json()["role"])

    def test_04_authorization_ownership_and_safe_responses(self):
        for path in ("/users/me", "/desks", "/bookings", "/equipment/999999", "/tickets/my", "/notifications"):
            self.request("GET", path, 401, role=None)
        self.request("GET", "/admin/users", 403)
        user = self.request("GET", "/users/me")
        for forbidden in ("password", "password_hash", "JWT_SECRET", "token_hash"):
            self.assertNotIn(forbidden, user)
        self.assertEqual(self.users["employee"]["id"], user["id"])

    def test_05_jwt_signature_expiry_and_logout(self):
        import jwt
        user, token = self.register_user("logout")
        parts = token.split(".")
        self.assertEqual(3, len(parts), "Case 6 requires a signed JWT")
        payload = jwt.decode(token, options={"verify_signature": False})
        self.assertIn("exp", payload)
        tampered = parts[0] + "." + parts[1] + "." + ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        self.request("GET", "/users/me", 401, token=tampered)
        payload["exp"] = int(dt.datetime.now(dt.UTC).timestamp()) - 60
        expired = jwt.encode(payload, self.settings.JWT_SECRET, algorithm="HS256")
        self.request("GET", "/users/me", 401, token=expired)
        self.request("POST", "/auth/logout", 204, token=token)
        self.request("GET", "/users/me", 401, token=token)

    def test_06_desk_crud_persistence_and_conflict(self):
        from server.models.desks import Desk
        desk = self.create_desk()
        detail = self.request("GET", f"/desks/{desk['id']}")
        self.assertEqual(desk["number"], detail["number"])
        changed = self.request("PATCH", f"/admin/desks/{desk['id']}", role="office_manager", payload={"sockets": 6})
        self.assertEqual(6, changed["sockets"])
        with self.SessionLocal() as session:
            self.assertEqual(6, session.get(Desk, desk["id"]).sockets)
        self.request("POST", "/admin/desks", 409, role="office_manager", payload={"floor": desk["floor"], "number": desk["number"]})
        self.request("DELETE", f"/admin/desks/{desk['id']}", 204, role="office_manager")
        self.request("GET", f"/desks/{desk['id']}", 404)

    def test_07_search_filter_sort_and_partial_loading(self):
        first = self.create_desk(floor=8, number=88001, sockets=4)
        self.create_desk(floor=8, number=88002, sockets=4)
        self.create_desk(floor=8, number=88003, sockets=4)
        result = self.items(self.request("GET", "/desks?floor=8&search=880&sort=number&order=desc&limit=2&offset=0"))
        self.assertEqual([88003, 88002], [item["number"] for item in result])
        second = self.items(self.request("GET", "/desks?floor=8&search=880&sort=number&order=desc&limit=2&offset=2"))
        self.assertEqual([first["id"]], [item["id"] for item in second])
        empty = self.items(self.request("GET", "/desks?floor=8&search=missing-office-desk-gear"))
        self.assertEqual([], empty)

    def test_08_equipment_crud_ownership_and_qr(self):
        equipment = self.create_equipment(assigned_user_id=self.users["employee"]["id"])
        equipment_id = equipment["id"]
        mine = self.items(self.request("GET", "/equipment/my"))
        self.assertIn(equipment_id, [item["id"] for item in mine])
        self.request("GET", f"/equipment/{equipment_id}", 403, role="other")
        self.request("PATCH", f"/admin/equipment/{equipment_id}", 403, payload={"type": "Forbidden"})
        changed = self.request("PATCH", f"/admin/equipment/{equipment_id}", role="it_admin", payload={"model": "Changed model"})
        self.assertEqual("Changed model", changed["model"])
        qr = self.request("GET", f"/admin/equipment/{equipment_id}/qr", role="it_admin")
        self.assertTrue(base64.b64decode(qr["qr_code_base64"]).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(qr["qr_code_url"].endswith("/" + str(equipment_id)))
        self.request("DELETE", f"/admin/equipment/{equipment_id}", 204, role="it_admin")
        self.request("GET", f"/equipment/{equipment_id}", 404, role="it_admin")

    def test_09_booking_conflicts_ownership_and_admin_notification(self):
        desk = self.create_desk()
        start = (dt.datetime.now() + dt.timedelta(days=5)).replace(hour=9, minute=0, second=0, microsecond=0)
        payload = {"desk_id": desk["id"], "planed_at": start.isoformat(), "duration_hours": 2}
        booking = self.request("POST", "/bookings", 201, payload=payload)
        self.request("POST", "/bookings", 409, role="other", payload=payload)
        self.request("DELETE", f"/bookings/{booking['id']}", 403, role="other")
        self.request("DELETE", f"/admin/bookings/{booking['id']}", 422, role="office_manager", payload={"reason": " "})
        self.request("DELETE", f"/admin/bookings/{booking['id']}", 204, role="office_manager", payload={"reason": "Acceptance maintenance"})
        notifications = self.items(self.request("GET", "/notifications"))
        self.assertTrue(any("Acceptance maintenance" in item.get("text", "") for item in notifications))
        self.request("POST", "/bookings", 201, role="other", payload=payload)

    def test_10_concurrent_booking_has_only_one_winner(self):
        from fastapi.testclient import TestClient
        desk = self.create_desk()
        start = (dt.datetime.now() + dt.timedelta(days=17)).replace(hour=9, minute=0, second=0, microsecond=0)
        payload = {"desk_id": desk["id"], "planed_at": start.isoformat(), "duration_hours": 1}
        import threading
        barrier = threading.Barrier(2)
        def reserve(role):
            client = TestClient(self.app, raise_server_exceptions=False)
            barrier.wait(timeout=10)
            try:
                return client.post(PREFIX + "/bookings", json=payload, headers={"Authorization": "Bearer " + self.tokens[role]}).status_code
            finally:
                client.close()
        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = sorted(workers.map(reserve, ("employee", "other")))
        self.assertEqual([201, 409], statuses)

    def test_11_ticket_history_comments_notifications_and_file(self):
        from PIL import Image
        import io
        image_bytes = io.BytesIO()
        Image.new("RGB", (8, 8), color="red").save(image_bytes, format="PNG")
        photo = self.request("POST", "/uploads", 201, payload={"filename": "damage.png", "image_base64": base64.b64encode(image_bytes.getvalue()).decode("ascii")})
        equipment = self.create_equipment(assigned_user_id=self.users["employee"]["id"])
        ticket = self.request("POST", "/tickets", 201, payload={"equipment_id": equipment["id"], "description": "Broken display", "issue_type": "repair", "photo_url": photo["photo_url"]})
        ticket_id = ticket["id"]
        self.request("GET", f"/tickets/{ticket_id}", 403, role="other")
        self.request("POST", f"/tickets/{ticket_id}/comments", 201, role="it_admin", payload={"text": "Диагностика начата"})
        self.request("PATCH", f"/admin/tickets/{ticket_id}/status", role="it_admin", payload={"status": "in_progress"})
        events = self.items(self.request("GET", f"/tickets/{ticket_id}/events"))
        self.assertGreaterEqual(len(events), 2)
        self.assertIn("Диагностика начата", json.dumps(events, ensure_ascii=False))
        notifications = self.items(self.request("GET", "/notifications"))
        self.assertTrue(notifications, "Ticket transition must notify its author")
        self.request("POST", f"/notifications/{notifications[-1]['id']}/read", 204)
        path = photo["photo_url"]
        response = self.client.get(path, headers={"Authorization": "Bearer " + self.tokens["employee"]})
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.headers["content-type"].startswith("image/"))
        self.assertEqual(401, self.client.get(path).status_code)

    def test_12_password_reset_revokes_existing_session(self):
        user, token = self.register_user("reset")
        self.request("POST", "/auth/password-reset/request", role=None, payload={"email": user["email"]})
        matching = []
        for path in (self.settings.DATA_DIR / "outbox").glob("*.json"):
            content = json.loads(path.read_text(encoding="utf-8"))
            if content.get("email") == user["email"]:
                matching.append((path.stat().st_mtime_ns, content))
        code = max(matching, key=lambda item: item[0])[1]["code"]
        new_password = PASSWORD + "-changed"
        self.request("POST", "/auth/password-reset/confirm", role=None, payload={"email": user["email"], "code": code, "new_password": new_password, "password_confirm": new_password})
        self.request("GET", "/users/me", 401, token=token)
        self.request("POST", "/auth/login", 401, role=None, payload={"email": user["email"], "password": PASSWORD})
        self.request("POST", "/auth/login", role=None, payload={"email": user["email"], "password": new_password})
        response = self.client.post(PREFIX + "/auth/password-reset/confirm", json={"email": user["email"], "code": code, "new_password": new_password, "password_confirm": new_password})
        self.assertIn(response.status_code, {400, 409, 422})

    def test_13_analytics_validation_and_role_access(self):
        report = self.request("GET", "/admin/analytics", role="office_manager")
        self.assertIn("occupancy_rate", report)
        self.assertGreaterEqual(report["occupancy_rate"], 0)
        self.assertLessEqual(report["occupancy_rate"], 1)
        self.request("GET", "/admin/analytics", 403)
        self.request("GET", "/admin/analytics?start_date=2026-10-29&end_date=2026-10-28", 422, role="office_manager")

    def test_14_normalized_features_and_foreign_key_integrity(self):
        from sqlalchemy import insert, select
        from sqlalchemy.exc import IntegrityError
        from server.models.desks import desk_feature_links
        features = self.request("GET", "/desk-features")
        feature_id = features[0]["id"]
        desk = self.create_desk(features=[feature_id])
        self.assertEqual([feature_id], desk["features"])
        with self.SessionLocal() as session:
            links = session.execute(select(desk_feature_links.c.feature_id).where(desk_feature_links.c.desk_id == desk["id"])).scalars().all()
            self.assertEqual([feature_id], links)
            with self.assertRaises(IntegrityError):
                session.execute(insert(desk_feature_links).values(desk_id=desk["id"], feature_id=2147483647))
                session.commit()
            session.rollback()
            with self.assertRaises(IntegrityError):
                session.execute(insert(desk_feature_links).values(desk_id=desk["id"], feature_id=feature_id))
                session.commit()
            session.rollback()

    def test_15_period_booking_reports_skipped_days_and_rejects_past(self):
        desk = self.create_desk()
        start = (dt.datetime.now() + dt.timedelta(days=25)).replace(hour=9, minute=0, second=0, microsecond=0)
        self.request("POST", "/bookings", 201, payload={"desk_id": desk["id"], "planed_at": start.isoformat(), "duration_hours": 2})
        payload = {"desk_id": desk["id"], "start_date": start.isoformat(), "end_date": (start + dt.timedelta(days=2)).isoformat(), "daily_duration_hours": 2}
        response = self.client.post(PREFIX + "/bookings/period", json=payload, headers={"Authorization": "Bearer " + self.tokens["other"]})
        self.assertEqual(201, response.status_code, response.text)
        self.assertEqual(2, len(response.json()))
        self.assertEqual("1", response.headers["X-Skipped-Count"])
        self.assertEqual(start.date().isoformat(), response.headers["X-Skipped-Days"])
        past = (dt.datetime.now() - dt.timedelta(days=10)).replace(hour=9, minute=0, second=0, microsecond=0)
        payload.update(start_date=past.isoformat(), end_date=past.isoformat())
        self.request("POST", "/bookings/period", 422, payload=payload)

    def test_16_conflicting_booking_edit_preserves_original(self):
        from server.models.bookings import Booking
        first, second = self.create_desk(), self.create_desk()
        start = (dt.datetime.now() + dt.timedelta(days=28)).replace(hour=9, minute=0, second=0, microsecond=0)
        payload = {"desk_id": first["id"], "planed_at": start.isoformat(), "duration_hours": 1}
        original = self.request("POST", "/bookings", 201, payload=payload)
        occupied = dict(payload, desk_id=second["id"], planed_at=(start + dt.timedelta(hours=2)).isoformat())
        self.request("POST", "/bookings", 201, role="other", payload=occupied)
        self.request("PUT", f"/bookings/{original['id']}", 403, role="other", payload=payload)
        self.request("PUT", f"/bookings/{original['id']}", 409, payload=occupied)
        with self.SessionLocal() as session:
            persisted = session.get(Booking, original["id"])
            self.assertEqual(first["id"], persisted.desk_id)
            self.assertEqual(start, persisted.planed_at)
        changed = self.request("PUT", f"/bookings/{original['id']}", payload=dict(payload, planed_at=(start + dt.timedelta(hours=1)).isoformat()))
        self.assertEqual((start + dt.timedelta(hours=1)).isoformat(), changed["planed_at"])

    def test_17_office_timezone_equivalence_and_future_availability(self):
        from server.core.time import office_now
        from zoneinfo import ZoneInfo
        desk = self.create_desk()
        current = (office_now() - dt.timedelta(minutes=5)).replace(microsecond=0)
        active = self.request("POST", "/bookings", 201, payload={"desk_id": desk["id"], "planed_at": current.isoformat(), "duration_hours": 1})
        self.assertEqual(active["id"], self.request("GET", "/bookings/active")["id"])
        start = (office_now() + dt.timedelta(days=35)).replace(hour=9, minute=0, second=0, microsecond=0)
        aware_utc = start.replace(tzinfo=ZoneInfo(self.settings.OFFICE_TIMEZONE)).astimezone(dt.UTC)
        payload = {"desk_id": desk["id"], "planed_at": aware_utc.isoformat(), "duration_hours": 1}
        future = self.request("POST", "/bookings", 201, role="other", payload=payload)
        self.assertEqual(start.isoformat(), future["planed_at"])
        self.request("POST", "/bookings", 409, payload=dict(payload, planed_at=start.isoformat()))
        self.request("DELETE", f"/bookings/{active['id']}", 204)

    def test_18_migrated_schema_matches_orm_metadata(self):
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from server.models import Base
        with self.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            differences = compare_metadata(context, Base.metadata)
        self.assertEqual([], differences, "Alembic migrations and ORM must describe the same schema")

    def test_19_demo_seed_is_idempotent(self):
        from sqlalchemy import func, select
        from server.database.base import seed_db
        from server.models.users import User
        from server.models.equipment import Equipment
        from server.models.desks import Desk
        from server.models.desk_features import DeskFeature
        models = (User, Desk, DeskFeature, Equipment)
        def counts():
            with self.SessionLocal() as session:
                return [session.scalar(select(func.count()).select_from(model)) for model in models]
        seed_db()
        first = counts()
        seed_db()
        self.assertEqual(first, counts())

    def test_20_sessions_return_connections_after_success_and_failure(self):
        for _ in range(3):
            self.request("GET", "/users/me")
            self.request("GET", "/admin/users", 403)
            self.request("POST", "/bookings", 404, payload={"desk_id": 2147483647, "planed_at": (dt.datetime.now() + dt.timedelta(days=40)).isoformat(), "duration_hours": 1})
        self.assertEqual(0, self.engine.pool.checkedout(), "Requests must not retain ORM connections")

    def test_21_ticket_restricts_foreign_assets_and_notifies_it(self):
        equipment = self.create_equipment(assigned_user_id=self.users["employee"]["id"])
        payload = {"equipment_id": equipment["id"], "description": "Acceptance IT notification"}
        self.request("POST", "/tickets", 403, role="other", payload=payload)
        ticket = self.request("POST", "/tickets", 201, payload=payload)
        notifications = self.items(self.request("GET", "/notifications", role="it_admin"))
        self.assertTrue(any(str(ticket["id"]) in item.get("text", "") for item in notifications), "New tickets must notify IT")

    def test_22_booking_lists_keep_owners_private_and_availability_public_to_staff(self):
        desk = self.create_desk()
        start = (dt.datetime.now() + dt.timedelta(days=46)).replace(hour=9, minute=0, second=0, microsecond=0)
        created = self.request("POST", "/bookings", 201, role="other", payload={"desk_id": desk["id"], "planed_at": start.isoformat(), "duration_hours": 1})
        visible = self.request("GET", f"/bookings?desk_id={desk['id']}&date={start.date()}")
        self.assertNotIn(created["id"], [item["id"] for item in visible])
        own = self.request("GET", f"/bookings?desk_id={desk['id']}&date={start.date()}", role="other")
        self.assertIn(created["id"], [item["id"] for item in own])
        availability = self.request("GET", f"/availability?desk_id={desk['id']}&date={start.date()}")
        self.assertEqual(1, len(availability))
        self.assertEqual({"desk_id", "planed_at", "duration"}, set(availability[0]))
        self.request("GET", "/availability", 401, role=None)

    def test_23_validation_errors_do_not_echo_secret_input(self):
        marker = "Private-password-must-not-be-echoed"
        response = self.client.post(PREFIX + "/auth/password-reset/confirm", json={"email": "bad", "code": "wrong", "new_password": marker})
        self.assertEqual(422, response.status_code)
        self.assertNotIn(marker, response.text)
        errors = response.json()["detail"]
        self.assertTrue(errors)
        self.assertTrue(all("input" not in item for item in errors))
        self.assertNotIn("traceback", response.text.lower())

    def test_24_report_snapshots_persist_and_export(self):
        from server.models.reports import Report
        path = "/admin/reports?start_date=2026-10-01&end_date=2026-10-29"
        self.request("POST", path, 403)
        created = self.request("POST", path, 201, role="office_manager")
        with self.SessionLocal() as session:
            persisted = session.get(Report, created["id"])
            self.assertEqual(created["data"], persisted.data)
        listing = self.request("GET", "/admin/reports", role="office_manager")
        self.assertIn(created["id"], [item["id"] for item in listing])
        exported = self.request("GET", f"/admin/reports/{created['id']}/export", role="office_manager")
        self.assertEqual(created["data"], exported["data"])
        self.request("GET", f"/admin/reports/{created['id']}/export", 403)

    def test_24b_availability_includes_reservations_crossing_midnight(self):
        desk = self.create_desk()
        start = (dt.datetime.now() + dt.timedelta(days=48)).replace(hour=23, minute=0, second=0, microsecond=0)
        self.request("POST", "/bookings", 201, role="other", payload={"desk_id": desk["id"], "planed_at": start.isoformat(), "duration_hours": 20})
        next_day = (start + dt.timedelta(days=1)).date()
        schedule = self.request("GET", f"/availability?desk_id={desk['id']}&date={next_day}")
        self.assertEqual(1, len(schedule), "Today's schedule must include an overlapping reservation that started yesterday")
        self.assertEqual(start.isoformat(), schedule[0]["planed_at"])
        results = self.request("GET", f"/desks?floor={desk['floor']}&search={desk['number']}&start_at={next_day}T09:00:00&end_at={next_day}T17:00:00")
        selected = next(item for item in self.items(results) if item["id"] == desk["id"])
        self.assertFalse(selected["is_free"])

    def test_25_changed_email_requires_confirmation_before_next_login(self):
        new_email = f"changed-{uuid.uuid4().hex}@company.ru"
        updated = self.request("PATCH", "/users/me", payload={"email": new_email})
        self.assertFalse(updated["is_verified"])
        self.request("GET", "/users/me", 403)
        self.request("POST", "/auth/login", 403, role=None, payload={"email": new_email, "password": PASSWORD})
        self.request("POST", "/auth/resend-verification", role=None, payload={"email": new_email})
        messages = []
        for path in (self.settings.DATA_DIR / "outbox").glob("*.json"):
            content = json.loads(path.read_text(encoding="utf-8"))
            if content.get("email") == new_email:
                messages.append((path.stat().st_mtime_ns, content))
        self.assertTrue(messages)
        code = max(messages, key=lambda item: item[0])[1]["code"]
        self.request("POST", "/auth/verify-email", role=None, payload={"email": new_email, "code": code})
        result = self.request("POST", "/auth/login", role=None, payload={"email": new_email, "password": PASSWORD})
        profile = self.request("GET", "/users/me", token=result["access_token"])
        self.assertEqual(self.users["employee"]["id"], profile["id"])
        self.assertEqual(new_email, profile["email"])


@unittest.skipIf(bool(os.getenv("ODG_TEST_SCHEMA")), "The worker directly runs PostgreSQL cases")
class TestIsolatedAcceptance(unittest.TestCase):
    def test_postgresql_acceptance_in_isolated_schema(self):
        self.assertEqual(0, run_isolated())


def load_tests(loader, _tests, _pattern):
    target = PostgreSQLAcceptance if os.getenv("ODG_TEST_SCHEMA") else TestIsolatedAcceptance
    return loader.loadTestsFromTestCase(target)


if __name__ == "__main__":
    if "--worker" in sys.argv:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
    else:
        raise SystemExit(run_isolated())
