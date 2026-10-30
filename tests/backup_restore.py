"""SAD 11.4.7: restore the current database and uploads, then read through API.

Run separately: python tests/backup_restore.py. Requires PostgreSQL client tools
and CREATE DATABASE permission. Only two generated databases are ever written;
the configured application database is never opened by this test.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACTS = ROOT / "tests" / "artifacts"
PREFIX = "/api/v1"
DATABASE_NAME = re.compile(r"odg_backup_(?:src|dst)_[0-9a-f]{32}")
WORK_DIRECTORY = re.compile(r"backup_restore_[0-9a-f]{32}")


class CheckFailure(RuntimeError):
    """Only hardcoded check labels, never SQL, credentials or API bodies."""


def check(condition, label: str) -> None:
    if not condition:
        raise CheckFailure(label)


def run_child(arguments, environment, phase: str, timeout=90):
    try:
        result = subprocess.run(arguments, cwd=ROOT, env=environment,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CheckFailure(phase + "_timeout") from None
    if result.returncode:
        # Child logs can include SQL parameters; never echo stdout/stderr.
        raise CheckFailure(phase + "_failed")
    return result


def worker(mode: str) -> None:
    """Fresh interpreter ensures no engine/settings from another DB survive."""
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    expected_name = os.environ["ODG_BACKUP_DATABASE"]
    check(DATABASE_NAME.fullmatch(expected_name), "worker_database_name")
    check(url.database == expected_name, "worker_database_url")
    working = Path(os.environ["ODG_BACKUP_WORK"]).resolve()
    check(working.parent == ARTIFACTS.resolve() and WORK_DIRECTORY.fullmatch(working.name),
          "worker_directory")
    data_dir = Path(os.environ["DATA_DIR"]).resolve()
    check(data_dir.parent == working and data_dir.name in {"source_data", "restored_data"},
          "worker_data_directory")
    # Verify the actual connection target before any migration or API writes.
    probe = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with probe.connect() as connection:
            actual = connection.execute(text("SELECT current_database(), current_schema()")).one()
            check(tuple(actual) == (expected_name, "public"), "worker_actual_database_schema")
    finally:
        probe.dispose()

    logging.basicConfig(level=logging.ERROR)
    if mode == "source":
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    from fastapi.testclient import TestClient
    from PIL import Image
    from server.database.session import engine
    from server.main import app

    for name in ("office_desk_gear", "httpx", "alembic"):
        logging.getLogger(name).setLevel(logging.ERROR)

    def request(client, method, path, token=None, payload=None, status=200):
        headers = {"Authorization": "Bearer " + token} if token else {}
        response = client.request(method, PREFIX + path, headers=headers, json=payload)
        check(response.status_code == status, "api_" + method.lower() + "_status")
        return response.json() if response.content else None

    def snapshot(client, fixture):
        token = fixture["token"]
        ticket_id = fixture["ticket_id"]
        result = {
            "profile": request(client, "GET", "/users/me", token),
            "bookings": request(client, "GET", "/bookings/history", token),
            "tickets": request(client, "GET", "/tickets/my", token),
            "ticket": request(client, "GET", f"/tickets/{ticket_id}", token),
            "events": request(client, "GET", f"/tickets/{ticket_id}/events", token),
            "notifications": request(client, "GET", "/notifications", token),
            "equipment": request(client, "GET", "/equipment/my", token),
        }
        image = client.get(fixture["photo_url"], headers={"Authorization": "Bearer " + token})
        check(image.status_code == 200 and image.headers["content-type"].startswith("image/"),
              "authenticated_attachment_read")
        check(client.get(fixture["photo_url"]).status_code == 401, "attachment_requires_auth")
        result["attachment_sha256"] = hashlib.sha256(image.content).hexdigest()
        check(engine.pool.checkedout() == 0, "api_connections_returned")
        return result

    fixture_path = working / "private_fixture.json"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            if mode == "source":
                password = secrets.token_urlsafe(24)

                def register(label, role):
                    email = label + "@backup-test.invalid"
                    user = request(client, "POST", "/auth/register", payload={
                        "full_name": "Backup fixture " + label, "job_title": "Synthetic",
                        "email": email, "password": password, "confirm_password": password,
                        "role": role}, status=201)
                    login = request(client, "POST", "/auth/login", payload={
                        "email": email, "password": password})
                    return user, login["access_token"]

                user, token = register("employee", "employee")
                _, it_token = register("technician", "it_admin")
                _, office_token = register("manager", "office_manager")
                desk = request(client, "POST", "/admin/desks", office_token, {
                    "floor": 4, "section": 2, "number": 421, "sockets": 3, "features": []}, 201)
                equipment = request(client, "POST", "/admin/equipment", it_token, {
                    "type": "Монитор", "model": "Restore fixture", "socket_serial": "BACKUP-SN-001",
                    "inventory_number": "BACKUP-INV-001", "desk_id": desk["id"],
                    "assigned_user_id": user["id"]}, 201)
                image = io.BytesIO()
                Image.new("RGB", (12, 12), color=(24, 112, 166)).save(image, format="PNG")
                photo = request(client, "POST", "/uploads", token, {
                    "image_base64": base64.b64encode(image.getvalue()).decode("ascii")}, 201)
                request(client, "PATCH", "/users/me", token, {
                    "phone": "+70000000000", "avatar_url": photo["photo_url"]})
                start = (dt.datetime.now() + dt.timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
                booking = request(client, "POST", "/bookings", token, {
                    "desk_id": desk["id"], "planed_at": start.isoformat(), "duration_hours": 2}, 201)
                ticket = request(client, "POST", "/tickets", token, {
                    "equipment_id": equipment["id"], "description": "Synthetic restore fixture",
                    "photo_url": photo["photo_url"]}, 201)
                request(client, "POST", f"/tickets/{ticket['id']}/comments", it_token,
                        {"text": "Restore fixture diagnostic"}, 201)
                request(client, "PATCH", f"/admin/tickets/{ticket['id']}/status", it_token,
                        {"status": "in_progress"})
                fixture = {"token": token, "ticket_id": ticket["id"], "booking_id": booking["id"],
                           "photo_url": photo["photo_url"], "desk_id": desk["id"],
                           "next_booking_at": (start + dt.timedelta(days=1)).isoformat()}
                fixture["snapshot"] = snapshot(client, fixture)
                check(len(fixture["snapshot"]["bookings"]) == 1, "source_booking_fixture")
                check(len(fixture["snapshot"]["events"]) >= 3, "source_ticket_events_fixture")
                check(fixture["snapshot"]["notifications"], "source_notification_fixture")
                fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
                fixture_path.chmod(0o600)
            else:
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                actual = snapshot(client, fixture)
                # Compares all saved API fields, timestamps, relations and byte hash.
                for key, expected in fixture["snapshot"].items():
                    check(actual[key] == expected, "restored_" + key)
                # A new write also proves pg_dump restored identity sequences.
                new_booking = request(client, "POST", "/bookings", fixture["token"], {
                    "desk_id": fixture["desk_id"], "planed_at": fixture["next_booking_at"],
                    "duration_hours": 1}, 201)
                check(new_booking["id"] > fixture["booking_id"], "restored_identity_sequence")
                check(engine.pool.checkedout() == 0, "restored_write_connections_returned")
                with engine.connect() as connection:
                    tables = inspect(connection).get_table_names()
                    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                result = {"application_tables": len(set(tables) - {"alembic_version"}),
                          "alembic_revision": revision, "api_snapshot_checks": len(actual),
                          "saved_auth_session": True, "attachment_bytes_equal": True,
                          "attachment_auth_required": True, "identity_sequence": True,
                          "connections_returned": True}
                (working / "restored_checks.json").write_text(json.dumps(result), encoding="utf-8")
    finally:
        engine.dispose()


def run_isolated() -> int:
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from scripts.database import binary, connection_environment

    load_dotenv(ROOT / ".env")
    started = time.monotonic()
    report = {"status": "failed", "database_cleanup": False, "private_files_removed": False}
    task_id = uuid.uuid4().hex
    names = ["odg_backup_src_" + task_id, "odg_backup_dst_" + task_id]
    working = ARTIFACTS / ("backup_restore_" + task_id)
    configured = make_url(os.getenv("DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/office_desk_gear"))
    check(configured.get_backend_name() == "postgresql", "postgresql_required")
    # Discard any inherited search_path before connecting or launching pg tools.
    base_url = configured.set(drivername="postgresql+psycopg").update_query_dict({
        "options": "-csearch_path=public -cstatement_timeout=15000 -clock_timeout=5000"})
    admin = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT",
                          connect_args={"connect_timeout": 5})
    created = []
    phase = "prepare"
    try:
        working.mkdir(parents=True, exist_ok=False)
        signing_secret = secrets.token_urlsafe(48)
        environments = []
        for name, folder in zip(names, ("source_data", "restored_data")):
            check(DATABASE_NAME.fullmatch(name), "generated_database_name")
            with admin.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
            created.append(name)
            environment = dict(os.environ, DATABASE_URL=base_url.set(database=name).render_as_string(hide_password=False),
                               PGOPTIONS="-csearch_path=public -cstatement_timeout=15000 -clock_timeout=5000",
                               PGCONNECT_TIMEOUT="5",
                               ODG_BACKUP_DATABASE=name, ODG_BACKUP_WORK=str(working.resolve()), ODG_SEED="0",
                               APP_ENV="test", ENVIRONMENT="test", JWT_SECRET=signing_secret,
                               REQUIRE_EMAIL_VERIFICATION="0", MAIL_MODE="outbox", SQL_ECHO="0",
                               DATA_DIR=str((working / folder).resolve()), PYTHONIOENCODING="utf-8")
            environments.append(environment)

        phase = "source_api_fixture"
        run_child([sys.executable, str(Path(__file__).resolve()), "--source"], environments[0], phase)
        phase = "existing_backup_cli"
        run_child([sys.executable, str(ROOT / "scripts" / "database.py"), "backup", "--output", str(working / "backups")],
                  environments[0], phase)
        dumps = list((working / "backups").glob("*.dump"))
        check(len(dumps) == 1 and dumps[0].stat().st_size > 0, "single_nonempty_database_dump")
        phase = "uploads_archive"
        uploads = working / "source_data" / "uploads"
        check(uploads.is_dir(), "source_uploads_directory")
        with tarfile.open(working / "uploads.tar.gz", "w:gz") as archive:
            archive.add(uploads, arcname="uploads")
        restored_data = working / "restored_data"
        restored_data.mkdir()
        with tarfile.open(working / "uploads.tar.gz", "r:gz") as archive:
            members = archive.getmembers()
            check(1 < len(members) <= 10, "bounded_uploads_archive")
            check(all(not member.issym() and not member.islnk() and member.size <= 5_000_000
                      and (member.name == "uploads" or member.name.startswith("uploads/"))
                      for member in members), "safe_uploads_archive")
            archive.extractall(restored_data, filter="data")
        phase = "pg_restore"
        restore_environment = connection_environment(base_url.set(database=names[1]))
        restore_environment["PGOPTIONS"] = environments[1]["PGOPTIONS"]
        restore_environment["PGCONNECT_TIMEOUT"] = "5"
        run_child([binary("pg_restore"), "--exit-on-error", "--no-owner", "--no-acl",
                   "--dbname", names[1], str(dumps[0].resolve())], restore_environment, phase)
        phase = "restored_api_reads"
        run_child([sys.executable, str(Path(__file__).resolve()), "--restored"], environments[1], phase)
        report.update(json.loads((working / "restored_checks.json").read_text(encoding="utf-8")))
        report.update(status="passed", database_dump_bytes=dumps[0].stat().st_size,
                      uploads_archive_bytes=(working / "uploads.tar.gz").stat().st_size)
    except Exception as exc:
        report.update(failed_phase=phase, error_type=type(exc).__name__)
        if isinstance(exc, CheckFailure):
            report["failed_check"] = str(exc)
    finally:
        cleanup_failed = False
        for name in reversed(created):
            try:
                check(DATABASE_NAME.fullmatch(name) and name in names, "cleanup_database_name")
                with admin.connect() as connection:
                    connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
            except Exception:
                cleanup_failed = True
        try:
            with admin.connect() as connection:
                remaining = connection.execute(text("SELECT datname FROM pg_database WHERE datname IN (:a,:b)"),
                                               {"a": names[0], "b": names[1]}).all()
            report["database_cleanup"] = not remaining and not cleanup_failed
        except Exception:
            report["database_cleanup"] = False
        admin.dispose()
        try:
            # Windows safety: verify the absolute recursive deletion target.
            resolved = working.resolve()
            check(resolved.parent == ARTIFACTS.resolve() and WORK_DIRECTORY.fullmatch(resolved.name),
                  "cleanup_directory")
            if resolved.exists():
                shutil.rmtree(resolved)
            report["private_files_removed"] = not resolved.exists()
        except Exception:
            report["private_files_removed"] = False
        if not report["database_cleanup"] or not report["private_files_removed"]:
            report["status"] = "failed"
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "backup_restore_latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in {"--source", "--restored"}:
        try:
            worker("source" if sys.argv[1] == "--source" else "restored")
        except Exception as failure:
            # Keep PII, SQL, DSNs, tokens and child tracebacks out of test output.
            print(json.dumps({"status": "failed", "error_type": type(failure).__name__,
                              "check": str(failure) if isinstance(failure, CheckFailure) else "worker_exception"}))
            raise SystemExit(1)
    else:
        raise SystemExit(run_isolated())
