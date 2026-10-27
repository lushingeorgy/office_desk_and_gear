import os
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_HOME", str(ROOT / "tests/_kivy_home"))
os.environ.setdefault("KIVY_NO_ARGS", "1")

from client.core.local_server import LocalServer
from server.core.config import settings
import main as entry


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class LauncherTests(unittest.TestCase):
    def test_owned_server_starts_and_stops(self):
        with LocalServer(port=free_port()) as server:
            with requests.get(server.base_url + "/system/info", timeout=2) as response:
                self.assertEqual(response.json()["service"], "office-desk-gear")
            self.assertTrue(server.thread.is_alive())
        self.assertFalse(server.thread.is_alive())

    def test_existing_server_is_reused_and_preserved(self):
        with LocalServer(port=free_port()) as owner:
            with LocalServer(port=owner.port) as borrowed:
                self.assertIsNone(borrowed.server)
            self.assertTrue(owner.thread.is_alive())
            with requests.get(owner.base_url + "/system/info", timeout=2) as response:
                self.assertEqual(response.status_code, 200)
        self.assertFalse(owner.thread.is_alive())

    def test_main_waits_for_api_before_opening_visual(self):
        port = free_port()
        url = f"http://127.0.0.1:{port}/api/v1"
        def visual():
            self.assertEqual(os.environ["DSH_API_URL"], url)
            with requests.get(url + "/system/info", timeout=2) as response:
                self.assertEqual(response.status_code, 200)
        with patch.object(settings, "HOST", "127.0.0.1"), patch.object(settings, "PORT", port), patch.dict(os.environ), patch.object(entry, "run_visual", side_effect=visual) as ui:
            os.environ.pop("DSH_API_URL", None)
            entry.main()
            ui.assert_called_once()
        self.assertFalse(any(thread.name == "office-desk-gear-api" for thread in threading.enumerate()))

    def test_ui_failure_still_closes_server(self):
        with patch.object(settings, "HOST", "127.0.0.1"), patch.object(settings, "PORT", free_port()), patch.dict(os.environ), patch.object(entry, "run_visual", side_effect=RuntimeError("ошибка интерфейса")):
            os.environ.pop("DSH_API_URL", None)
            with self.assertRaisesRegex(RuntimeError, "ошибка интерфейса"):
                entry.main()
        self.assertFalse(any(thread.name == "office-desk-gear-api" for thread in threading.enumerate()))

    def test_startup_failure_does_not_open_visual(self):
        with patch.object(settings, "HOST", "127.0.0.1"), patch.object(settings, "PORT", free_port()), patch("server.api.api.init_db", side_effect=RuntimeError("ошибка подключения к БД")), patch.object(entry, "run_visual") as visual:
            with self.assertRaisesRegex(RuntimeError, "Сервер не запустился"):
                entry.main()
            visual.assert_not_called()
        self.assertFalse(any(thread.name == "office-desk-gear-api" for thread in threading.enumerate()))

    def test_phone_runs_visual_without_local_server(self):
        with patch("kivy.utils.platform", "android"), patch("client.core.local_server.LocalServer") as server, patch.object(entry, "run_visual") as visual:
            entry.main()
            server.assert_not_called()
            visual.assert_called_once()

    def test_real_window_and_server_close_together(self):
        from kivy.clock import Clock
        from kivymd.app import MDApp

        port = free_port()
        completed = []
        errors = []
        def close_window(_dt):
            app = MDApp.get_running_app()
            try:
                self.assertEqual(set(app.root.fields), {"full_name", "job_title", "email", "password", "confirm_password"})
                self.assertEqual(app.root.api.base_url, f"http://127.0.0.1:{port}/api/v1")
                with requests.get(app.root.api.base_url + "/system/info", timeout=2) as response:
                    self.assertEqual(response.status_code, 200)
                completed.append(True)
            except Exception as error:
                errors.append(error)
            finally:
                app.stop()

        with patch.object(settings, "HOST", "127.0.0.1"), patch.object(settings, "PORT", port), patch.dict(os.environ):
            os.environ.pop("DSH_API_URL", None)
            Clock.schedule_once(close_window, 1)
            entry.main()
        if errors:
            raise errors[0]
        self.assertTrue(completed)
        self.assertFalse(any(thread.name == "office-desk-gear-api" for thread in threading.enumerate()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
