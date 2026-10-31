"""Back up PostgreSQL or adopt a checked legacy schema without losing rows."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def binary(name: str) -> str:
    found = shutil.which(name)
    windows_path = Path(r"C:\Program Files\PostgreSQL\18\bin") / (name + ".exe")
    if found:
        return found
    if windows_path.is_file():
        return str(windows_path)
    raise RuntimeError(f"{name} is required; add PostgreSQL bin to PATH")


def connection_environment(url):
    env = os.environ.copy()
    env.update(PGHOST=url.host or "127.0.0.1", PGPORT=str(url.port or 5432),
               PGUSER=url.username or "postgres", PGDATABASE=url.database or "office_desk_gear")
    if url.password:
        env["PGPASSWORD"] = url.password
    return env


def backup(output: Path):
    from datetime import datetime
    from server.core.config import settings
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"office_desk_gear_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.dump"
    subprocess.run([binary("pg_dump"), "--format=custom", "--no-owner", "--no-acl", "--file", str(path)],
                   env=connection_environment(settings.DATABASE_URL), check=True)
    print(f"Backup saved: {path}")
    return path


def adopt_legacy():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from server.database.session import engine
    expected = {"users", "desks", "desk_features", "bookings", "equipment", "tickets", "reports", "auth_sessions", "password_reset_codes"}
    with engine.connect() as connection:
        inspector = inspect(connection)
        actual = set(inspector.get_table_names())
        if actual != expected:
            raise RuntimeError("Legacy adoption expects exactly the nine original tables and no Alembic version; no changes made")
        checks = {"users": {"id", "email", "password_hash", "role", "current_bookings"},
                  "desks": {"id", "features", "floor", "number"},
                  "bookings": {"id", "user_id", "desk_id", "duration", "is_cancelled"}}
        for table, required in checks.items():
            if not required.issubset({c["name"] for c in inspector.get_columns(table)}):
                raise RuntimeError(f"Unexpected legacy schema in {table}; no changes made")
    backup(ROOT / "db_backup")
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(748120936)")
        config.attributes["connection"] = connection
        command.stamp(config, "0001")
        command.upgrade(config, "head")
    print("Legacy database migrated to head; original rows retained")


def verify_restore(dump: Path):
    from sqlalchemy import create_engine, text
    from server.core.config import settings
    name = "odg_restore_" + uuid.uuid4().hex
    assert re.fullmatch(r"odg_restore_[0-9a-f]{32}", name)
    admin = create_engine(settings.DATABASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    restored = None
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        created = True
        env = connection_environment(settings.DATABASE_URL.set(database=name))
        subprocess.run([binary("pg_restore"), "--exit-on-error", "--no-owner", "--no-acl", "--dbname", name, str(dump.resolve())], env=env, check=True)
        restored = create_engine(settings.DATABASE_URL.set(database=name))
        with restored.connect() as connection:
            tables = connection.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")).scalar_one()
            if tables < 9:
                raise RuntimeError("Restore is missing application tables")
            for child, key, parent in (("bookings", "user_id", "users"), ("bookings", "desk_id", "desks"), ("tickets", "equipment_id", "equipment")):
                count = connection.execute(text(f"SELECT count(*) FROM {child} c LEFT JOIN {parent} p ON p.id=c.{key} WHERE p.id IS NULL")).scalar_one()
                if count:
                    raise RuntimeError("Orphaned rows after restore")
        print(f"Restore verified: {tables} tables; booking/ticket foreign keys intact")
    finally:
        if restored:
            restored.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--output", type=Path, default=ROOT / "db_backup")
    sub.add_parser("adopt-legacy")
    r = sub.add_parser("verify-restore")
    r.add_argument("dump", type=Path)
    args = parser.parse_args()
    if args.command == "backup":
        backup(args.output)
    elif args.command == "adopt-legacy":
        adopt_legacy()
    else:
        verify_restore(args.dump)


if __name__ == "__main__":
    main()
