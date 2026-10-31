"""Temporary loopback-only PostgreSQL API for a physical-phone acceptance run.

Run ``python tests/phone_api.py --port 8094`` and use the printed adb reverse
command separately after the phone has been authorized. This helper never
installs an APK, operates the phone UI, or modifies the normal database schema.
Ctrl+C, the advertised stop file, or the duration limit trigger cleanup.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PhoneAPIError(RuntimeError):
    """An intentionally safe, credential-free user-facing diagnostic."""


def serve(port: int, duration_seconds: int) -> None:
    if any(name in sys.modules for name in ("server.core.config", "server.database.session", "server.api.api", "server.main")):
        raise PhoneAPIError("Run phone_api.py in a fresh Python process; no schema was created")
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    # Keep the actual listening socket reserved until Uvicorn takes ownership.
    # Failure here precedes all database DDL and avoids a check/bind race.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
    except OSError as error:
        listener.close()
        raise PhoneAPIError(f"Port 127.0.0.1:{port} is unavailable; no schema was created") from error

    load_dotenv(ROOT / ".env")
    database_url = make_url(os.getenv("DATABASE_URL") or "postgresql+psycopg://postgres@127.0.0.1:5432/office_desk_gear")
    if database_url.get_backend_name() != "postgresql":
        listener.close()
        raise PhoneAPIError("The temporary phone API requires PostgreSQL")
    database_url = database_url.set(drivername="postgresql+psycopg")
    schema = "phone_" + uuid.uuid4().hex
    if re.fullmatch(r"phone_[0-9a-f]{32}", schema) is None:
        listener.close()
        raise PhoneAPIError("Invalid generated schema name")
    directory = ROOT / "tests" / "artifacts" / schema
    directory.mkdir(parents=True, exist_ok=False)
    connection_path, stop_path = directory / "connection.json", directory / "stop"
    connection = {
        "status": "preparing",
        "schema": schema,
        "host": "127.0.0.1",
        "port": port,
        "api_url": f"http://127.0.0.1:{port}/api/v1",
        "health_url": f"http://127.0.0.1:{port}/health/ready",
        "adb_reverse_command": f"adb reverse tcp:{port} tcp:{port}",
        "data_directory": str(directory),
        "connection_file": str(connection_path),
        "stop_file": str(stop_path),
        "duration_seconds": duration_seconds,
        "created_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "schema_removed": False,
    }

    def write_connection():
        temporary = connection_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(connection, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, connection_path)

    # This control pool never inherits the phone schema's PGOPTIONS. It only
    # creates/removes the exact unique schema generated in this invocation.
    control = create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 5, "options": "-csearch_path=public"})
    application_engine = None
    created = False
    finished = threading.Event()
    monitor = None
    try:
        with control.begin() as database:
            database.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        # A URL's libpq options override PGOPTIONS. Replace them for this
        # temporary application instead of trusting an inherited search_path.
        application_url = database_url.update_query_dict({"options": f"-csearch_path={schema}"})
        # No server module is imported until every application setting is fixed.
        os.environ.update(
            DATABASE_URL=application_url.render_as_string(hide_password=False),
            PGOPTIONS=f"-csearch_path={schema}",
            APP_ENV="test", ENVIRONMENT="test", ODG_SEED="0",
            JWT_SECRET=secrets.token_urlsafe(48), DATA_DIR=str(directory),
            REQUIRE_EMAIL_VERIFICATION="0", MAIL_MODE="outbox",
            HOST="127.0.0.1", PORT=str(port),
        )
        from alembic import command
        from alembic.config import Config
        from server.database.session import engine
        application_engine = engine
        with application_engine.connect() as database:
            actual_schema = database.execute(text("SELECT current_schema()")).scalar_one_or_none()
            if actual_schema != schema:
                raise PhoneAPIError("Temporary phone database isolation could not be verified; migrations were not run")
        connection["schema_verified"] = True
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        from server.database.base import seed_db
        seed_db()
        import uvicorn
        from server.main import app
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, access_log=False,
            timeout_graceful_shutdown=10, log_level="info",
        ))
        deadline = time.monotonic() + duration_seconds

        def monitor_shutdown():
            while not finished.wait(.5):
                if stop_path.exists() or time.monotonic() >= deadline:
                    server.should_exit = True
                    return
                if server.started and connection["status"] == "starting":
                    connection["status"] = "ready"
                    write_connection()

        monitor = threading.Thread(target=monitor_shutdown, name="phone-api-expiry", daemon=True)
        monitor.start()
        connection["status"] = "starting"
        connection["expires_at_utc"] = (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=duration_seconds)).isoformat()
        write_connection()
        print(f"Temporary phone API: {connection['api_url']}", flush=True)
        print(f"Phone forwarding: {connection['adb_reverse_command']}", flush=True)
        print(f"Connection file: {connection_path}", flush=True)
        print(f"Stop: Ctrl+C or create {stop_path}; automatic stop in {duration_seconds} seconds", flush=True)
        server.run(sockets=[listener])
    except KeyboardInterrupt:
        pass
    finally:
        finished.set()
        if monitor:
            monitor.join(timeout=2)
        if application_engine is not None:
            application_engine.dispose()
        listener.close()
        try:
            if created:
                # Never derive a removal target from a saved state file or CLI input.
                if re.fullmatch(r"phone_[0-9a-f]{32}", schema) is None:
                    raise PhoneAPIError("Unsafe phone schema cleanup target")
                with control.begin() as database:
                    database.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                connection["schema_removed"] = True
        finally:
            control.dispose()
            connection["status"] = "stopped"
            connection["stopped_at_utc"] = dt.datetime.now(dt.UTC).isoformat()
            write_connection()
        if created:
            print(f"Removed isolated schema {schema}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8094)
    parser.add_argument("--duration-seconds", type=int, default=3600)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if not 1 <= args.duration_seconds <= 7200:
        parser.error("--duration-seconds must be between 1 and 7200")
    try:
        serve(args.port, args.duration_seconds)
    except PhoneAPIError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:
        # Never echo DSNs, connection secrets, environment values or reset codes.
        print(f"Temporary phone API failed ({type(error).__name__}); inspect database availability and migration diagnostics", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
