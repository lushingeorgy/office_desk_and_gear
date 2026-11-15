"""Real PostgreSQL regressions for registration and removal of email verification.

Every database write uses a unique schema that is removed after the suite.
Run: python -m unittest discover -s tests -p test_registration_auth.py -v
"""
from pathlib import Path
import sys
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from server.api.api import app
from server.core.config import settings
from server.core.security import hash_password
from server.database.session import get_db
from server.repositories.auth_repository import AuthRepository
from server.repositories.user_repository import UserRepository
from server.services.auth_service import AuthService
from server.services.user_service import UserService

PASSWORD = "Registration-password-2026"


class RegistrationAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "registration_" + uuid.uuid4().hex
        cls.control = create_engine(settings.DATABASE_URL, connect_args={"connect_timeout": 5})
        with cls.control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        cls.addClassCleanup(cls.remove_schema)
        cls.engine = create_engine(settings.DATABASE_URL, connect_args={
            "connect_timeout": 5, "options": f"-csearch_path={cls.schema}",
        })
        cls.addClassCleanup(cls.engine.dispose)
        cls.sessions = sessionmaker(bind=cls.engine, expire_on_commit=False)
        with cls.engine.connect() as connection:
            configuration = Config(str(ROOT / "alembic.ini"))
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "0003")
            connection.execute(text(
                "INSERT INTO users(full_name,email,password_hash,created_at,role,is_verified) "
                "VALUES ('Legacy unverified','legacy@example.org',:password,CURRENT_DATE,'employee',false)"
            ), {"password": hash_password(PASSWORD)})
            connection.execute(text(
                "INSERT INTO email_verification_codes(user_id,code_hash,expires_at) "
                "SELECT id,repeat('a',64),CURRENT_TIMESTAMP + interval '30 minutes' FROM users"
            ))
            connection.commit()
            command.upgrade(configuration, "head")

    @classmethod
    def remove_schema(cls):
        # This exact identifier was generated here and has no user-controlled text.
        with cls.control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{cls.schema}" CASCADE'))
        cls.control.dispose()

    def setUp(self):
        from server.core.rate_limit import _attempts, _lock
        with _lock:
            _attempts.clear()

        def database():
            with self.sessions() as session:
                yield session

        self.overrides = patch.dict(app.dependency_overrides, {get_db: database})
        self.overrides.start()
        self.addCleanup(self.overrides.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_all_three_roles_register_then_login_without_mail(self):
        with patch("server.services.user_service.send_code") as deliver:
            for role in ("employee", "office_manager", "it_admin"):
                with self.subTest(role=role):
                    email = f"{role}-{uuid.uuid4().hex}@example.org"
                    response = self.client.post("/api/v1/auth/register", json={
                        "full_name": "Registered user", "email": email, "password": PASSWORD,
                        "confirm_password": PASSWORD, "role": role,
                    })
                    self.assertEqual(201, response.status_code, response.text)
                    self.assertEqual(role, response.json()["role"])
                    self.assertNotIn("is_verified", response.json())
                    login = self.client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
                    self.assertEqual(200, login.status_code, login.text)
                    profile = self.client.get("/api/v1/users/me", headers={
                        "Authorization": "Bearer " + login.json()["access_token"],
                    })
                    self.assertEqual(200, profile.status_code, profile.text)
                    self.assertEqual(role, profile.json()["role"])
            deliver.assert_not_called()

    def test_existing_unverified_account_survives_migration_and_can_login(self):
        inspector = inspect(self.engine)
        self.assertNotIn("email_verification_codes", inspector.get_table_names())
        self.assertNotIn("is_verified", {column["name"] for column in inspector.get_columns("users")})
        response = self.client.post("/api/v1/auth/login", json={"email": "legacy@example.org", "password": PASSWORD})
        self.assertEqual(200, response.status_code, response.text)
        with self.sessions() as session:
            users = UserService(UserRepository(session))
            auth = AuthService(AuthRepository(session), users)
            self.assertEqual("legacy@example.org", auth.authenticate_token(response.json()["access_token"]).email)

    def test_verification_endpoints_and_schema_are_removed(self):
        for endpoint in ("verify-email", "resend-verification"):
            response = self.client.post("/api/v1/auth/" + endpoint, json={"email": "legacy@example.org", "code": "123456"})
            self.assertEqual(404, response.status_code)
            self.assertNotIn("/api/v1/auth/" + endpoint, app.openapi()["paths"])
        self.assertNotIn("EmailVerify", app.openapi()["components"]["schemas"])

    def test_password_reset_mail_remains_available(self):
        with self.sessions() as session, patch("server.services.user_service.send_code") as deliver:
            users = UserService(UserRepository(session))
            code = users.issue_reset_code("legacy@example.org")
            deliver.assert_called_once_with("legacy@example.org", code, "Восстановление пароля")
            new_password = "Changed-password-2026"
            users.confirm_reset("legacy@example.org", code, new_password)
            self.assertEqual("legacy@example.org", users.authenticate_model("legacy@example.org", new_password).email)
            # Keep the migration fixture reusable regardless of test order.
            users.authenticate_model("legacy@example.org", new_password).password_hash = hash_password(PASSWORD)
            session.commit()


if __name__ == "__main__":
    unittest.main(verbosity=2)
