"""Regression coverage for public roles and the two documented desk responses."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from pydantic import ValidationError as SchemaValidationError
from server.api.api import get_current_user
from server.api.pydantic_schemas import DeskCreate, DeskResponse, DeskUpdate, UserCreate
from server.core.exceptions import ValidationError
from server.database.session import get_db
from server.main import app
from server.services.desk_service import DeskListResult
from server.services.user_service import UserService


class PublicRegistrationTests(unittest.TestCase):
    def test_privileged_roles_are_rejected_by_schema_and_direct_service(self):
        for role in ("office_manager", "it_admin", "superuser", None, ""):
            with self.subTest(role=role):
                payload = dict(full_name="New employee", email="new@example.org", password="Password-2026",
                               confirm_password="Password-2026", role=role)
                with self.assertRaises(SchemaValidationError):
                    UserCreate(**payload)
                repository = Mock()
                repository.get_user_by_email.return_value = None
                with self.assertRaises(ValidationError):
                    UserService(repository).register(SimpleNamespace(**payload))
                repository.session.add.assert_not_called()
                repository.commit.assert_not_called()

    def test_new_accounts_default_to_employee(self):
        payload = UserCreate(full_name="New employee", email="new@example.org", password="Password-2026",
                             confirm_password="Password-2026")
        self.assertEqual("employee", payload.role)


class DeskContractTests(unittest.TestCase):
    def test_optional_legacy_coordinates_and_ground_floor_are_supported(self):
        for model in (DeskCreate, DeskUpdate):
            with self.subTest(model=model):
                values = {"number": 1} if model is DeskCreate else {}
                self.assertEqual(0, model(**values, floor=0, section=1).floor)
                self.assertIsNone(model(**values, floor=None, section=None).floor)
                for invalid in ({"floor": -1}, {"section": 0}, {"section": -1}):
                    with self.assertRaises(SchemaValidationError):
                        model(**values, **invalid)

    def test_openapi_and_runtime_cover_strict_and_softened_search_results(self):
        desk = DeskResponse(id=1, floor=0, section=1, number=1, sockets=2)
        service = Mock()
        with patch.dict(app.dependency_overrides, {get_current_user: lambda: object(), get_db: lambda: None}), \
                patch("server.api.api._desk_service", return_value=service), \
                patch.object(app, "openapi_schema", None):
            client = TestClient(app)
            try:
                service.filter_desks.return_value = DeskListResult(desks=[desk])
                strict = client.get("/api/v1/desks")
                self.assertEqual(200, strict.status_code)
                self.assertEqual([desk.model_dump()], strict.json())
                self.assertEqual("false", strict.headers["x-partial-match"])
                service.filter_desks.return_value = DeskListResult(desks=[desk], partial=True, message="Nearby options")
                softened = client.get("/api/v1/desks?sockets=99")
                self.assertEqual(200, softened.status_code)
                self.assertEqual([desk.model_dump()], softened.json()["items"])
                self.assertTrue(softened.json()["soft_filter"])
                self.assertEqual("true", softened.headers["x-partial-match"])
                schema = client.get("/openapi.json").json()["paths"]["/api/v1/desks"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
                self.assertTrue(any(item.get("type") == "array" for item in schema["anyOf"]))
                self.assertIn({"$ref": "#/components/schemas/DeskListResponse"}, schema["anyOf"])
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
