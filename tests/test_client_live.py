"""Real Kivy window + HTTP + PostgreSQL + two separate app processes.

Run python tests/test_client_live.py. This opens brief desktop test windows;
Android camera, gallery and Keystore still require a physical-device check.
All database changes use a disposable schema, never application rows.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def launcher():
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    import requests
    load_dotenv(ROOT / ".env")
    schema = "client_ui_" + uuid.uuid4().hex
    artifacts = ROOT / "tests" / "artifacts" / schema
    artifacts.mkdir(parents=True)
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
    url = f"http://127.0.0.1:{port}/api/v1"
    database_url = make_url(os.environ.get("DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5432/office_desk_gear"))
    control = create_engine(database_url.set(drivername="postgresql+psycopg"))
    environment = dict(os.environ, PGOPTIONS=f"-csearch_path={schema}", DATA_DIR=str(artifacts / "server"),
                       DSH_API_URL=url, ODG_CLIENT_DATA=str(artifacts / "device"), KIVY_HOME=str(ROOT / "tests" / "_kivy_home"),
                       ENVIRONMENT="test", APP_ENV="test", JWT_SECRET=secrets.token_urlsafe(48),
                       REQUIRE_EMAIL_VERIFICATION="1", MAIL_MODE="outbox", ODG_SEED="0", PYTHONIOENCODING="utf-8",
                       ODG_UI_ARTIFACTS=str(artifacts), ODG_UI_PORT=str(port))
    environment["KIVY_NO_ARGS"] = "1"
    server = None
    try:
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with (artifacts / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([sys.executable, __file__, "--server"], cwd=ROOT, env=environment, stdout=log, stderr=log)
            ready = False
            for _ in range(120):
                if server.poll() is not None:
                    raise RuntimeError("UI test server failed; see " + str(artifacts / "server.log"))
                try:
                    response = requests.get(url + "/system/info", timeout=0.3)
                    ready = response.status_code == 200
                    response.close()
                    if ready:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.1)
            if not ready:
                raise RuntimeError("UI test server did not become ready")
            for phase in ("--first", "--restore"):
                with (artifacts / (phase[2:] + ".log")).open("w", encoding="utf-8") as gui_log:
                    result = subprocess.run([sys.executable, __file__, phase], cwd=ROOT, env=environment,
                                            stdout=gui_log, stderr=gui_log, timeout=90)
                if result.returncode:
                    raise RuntimeError("UI phase failed: " + phase + "; see " + str(artifacts / (phase[2:] + ".log")))
        print("PASS: real HTTP mobile UI flows, all pages, private media, persistent login across processes, logout/cache erasure")
        print("UI screenshots and logs: " + str(artifacts))
        return 0
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        if re.fullmatch(r"client_ui_[0-9a-f]{32}", schema) is None:
            raise RuntimeError("Unsafe test schema")
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        control.dispose()


def server_main():
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    from server.database.base import seed_db
    seed_db()
    import uvicorn
    uvicorn.run("server.main:app", host="127.0.0.1", port=int(os.environ["ODG_UI_PORT"]), log_level="warning")


def gui_main(restored=False):
    from kivy.clock import Clock
    from kivymd.uix.button import MDButton
    from client.app import OfficeDeskGearApp
    from client.core.storage import LocalState
    from PIL import Image

    directory = Path(os.environ["ODG_UI_ARTIFACTS"])
    app = OfficeDeskGearApp()
    stages, data, errors = [], {}, []
    screenshot_counter = [0]

    def field(title):
        return next(w for w in app.body.walk() if getattr(w, "_title", None) == title)

    def fields(mapping, values=None):
        result = {key: field(title) for key, title in mapping.items()}
        for key, value in (values or {}).items():
            result[key].text = str(value)
        return result

    def snap(name):
        screenshot_counter[0] += 1
        app.root.export_to_png(str(directory / (name + ".png")))

    def choose_menu_option(title, text):
        widget = field(title)
        option = next(item for item in widget._menu.items if item["text"] == text)
        option["on_release"]()
        assert widget.text == text

    def press_button(title):
        button = next(widget for widget in app.body.walk()
                      if isinstance(widget, MDButton) and any(getattr(child, "text", None) == title for child in widget.children))
        button.dispatch("on_release")

    def expect_page(page):
        assert app.page == page, f"Expected {page}, got {app.page}: {app.status.text}"
        if app.status.text_color == list(__import__("client.registration", fromlist=["ERROR"]).ERROR):
            raise AssertionError("UI error: " + app.status.text)

    def stage(name, action, check=None):
        stages.append((name, action, check))

    def code_for(email, purpose):
        letters = [json.loads(path.read_text(encoding="utf-8")) for path in
                   sorted((directory / "server" / "outbox").glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)]
        return next(letter["code"] for letter in letters if letter["email"] == email and purpose in letter["purpose"])

    if restored:
        def verify_restored():
            expect_page("home")
            assert app.user["email"] == "it@company.ru"
            assert app.state.cache_get("home")
            assert app.api.token
            snap("restored-home")
        stage("restore persisted signed token and cached profile", verify_restored)
        stage("logout through API", app.logout)

        def verify_logout():
            expect_page("login")
            state = LocalState(os.environ["ODG_CLIENT_DATA"])
            assert state.account() == (None, None)
            assert not state.data.get("cache")
            assert not list((state.directory / "media").glob("*"))
            snap("logged-out")
        stage("logout clears disk and UI", verify_logout)
    else:
        stage("initial layout", lambda: None)
        stage("login initial", lambda: (expect_page("login"), snap("login")))
        stage("registration view", app.register)

        def register():
            values = {"full_name": "UI Smoke Employee", "job_title": "Специалист", "email": "ui-smoke@company.ru",
                      "password": "Smoke-password-2026", "confirm_password": "Smoke-password-2026"}
            fs = fields({"full_name": "ФИО", "job_title": "Должность", "email": "Рабочая почта",
                         "password": "Пароль от 6 символов", "confirm_password": "Повторите пароль"}, values)
            snap("registration")
            app._register(fs)
        stage("register through real API", register, lambda: expect_page("verify"))
        stage("return to login before activation", app.login)

        def unverified_login():
            fs = fields({"email": "Рабочая почта", "password": "Пароль"}, {"email": "ui-smoke@company.ru", "password": "Smoke-password-2026"})
            app._login(fs)
        stage("unverified login returns to activation", unverified_login, lambda: expect_page("verify"))

        def verify_registration():
            field("Код подтверждения").text = code_for("ui-smoke@company.ru", "Подтверждение")
            app._verify("ui-smoke@company.ru", field("Код подтверждения"))
        stage("activate using delivered email code", verify_registration, lambda: expect_page("login"))
        stage("reset password form", app.reset)

        def reset_request():
            field("Рабочая почта").text = "ui-smoke@company.ru"
            app._reset_request(field("Рабочая почта"))
        stage("send password reset code", reset_request)

        def reset_confirm():
            fs = fields({"email": "Рабочая почта", "code": "Код из письма", "new_password": "Новый пароль от 6 символов", "confirm": "Повторите новый пароль"},
                        {"email": "ui-smoke@company.ru", "code": code_for("ui-smoke@company.ru", "Восстановление"),
                         "new_password": "New-smoke-password-2026", "confirm": "New-smoke-password-2026"})
            app._reset_confirm(fs)
        stage("reset password through UI", reset_confirm, lambda: expect_page("login"))

        def employee_login():
            fs = fields({"email": "Рабочая почта", "password": "Пароль"}, {"email": "ui-smoke@company.ru", "password": "New-smoke-password-2026"})
            app._login(fs)
        stage("employee login after reset", employee_login, lambda: expect_page("home"))
        stage("employee profile", app.profile)

        def employee_email_change():
            fs = fields({"full_name": "ФИО", "email": "Рабочая почта", "job_title": "Должность", "phone": "Телефон"},
                        {"email": "ui-updated@company.ru"})
            app._profile_save(fs, field("Фото профиля"))
        stage("email change requires new activation and removes cached login", employee_email_change,
              lambda: (expect_page("verify"), app.user is None or (_ for _ in ()).throw(AssertionError("Unverified account remains active"))))

        def verify_changed_email():
            field("Код подтверждения").text = code_for("ui-updated@company.ru", "Подтверждение")
            app._verify("ui-updated@company.ru", field("Код подтверждения"))
        stage("activate changed address", verify_changed_email, lambda: expect_page("login"))

        def login():
            fs = fields({"email": "Рабочая почта", "password": "Пароль"}, {"email": "it@company.ru", "password": "password123"})
            app._login(fs)
        stage("login through API", login, lambda: expect_page("home"))
        stage("home view", lambda: snap("home"))
        stage("profile view", app.profile)

        def profile_save():
            photo = directory / "fixture.png"
            Image.new("RGB", (120, 120), "#8BADFF").save(photo)
            fs = fields({"full_name": "ФИО", "email": "Рабочая почта", "job_title": "Должность", "phone": "Телефон"},
                        {"job_title": "IT специалист", "phone": "+7 999 111 22 33"})
            field("Фото профиля").text = str(photo)
            app._profile_save(fs, field("Фото профиля"))
        stage("update profile and upload private image", profile_save,
              lambda: (app.user["phone"] == "+7 999 111 22 33" or (_ for _ in ()).throw(AssertionError(app.status.text))))
        stage("desk list", app.desks)
        stage("desk list screenshot", lambda: snap("desks"))
        stage("desk map", app._toggle_map)
        stage("desk map screenshot", lambda: snap("desk-map"))
        stage("desk filter form", app.desk_filters)
        stage("desk filters screenshot", lambda: snap("desk-filters"))
        stage("create desk form", app.desk_form)

        def desk_save():
            fs = fields({"number": "Номер стола", "floor": "Этаж", "section": "Секция", "sockets": "Число розеток"},
                        {"number": 701, "floor": 7, "section": 1, "sockets": 3})
            feature = app.last_feature_picker
            feature.selected = {1, 2}
            app._desk_save({}, fs, field("Доступность"), feature)
        stage("create desk via mobile form", desk_save, lambda: expect_page("desk"))

        def lookup_desk():
            data["desk"] = app.api.get_desks(search="701")[0]
            app.booking_form(data["desk"])
        stage("booking form", lookup_desk)

        def create_booking():
            start = (dt.date.today() + dt.timedelta(days=2)).isoformat() + " 09:00"
            fs = fields({"start": "Дата и время начала", "duration": "Часов в день · 1–24"}, {"start": start, "duration": 8})
            app._booking_save(data["desk"], fs, False, None)
        stage("create booking through UI", create_booking, lambda: expect_page("bookings"))

        def booking_detail():
            data["booking"] = app.api.get_booking_history()[0]
            app.booking_detail(data["booking"])
        stage("booking detail", booking_detail)
        stage("bookings screenshot", lambda: snap("booking"))
        stage("booking period form", lambda: app.booking_form(data["desk"], period=True))
        stage("equipment registry", lambda: app.equipment(True))
        stage("equipment creation form", app.equipment_form)

        def equipment_save():
            fs = fields({"type": "Тип оборудования", "model": "Модель", "socket_serial": "Серийный номер",
                         "inventory_number": "Инвентарный номер"},
                        {"type": "Монитор", "model": "UI Test 27", "socket_serial": "UI-SERIAL-001", "inventory_number": "UI-001"})
            app._equipment_save({}, fs, field("Требуется розетка"), field("Статус"))
        stage("create equipment via UI", equipment_save, lambda: expect_page("equipment_detail"))

        def equipment_detail():
            data["equipment"] = next(v for v in app.api.admin_list_equipment() if v["socket_serial"] == "UI-SERIAL-001")
            app.equipment_detail(data["equipment"])
        stage("equipment detail", equipment_detail)
        stage("equipment detail screenshot", lambda: snap("equipment"))
        stage("assignment form", lambda: app.assign_form(data["equipment"]))

        def assignment_persisted(user_id, desk_id, status):
            import requests

            expect_page("equipment_detail")
            # Read through a fresh HTTP request instead of trusting the UI's
            # selected values or the PATCH response that populated the card.
            with requests.get(app.api.base_url + f"/equipment/{data['equipment']['id']}",
                              headers={"Authorization": "Bearer " + app.api.token}, timeout=5) as response:
                response.raise_for_status()
                item = response.json()
            assert item["assigned_user_id"] == user_id, item
            assert item["desk_id"] == desk_id, item
            assert item["status"] == status, item
            displayed_text = "\n".join(str(getattr(widget, "text", "")) for widget in app.body.walk())
            expected_desk = f"№{data['desk']['number']} · этаж {data['desk']['floor']} · секция {data['desk']['section']}" if desk_id is not None else "не закреплён"
            expected_user = data["assigned_employee"]["full_name"] if user_id is not None else "не закреплён"
            assert "Стол: " + expected_desk in displayed_text, displayed_text
            assert "Сотрудник: " + expected_user in displayed_text, displayed_text
            data["equipment"] = item

        def select_and_save_assignment():
            employee = next(user for user in app.api.admin_list_users() if user["email"] == "ui-updated@company.ru")
            data["assigned_employee"] = employee
            choose_menu_option("Сотрудник", f"{employee['full_name']} · {employee['email']}")
            choose_menu_option("Рабочее место", f"Стол {data['desk']['number']} · этаж {data['desk']['floor']}")
            press_button("Сохранить назначение")
        stage("save employee and desk selected in assignment menus", select_and_save_assignment,
              lambda: assignment_persisted(data["assigned_employee"]["id"], data["desk"]["id"], "assigned"))
        stage("assignment confirmation screenshot", lambda: snap("equipment-assigned"))
        stage("reopen assignment to remove employee", lambda: app.assign_form(data["equipment"]))

        def remove_employee_assignment():
            choose_menu_option("Сотрудник", "Снять с сотрудника")
            press_button("Сохранить назначение")
        stage("remove employee through assignment menu while preserving desk", remove_employee_assignment,
              lambda: assignment_persisted(None, data["desk"]["id"], "assigned"))
        stage("reopen assignment to remove desk", lambda: app.assign_form(data["equipment"]))

        def remove_desk_assignment():
            choose_menu_option("Рабочее место", "Без закрепления за столом")
            press_button("Сохранить назначение")
        stage("remove desk through assignment menu and return equipment to stock", remove_desk_assignment,
              lambda: assignment_persisted(None, None, "in_stock"))
        stage("QR display", lambda: app.qr_code(data["equipment"]))
        stage("QR screenshot", lambda: snap("qr"))
        stage("new ticket", lambda: app.ticket_form(data["equipment"]))

        def ticket_save():
            field("Опишите проблему").text = "Экран мигает после подключения"
            field("Фотография").text = str(directory / "fixture.png")
            app._ticket_save(data["equipment"], field("Тип неисправности"), field("Опишите проблему"), field("Фотография"))
        stage("ticket with photo through UI", ticket_save, lambda: expect_page("ticket_detail"))
        stage("ticket screenshot", lambda: snap("ticket"))
        stage("own tickets", app.tickets)
        stage("admin tickets", lambda: app.tickets(True))
        stage("notifications", app.notifications)
        stage("analytics", app.analytics)
        stage("analytics screenshot", lambda: snap("analytics"))
        stage("report create", lambda: app._save_report(fields({"start_date": "Начало · ГГГГ-ММ-ДД", "end_date": "Конец · ГГГГ-ММ-ДД"})))
        stage("reports archive", app.reports)
        stage("role management", app.users)
        stage("settings", app.settings)
        stage("admin navigation", app.admin)
        stage("admin screenshot", lambda: snap("admin"))
        stage("home before shutdown", app.home)

        def offline():
            import requests
            data["transport"] = app.api._transport
            def fail(*_a, **_k):
                raise requests.ConnectionError("Injected offline state")
            app.api._transport = fail
            app.home()
        stage("offline home retains account and reads disk cache", offline,
              lambda: (app.user is not None and "кэш" in app.status.text.lower()) or (_ for _ in ()).throw(AssertionError(app.status.text)))
        stage("offline screenshot", lambda: snap("offline-home"))

        def online():
            app.api._transport = data["transport"]
            app.home()
        stage("connection recovery", online)

        def busy_navigation():
            page = app.page
            button = app.button("Столы", app.desks)
            app.busy = True
            button.dispatch("on_release")
            app.busy = False
            assert app.page == page
            app.note("")
        stage("navigation does not abandon a pending load", busy_navigation)

        def persisted():
            state = LocalState(os.environ["ODG_CLIENT_DATA"])
            token, user = state.account()
            assert token and user["email"] == "it@company.ru"
            assert state.cache_get("home")
            assert token not in state.path.read_text(encoding="utf-8") if os.name == "nt" else True
        stage("persisted credential and cache", persisted)

    index, waiting, deadline = [0], [False], [time.monotonic() + 70]

    def step(_dt):
        if time.monotonic() > deadline[0]:
            errors.append("UI scenario timed out at stage " + str(index[0]))
            app.stop()
            return False
        if app.busy:
            return True
        try:
            if waiting[0]:
                name, _action, check = stages[index[0]]
                if check:
                    check()
                print("PASS UI: " + name, flush=True)
                index[0] += 1
                waiting[0] = False
            if index[0] >= len(stages):
                app.stop()
                return False
            _name, action, _check = stages[index[0]]
            action()
            waiting[0] = True
        except Exception as error:
            import traceback
            traceback.print_exc()
            errors.append(str(error))
            snap("failure")
            app.stop()
            return False
        return True

    Clock.schedule_interval(step, 0.25)
    app.run()
    if errors:
        raise AssertionError("; ".join(errors))
    (directory / ("restore-results.json" if restored else "first-results.json")).write_text(
        json.dumps({"passed": len(stages), "screenshots": screenshot_counter[0]}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    if "--server" in sys.argv:
        server_main()
    elif "--first" in sys.argv:
        sys.exit(gui_main())
    elif "--restore" in sys.argv:
        sys.exit(gui_main(restored=True))
    else:
        sys.exit(launcher())
