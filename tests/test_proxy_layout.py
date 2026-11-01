"""The shared host's URL prefix must work independently of /opt/projects."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ProxyLayoutTests(unittest.TestCase):
    def test_shared_proxy_routes_docs_and_private_uploads(self):
        from fastapi.testclient import TestClient
        from server.main import app

        # No lifespan/database mutation: only docs, system info and auth rejection.
        with patch.object(app, "root_path", "/office-desk-gear"), \
                patch.object(app, "openapi_schema", None), patch.object(app, "servers", list(app.servers)):
            client = TestClient(app, root_path="/office-desk-gear")
            try:
                response = client.get("/api/v1/system/info")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["service"], "office-desk-gear")
                self.assertIn('/office-desk-gear/openapi.json', client.get("/docs").text)
                self.assertEqual(client.get("/openapi.json").json()["servers"][0]["url"], "/office-desk-gear")
                self.assertEqual(client.get("/api/v1/uploads/unknown-private-id").status_code, 401)
            finally:
                client.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
