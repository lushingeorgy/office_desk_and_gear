"""Offline navigation regression checks in a real Kivy window.

Run: .venv/Scripts/python.exe tests/client_navigation_runtime.py
No backend, database or account is contacted. HTTP completions are controlled
to reproduce rapid tab changes, late responses and offline cached lists.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_HOME", str(ROOT / "tests" / "_kivy_home"))
ARTIFACTS = ROOT / "tests" / "artifacts" / "navigation"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

from kivy.clock import Clock
from kivy.core.window import Window
from client.app import OfficeDeskGearApp
from client.services.api_client import ApiError

USER = {"id": 42, "full_name": "Анна Смирнова", "email": "anna@example.org", "role": "employee",
        "job_title": "Дизайнер", "phone": "", "avatar_url": None}
BOOKINGS = [{"id": 1, "desk_id": 7, "desk_number": 12, "desk_floor": 2, "desk_section": 1,
             "planed_at": "2026-11-02T09:00:00", "duration": "08:00:00", "is_cancelled": False}]


class DeferredWorker:
    def __init__(self):
        self.pending = []

    def submit(self, function, success, failure):
        self.pending.append((success, failure))
        assert len(self.pending) == 1, "Navigation filled the network queue"
        return True

    def complete(self, value):
        self.pending.pop(0)[0](value)

    def fail(self, error):
        self.pending.pop(0)[1](error)

    def stop(self):
        self.pending.clear()


class NavigationCheck(OfficeDeskGearApp):
    def on_start(self):
        self.worker.stop()
        self.worker = self.model.worker = DeferredWorker()
        self.navigation_ids = [id(item) for item in self.navigation.children]
        self.errors = []
        self.steps = []
        self.add_checks()
        super().on_start()
        def first_frame(_dt):
            Clock.schedule_once(lambda _dt: self.root.export_to_png(str(ARTIFACTS / "startup.png")), 0.06)
            Clock.schedule_once(self.step, 0.8)
        Clock.schedule_once(first_frame, 0)

    def check(self, condition, message):
        if not condition:
            raise AssertionError(message)

    def snap(self, name):
        self.root.export_to_png(str(ARTIFACTS / (name + ".png")))

    def add_checks(self):
        def initial():
            self.check(self.page == "login" and not self.startup.parent, "Startup did not finish")
            self.check(self.profile_button.disabled and self.navigation.disabled, "Private navigation before login")
            self.snap("login")
            self.user = dict(USER)
            self.home()
            self.worker.complete({"active": None, "user": dict(USER), "desk": None})
        self.steps.append(("startup and login", initial))

        def home():
            self.snap("home")
            self.check(self.nav_items["home"].active, "Home is not selected")
            labels = [getattr(w, "text", "") for w in self.body.walk()]
            self.check("Мой профиль" not in labels and "Выйти из аккаунта" not in labels, "Profile actions remain on home")
            self.profile_button.dispatch("on_release")
        self.steps.append(("persistent home navigation", home))

        def profile():
            self.check(self.page == "profile", "Header profile button failed")
            self.check(self.profile_button.center_x > self.root.width * 0.8, "Profile button is not on the right")
            self.check(self.profile_button.center_y > self.root.height * 0.8, "Profile button is not at the top")
            self.check(not any(w.active for w in self.nav_items.values()), "Profile selected a bottom tab")
            self.snap("profile")
            field = next(w for w in self.body.walk() if getattr(w, "_title", None) == "ФИО")
            field.focus = True
            self.nav_items["bookings"].dispatch("on_release")
            self.check(not field.focus, "An outgoing field retained the keyboard")
        self.steps.append(("profile button and form", profile))

        def rapid_tabs():
            self.check(self.busy and self.page == "bookings", "Expected a pending bookings request")
            old_body = self.body
            self.nav_items["equipment"].dispatch("on_release")
            self.nav_items["tickets"].dispatch("on_release")
            self.check(self.page == "tickets" and self.busy, "Reads blocked navigation")
            self.check(len(self.worker.pending) == 1 and self._pending_job, "Expected one active request and latest destination")
            self.worker.complete(BOOKINGS)
            self.check(self.page == "tickets" and self.body is not old_body, "Late bookings response replaced tickets")
            self.check(len(self.worker.pending) == 1, "Latest tab did not load after prior response")
            self.worker.complete([])
            self.check(not self.busy and self._pending_job is None, "Request state did not settle")
        self.steps.append(("rapid tabs discard obsolete responses", rapid_tabs))

        def settled():
            self.check(len(self.pages.children) == 1, "Outgoing page was retained after transition")
            self.check([id(w) for w in self.navigation.children] == self.navigation_ids, "Navigation was rebuilt")
            self.check(self.scroll.opacity == 1 and self.scroll.interactive, "Incoming screen is not ready")
            self.nav_items["bookings"].dispatch("on_release")
            self.worker.complete(BOOKINGS)
        self.steps.append(("page cleanup and stable navigation", settled))

        def cached():
            self.snap("bookings")
            self.bookings()
            content = self.body.children[0]
            cached_widgets = content.children[:]
            self.worker.complete(BOOKINGS)
            self.check(content.children == cached_widgets, "Unchanged cache rebuilt the list")
            self.check(content.opacity == 1, "Unchanged list flashed")
            self.bookings()
            self.worker.fail(ApiError("Offline fixture", 503))
            self.check("кэш" in self.status.text and not self.busy, "Offline cache was lost")
        self.steps.append(("unchanged cache and offline fallback", cached))

        def saving():
            self.profile()
            self.mutate(lambda: None, lambda _r: None)
            self.nav_items["home"].dispatch("on_release")
            self.check(self.page == "profile" and self._mutating, "Navigation abandoned a save")
            self.worker.complete(None)
            self.check(not self.busy and not self._mutating, "Save lock did not release")
            self.nav_items["equipment"].dispatch("on_release")
            self.worker.complete([])
        self.steps.append(("saving blocks navigation until confirmation", saving))

        def nested_back():
            self.equipment_detail({"id": 9, "type": "Монитор", "status": "assigned"})
            self.back_button.dispatch("on_release")
            self.check(self.page == "equipment", "Back skipped the parent section")
            self.worker.complete([])
            Window.size = (320, 640)
        self.steps.append(("back returns to parent section", nested_back))

        def narrow():
            self.check(all(item.width >= 44 for item in self.nav_items.values()), "Narrow screen has tiny navigation targets")
            self.check(self.body.width <= self.scroll.width, "Body overflows narrow screen")
            self.check(self.scroll.pos == self.pages.pos, "Page is not inside its content area")
            self.check(self.scroll.y >= self.navigation.top, "Page overlaps bottom navigation")
            self.snap("narrow")
            for _ in range(12):
                self.profile()
                self.home()
            self.check(len(self.pages.children) <= 2, "Rapid transitions accumulate page trees")
            self.worker.complete({"active": None, "user": dict(USER), "desk": None})
            if self.worker.pending:
                self.worker.complete({"active": None, "user": dict(USER), "desk": None})
        self.steps.append(("narrow screen and repeated transitions", narrow))

        def pause_and_logout():
            self.on_pause()
            self.on_resume()
            if self.worker.pending:
                self.worker.complete(dict(USER))
            self.auth_lost("Тест завершения сессии")
            self.check(not self.user and self.page == "login", "Auth loss did not clear session")
            self.check(len(self.pages.children) == 1 and self.pages.current is None, "Private page survived auth loss")
            self.check(self.profile_button.disabled and self.navigation.disabled, "Private controls survived auth loss")
        self.steps.append(("pause, resume and private page cleanup", pause_and_logout))

    def step(self, _dt):
        try:
            if not self.steps:
                print("PASS: all 10 native navigation checks", flush=True)
                self.stop()
                return
            name, action = self.steps.pop(0)
            action()
            print("PASS: " + name, flush=True)
            Clock.schedule_once(self.step, 0.4)
        except Exception:
            self.errors.append(traceback.format_exc())
            print(self.errors[-1], flush=True)
            self.snap("failure")
            self.stop()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="device-", dir=ARTIFACTS) as folder:
        os.environ["ODG_CLIENT_DATA"] = folder
        application = NavigationCheck()
        application.run()
        if application.errors:
            sys.exit(1)
