"""Desktop entry points must work without activating the project environment."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from client import desktop


class DesktopBootstrapTests(unittest.TestCase):
    def test_system_python_delegates_to_project_environment(self):
        run = Mock()
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "prefix", str(ROOT)), \
                patch.object(desktop.subprocess, "call", return_value=7) as delegate:
            self.assertEqual(desktop.launch(run, ["--plan"]), 7)
        arguments = delegate.call_args.args[0]
        self.assertEqual(Path(arguments[0]), ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        self.assertEqual(arguments[1:], [str(ROOT / "main.py"), "--plan"])
        run.assert_not_called()

    def test_mobile_keeps_the_packaged_interpreter(self):
        for marker in ("ANDROID_ARGUMENT", "ANDROID_PRIVATE"):
            with self.subTest(marker=marker), patch.dict(os.environ, {marker: "app"}, clear=True), \
                    patch.object(desktop.subprocess, "call") as delegate:
                run = Mock(return_value=3)
                self.assertEqual(desktop.launch(run, []), 3)
                delegate.assert_not_called()
                run.assert_called_once_with()

    def test_configured_environment_opens_plan_without_reinstalling(self):
        run = Mock(return_value=0)
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "prefix", str(ROOT / ".venv")), \
                patch.object(desktop.importlib.util, "find_spec", return_value=object()), \
                patch.object(desktop.subprocess, "check_call") as install:
            self.assertEqual(desktop.launch(run, ["--plan"]), 0)
            self.assertEqual(os.environ["ODG_START_PAGE"], "desks")
            self.assertEqual(os.environ["ODG_START_PLAN"], "1")
            self.assertEqual(Path(os.environ["KIVY_HOME"]), ROOT / ".kivy")
            self.assertEqual(Path(os.environ["ODG_CLIENT_DATA"]), ROOT / ".appdata")
            install.assert_not_called()
            run.assert_called_once_with()

    def test_missing_native_dependency_installs_declared_requirements(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(sys, "prefix", str(ROOT / ".venv")), \
                patch.object(desktop.importlib.util, "find_spec", side_effect=lambda name: None if name == "cairo" else object()), \
                patch.object(desktop.subprocess, "check_call") as install:
            desktop.launch(Mock(), [])
        command = install.call_args.args[0]
        self.assertEqual(command[:3], [sys.executable, "-m", "pip"])
        self.assertIn(str(ROOT / "requirements-client.txt"), command)


if __name__ == "__main__":
    unittest.main(verbosity=2)
