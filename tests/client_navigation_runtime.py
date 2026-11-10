"""Offline navigation regression checks in a real Kivy window.

Run: .venv/Scripts/python.exe tests/client_navigation_runtime.py
No backend, database or account is contacted. HTTP completions are controlled
to reproduce rapid tab changes, late responses and offline cached lists.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_HOME", str(ROOT / "tests" / "_kivy_home"))
ARTIFACTS = Path(os.getenv("ODG_NAV_ARTIFACTS", str(ROOT / "tests" / "artifacts" / "navigation")))
ARTIFACTS.mkdir(parents=True, exist_ok=True)

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivymd.uix.button import MDButton
from client.app import OfficeDeskGearApp
from client.services.api_client import ApiError
from client.registration import ACCENT, BACKGROUND, BORDER, ERROR, MUTED, SUCCESS, SURFACE, TEXT
from client.ui import CameraPreview

USER = {"id": 42, "full_name": "Анна Смирнова", "email": "anna@example.org", "role": "employee",
        "job_title": "Дизайнер", "phone": "", "avatar_url": None}
BOOKINGS = [{"id": 1, "desk_id": 7, "desk_number": 12, "desk_floor": 2, "desk_section": 1,
             "planed_at": "2026-11-02T09:00:00", "duration": "08:00:00", "is_cancelled": False}]
DESKS = [
    {"id": 7, "number": 12, "floor": 2, "section": 1, "sockets": 2, "features": [],
     "is_free": True, "is_operational": True},
    {"id": 8, "number": 13, "floor": 2, "section": 1, "sockets": 2, "features": [],
     "is_free": False, "is_operational": True},
    {"id": 9, "number": 14, "floor": 2, "section": 1, "sockets": 2, "features": [],
     "is_free": False, "is_operational": False},
]
DESK_PAGE = DESKS + [dict(DESKS[0], id=100 + index, number=100 + index) for index in range(17)]


class DeferredWorker:
    def __init__(self):
        self.pending = []

    def submit(self, function, success, failure):
        self.pending.append((success, failure, function))
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
        if os.getenv("ODG_HIDE_TEST_WINDOW"):
            Clock.schedule_once(lambda _dt: Window.hide(), 1)
        self.worker.stop()
        self.worker = self.model.worker = DeferredWorker()
        self.navigation_ids = [id(item) for item in self.navigation.children]
        self.errors = []
        self.steps = []
        self.deadline = time.monotonic() + 180
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

    def field_named(self, title):
        return next(widget for widget in self.body.walk() if getattr(widget, "_title", None) == title)

    def press(self, title):
        button = next(widget for widget in self.body.walk() if isinstance(widget, MDButton)
                      and any(getattr(child, "text", None) == title for child in widget.children))
        button.dispatch("on_release")

    def complete_desk_request(self, start_time="10:30", end_time="12:00", desks=None, end_date=None):
        """Run the queued read with controlled API responses and inspect its query."""
        self.check(len(self.worker.pending) == 1, "Expected one pending desk request")
        function = self.worker.pending[0][2]
        with patch.object(self.api, "get_desks", return_value=DESK_PAGE if desks is None else desks) as get_desks, \
                patch.object(self.api, "get_availability", return_value=[]) as get_availability:
            result = function()
        get_desks.assert_called_once()
        arguments = get_desks.call_args.kwargs
        day = self.filters["date"]
        self.check(arguments["start_at"] == day + "T" + start_time + ":00", "Search sent the wrong start time")
        self.check(arguments["end_at"] == (end_date or day) + "T" + end_time + ":00", "Search sent the wrong end time")
        self.check(arguments["offset"] == 0 and arguments["limit"] == 200, "Search did not load the complete result")
        get_availability.assert_called_once_with(date=day)
        self.worker.complete(result)

    def check_desk_statuses(self):
        for desk, status, caption, color in (
                (DESKS[0], "free", "Свободен", SUCCESS),
                (DESKS[1], "occupied", "Занят", ERROR),
                (DESKS[2], "unavailable", "Недоступен" if self.map_mode else "Вне эксплуатации", ERROR)):
            item = next(widget for widget in self.desk_results.walk()
                        if getattr(widget, "_desk_id", None) == desk["id"])
            self.check(item._availability == status, "Desk state does not match availability")
            labels = [widget for widget in item.walk() if caption in getattr(widget, "text", "")]
            self.check(bool(labels), "Desk status has no readable caption: " + caption)
            self.check(any(list(widget.text_color) == list(color) for widget in labels),
                       "Desk status has the wrong color: " + caption)

    def add_checks(self):
        def initial():
            self.check(self.page == "login" and not self.startup.parent, "Startup did not finish")
            self.check(self.profile_button.disabled and self.navigation.disabled, "Private navigation before login")
            for name, color in (("background", BACKGROUND), ("surface", SURFACE), ("border", BORDER),
                                ("text", TEXT), ("muted", MUTED), ("accent", ACCENT)):
                self.check(max(color[:3]) - min(color[:3]) < 0.005, f"{name} is not neutral gray")
            self.check(SUCCESS[1] > SUCCESS[0] and SUCCESS[1] > SUCCESS[2], "Free desk color must be green")
            self.check(ERROR[0] > ERROR[1] and ERROR[0] > ERROR[2], "Occupied desk color must be red")
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
            self.check(not any(getattr(w, "_title", None) == "Должность" for w in self.body.walk()),
                       "Position can still be edited in the profile")
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
            self.long_caption = self.action("Назначить или снять оборудование с сотрудника", lambda: None)
            self.long_caption.size_hint_x = None
            self.long_caption.width = dp(220)
            Window.size = (320, 640)
        self.steps.append(("back returns to parent section", nested_back))

        def narrow():
            self.check(all(item.width >= 44 for item in self.nav_items.values()), "Narrow screen has tiny navigation targets")
            self.check(self.body.width <= self.scroll.width, "Body overflows narrow screen")
            self.check(self.scroll.pos == self.pages.pos, "Page is not inside its content area")
            self.check(self.scroll.y >= self.navigation.top, "Page overlaps bottom navigation")
            caption = self.long_caption._button_text
            self.check(caption.texture_size[0] <= self.long_caption.width - dp(48) + 1,
                       "Long button text overflows a narrow screen")
            self.check(caption.texture_size[1] + dp(24) <= self.long_caption.height + 1,
                       "Long button text is clipped vertically")
            self.check(self.long_caption.height > dp(58), "Long captions do not wrap on narrow controls")
            self.snap("narrow")
            for _ in range(12):
                self.profile()
                self.home()
            self.check(len(self.pages.children) <= 2, "Rapid transitions accumulate page trees")
            self.worker.complete({"active": None, "user": dict(USER), "desk": None})
            if self.worker.pending:
                self.worker.complete({"active": None, "user": dict(USER), "desk": None})
        self.steps.append(("narrow screen and repeated transitions", narrow))

        def ticket_creation():
            devices = [{"id": 21, "type": "Ноутбук"}, {"id": 22, "type": "Монитор"}]
            self.equipment_detail(dict(devices[0], status="assigned"))
            self.check("Сообщить о проблеме" not in [getattr(w, "text", "") for w in self.body.walk()],
                       "Ticket creation remains in the equipment card")
            self.tickets()
            self.worker.complete([])
            self.press("Создать заявку")
            self.worker.complete([])
            self.check(self.page == "tickets" and "Нет доступной техники" in self.status.text,
                       "No-equipment case must stay in the ticket section")
            with patch.object(self.api, "get_my_equipment", return_value=devices) as read:
                self.press("Создать заявку")
                items = self.worker.pending[0][2]()
            read.assert_called_once()
            self.worker.complete(items)
            self.check(self.page == "ticket_form" and self.nav_items["tickets"].active,
                       "Ticket form did not open in its section")
            equipment = self.field_named("Оборудование")
            self._choose(equipment, 22, "Монитор · №22")
            self.field_named("Опишите проблему").text = "Монитор не включается"
            self.press("Отправить заявку")
            self._choose(equipment, 21, "Ноутбук · №21")
            ticket = {"id": 19, "equipment_id": 22, "description": "Монитор не включается",
                      "status": "new", "created_at": "2026-11-09T09:00:00"}
            with patch.object(self.api, "create_ticket", return_value=ticket) as create:
                saved = self.worker.pending[0][2]()
            self.check(create.call_args.args[0] == 22, "Ticket used a different device from the selected one")
            self.worker.complete(saved)
            self.worker.complete([])
            self.check(self.page == "ticket_detail", "Saved ticket did not open")
            self._back()
            self.worker.complete([ticket])
            self.check(self.page == "tickets", "Back did not return to tickets")
        self.steps.append(("create a ticket from its section for the selected device", ticket_creation))

        def qr_paths():
            for code in ("21", "/equipment/21", "/api/v1/equipment/21", self.api.base_url + "/equipment/21"):
                self._open_code(code)
                with patch.object(self.api, "get_equipment", return_value={"id": 21, "type": "Ноутбук", "status": "assigned"}) as read:
                    item = self.worker.pending[0][2]()
                read.assert_called_once_with(21)
                self.worker.complete(item)
                self.check(self.page == "equipment_detail", "A supported QR path did not open its equipment")
            self._open_code("https://example.org/unrelated/21")
            self.check(not self.worker.pending and "не относится" in self.status.text,
                       "An unrelated QR code was accepted")
        self.steps.append(("QR codes support both plain IDs and the configured server prefix", qr_paths))

        def desk_search_validation():
            Window.size = (420, 840)
            self.filters.update(date=(dt.date.today() + dt.timedelta(days=2)).isoformat(),
                                start_time="09:00", end_time="17:00")
            self.desks()
            self.complete_desk_request("09:00", "17:00")
            search = self.field_named("Поиск по номеру стола")
            date = self.field_named("Дата · ГГГГ-ММ-ДД")
            start = self.field_named("С · ЧЧ:ММ")
            end = self.field_named("До · ЧЧ:ММ")
            saved = self.filters.copy()
            for invalid_start, invalid_end in (("25:00", "12:00"), ("10:60", "12:00"),
                                                ("9:00", "12:00"), ("", "12:00"),
                                                ("10:00", ""), ("12:00", "12:00"),
                                                ("13:00", "12:00")):
                start.text, end.text = invalid_start, invalid_end
                self._desk_search(search, date, start, end)
                self.check(not self.busy and not self.worker.pending, "Invalid time made a network request")
                self.check(self.filters == saved, "Invalid time changed the active search")
                self.check(bool(self.status.text) and list(self.status.text_color) == list(ERROR),
                           "Invalid time has no visible validation error")
            start.text, end.text = "10:30", "12:00"
            self._desk_search(search, date, start, end)
            self.check(self.filters["start_time"] == "10:30" and self.filters["end_time"] == "12:00",
                       "Valid times were not saved")
            self.complete_desk_request()
        self.steps.append(("desk search validates hours and sends the selected interval", desk_search_validation))

        def list_ready():
            self.check_desk_statuses()
            first_desk = next(widget for widget in self.desk_results.walk() if getattr(widget, "_desk_id", None) == 7)
            self.scroll.scroll_to(first_desk, padding=16, animate=False)
        self.steps.append(("desk list finishes layout before visual review", list_ready))
        self.steps.append(("desk list screenshot", lambda: self.snap("desks-interval-list")))

        def desk_list_and_booking():
            self.desk_detail(DESKS[0])
            self.worker.complete({"schedule": [], "features": []})
            self.check(self.page == "desk", "Desk card did not open")
            self.booking_form(DESKS[0])
            start = dt.datetime.fromisoformat(self.field_named("Дата и время начала").text)
            self.check(start.isoformat() == self.filters["date"] + "T10:30:00",
                       "Booking did not inherit the search start")
            self.check(self.field_named("Часов в день · 1–24").text == "2",
                       "Fractional search interval must round up to whole booking hours")
            text = "\n".join(getattr(widget, "text", "") for widget in self.body.walk())
            self.check("12:30" in text, "Rounded booking does not show its actual end time")
            duration = self.field_named("Часов в день · 1–24")
            duration.text = "3"
            text = "\n".join(getattr(widget, "text", "") for widget in self.body.walk())
            self.check("13:30" in text, "Changing the duration did not refresh the actual end time")
            duration.text = "2"
            text = "\n".join(getattr(widget, "text", "") for widget in self.body.walk())
            self.check("12:30" in text, "Restoring the duration did not refresh the actual end time")
        self.steps.append(("list statuses and booking inherit the selected search interval", desk_list_and_booking))

        def booking_ready():
            self.snap("booking-from-search")
            self._back()
            self.check(self.page == "desks", "Back did not return to the desk search")
            self.complete_desk_request()
        self.steps.append(("booking screenshot and return to selected search", booking_ready))

        def desk_map():
            self.check(self.field_named("С · ЧЧ:ММ").text == "10:30"
                       and self.field_named("До · ЧЧ:ММ").text == "12:00", "Back lost selected hours")
            self._toggle_map()
            self.complete_desk_request()
        self.steps.append(("map preserves the selected interval", desk_map))

        def map_ready():
            self.check_desk_statuses()
            occupied = next(widget for widget in self.desk_results.walk() if getattr(widget, "_desk_id", None) == 8)
            self.scroll.scroll_to(occupied, padding=16, animate=False)
        self.steps.append(("desk map finishes layout before visual review", map_ready))
        self.steps.append(("desk map screenshot", lambda: self.snap("desks-interval-map")))

        def floor_navigation():
            all_desks = [dict(DESKS[index % 3], id=1000 + index, number=978700 + index,
                              floor=2, section=1 + index // 15) for index in range(35)]
            all_desks += [dict(DESKS[0], id=2000 + floor, floor=floor) for floor in (0, 5)]
            self._display_desks({"desks": all_desks})
            plan = self.floor_plan
            self.check(plan.selected_floor == 2, "The chosen floor was not preserved")
            self.check(len([w for w in plan.content.walk() if hasattr(w, "_desk_id")]) == 35,
                       "The floor was cut off after 20 desks or one section")
            labels = [getattr(w, "text", "") for w in self.body.walk()]
            self.check("Предыдущие 20" not in labels and "Следующие 20" not in labels,
                       "Pagination buttons remain")
            plan.up.dispatch("on_release")
            self.check(plan.selected_floor == 5 and plan.up.disabled, "Up skipped or exceeded the highest floor")
            plan.down.dispatch("on_release")
            self.check(plan.selected_floor == 2, "Down must select the previous existing floor")
            plan.floor_buttons[0].dispatch("on_release")
            self.check(plan.selected_floor == 0 and plan.down.disabled, "Floor zero is not selectable")
            self.check(not self.worker.pending, "Switching floors issued another HTTP request")
            plan.floor_buttons[2].dispatch("on_release")
            self.check(self.map_floor == 2, "Floor selection was not remembered")
            Window.size = (320, 640)
            self.scroll.scroll_to(plan, padding=0, animate=False)
        self.steps.append(("complete floor and right-hand floor navigation", floor_navigation))

        def narrow_floor():
            plan = self.floor_plan
            self.check(plan.rail.x >= plan.plan_scroll.right, "Floor selector overlaps the plan")
            self.check(plan.right <= self.body.right + 1, "Plan overflows a narrow phone")
            self.check(all(w.width >= dp(44) and w.height >= dp(44) for w in plan.floor_buttons.values()),
                       "Floor buttons are too small for touch")
            for widget in plan.content.walk():
                if hasattr(widget, "_desk_id"):
                    self.check(widget.width >= dp(88), "Desk tiles are too narrow")
            self.snap("floor-plan-narrow")
            plan.plan_scroll.scroll_y = 0
        self.steps.append(("floor plan fits 320px with accessible touch controls", narrow_floor))
        self.steps.append(("end of complete floor screenshot", lambda: self.snap("floor-plan-end")))

        def complete_results():
            self.desks()
            function = self.worker.pending[0][2]
            batch = [dict(DESKS[0], id=3000 + index, number=index, floor=2) for index in range(420)]
            with patch.object(self.api, "get_desks", side_effect=[batch[:200], batch[200:400], batch[400:]]) as read, \
                    patch.object(self.api, "get_availability", return_value=[]):
                result = function()
            self.check([call.kwargs["offset"] for call in read.call_args_list] == [0, 200, 400],
                       "Complete-floor loading skipped a backend page")
            self.check(len(result["desks"]) == 420, "A floor above the API page limit was truncated")
            self.worker.complete({"desks": DESK_PAGE})
            self.desk_filters()
            self.worker.complete([])
            self._back()
            self.complete_desk_request()
        self.steps.append(("all API pages and filter navigation preserve selected hours", complete_results))

        def narrow_search():
            Window.size = (320, 640)
            self.scroll.scroll_y = 1
        self.steps.append(("prepare the time search on a narrow screen", narrow_search))

        def narrow_search_ready():
            start, end = self.field_named("С · ЧЧ:ММ"), self.field_named("До · ЧЧ:ММ")
            self.check(start.width >= 100 and end.width >= 100, "Time fields are too narrow")
            self.check(start.right <= end.x and end.right <= self.body.right, "Time fields overlap or overflow")
            self.check(start.height >= dp(66) and start.height <= start.parent.height,
                       "Larger fields overflow their row")
            self.snap("desk-search-narrow")
            Window.size = (420, 840)
        self.steps.append(("time fields remain readable on a 320px screen", narrow_search_ready))

        def desk_cache_intervals():
            previous_keys = {key for key in self.state.data.get("cache", {}) if key.startswith("desks:")}
            search = self.field_named("Поиск по номеру стола")
            date = self.field_named("Дата · ГГГГ-ММ-ДД")
            start, end = self.field_named("С · ЧЧ:ММ"), self.field_named("До · ЧЧ:ММ")
            start.text, end.text = "14:00", "16:00"
            self._desk_search(search, date, start, end)
            self.complete_desk_request("14:00", "16:00")
            current_keys = {key for key in self.state.data.get("cache", {}) if key.startswith("desks:")}
            self.check(len(current_keys - previous_keys) == 1, "Different hours reused the prior availability cache")
            self.booking_form(DESKS[0])
            self.check(self.field_named("Дата и время начала").text.endswith("14:00"), "Whole-hour booking start is wrong")
            self.check(self.field_named("Часов в день · 1–24").text == "2", "Whole-hour duration changed")
            self._back()
            self.complete_desk_request("14:00", "16:00")
        self.steps.append(("availability cache distinguishes time intervals", desk_cache_intervals))

        def overnight_interval():
            start = dt.datetime.combine(dt.date.today() + dt.timedelta(days=2), dt.time(22))
            end = start + dt.timedelta(hours=8)
            self.desks(interval=(start, end))
            end_date = end.date().isoformat()
            self.complete_desk_request("22:00", "06:00", end_date=end_date)
            self._toggle_map()
            self.complete_desk_request("22:00", "06:00", end_date=end_date)
            self.check(self.filters["end_date"] == end_date, "Overnight search lost its end date")
            search = self.field_named("Поиск по номеру стола")
            date = self.field_named("Дата · ГГГГ-ММ-ДД")
            first, last = self.field_named("С · ЧЧ:ММ"), self.field_named("До · ЧЧ:ММ")
            end_field = self.field_named("Дата окончания · ГГГГ-ММ-ДД")
            saved = self.filters.copy()
            date.text = (start.date() - dt.timedelta(days=2)).isoformat()
            self._desk_search(search, date, first, last, end_field)
            self.check(not self.busy and not self.worker.pending and self.filters == saved,
                       "An interval longer than 24 hours was submitted")
            self.check(end_field.error and "24" in self.status.text, "Long interval has no clear validation error")
            date.text = end_field.text = start.date().isoformat()
            last.text = "23:00"
            self._desk_search(search, date, first, last, end_field)
            self.complete_desk_request("22:00", "23:00")
            self.check("end_date" not in self.filters, "Same-day search retained an obsolete overnight end date")
        self.steps.append(("overnight intervals survive navigation and reject periods above 24 hours", overnight_interval))

        def prepare_camera_preview():
            from PIL import Image as Picture, ImageDraw
            from kivy.uix.image import Image
            target = Picture.new("RGB", (80, 120))
            draw = ImageDraw.Draw(target)
            for color, box in (("red", (0, 0, 39, 59)), ("lime", (40, 0, 79, 59)),
                               ("blue", (0, 60, 39, 119)), ("yellow", (40, 60, 79, 119))):
                draw.rectangle(box, fill=color)
            raw = ARTIFACTS / "sensor-frame.png"
            target.transpose(Picture.Transpose.ROTATE_90).save(raw)
            self.page_start("scanner", "Проверка предпросмотра")
            self.fixture_preview = CameraPreview(Image(source=str(raw)), 90, size_hint_y=None, height=dp(300))
            self.body.add_widget(self.fixture_preview)
        self.steps.append(("prepare a sideways sensor image without a real camera", prepare_camera_preview))

        def inspect_camera_preview():
            from PIL import Image
            path = ARTIFACTS / "camera-preview.png"
            self.fixture_preview.export_to_png(str(path))
            with Image.open(path).convert("RGB") as picture:
                width = min(picture.width, picture.height * 2 / 3)
                height = width * 3 / 2
                left, top = (picture.width - width) / 2, (picture.height - height) / 2
                for x, y, expected in ((0.25, 0.25, (255, 0, 0)), (0.75, 0.25, (0, 255, 0)),
                                       (0.25, 0.75, (0, 0, 255)), (0.75, 0.75, (255, 255, 0))):
                    actual = picture.getpixel((int(left + width * x), int(top + height * y)))
                    self.check(all(abs(a - b) < 10 for a, b in zip(actual, expected)),
                               "Camera preview is sideways, mirrored or upside down: " + str(actual))
            self.home()
            self.worker.complete({"active": None, "user": dict(USER), "desk": None})
        self.steps.append(("camera preview rotates clockwise and preserves all four corners", inspect_camera_preview))

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
            self.check(time.monotonic() < self.deadline, "Native UI checks timed out")
            # Large widget trees can take longer than the scheduled delay to
            # construct. Wait until the staged page has actually been revealed.
            if self.startup.parent or self.pages.current is not self.scroll or self.scroll.opacity < 0.99:
                Clock.schedule_once(self.step, 0.1)
                return
            if not self.steps:
                print("PASS: all native navigation, theme and desk search checks", flush=True)
                self.stop()
                return
            name, action = self.steps.pop(0)
            action()
            print("PASS: " + name, flush=True)
            Clock.schedule_once(lambda _dt: Clock.schedule_once(self.step, 0.4), 0)
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
        if application.steps and not application.errors:
            application.errors.append("Window closed before all navigation checks completed")
        if application.errors:
            print("\n".join(application.errors), flush=True)
            sys.exit(1)
