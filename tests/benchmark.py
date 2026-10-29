"""Local PostgreSQL microbenchmark; deliberately excluded from unittest discovery.

Run ``python tests/benchmark.py``. This measures FastAPI's HTTP application with
TestClient and a real PostgreSQL, not network/TLS latency or production capacity.
Only a uniquely created benchmark_<UUID> schema is changed and then removed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "tests" / "artifacts" / "benchmark_latest.json"
PREFIX = "/api/v1"
WORKERS = 10
READS_PER_WORKER = 100
WRITES_PER_WORKER = 4
PASSWORD = "Local-benchmark-password-2026"
TARGETS = {"desk_search": 1.0, "booking_history": 1.0, "booking_create": 2.0, "ticket_create": 2.0, "analytics_30d": 3.0}


def launch() -> int:
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    load_dotenv(ROOT / ".env")
    schema = "benchmark_" + uuid.uuid4().hex
    database_url = make_url(os.getenv("DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/office_desk_gear"))
    if database_url.get_backend_name() != "postgresql":
        raise RuntimeError("This benchmark requires PostgreSQL")
    control = create_engine(database_url.set(drivername="postgresql+psycopg"), connect_args={"connect_timeout": 5})
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        data_dir = ROOT / "tests" / "artifacts" / schema
        data_dir.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ, PGOPTIONS=f"-csearch_path={schema}", ODG_BENCHMARK_SCHEMA=schema,
                           ODG_SEED="0", APP_ENV="test", ENVIRONMENT="test", REQUIRE_EMAIL_VERIFICATION="0", MAIL_MODE="outbox",
                           JWT_SECRET=secrets.token_urlsafe(48), DATA_DIR=str(data_dir), PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"], cwd=ROOT, env=environment).returncode
    finally:
        if created:
            if re.fullmatch(r"benchmark_[0-9a-f]{32}", schema) is None:
                raise RuntimeError("Unsafe benchmark schema name")
            with control.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            print("Removed isolated benchmark schema", flush=True)
        control.dispose()


def seed_database():
    from server.core.security import hash_password
    from server.core.time import office_now
    from server.database.session import SessionLocal
    from server.models.users import User
    from server.models.desks import Desk
    from server.models.desk_features import DeskFeature
    from server.models.equipment import Equipment
    from server.models.bookings import Booking
    from server.models.tickets import Ticket
    from server.models.activity import TicketEvent

    now = office_now().replace(microsecond=0)
    shared_hash = hash_password(PASSWORD)
    with SessionLocal.begin() as session:
        users = [User(full_name=f"Benchmark employee {index}", email=f"bench-{index}@company.ru", password_hash=shared_hash,
                      role="it_admin" if index == 98 else "office_manager" if index == 99 else "employee", is_verified=True)
                 for index in range(100)]
        session.add_all(users)
        session.flush()
        features = [DeskFeature(name="Monitor", description="External monitor"), DeskFeature(name="Type C", description="Dock station")]
        session.add_all(features)
        session.flush()
        desks = [Desk(floor=1 + index // 25, section=1 + index % 3, number=1001 + index,
                      sockets=2 + index % 3, is_free=True, feature_records=features[:1 + index % 2]) for index in range(50)]
        session.add_all(desks)
        session.flush()
        equipment = [Equipment(type=("Monitor", "Laptop", "Dock", "Keyboard")[index % 4], model=f"Model {index % 12}",
                               socket_serial=f"BENCH-SN-{index}", inventory_number=f"BENCH-INV-{index}",
                               assigned_user_id=users[index % 98].id, status="assigned", socket_needed=index % 2 == 0)
                     for index in range(200)]
        session.add_all(equipment)
        session.flush()
        bookings = []
        for day_index in range(30):
            start = (now - dt.timedelta(days=30 - day_index)).replace(hour=9, minute=0, second=0)
            for desk_index, desk in enumerate(desks):
                # At most one desk per user and one reservation per desk/day.
                bookings.append(Booking(user_id=users[(desk_index + day_index * 11) % 98].id, desk_id=desk.id,
                                        planed_at=start, duration=dt.timedelta(hours=6), created_at=start - dt.timedelta(days=1), is_cancelled=False))
        session.add_all(bookings)
        tickets = [Ticket(author_id=item.assigned_user_id, equipment_id=item.id, description=f"Benchmark issue {index}",
                          status="new", created_at=now - dt.timedelta(days=1 + index % 29)) for index, item in enumerate(equipment)]
        session.add_all(tickets)
        session.flush()
        session.add_all([TicketEvent(ticket_id=ticket.id, author_id=ticket.author_id, kind="status", text="new", created_at=ticket.created_at) for ticket in tickets])
        return {"desks": [item.id for item in desks], "equipment": [item.id for item in equipment], "now": now}


def summarize(values):
    ordered = sorted(values)
    def percentile(fraction):
        return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] * 1000, 3) if ordered else None
    return {"count": len(values), "p50_ms": percentile(.50), "p95_ms": percentile(.95), "max_ms": round(max(values) * 1000, 3) if values else None}


def worker() -> int:
    if re.fullmatch(r"benchmark_[0-9a-f]{32}", os.getenv("ODG_BENCHMARK_SCHEMA", "")) is None:
        raise RuntimeError("Use the isolated launcher")
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from server.database.session import engine

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    seed = seed_database()
    from server.main import app
    timings = {name: [] for name in TARGETS}
    errors = []
    started = time.perf_counter()
    try:
        with TestClient(app, raise_server_exceptions=False) as bootstrap:
            tokens = []
            for index in list(range(WORKERS)) + [99]:
                response = bootstrap.post(PREFIX + "/auth/login", json={"email": f"bench-{index}@company.ru", "password": PASSWORD})
                if response.status_code != 200:
                    raise RuntimeError(f"Benchmark login returned HTTP {response.status_code}")
                tokens.append(response.json()["access_token"])
            # Warm pool and query compilation before collecting timings.
            headers = {"Authorization": "Bearer " + tokens[0]}
            for path in ("/desks?limit=20", "/bookings/history"):
                if bootstrap.get(PREFIX + path, headers=headers).status_code != 200:
                    raise RuntimeError("Benchmark warmup failed")

            def exercise(index):
                local_timings = {name: [] for name in TARGETS}
                local_errors = []
                client = TestClient(app, raise_server_exceptions=False)
                auth = {"Authorization": "Bearer " + tokens[index]}
                manager_auth = {"Authorization": "Bearer " + tokens[-1]}
                def measured(name, method, path, expected, payload=None, use_manager=False):
                    before = time.perf_counter()
                    try:
                        response = client.request(method, PREFIX + path, headers=manager_auth if use_manager else auth, json=payload)
                        local_timings[name].append(time.perf_counter() - before)
                        if response.status_code != expected:
                            local_errors.append({"operation": name, "status": response.status_code})
                    except Exception as error:
                        local_timings[name].append(time.perf_counter() - before)
                        local_errors.append({"operation": name, "exception": type(error).__name__})
                try:
                    for turn in range(READS_PER_WORKER):
                        measured("desk_search", "GET", f"/desks?floor={1 + turn % 2}&search=10&sort=sockets&order=desc&limit=20&offset=0", 200)
                        measured("booking_history", "GET", "/bookings/history", 200)
                    for turn in range(WRITES_PER_WORKER):
                        date = (seed["now"] + dt.timedelta(days=45 + turn)).replace(hour=9, minute=0, second=0)
                        measured("booking_create", "POST", "/bookings", 201,
                                 {"desk_id": seed["desks"][index], "planed_at": date.isoformat(), "duration_hours": 2})
                        measured("ticket_create", "POST", "/tickets", 201,
                                 {"equipment_id": seed["equipment"][index], "description": f"Load test incident {turn}"})
                    start_date = (seed["now"].date() - dt.timedelta(days=29)).isoformat()
                    end_date = seed["now"].date().isoformat()
                    for _ in range(3):
                        measured("analytics_30d", "GET", f"/admin/analytics?start_date={start_date}&end_date={end_date}", 200, use_manager=True)
                finally:
                    client.close()
                return local_timings, local_errors

            with ThreadPoolExecutor(max_workers=WORKERS) as executor:
                for local_timings, local_errors in executor.map(exercise, range(WORKERS)):
                    for name, values in local_timings.items():
                        timings[name].extend(values)
                    errors.extend(local_errors)
            pool_checked_out = engine.pool.checkedout()
            with engine.connect() as connection:
                postgres_version = connection.execute(text("SHOW server_version")).scalar_one()
        metrics = {name: summarize(values) for name, values in timings.items()}
        failures = [name for name, metric in metrics.items() if metric["p95_ms"] is None or metric["p95_ms"] > TARGETS[name] * 1000]
        if errors:
            failures.append("http_errors")
        if pool_checked_out:
            failures.append("connection_leak")
        report = {
            "measured_at_utc": dt.datetime.now(dt.UTC).isoformat(),
            "kind": "local PostgreSQL and FastAPI TestClient microbenchmark; excludes external network/TLS and phone rendering",
            "environment": {"os": platform.platform(), "python": platform.python_version(), "cpu": platform.processor(), "logical_cpus": os.cpu_count(), "postgresql": postgres_version},
            "seed_counts": {"users": 100, "desks": 50, "equipment": 200, "bookings": 1500, "tickets": 200},
            "concurrent_clients": WORKERS,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "metrics": metrics,
            "p95_targets_ms": {name: value * 1000 for name, value in TARGETS.items()},
            "errors": errors, "connections_checked_out_after_requests": pool_checked_out,
            "passed": not failures, "failed_targets": failures,
        }
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 1 if failures else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(worker() if "--worker" in sys.argv else launch())
