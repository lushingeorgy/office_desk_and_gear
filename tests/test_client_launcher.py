"""The mobile/remote launcher must not import or start the database server."""
from pathlib import Path
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main
from client.core.storage import LocalState


class LauncherTests(unittest.TestCase):
    def test_android_does_not_start_local_server(self):
        application = types.SimpleNamespace(run=lambda: None)
        with patch.dict(sys.modules, {"kivy.utils": types.SimpleNamespace(platform="android")}), \
                patch.object(main, "create_visual_app", return_value=application), \
                patch.object(main, "run_visual") as run:
            main.main()
            run.assert_called_once_with(application)

    def test_explicit_remote_api_does_not_start_local_server(self):
        application = types.SimpleNamespace(run=lambda: None)
        with patch.dict(sys.modules, {"kivy.utils": types.SimpleNamespace(platform="win")}), \
                patch.dict(os.environ, {"DSH_API_URL": "https://office.example.org/api/v1"}), \
                patch.object(main, "create_visual_app", return_value=application), \
                patch.object(main, "run_visual") as run:
            main.main()
            run.assert_called_once_with(application)

    def test_saved_remote_setting_survives_launcher_restart(self):
        temporary = ROOT / "tests" / "artifacts" / "launcher"
        temporary.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as folder:
            LocalState(folder).set_api_url("https://office.example.org/api/v1")
            application = types.SimpleNamespace(run=lambda: None)
            with patch.dict(sys.modules, {"kivy.utils": types.SimpleNamespace(platform="win")}), \
                    patch.dict(os.environ, {"DSH_API_URL": "", "ODG_CLIENT_ONLY": "0", "ODG_CLIENT_DATA": folder}), \
                    patch.object(main, "create_visual_app", return_value=application), \
                    patch.object(main, "run_visual") as run:
                main.main()
                run.assert_called_once_with(application)


if __name__ == "__main__":
    unittest.main(verbosity=2)
