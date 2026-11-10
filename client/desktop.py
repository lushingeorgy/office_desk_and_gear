"""Desktop entry point: use the project's environment before importing Kivy."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

CLIENT_MODULES = ("kivy", "kivymd", "requests", "PIL", "plyer", "cv2", "certifi", "urllib3",
                  "idna", "charset_normalizer", "materialyoucolor", "materialshapes",
                  "asynckivy", "asyncgui", "cairo")


def prepare_paths(root):
    # Keep desktop files writable and independent of the terminal's cwd.
    os.environ.setdefault("KIVY_HOME", str(root / ".kivy"))
    os.environ.setdefault("ODG_CLIENT_DATA", str(root / ".appdata"))


def launch(run_application, argv=None):
    # python-for-android owns its interpreter and dependencies.
    if os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_PRIVATE") or sys.platform == "ios":
        return run_application()

    root = Path(__file__).resolve().parents[1]
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Office Desk & Gear")
    parser.add_argument("--page", choices=("home", "desks"), default="home")
    parser.add_argument("--plan", action="store_true", help="Open desk search with the floor plan")
    parser.add_argument("--check", action="store_true", help="Check dependencies and the configured API")
    parser.add_argument("--install", action="store_true", help="Install or repair the client dependencies")
    options = parser.parse_args(args)

    environment = root / ".venv"
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not interpreter.is_file():
        print("Preparing the project Python environment...", flush=True)
        import venv
        venv.EnvBuilder(with_pip=True).create(environment)
    if Path(sys.prefix).resolve() != environment.resolve():
        return subprocess.call([str(interpreter), str(root / "main.py"), *args], cwd=root)

    prepare_paths(root)
    missing = [name for name in CLIENT_MODULES if importlib.util.find_spec(name) is None]
    if missing or options.install:
        print("Installing client dependencies...", flush=True)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                               "-r", str(root / "requirements-client.txt")], cwd=root)
        importlib.invalidate_caches()
    if options.check:
        from client.services.api_client import ApiClient, ApiError
        result = {"python": sys.executable,
                  "missing_modules": [name for name in CLIENT_MODULES if importlib.util.find_spec(name) is None]}
        api = ApiClient(timeout=5)
        try:
            result["server"] = api.check_connection()
        except ApiError as error:
            result["connection_error"] = str(error)
        finally:
            api.close()
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return int(bool(result["missing_modules"]) or "connection_error" in result)

    os.environ["ODG_START_PAGE"] = "desks" if options.plan else options.page
    os.environ["ODG_START_PLAN"] = "1" if options.plan else "0"
    return run_application()
