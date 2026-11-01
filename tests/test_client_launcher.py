"""UI startup never launches a backend; the default API needs no device setup."""
import builtins
import os
from pathlib import Path
import runpy
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main


class LauncherTests(unittest.TestCase):
    def test_startup_never_imports_backend_on_desktop_or_mobile(self):
        original_import = builtins.__import__

        def client_only_import(name, *args, **kwargs):
            if name == "server" or name.startswith("server.") or name == "client.core.local_server":
                self.fail("Client startup attempted to import the backend: " + name)
            return original_import(name, *args, **kwargs)

        for platform in ("android", "ios", "win", "linux"):
            with self.subTest(platform=platform), \
                    patch.dict(sys.modules, {"kivy.utils": types.SimpleNamespace(platform=platform)}), \
                    patch.dict(os.environ, {"DSH_API_URL": "", "ODG_CLIENT_ONLY": "0"}), \
                    patch("builtins.__import__", side_effect=client_only_import), \
                    patch.object(main, "create_visual_app") as create, \
                    patch.object(main, "run_visual") as run:
                main.main()
                create.assert_called_once_with()
                run.assert_called_once_with(create.return_value)

    def test_fixed_api_is_used_without_environment_configuration(self):
        for environment in ({}, {"DSH_API_URL": ""}):
            with self.subTest(environment=environment), patch.dict(os.environ, environment, clear=True):
                config = runpy.run_path(str(ROOT / "client" / "core" / "config.py"))
                self.assertEqual(config["API_BASE_URL"], "https://138.16.179.111/api/v1")

    def test_isolated_test_process_can_use_local_fixture(self):
        with patch.dict(os.environ, {"DSH_API_URL": "http://127.0.0.1:8094/api/v1/"}):
            config = runpy.run_path(str(ROOT / "client" / "core" / "config.py"))
            self.assertEqual(config["API_BASE_URL"], "http://127.0.0.1:8094/api/v1")
            self.assertEqual(config["DEFAULT_API_URL"], "https://138.16.179.111/api/v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
