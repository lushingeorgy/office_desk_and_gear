from __future__ import annotations

import base64
import csv
import datetime as dt
import hashlib
import logging
import os
import re
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.image import Image
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.utils import platform
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonIcon, MDButtonText
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDIcon
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.selectioncontrol import MDCheckbox
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText, MDTextFieldTrailingIcon

from client.core.config import API_BASE_URL
from client.core.device import DeviceTools
from client.core.storage import LocalState
from client.core.worker import BackgroundWorker
from client.registration import ACCENT, BACKGROUND, BORDER, ERROR, MUTED, SUCCESS, SURFACE, TEXT, label
from client.services.api_client import ApiClient, ApiError
from client.viewmodels.session import SessionViewModel

LOG = logging.getLogger(__name__)
ROLES = {"employee": "Сотрудник", "office_manager": "Офис-менеджер", "it_admin": "IT-администратор"}
STATUSES = {"new": "Новая", "in_progress": "В работе", "resolved": "Решена", "closed": "Закрыта",
            "in_stock": "На складе", "assigned": "Закреплена", "in_repair": "В ремонте", "written_off": "Списана"}
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def moment(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (parsed.astimezone() if parsed.tzinfo is not None else parsed).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        return str(value or "—")


class OccupancyBar(Widget):
    def __init__(self, value, **kwargs):
        super().__init__(size_hint_y=None, height=dp(18), **kwargs)
        self.value = max(0.0, min(1.0, float(value)))
        self.bind(pos=self.redraw, size=self.redraw)

    def redraw(self, *_):
        self.canvas.clear()
        with self.canvas:
            Color(*BORDER)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(7)])
            Color(*ACCENT)
            RoundedRectangle(pos=self.pos, size=(self.width * self.value, self.height), radius=[dp(7)])


class FeaturePicker(MDBoxLayout):
    def __init__(self, selected=(), **kwargs):
        super().__init__(orientation="vertical", adaptive_height=True, spacing=dp(6), **kwargs)
        self.selected = set(selected)

    def fill(self, items):
        self.clear_widgets()
        for feature in items:
            row = MDBoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
            check = MDCheckbox(active=feature["id"] in self.selected, size_hint=(None, None), size=(dp(44), dp(44)),
                               pos_hint={"center_y": 0.5})
            check.bind(active=partial(self._changed, feature["id"]))
            row.add_widget(check)
            row.add_widget(label(feature["name"], 52, 13))
            self.add_widget(row)

    def _changed(self, feature_id, _widget, active):
        self.selected.add(feature_id) if active else self.selected.discard(feature_id)


class OfficeDeskGearApp(MDApp):
    """Mobile shell. All HTTP work is serialized off the Kivy thread."""

    title = "Office Desk & Gear"

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Blue"
        Window.clearcolor = BACKGROUND
        Window.softinput_mode = "below_target"
        if platform not in {"android", "ios"}:
            Window.size = (dp(420), dp(840))
            Window.minimum_width, Window.minimum_height = dp(320), dp(480)
        directory = os.environ.get("ODG_CLIENT_DATA") or str(Path(self.user_data_dir) / "private")
        self.state = LocalState(directory, platform)
        self.api = ApiClient(self.state.api_url or os.environ.get("DSH_API_URL") or API_BASE_URL)
        self.user = None
        self.worker = BackgroundWorker(Clock.schedule_once)
        self.model = SessionViewModel(self.api, self.state, self.worker,
                                      lambda: (self.api.base_url, self.api.token, (self.user or {}).get("id")))
        self.device = DeviceTools(platform, self.state, Clock.schedule_once)
        self.api.on_auth_lost = lambda message: Clock.schedule_once(lambda _dt: self.auth_lost(message))
        self.busy, self.stopped = False, False
        self.generation = 0
        self.page = "login"
        self._menus, self._events = [], []
        self.camera = None
        self._scan_busy = False
        self._pause_camera_mode = None
        self.filters = {"search": "", "floor": "", "section": "", "sockets": "", "features": "",
                        "sort": "number", "order": "asc", "date": dt.date.today().isoformat()}
        self.desk_offset, self.map_mode = 0, False
        self.root_screen = MDScreen(md_bg_color=BACKGROUND)
        self.shell = MDBoxLayout(orientation="vertical", padding=(dp(16), dp(10)), spacing=dp(5))
        self.root_screen.add_widget(self.shell)
        header = MDBoxLayout(size_hint_y=None, height=dp(42), spacing=dp(10))
        header.add_widget(MDIcon(icon="office-building-outline", theme_icon_color="Custom", icon_color=ACCENT,
                                 size_hint_x=None, width=dp(28)))
        header.add_widget(label("Office Desk & Gear", 42, 16))
        self.shell.add_widget(header)
        self.status = label("", 44, 12, MUTED)
        self.status.bind(texture_size=lambda w, size: setattr(w, "height", max(dp(42), min(dp(100), size[1] + dp(12)))))
        self.shell.add_widget(self.status)
        self.scroll = MDScrollView(do_scroll_x=False, bar_width=dp(2))
        self.anchor = AnchorLayout(anchor_y="top", padding=(dp(8), dp(12)), size_hint_y=None)
        self.body = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(14), size_hint_x=None)
        self.anchor.add_widget(self.body)
        self.scroll.add_widget(self.anchor)
        self.shell.add_widget(self.scroll)
        self.navigation = MDBoxLayout(size_hint_y=None, height=dp(52), spacing=dp(2))
        self.shell.add_widget(self.navigation)
        self.body.bind(height=self._layout)
        self.root_screen.bind(size=self._layout)
        self._layout()
        return self.root_screen

    def on_start(self):
        self._configure_system_bars()
        Window.bind(on_keyboard=self._on_keyboard)
        self.user = self.model.restore()
        if self.user:
            self.home()
        else:
            self.login()

    def _configure_system_bars(self):
        if platform == "android":
            # The pinned p4a bridge observes WindowInsets on Android 15+ and
            # pads the native content view. SDL then resizes Window normally;
            # older Android versions already keep the surface inside the bars.
            # Keep system bars visible and use the same background as the UI.
            from android.display_cutout import update_system_ui
            color = "#" + "".join(f"{round(channel * 255):02x}" for channel in BACKGROUND[:3])
            update_system_ui(color, color, "Light", pad_status=True, pad_nav=True)

    def _layout(self, *_):
        self.body.width = max(dp(260), min(dp(620), self.root_screen.width - dp(48)))
        self.anchor.height = max(self.scroll.height, self.body.height + dp(24))

    def note(self, text, error=False, success=False):
        self.status.text = text
        self.status.text_color = ERROR if error else SUCCESS if success else MUTED

    def _cleanup_page(self):
        for event in self._events:
            event.cancel()
        self._events.clear()
        for menu in self._menus:
            menu.dismiss()
        self._menus.clear()
        self._stop_camera()

    def page_start(self, name, title, subtitle=""):
        self._cleanup_page()
        self.generation += 1
        self.page = name
        self.body.clear_widgets()
        self.navigation.clear_widgets()
        self.note("")
        self.scroll.scroll_y = 1
        self.body.add_widget(label(title, 42, 26))
        if subtitle:
            self.text(subtitle, MUTED)
        if self.user:
            for title_, method in (("Главная", self.home), ("Столы", self.desks), ("Брони", self.bookings),
                                   ("Техника", self.equipment), ("Заявки", self.tickets)):
                self.navigation.add_widget(self.button(title_, method, compact=True))
        else:
            self.navigation.add_widget(self.button("Настроить сервер", self.settings, compact=True))

    def text(self, value, color=TEXT, target=None, size=14):
        item = label(str(value), 32, size, color)
        item.bind(texture_size=lambda w, v: setattr(w, "height", max(dp(28), v[1] + dp(8))))
        (target or self.body).add_widget(item)
        return item

    def button(self, title, action, primary=False, compact=False, icon=None):
        text = MDButtonText(text=title, theme_text_color="Custom", text_color=BACKGROUND if primary else ACCENT,
                            theme_font_size="Custom", font_size=sp(11 if compact else 14))
        children = [text]
        if icon:
            children.insert(0, MDButtonIcon(icon=icon, theme_icon_color="Custom", icon_color=ACCENT))
        item = MDButton(*children, style="filled" if primary else "text", theme_width="Custom",
                        size_hint=(1, None), height=dp(44 if compact else 50), radius=[dp(12)],
                        theme_bg_color="Custom", md_bg_color=ACCENT if primary else SURFACE)
        def released(*_):
            if self.busy:
                self.note("Дождитесь завершения текущего запроса")
                return
            action()
        item.bind(on_release=released)
        return item

    def action(self, title, method, primary=False, target=None):
        item = self.button(title, method, primary=primary)
        (target or self.body).add_widget(item)
        return item

    def field(self, title, value="", *, secret=False, required=False, kind="text", target=None, multiline=False):
        item = MDTextField(MDTextFieldHintText(text=title + (" *" if required else ""), text_color_normal=MUTED,
                                             text_color_focus=ACCENT),
                           text=str(value or ""), mode="outlined", size_hint_y=None, height=dp(110 if multiline else 58),
                           radius=[dp(10)], multiline=multiline, write_tab=False, password=secret,
                           input_type="mail" if kind == "email" else "number" if kind == "int" else "text",
                           keyboard_suggestions=not secret and kind not in {"email", "int", "datetime", "date"},
                           theme_text_color="Custom", text_color_normal=TEXT, text_color_focus=TEXT,
                           theme_line_color="Custom", line_color_normal=BORDER, line_color_focus=ACCENT, error_color=ERROR)
        item._required, item._kind, item._title = required, kind, title
        item.bind(text=lambda w, _t: setattr(w, "error", False), focus=self._focus)
        if kind in {"date", "datetime"}:
            item.add_widget(MDTextFieldTrailingIcon(icon="calendar-month-outline"))
            item.bind(on_touch_down=lambda w, touch: self._calendar_touch(w, touch))
        (target or self.body).add_widget(item)
        return item

    def _calendar_touch(self, field, touch):
        if field.collide_point(*touch.pos) and touch.x > field.right - dp(48) and not getattr(touch, "is_mouse_scrolling", False):
            from kivymd.uix.pickers import MDModalDatePicker
            try:
                date = dt.date.fromisoformat(field.text[:10])
            except ValueError:
                date = dt.date.today()
            dialog = MDModalDatePicker(year=date.year, month=date.month, day=date.day,
                                       text_button_ok="Выбрать", text_button_cancel="Отмена")

            def selected(picker):
                dates = picker.get_date()
                if dates:
                    field.text = dates[0].isoformat() + (" " + (field.text[11:16] or "09:00") if field._kind == "datetime" else "")
                picker.dismiss()
            dialog.bind(on_ok=selected, on_cancel=lambda picker: picker.dismiss())
            self._menus.append(dialog)
            dialog.open()
            field.focus = False
            return True
        return False

    def _focus(self, field, focused):
        if focused:
            self._events.append(Clock.schedule_once(lambda _dt: self.scroll.scroll_to(field, padding=dp(18)), 0.2))

    def values(self, fields):
        result = {}
        try:
            for key, field in fields.items():
                value = field.text if field.password else field.text.strip()
                if field._required and not value:
                    raise ValueError(f"Заполните поле «{field._title}»")
                if len(str(value)) > (5000 if field.multiline else 1024 if field.password else 255):
                    raise ValueError(f"Поле «{field._title}» слишком длинное")
                if not value:
                    result[key] = None
                    continue
                if field._kind == "email" and not EMAIL.fullmatch(value):
                    raise ValueError("Введите корректный адрес электронной почты")
                if field._kind == "int":
                    value = int(value)
                    if value < 0:
                        raise ValueError(f"«{field._title}» должно быть не меньше нуля")
                if field._kind == "datetime":
                    value = dt.datetime.fromisoformat(value).isoformat(timespec="seconds")
                if field._kind == "date":
                    value = dt.date.fromisoformat(value).isoformat()
                result[key] = value
            return result
        except (ValueError, TypeError) as error:
            field.error = True
            field.focus = True
            self.note(str(error) if str(error) and "invalid literal" not in str(error) and "Invalid isoformat" not in str(error)
                      else f"Проверьте поле «{field._title}»", error=True)
            return None

    def choice(self, title, options, value=None, target=None):
        field = self.field(title, options.get(value, value) or next(iter(options.values())), target=target)
        field.readonly, field.keyboard_mode = True, "managed"
        field._choice = value if value in options else next(iter(options))
        menu = MDDropdownMenu(caller=field, position="bottom", md_bg_color=SURFACE,
                              items=[{"text": text_, "height": dp(48), "on_release":
                                      partial(self._choose, field, key, text_)} for key, text_ in options.items()])
        field._menu = menu
        field.bind(on_touch_down=lambda w, touch: self._choice_touch(w, touch, menu))
        self._menus.append(menu)
        return field

    @staticmethod
    def _choice_touch(field, touch, menu):
        if field.collide_point(*touch.pos) and not getattr(touch, "is_mouse_scrolling", False):
            field.focus = False
            menu.width = field.width
            menu.open()
            return True
        return False

    @staticmethod
    def _choose(field, key, title):
        field._choice, field.text = key, title
        field._menu.dismiss()

    def card(self, title, description="", action=None):
        box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(6), padding=dp(16),
                          md_bg_color=SURFACE, radius=[dp(14)])
        self.text(title, target=box, size=16)
        if description:
            self.text(description, MUTED, target=box, size=13)
        if action:
            self.action("Открыть", action, target=box)
        self.body.add_widget(box)
        return box

    def job(self, function, success, message="Загрузка…", cache_key=None, cached=None):
        generation = self.generation
        if self.busy:
            self.note("Дождитесь завершения текущего запроса")
            return
        entry = self.model.cached(cache_key)
        if entry and cached:
            cached(entry["payload"])
            self.note("Сохранённые данные · обновляем…")
        else:
            self.note(message)
        self.busy = True

        def done(value, stale=False):
            self.busy = False
            if self.stopped or stale:
                return
            if generation == self.generation:
                self.note("")
                success(value)

        def failed(error):
            self.busy = False
            if self.stopped or generation != self.generation:
                return
            if isinstance(error, ApiError):
                message_ = str(error)
                if error.status_code == 401 and self.user:
                    self.auth_lost(message_)
                    return
            else:
                LOG.exception("Client operation failed", exc_info=(type(error), error, error.__traceback__))
                message_ = "Не удалось выполнить операцию. Проверьте данные и повторите"
            if entry and cached:
                self.note("Нет связи · показан кэш от " + dt.datetime.fromtimestamp(entry["at"]).strftime("%d.%m %H:%M"), error=True)
            else:
                self.note(message_, error=True)

        if not self.model.execute(function, done, failed, cache_key=cache_key):
            self.busy = False
            self.note("Очередь запросов занята. Повторите действие", error=True)

    def mutate(self, function, success, message="Сохраняем…"):
        def finish(value):
            self.model.invalidate()
            success(value)
        self.job(function, finish, message)

    def auth_lost(self, message="Сессия завершилась. Войдите снова"):
        if not self.user:
            return
        self.model.forget()
        self.user = None
        self.login()
        self.note(message, error=True)

    def login(self):
        self.page_start("login", "С возвращением", "Войдите с рабочей почтой. После закрытия приложения вход сохранится.")
        fields = {"email": self.field("Рабочая почта", required=True, kind="email"),
                  "password": self.field("Пароль", required=True, secret=True)}
        self.action("Войти", lambda: self._login(fields), True)
        self.action("Создать аккаунт", self.register)
        self.action("Забыли пароль?", self.reset)
        self.action("Подтвердить рабочую почту", lambda: self._verify_entry(fields["email"]))
        self.text("Сервер: " + self.api.base_url, MUTED, size=12)

    def _login(self, fields):
        values = self.values(fields)
        if not values:
            return

        def logged_in(data):
            if isinstance(data, dict) and data.get("verify_email"):
                self.verify(data["verify_email"])
                self.note("Для входа сначала подтвердите рабочую почту")
                return
            result, user = data
            try:
                self.model.remember(result, user)
            except Exception:
                self.api.set_token(None)
                self.note("Не удалось безопасно сохранить вход. Проверьте хранилище устройства", error=True)
                return
            self.user = user
            self.home()
        def authenticate():
            try:
                return self.model.authenticate(**values)
            except ApiError as error:
                if error.status_code == 403:
                    return {"verify_email": values["email"]}
                raise
        self.job(authenticate, logged_in, "Входим в аккаунт…")

    def _verify_entry(self, email_field):
        values = self.values({"email": email_field})
        if values:
            self.verify(values["email"])

    def register(self):
        self.page_start("register", "Создать аккаунт", "Зарегистрируйте рабочий профиль и выберите роль в системе.")
        fields = {"full_name": self.field("ФИО", required=True), "job_title": self.field("Должность", required=True),
                  "email": self.field("Рабочая почта", required=True, kind="email"),
                  "password": self.field("Пароль от 6 символов", required=True, secret=True),
                  "confirm_password": self.field("Повторите пароль", required=True, secret=True)}
        self._registration_role = self.choice("Роль", ROLES, "employee")
        self._registration_role.add_widget(MDTextFieldTrailingIcon(icon="menu-down"))
        self.action("Создать аккаунт", lambda: self._register(fields, self._registration_role), True)
        self.action("У меня уже есть аккаунт", self.login)

    def _register(self, fields, role=None):
        values = self.values(fields)
        if not values:
            return
        selected_role = role if role is not None else getattr(self, "_registration_role", None)
        values["role"] = selected_role._choice if selected_role is not None else "employee"
        if len(values["password"]) < 6 or values["password"] != values["confirm_password"]:
            fields["confirm_password"].error = True
            self.note("Пароль — от 6 символов. Пароли должны совпадать", error=True)
            return

        def registered(user):
            if user.get("is_verified") is False:
                self.verify(values["email"])
            else:
                self.login()
                self.note("Профиль создан. Войдите в аккаунт", success=True)
        self.job(lambda: self.api.register(**values), registered, "Создаём профиль…")

    def verify(self, email):
        self.page_start("verify", "Подтвердить почту", "Введите код из письма для " + email)
        code = self.field("Код подтверждения", required=True)
        self.action("Подтвердить", lambda: self._verify(email, code), True)
        self.action("Отправить код ещё раз", lambda: self.job(lambda: self.api.resend_verification(email),
                    lambda result: self.note(result.get("message", "Письмо отправлено"), success=True)))
        self.action("Ко входу", self.login)

    def _verify(self, email, code):
        values = self.values({"code": code})
        if values:
            self.job(lambda: self.api.verify_email(email, values["code"]),
                     lambda _result: self._verified(), "Проверяем код…")

    def _verified(self):
        self.login()
        self.note("Почта подтверждена. Теперь можно войти", success=True)

    def reset(self):
        self.page_start("reset", "Восстановить пароль", "Получите код на рабочую почту, затем задайте новый пароль.")
        email = self.field("Рабочая почта", required=True, kind="email")
        self.action("Получить код", lambda: self._reset_request(email))
        fields = {"email": email, "code": self.field("Код из письма", required=True),
                  "new_password": self.field("Новый пароль от 6 символов", secret=True, required=True),
                  "confirm": self.field("Повторите новый пароль", secret=True, required=True)}
        self.action("Сменить пароль", lambda: self._reset_confirm(fields), True)
        self.action("Ко входу", self.login)

    def _reset_request(self, field):
        value = self.values({"email": field})
        if value:
            self.job(lambda: self.api.request_password_reset(value["email"]),
                     lambda result: self.note(result.get("message", "Код отправлен"), success=True))

    def _reset_confirm(self, fields):
        values = self.values(fields)
        if not values:
            return
        if len(values["new_password"]) < 6 or values.pop("confirm") != values["new_password"]:
            self.note("Новый пароль — от 6 символов. Подтверждение должно совпадать", error=True)
            return
        self.job(lambda: self.api.confirm_password_reset(**values), lambda _r: self._reset_done())

    def _reset_done(self):
        self.login()
        self.note("Пароль изменён. Войдите с новым паролем", success=True)

    def home(self):
        self.page_start("home", "Рабочее пространство", self.user.get("full_name", "") + " · " + ROLES.get(self.user.get("role"), "Сотрудник"))
        content = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(12))
        self.body.add_widget(content)

        def display(data):
            content.clear_widgets()
            booking = data.get("active")
            if booking:
                desk = data.get("desk") or {}
                self.text("Ваше место сегодня", ACCENT, target=content)
                self.text(f"Стол {desk.get('number', '#' + str(booking['desk_id']))} · {moment(booking['planed_at'])}\n{booking['duration']} ч\n" +
                          f"Этаж {desk.get('floor', '—')} · секция {desk.get('section', '—')}", target=content)
                self.action("Открыть бронь", lambda: self.booking_detail(booking), target=content)
                self.action("Показать на плане", lambda: self._home_map(booking, desk), target=content)
            else:
                self.text("Сегодня пока нет активной брони", MUTED, target=content)
                self.action("Выбрать рабочее место", self.desks, target=content, primary=True)

        self.action("Уведомления", self.notifications)
        self.action("Мой профиль", self.profile)
        if self.user.get("role") in {"it_admin", "office_manager"}:
            self.action("Управление офисом", self.admin)
        self.action("Настройки сервера", self.settings)
        self.action("Выйти из аккаунта", self.logout)

        def fetch():
            booking = self.api.get_active_booking()
            return {"active": booking, "user": self.api.get_me(),
                    "desk": self.api.get_desk(booking["desk_id"]) if booking else None}

        def loaded(data):
            self.user = data["user"]
            self.state.update_user(self.user)
            display(data)
        self.job(fetch, loaded, cache_key="home", cached=display)

    def _home_map(self, booking, desk):
        self.map_mode = True
        self.filters["search"] = ""
        self.filters["floor"] = str(desk.get("floor") or "")
        self.filters["section"] = str(desk.get("section") or "")
        self.desks()

    def settings(self):
        self.page_start("settings", "Подключение к серверу", "На телефоне укажите HTTPS-адрес удалённого API. Изменение сервера завершает текущий вход.")
        field = self.field("Адрес API", self.api.base_url, required=True)
        self.action("Проверить и сохранить", lambda: self._save_server(field), True)
        self.action("Назад", self.home if self.user else self.login)
        self.text("Пример: https://office.example.org/api/v1", MUTED)

    def _save_server(self, field):
        value = field.text.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            self.note("Укажите адрес http(s) без пароля, параметров и фрагмента", error=True)
            return
        if parsed.path.rstrip("/") != "/api/v1":
            value += "/api/v1" if parsed.path in {"", "/"} else ""
        if urlparse(value).path != "/api/v1":
            self.note("Адрес API должен оканчиваться на /api/v1", error=True)
            return
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "10.0.2.2"}:
            self.note("Для удалённого сервера используйте HTTPS", error=True)
            return
        candidate = ApiClient(value)

        def check():
            try:
                return candidate.check_connection()
            finally:
                candidate.close()

        def saved(_data):
            self.api.close()
            self.api.base_url = value
            self.api.set_token(None)
            self.state.set_api_url(value)
            self.user = None
            self.login()
            self.note("Сервер подключён", success=True)
        self.job(check, saved, "Проверяем сервер…")

    def logout(self):
        def clear():
            self.model.forget()
            self.user = None
            self.login()
            self.note("Вы вышли из аккаунта", success=True)

        def request():
            try:
                self.api.logout()
            except ApiError as error:
                # Offline exit still erases the device credential and all private cache.
                return error
            return None

        def done(error):
            clear()
            if error and error.status_code not in {401, 403}:
                self.note("Вход и кэш удалены с устройства. Серверная сессия истечёт автоматически")
        self.job(request, done, "Выходим…")

    def profile(self):
        self.page_start("profile", "Мой профиль", ROLES.get(self.user.get("role"), "Сотрудник"))
        fields = {"full_name": self.field("ФИО", self.user.get("full_name"), required=True),
                  "email": self.field("Рабочая почта", self.user.get("email"), required=True, kind="email"),
                  "job_title": self.field("Должность", self.user.get("job_title"), required=True),
                  "phone": self.field("Телефон", self.user.get("phone"))}
        photo = self.field("Фото профиля", "Фото установлено" if self.user.get("avatar_url") else "")
        photo.readonly = True
        if self.user.get("avatar_url"):
            self.action("Посмотреть фото профиля", lambda: self.private_photo(self.user["avatar_url"]))
        self.action("Выбрать фото", lambda: self.choose_photo(lambda path: self._selected_photo(photo, path)))
        self.action("Сделать фото", lambda: self.camera_photo(lambda path: self._selected_photo(photo, path)))
        self.action("Сохранить профиль", lambda: self._profile_save(fields, photo), True)

    def _profile_save(self, fields, photo):
        values = self.values(fields)
        if not values:
            return
        if values.get("phone") and not re.fullmatch(r"[+\d() \-]{5,30}", values["phone"]):
            self.note("Проверьте номер телефона", error=True)
            return
        path = getattr(photo, "_file_path", photo.text.strip())

        def request():
            if path and Path(path).is_file():
                values["avatar_url"] = self.api.upload_photo(path)["photo_url"]
            return self.api.update_me(**values)

        def saved(user):
            if user.get("is_verified") is False:
                self.model.forget()
                self.user = None
                self.verify(user["email"])
                self.note("Новая почта ожидает подтверждения. Введите код из письма")
                return
            self.user = user
            self.state.update_user(user)
            self.note("Профиль сохранён", success=True)
        self.mutate(request, saved)

    def desks(self, booking_notice=None, interval=None):
        self.page_start("desks", "Рабочие места", "Выберите дату, этаж и комплектацию. Кэш показывает последнее известное состояние.")
        if booking_notice:
            self.text("Бронирование не выполнено: " + booking_notice, ERROR)
        search = self.field("Поиск стола на сервере", self.filters["search"])
        date = self.field("Дата · ГГГГ-ММ-ДД", self.filters["date"], kind="date", required=True)
        self.action("Поиск и обновление", lambda: self._desk_search(search, date), True)
        self.action("Параметры фильтра и сортировки", self.desk_filters)
        self.action("Показать списком" if self.map_mode else "Показать план этажа", self._toggle_map)
        self.desk_results = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(12))
        self.body.add_widget(self.desk_results)
        self.action("Предыдущие 20", lambda: self._desk_page(-20))
        self.action("Следующие 20", lambda: self._desk_page(20))
        self._load_desks(interval=interval)

    def _desk_search(self, search, date):
        values = self.values({"date": date})
        if values:
            self.filters.update(search=search.text.strip(), date=values["date"])
            self.desk_offset = 0
            self.desks()

    def _toggle_map(self):
        self.map_mode = not self.map_mode
        self.desks()

    def _desk_page(self, delta):
        if delta > 0 and not getattr(self, "desk_has_more", False):
            self.note("Это последняя страница")
            return
        self.desk_offset = max(0, self.desk_offset + delta)
        self.desks()

    def _load_desks(self, interval=None):
        filters = self.filters.copy()
        args = {k: int(filters[k]) if filters[k] else None for k in ("floor", "section", "sockets")}
        args.update(search=filters["search"] or None, sort=filters["sort"], order=filters["order"],
                    features=[int(v) for v in filters["features"].split(",") if v.strip()], limit=20, offset=self.desk_offset)
        day = dt.date.fromisoformat(filters["date"])
        start_at, end_at = interval or (dt.datetime.combine(day, dt.time(9)), dt.datetime.combine(day, dt.time(17)))
        args.update(start_at=start_at.isoformat(), end_at=end_at.isoformat())
        key = "desks:" + repr(sorted(args.items())) + filters["date"]

        def fetch():
            desks = self.api.get_desks(**args)
            partial_message = self.api.last_meta.get("x-partial-match")
            return {"desks": desks, "bookings": self.api.get_availability(date=filters["date"]), "partial": partial_message,
                    "interval_label": start_at.strftime("%d.%m %H:%M") + "–" + end_at.strftime("%d.%m %H:%M")}
        self.job(fetch, self._display_desks, cache_key=key, cached=self._display_desks)

    def _display_desks(self, data):
        self.desk_results.clear_widgets()
        desks, bookings = data.get("desks") or [], data.get("bookings") or []
        self.desk_has_more = len(desks) == 20
        self.text("Записи " + str(self.desk_offset + 1) + "–" + str(self.desk_offset + len(desks)) if desks else "Столы не найдены", MUTED, target=self.desk_results)
        interval_label = data.get("interval_label", "09:00–17:00")
        self.text("Доступность за " + interval_label, MUTED, target=self.desk_results)
        if data.get("partial") == "true":
            self.text("Точных совпадений нет. Показаны близкие варианты", ACCENT, target=self.desk_results)
        if self.map_mode:
            self.text("План по этажам и секциям · голубой — доступен, красный — недоступен", MUTED, target=self.desk_results)
            groups = {}
            for desk in desks:
                groups.setdefault((desk.get("floor"), desk.get("section")), []).append(desk)
            for (floor, section), items in groups.items():
                self.text(f"Этаж {floor or '—'} · секция {section or '—'}", ACCENT, target=self.desk_results)
                grid = MDGridLayout(cols=2, adaptive_height=True, spacing=dp(8))
                self.desk_results.add_widget(grid)
                for desk in items:
                    busy = sum(1 for b in bookings if b["desk_id"] == desk["id"] and not b.get("is_cancelled"))
                    title = f"Стол {desk['number']}\n" + ("Занят / закрыт" if not desk["is_free"] else "Свободен")
                    button = self.button(title, partial(self.desk_detail, desk))
                    button.height = dp(80)
                    if not desk["is_free"]:
                        button.children[0].text_color = ERROR
                    grid.add_widget(button)
        else:
            for desk in desks:
                busy = sum(1 for b in bookings if b["desk_id"] == desk["id"] and not b.get("is_cancelled"))
                box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8), padding=dp(14), md_bg_color=SURFACE, radius=[dp(12)])
                self.text(f"Стол {desk['number']} · этаж {desk.get('floor', '—')}", target=box, size=16)
                self.text(f"Секция {desk.get('section', '—')} · розеток {desk.get('sockets', 0)}\n" +
                          ("Свободен в выбранное время" if desk["is_free"] else "Занят в выбранное время" if desk.get("is_operational", True) else "Вне эксплуатации") + (f" · броней за день: {busy}" if busy else ""), MUTED, target=box)
                self.action("Подробнее и забронировать", partial(self.desk_detail, desk), target=box)
                self.desk_results.add_widget(box)

    def desk_filters(self):
        self.page_start("filters", "Фильтры столов")
        fields = {key: self.field(title, self.filters[key], kind="int") for key, title in
                  (("floor", "Этаж"), ("section", "Секция"), ("sockets", "Минимум розеток"))}
        self.text("Необходимое оборудование", ACCENT)
        features = FeaturePicker(int(value) for value in self.filters["features"].split(",") if value)
        self.body.add_widget(features)
        sort = self.choice("Сортировать по", {"number": "Номеру", "floor": "Этажу", "sockets": "Розеткам"}, self.filters["sort"])
        order = self.choice("Порядок", {"asc": "По возрастанию", "desc": "По убыванию"}, self.filters["order"])
        def display(items):
            features.fill(items)
        self.action("Применить фильтры", lambda: self._apply_filters(fields, features, sort, order), True)
        self.action("Сбросить", self._reset_filters)
        self.job(self.api.get_desk_features, display, cache_key="features", cached=display)

    def _apply_filters(self, fields, features, sort, order):
        values = self.values(fields)
        if values is None:
            return
        feature_ids = sorted(features.selected)
        self.filters.update({key: str(value) if value is not None else "" for key, value in values.items()})
        self.filters.update(features=",".join(map(str, feature_ids)), sort=sort._choice, order=order._choice)
        self.desk_offset = 0
        self.desks()

    def _reset_filters(self):
        self.filters.update(search="", floor="", section="", sockets="", features="", sort="number", order="asc")
        self.desk_offset = 0
        self.desks()

    def desk_detail(self, desk):
        self.page_start("desk", f"Стол {desk['number']}", f"Этаж {desk.get('floor', '—')} · секция {desk.get('section', '—')}")
        features_text = self.text(f"Розеток: {desk.get('sockets', 0)}")
        self.text("Доступен для бронирования" if desk["is_free"] else "Есть брони в выбранное время" if desk.get("is_operational", True) else "Место выведено из эксплуатации", SUCCESS if desk["is_free"] else ERROR)
        availability = self.text("Расписание загружается…", MUTED)
        if desk.get("is_operational", desk["is_free"]):
            self.action("Забронировать на день", lambda: self.booking_form(desk), True)
            self.action("Забронировать на период", lambda: self.booking_form(desk, period=True))
        if self.user.get("role") in {"it_admin", "office_manager"}:
            self.action("Редактировать стол", lambda: self.desk_form(desk))
            self.action("Удалить стол", lambda: self.confirm("Удалить стол?", "Стол с историей бронирований удалять нельзя.",
                        lambda: self.mutate(lambda: self.api.admin_delete_desk(desk["id"]), lambda _: self.desks())))

        def display(items):
            schedule, dictionary = items["schedule"], items["features"]
            features_text.text = f"Розеток: {desk.get('sockets', 0)}\n" + ", ".join(feature["name"] for feature in dictionary if feature["id"] in desk.get("features", []))
            availability.text = "Расписание на " + self.filters["date"] + ":\n" + ("\n".join(
                f"{moment(item['planed_at'])} · {item['duration']} ч" for item in schedule) or "Бронирований нет")
        self.job(lambda: {"schedule": self.api.get_availability(desk_id=desk["id"], date=self.filters["date"]),
                          "features": self.api.get_desk_features()}, display,
                 cache_key=f"schedule:{desk['id']}:{self.filters['date']}", cached=display)

    def booking_form(self, desk, period=False, booking=None):
        self.page_start("booking_form", "Изменить бронь" if booking else "Бронь на период" if period else "Бронь на день",
                        f"Стол {desk['number']} · время офиса, формат ГГГГ-ММ-ДД ЧЧ:ММ")
        default = self.filters["date"] + " 09:00"
        fields = {"start": self.field("Дата и время начала", booking.get("planed_at", default) if booking else default,
                                      required=True, kind="datetime"),
                  "duration": self.field("Часов в день · 1–24", str(int(booking["duration"].split(":")[0])) if booking else "8", required=True, kind="int")}
        if period:
            fields["end"] = self.field("Конец периода · ГГГГ-ММ-ДД", self.filters["date"], required=True, kind="date")
        self.action("Сохранить бронь", lambda: self._booking_save(desk, fields, period, booking), True)
        self.action("Назад к столу", lambda: self.desk_detail(desk))

    def _booking_save(self, desk, fields, period, booking):
        values = self.values(fields)
        if not values:
            return
        if not 1 <= values["duration"] <= 24:
            self.note("Длительность — от 1 до 24 часов", error=True)
            return
        start = dt.datetime.fromisoformat(values["start"])
        if start.replace(tzinfo=None) < dt.datetime.now() - dt.timedelta(minutes=1):
            self.note("Начало брони должно быть в будущем", error=True)
            return
        if period and (dt.date.fromisoformat(values["end"]) < start.date() or (dt.date.fromisoformat(values["end"]) - start.date()).days > 92):
            self.note("Период — до 92 дней; конец не раньше начала", error=True)
            return

        def request():
            try:
                if booking:
                    result = self.api.update_booking(booking["id"], desk["id"], values["start"], values["duration"])
                elif period:
                    result = self.api.create_booking_period(desk["id"], values["start"], values["end"] + "T23:59:59", values["duration"])
                else:
                    result = self.api.create_booking(desk["id"], values["start"], values["duration"])
            except ApiError as error:
                if error.status_code != 409:
                    raise
                return {"conflict": str(error)}
            return result, self.api.last_meta.copy()

        def saved(data):
            if isinstance(data, dict) and "conflict" in data:
                self.filters["date"] = start.date().isoformat()
                self.desk_offset, self.map_mode = 0, True
                self.desks(booking_notice=data["conflict"], interval=(start, start + dt.timedelta(hours=values["duration"])))
                return
            result, meta = data
            self.bookings()
            skipped = meta.get("x-skipped-days")
            self.note((f"Создано броней: {len(result)}" if isinstance(result, list) else "Бронь сохранена") +
                      (". Занятые дни пропущены: " + skipped if skipped else ""), success=True)
        self.mutate(request, saved)

    def bookings(self, admin=False):
        self.page_start("admin_bookings" if admin else "bookings", "Все бронирования" if admin else "Мои бронирования", "Активные, будущие и история визитов")
        content = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(14))
        self.body.add_widget(content)

        def display(items):
            content.clear_widgets()
            if not items:
                self.text("Бронирований пока нет", MUTED, target=content)
            for item in sorted(items, key=lambda b: b["planed_at"], reverse=True):
                self.action(f"Стол #{item['desk_id']} · {moment(item['planed_at'])}" + (" · отменена" if item.get("is_cancelled") else ""),
                            partial(self.booking_detail, item, admin), target=content)
        self.job(self.api.get_bookings if admin else self.api.get_booking_history, display,
                 cache_key="all_bookings" if admin else "my_bookings", cached=display)

    def booking_detail(self, booking, admin=False):
        self.page_start("booking", f"Бронь №{booking['id']}", f"Стол #{booking['desk_id']} · {moment(booking['planed_at'])}")
        self.text("Длительность: " + booking["duration"] + " ч")
        if admin:
            self.text("Сотрудник #" + str(booking.get("user_id", "—")))
        cancelled = booking.get("is_cancelled", False)
        if cancelled:
            self.text("Отменена: " + (booking.get("cancellation_reason") or "пользователем"), ERROR)
        else:
            if admin:
                reason = self.field("Причина отмены", required=True)
                self.action("Отменить от имени администратора", lambda: self._admin_cancel(booking, reason))
            else:
                self.action("Изменить дату и длительность", lambda: self.booking_form(
                    {"id": booking["desk_id"], "number": booking["desk_id"]}, booking=booking))
                self.action("Отменить бронирование", lambda: self.confirm("Отменить бронь?", "Место станет доступно коллегам.",
                            lambda: self.mutate(lambda: self.api.cancel_booking(booking["id"]), lambda _r: self._cancelled(False))))
        self.action("К списку броней", lambda: self.bookings(admin))

    def _admin_cancel(self, booking, field):
        values = self.values({"reason": field})
        if values:
            self.confirm("Отменить бронь сотрудника?", values["reason"], lambda: self.mutate(
                lambda: self.api.admin_cancel_booking(booking["id"], values["reason"]), lambda _r: self._cancelled(True)))

    def _cancelled(self, admin):
        self.bookings(admin)
        self.note("Бронь отменена", success=True)

    def equipment(self, admin=False):
        self.page_start("admin_equipment" if admin else "equipment", "Реестр техники" if admin else "Моя техника",
                        "Оборудование, закреплённое за вами и вашим текущим столом" if not admin else "Учет, назначение и QR-коды")
        self.action("Сканировать QR-код", self.scanner)
        if admin:
            self.action("Добавить оборудование", self.equipment_form, True)
        content = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(12))
        self.body.add_widget(content)

        def display(items):
            content.clear_widgets()
            if not items:
                self.text("Оборудование не найдено", MUTED, target=content)
            for item in items:
                self.action(f"#{item['id']} · {item['type']} · {STATUSES.get(item['status'], item['status'])}",
                            partial(self.equipment_detail, item), target=content)
        self.job(self.api.admin_list_equipment if admin else self.api.get_my_equipment, display,
                 cache_key="equipment:all" if admin else "equipment:my", cached=display)

    def equipment_detail(self, item):
        inventory = item.get("inventory_number")
        serial = item.get("socket_serial")
        identity = f"Инв. №{inventory}" if inventory else f"Серийный №{serial}" if serial else "Оборудование"
        self.page_start("equipment_detail", item["type"], identity + " · " + STATUSES.get(item["status"], item["status"]))
        desk = "не закреплён"
        if item.get("desk_number") is not None:
            desk = "№" + str(item["desk_number"])
            if item.get("desk_floor") is not None:
                desk += " · этаж " + str(item["desk_floor"])
            if item.get("desk_section") is not None:
                desk += " · секция " + str(item["desk_section"])
        elif item.get("desk_id") is not None:
            desk = "место назначено"
        user = item.get("assigned_user_name") or ("сотрудник назначен" if item.get("assigned_user_id") is not None else "не закреплён")
        self.text("Модель: " + str(item.get("model") or "—") + "\nСерийный номер: " + str(item.get("socket_serial") or "—") +
                  "\nИнвентарный номер: " + str(item.get("inventory_number") or "—") +
                  f"\nСтол: {desk}\nСотрудник: {user}")
        self.action("Сообщить о проблеме", lambda: self.ticket_form(item), True)
        if self.user.get("role") == "it_admin":
            self.action("Изменить устройство", lambda: self.equipment_form(item))
            self.action("Назначить или снять с сотрудника", lambda: self.assign_form(item))
            self.action("QR-код для печати", lambda: self.qr_code(item))
            self.action("Удалить устройство", lambda: self.confirm("Удалить оборудование?", "Удаление доступно только для оборудования без заявок и назначений.",
                        lambda: self.mutate(lambda: self.api.admin_delete_equipment(item["id"]), lambda _r: self.equipment(True))))

    def ticket_form(self, equipment):
        self.page_start("ticket_form", "Сообщить о проблеме", equipment["type"] + f" · #{equipment['id']}")
        issue = self.choice("Тип неисправности", {"hardware": "Не включается / аппаратная неисправность", "software": "Программная ошибка", "replace": "Нужна замена", "other": "Другое"})
        description = self.field("Опишите проблему", required=True, multiline=True)
        photo = self.field("Фотография", "")
        photo.readonly = True
        self.action("Выбрать фото из галереи", lambda: self.choose_photo(lambda path: self._selected_photo(photo, path)))
        self.action("Сделать снимок", lambda: self.camera_photo(lambda path: self._selected_photo(photo, path)))
        self.action("Отправить заявку", lambda: self._ticket_save(equipment, issue, description, photo), True)

    def _ticket_save(self, equipment, issue, description, photo):
        values = self.values({"description": description})
        if not values:
            return
        if len(values["description"]) > 5000:
            self.note("Описание — не более 5000 символов", error=True)
            return
        path = getattr(photo, "_file_path", photo.text.strip())

        def request():
            url = self.api.upload_photo(path)["photo_url"] if path else None
            return self.api.create_ticket(equipment["id"], f"{issue.text}: {values['description']}", url)

        def sent(item):
            self.ticket_detail(item)
            self.note("Заявка отправлена в IT-отдел", success=True)
        self.mutate(request, sent, "Отправляем заявку и фото…")

    def tickets(self, admin=False):
        self.page_start("admin_tickets" if admin else "tickets", "Заявки IT-отдела" if admin else "Мои обращения")
        content = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(12))
        self.body.add_widget(content)

        def display(items):
            content.clear_widgets()
            if not items:
                self.text("Заявок пока нет. Создайте обращение из карточки устройства", MUTED, target=content)
            for item in sorted(items, key=lambda t: t["created_at"], reverse=True):
                self.action(f"#{item['id']} · {STATUSES.get(item['status'], item['status'])} · устройство #{item['equipment_id']}",
                            partial(self.ticket_detail, item), target=content)
        self.job(self.api.admin_list_tickets if admin else self.api.get_my_tickets, display,
                 cache_key="tickets:all" if admin else "tickets:my", cached=display)

    def ticket_detail(self, item):
        self.page_start("ticket_detail", f"Обращение №{item['id']}", STATUSES.get(item["status"], item["status"]) + " · " + moment(item["created_at"]))
        self.text(item["description"])
        self.text("Устройство #" + str(item["equipment_id"]), MUTED)
        if item.get("photo_url"):
            self.action("Посмотреть фото", lambda: self.private_photo(item["photo_url"]))
        events = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(6))
        self.body.add_widget(events)
        if self.user.get("role") == "it_admin":
            status = self.choice("Новый статус", {key: STATUSES[key] for key in ("new", "in_progress", "resolved", "closed")}, item["status"])
            self.action("Обновить статус", lambda: self.mutate(lambda: self.api.admin_update_ticket_status(item["id"], status._choice), self.ticket_detail), True)
            comment = self.field("Комментарий IT-специалиста", required=True, multiline=True)
            self.action("Добавить комментарий", lambda: self._comment(item, comment))

        def display(history):
            events.clear_widgets()
            self.text("История обращения", ACCENT, target=events)
            if not history:
                self.text("Заявка создана", MUTED, target=events)
            for event in history:
                raw = event.get("text") or event.get("comment") or event.get("status") or "Обновление"
                text_ = STATUSES.get(raw, raw)
                self.text(f"{moment(event.get('created_at'))} · {text_}", MUTED, target=events, size=13)
        self.job(lambda: self.api.ticket_events(item["id"]), display, cache_key=f"ticket-events:{item['id']}", cached=display)

    def _comment(self, item, field):
        values = self.values({"text": field})
        if values:
            self.mutate(lambda: self.api.comment_ticket(item["id"], values["text"]), lambda _r: self.ticket_detail(item))

    def notifications(self):
        self.page_start("notifications", "Уведомления", "Изменения бронирований и статусов обращений")
        content = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(12))
        self.body.add_widget(content)

        def display(items):
            content.clear_widgets()
            if not items:
                self.text("Новых уведомлений нет", MUTED, target=content)
            for item in items:
                self.text(moment(item.get("created_at")) + " · " + item.get("text", ""), MUTED if item.get("is_read") else TEXT, target=content)
                if not item.get("is_read"):
                    self.action("Отметить прочитанным", partial(self._read_notification, item["id"]), target=content)
        self.job(self.api.get_notifications, display, cache_key="notifications", cached=display)

    def _read_notification(self, notification_id):
        self.mutate(lambda: self.api.read_notification(notification_id), lambda _r: self.notifications())

    def admin(self):
        self.page_start("admin", "Управление офисом", ROLES.get(self.user.get("role"), ""))
        self.action("Добавить рабочее место", self.desk_form, True)
        self.action("Рабочие места и параметры", self.desks)
        self.action("Бронирования сотрудников", lambda: self.bookings(True))
        self.action("Аналитика и отчёт", self.analytics)
        if self.user.get("role") == "it_admin":
            self.action("Реестр оборудования", lambda: self.equipment(True))
            self.action("Входящие обращения", lambda: self.tickets(True))
            self.action("Сотрудники и роли", self.users)

    def desk_form(self, item=None):
        item = item or {}
        self.page_start("desk_form", "Изменить рабочее место" if item else "Новое рабочее место")
        fields = {key: self.field(title, item.get(key), required=True, kind="int") for key, title in
                  (("number", "Номер стола"), ("floor", "Этаж"), ("section", "Секция"), ("sockets", "Число розеток"))}
        available = self.choice("Доступность", {True: "В эксплуатации", False: "Временно недоступен"}, item.get("is_operational", item.get("is_free", True)))
        self.text("Комплектация места", ACCENT)
        features = FeaturePicker(item.get("features", []))
        self.body.add_widget(features)
        self.last_feature_picker = features
        self.action("Сохранить стол", lambda: self._desk_save(item, fields, available, features), True)
        self.job(self.api.get_desk_features, features.fill, cache_key="features", cached=features.fill)

    def _desk_save(self, item, fields, available, features):
        values = self.values(fields)
        if not values:
            return
        if any(values[key] <= 0 for key in ("number", "floor", "section")):
            self.note("Номер стола, этаж и секция должны быть положительными", error=True)
            return
        values["features"] = sorted(features.selected)
        values["is_free"] = available._choice
        self.mutate(lambda: self.api.admin_update_desk(item["id"], **values) if item else self.api.admin_create_desk(**values),
                    lambda desk: self._desk_saved(desk))

    def _desk_saved(self, desk):
        self.desk_detail(desk)
        self.note("Рабочее место сохранено", success=True)

    def equipment_form(self, item=None):
        item = item or {}
        self.page_start("equipment_form", "Изменить оборудование" if item else "Добавить оборудование")
        fields = {"type": self.field("Тип оборудования", item.get("type"), required=True),
                  "model": self.field("Модель", item.get("model"), required=not bool(item)),
                  "socket_serial": self.field("Серийный номер", item.get("socket_serial"), required=True),
                  "inventory_number": self.field("Инвентарный номер", item.get("inventory_number"), required=not bool(item))}
        self._equipment_desk_choice = None
        desk_holder = MDBoxLayout(orientation="vertical", adaptive_height=True)
        self.body.add_widget(desk_holder)
        if item:
            self.text("Закрепление меняется в отдельной форме назначения", MUTED, target=desk_holder)
            self.action("Изменить закрепление", lambda: self.assign_form(item), target=desk_holder)
        socket = self.choice("Требуется розетка", {True: "Да", False: "Нет"}, item.get("socket_needed", False))
        statuses = {k: STATUSES[k] for k in ("in_stock", "assigned", "in_repair", "written_off")}
        status = self.choice("Статус", statuses, item.get("status", "in_stock"))
        self.action("Сохранить устройство", lambda: self._equipment_save(item, fields, socket, status), True)

        def display(desks):
            desk_holder.clear_widgets()
            options = {None: "Без закрепления за столом"}
            options.update({desk["id"]: f"Стол {desk['number']} · этаж {desk.get('floor', '—')}" for desk in desks})
            self._equipment_desk_choice = self.choice("Рабочее место", options, item.get("desk_id"), target=desk_holder)
        if not item:
            self.job(lambda: self.api.get_desks(limit=200), display, cache_key="desks:assign", cached=display)

    def _equipment_save(self, item, fields, socket, status):
        values = self.values(fields)
        if not values:
            return
        values = {key: value for key, value in values.items() if value is not None}
        desk_choice = getattr(self, "_equipment_desk_choice", None)
        if desk_choice is not None and desk_choice._choice is not None:
            values["desk_id"] = desk_choice._choice
        values.update(socket_needed=socket._choice, status=status._choice)

        def request():
            return self.api.admin_update_equipment(item["id"], **values) if item else self.api.admin_create_equipment(**values)

        def saved(equipment):
            self.equipment_detail(equipment)
            self.note("Оборудование сохранено", success=True)
        self.mutate(request, saved)

    def assign_form(self, item):
        self.page_start("assign", "Закрепление техники", item["type"] + f" · #{item['id']}")
        holder = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(14))
        self.body.add_widget(holder)

        def display(data):
            holder.clear_widgets()
            people = {None: "Снять с сотрудника"}
            people.update({user["id"]: f"{user['full_name']} · {user['email']}" for user in data["users"]})
            desks = {None: "Без закрепления за столом"}
            desks.update({desk["id"]: f"Стол {desk['number']} · этаж {desk.get('floor', '—')}" for desk in data["desks"]})
            fields = {"assigned_user_id": self.choice("Сотрудник", people, item.get("assigned_user_id"), target=holder),
                      "desk_id": self.choice("Рабочее место", desks, item.get("desk_id"), target=holder)}
            self.action("Сохранить назначение", lambda: self._assign_save(item, fields), True, target=holder)
        self.job(lambda: {"users": self.api.admin_list_users(), "desks": self.api.get_desks(limit=200)}, display,
                 cache_key="assignment-options", cached=display)

    def _assign_save(self, item, fields):
        values = {key: field._choice for key, field in fields.items()}
        self.mutate(lambda: self.api.admin_assign_equipment(item["id"], **values), self.equipment_detail)

    def users(self):
        self.page_start("users", "Сотрудники и роли", "Повышение прав доступно IT-администратору")
        content = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(12))
        self.body.add_widget(content)

        def display(users):
            content.clear_widgets()
            for user in users:
                self.text(f"#{user['id']} · {user['full_name']}\n{user['email']}", target=content)
                role = self.choice("Роль", ROLES, user["role"], target=content)
                self.action("Сохранить роль", partial(self._role_save, user, role), target=content)
        self.job(self.api.admin_list_users, display)

    def _role_save(self, user, role):
        self.confirm("Изменить права сотрудника?", f"{user['full_name']}: {role.text}", lambda: self.mutate(
            lambda: self.api.admin_update_role(user["id"], role._choice), lambda _r: self.users()))

    def qr_code(self, equipment):
        self.page_start("qr", "QR-код оборудования", equipment["type"] + f" · #{equipment['id']}")

        def display(data):
            encoded = data.get("qr_code_base64", "")
            if not encoded:
                self.note("Сервер не вернул изображение QR-кода", error=True)
                return
            path = self.state.media_path(f"equipment-{equipment['id']}-qr.png")
            path.write_bytes(base64.b64decode(encoded.split(",")[-1]))
            self.body.add_widget(Image(source=str(path), size_hint_y=None, height=dp(280), fit_mode="contain"))
            self.text(data.get("qr_code_url", ""), MUTED)
            self.action("Поделиться / отправить на печать", lambda: self._share(path, "image/png"), True)
            self.text("Изображение сохранено в приватном кэше до выхода из аккаунта", MUTED, size=12)
        self.job(lambda: self.api.admin_generate_qr(equipment["id"]), display)

    def _share(self, path, mime):
        try:
            self.device.share(path, mime)
            self.note("Файл готов для отправки и печати", success=True)
        except Exception:
            self.note("Не удалось открыть системное меню отправки", error=True)

    def analytics(self):
        self.page_start("analytics", "Аналитика офиса", "Загрузка рабочих мест и частые неисправности за выбранный период")
        today = dt.date.today()
        fields = {"start_date": self.field("Начало · ГГГГ-ММ-ДД", (today - dt.timedelta(days=29)).isoformat(), kind="date", required=True),
                  "end_date": self.field("Конец · ГГГГ-ММ-ДД", today.isoformat(), kind="date", required=True)}
        self.action("Построить отчёт", lambda: self._analytics_load(fields), True)
        self.action("Сохранить отчёт на сервере", lambda: self._save_report(fields))
        self.action("Архив отчётов", self.reports)
        self.analytics_results = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(14))
        self.body.add_widget(self.analytics_results)
        self._analytics_load(fields)

    def _analytics_load(self, fields):
        values = self.values(fields)
        if not values:
            return
        if values["end_date"] < values["start_date"]:
            self.note("Конец периода должен быть не раньше начала", error=True)
            return

        def display(report):
            target = self.analytics_results
            target.clear_widgets()
            rate = float(report.get("occupancy_rate", 0))
            fraction = rate / 100 if rate > 1 else rate
            self.text(report.get("period", ""), MUTED, target=target)
            self.text(f"Загрузка рабочих мест: {fraction:.1%}", target=target, size=18)
            target.add_widget(OccupancyBar(fraction))
            self.text(f"Бронирований: {report.get('total_bookings', 0)}", target=target)
            issues = report.get("most_frequent_issues", [])
            self.text("Частые неисправности", ACCENT, target=target)
            maximum = max([v.get("ticket_count", 0) for v in issues] + [1])
            for row in issues:
                self.text(f"{row['equipment_type']} · {row['ticket_count']}", MUTED, target=target)
                target.add_widget(OccupancyBar(row["ticket_count"] / maximum))
            self.action("Экспорт отчёта CSV", lambda: self._export_report(report), target=target)
        self.job(lambda: self.api.admin_get_analytics(**values), display, cache_key="analytics:" + repr(values), cached=display)

    def _export_report(self, report):
        path = self.state.media_path("office-report.csv")
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["Период", "Загрузка", "Всего бронирований"])
            writer.writerow([report.get("period"), report.get("occupancy_rate"), report.get("total_bookings")])
            writer.writerow(["Оборудование", "Заявок"])
            for issue in report.get("most_frequent_issues", []):
                # CSV cells are text, not executable spreadsheet formulas.
                name = str(issue["equipment_type"])
                writer.writerow(["'" + name if name[:1] in {"=", "+", "-", "@"} else name, issue["ticket_count"]])
        self._share(path, "text/csv")

    def _save_report(self, fields):
        values = self.values(fields)
        if values:
            if values["end_date"] < values["start_date"]:
                self.note("Конец периода не должен быть раньше начала", error=True)
                return
            self.mutate(lambda: self.api.create_report(**values), lambda result: self.note(f"Отчёт №{result['id']} сохранён на сервере", success=True))

    def reports(self):
        self.page_start("reports", "Архив отчётов", "Сохранённые снимки аналитики")
        content = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(12))
        self.body.add_widget(content)

        def display(items):
            content.clear_widgets()
            if not items:
                self.text("Сохранённых отчётов пока нет", MUTED, target=content)
            for item in items:
                self.text(f"Отчёт №{item['id']} · {moment(item['generated_at'])}", target=content)
                self.action("Экспортировать JSON", partial(self._export_snapshot, item), target=content)
        self.job(self.api.list_reports, display, cache_key="reports", cached=display)

    def _export_snapshot(self, report):
        import json
        path = self.state.media_path(f"office-report-{report['id']}.json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self._share(path, "application/json")

    def confirm(self, title, explanation, confirmed):
        self.page_start("confirm", title, explanation)
        self.action("Подтвердить", confirmed, True)
        self.action("Отмена", self.home)

    def choose_photo(self, callback):
        if platform == "android":
            try:
                self.device.choose_photo_android(callback, lambda message: self.note(message, error=True))
            except Exception:
                self.note("Не удалось открыть галерею", error=True)
            return
        from kivy.uix.filechooser import FileChooserListView
        box = MDBoxLayout(orientation="vertical", spacing=dp(8), padding=dp(12))
        chooser = FileChooserListView(path=str(Path.home()), filters=["*.jpg", "*.jpeg", "*.png", "*.webp"])
        box.add_widget(chooser)
        popup = Popup(title="Выберите фотографию", content=box, size_hint=(0.95, 0.85))

        def picked():
            if chooser.selection:
                path = chooser.selection[0]
                popup.dismiss()
                callback(path)
        box.add_widget(self.button("Выбрать", picked, primary=True))
        box.add_widget(self.button("Закрыть", popup.dismiss))
        popup.open()

    def _selected_photo(self, field, path):
        if not Path(path).is_file() or Path(path).stat().st_size > 5_000_000:
            self.note("Выберите изображение размером до 5 МБ", error=True)
            return
        field._file_path = str(path)
        field.text = Path(path).name

    def camera_photo(self, callback):
        def granted(allowed):
            if not allowed:
                self.note("Разрешите доступ к камере в настройках телефона", error=True)
                return
            try:
                from kivy.uix.camera import Camera
                box = MDBoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
                camera = Camera(index=0, resolution=(640, 480), play=True)
                box.add_widget(camera)
                popup = Popup(title="Фотография", content=box, size_hint=(0.96, 0.9))

                def take():
                    if not camera.texture:
                        return
                    path = self.state.media_path("camera-photo.png")
                    camera.texture.save(str(path))
                    popup.dismiss()
                    callback(str(path))

                def release(*_):
                    camera.play = False
                    provider = getattr(camera, "_camera", None)
                    if provider is not None and hasattr(provider, "_release_camera"):
                        provider._release_camera()
                    if getattr(self, "_photo_popup", None) is popup:
                        self._photo_popup = None
                box.add_widget(self.button("Сделать снимок", take, primary=True))
                box.add_widget(self.button("Закрыть", popup.dismiss))
                popup.bind(on_dismiss=release)
                self._photo_popup = popup
                popup.open()
            except Exception:
                self.note("Камера недоступна. Выберите фотографию из галереи", error=True)
        self.device.camera_permission(granted)

    def scanner(self):
        self.page_start("scanner", "Сканировать QR-код", "Наведите камеру на наклейку оборудования. При плохом освещении введите номер вручную.")
        self.scanner_container = MDBoxLayout(orientation="vertical", size_hint_y=None, height=dp(280))
        self.body.add_widget(self.scanner_container)
        code = self.field("Код или ID устройства", required=True)
        self.action("Открыть устройство", lambda: self._open_code(code.text), True)
        self.action("Включить камеру", lambda: self.device.camera_permission(self._start_scanner))
        self.device.camera_permission(self._start_scanner)

    def _start_scanner(self, allowed):
        if self.page != "scanner" or self.camera is not None:
            return
        if not allowed:
            self.note("Камера запрещена. Введите код вручную", error=True)
            return
        try:
            import cv2  # OpenCV recipe is included in the Android build.
            from kivy.uix.camera import Camera
            self._qr_detector = cv2.QRCodeDetector()
            self.camera = Camera(index=0, resolution=(640, 480), play=True)
            self.scanner_container.add_widget(self.camera)
            self._events.append(Clock.schedule_interval(self._scan_frame, 0.8))
            self.note("Камера включена. Держите QR-код в центре")
        except Exception:
            self._stop_camera()
            self.note("Сканер недоступен на этом устройстве. Введите код вручную", error=True)

    def _scan_frame(self, _dt):
        if self._scan_busy or self.busy or self.camera is None or self.camera.texture is None:
            return
        texture = self.camera.texture
        pixels, size = texture.pixels, texture.size
        generation = self.generation
        self._scan_busy = True

        def detect():
            import numpy as np
            import cv2
            frame = np.frombuffer(pixels, dtype=np.uint8).reshape((size[1], size[0], 4))
            bgr = cv2.cvtColor(frame, cv2.COLOR_RGBA2BGR)
            # OpenGL texture rows are commonly bottom-up; some camera providers
            # already correct them, so accept either orientation.
            code, _points, _rectified = self._qr_detector.detectAndDecode(cv2.flip(bgr, 0))
            if not code:
                code, _points, _rectified = self._qr_detector.detectAndDecode(bgr)
            return code

        def done(code):
            self._scan_busy = False
            if code and generation == self.generation:
                self._stop_camera()
                self._open_code(code)

        def failed(_error):
            self._scan_busy = False
        if not self.worker.submit(detect, done, failed):
            self._scan_busy = False

    def _open_code(self, value):
        value = value.strip()
        if value.isdigit():
            equipment_id = int(value)
        else:
            match = re.fullmatch(r"(?:https?://[^\s/]+)?(?:/api/v1)?/equipment/(\d+)/?", value)
            if not match:
                self.note("QR-код не относится к оборудованию Office Desk & Gear", error=True)
                return
            equipment_id = int(match.group(1))
        if equipment_id <= 0:
            self.note("ID устройства должен быть положительным", error=True)
            return
        self.job(lambda: self.api.get_equipment(equipment_id), self.equipment_detail, "Открываем устройство…")

    def _stop_camera(self):
        if self.camera is not None:
            self.camera.play = False
            provider = getattr(self.camera, "_camera", None)
            if provider is not None and hasattr(provider, "_release_camera"):
                provider._release_camera()
            if self.camera.parent:
                self.camera.parent.remove_widget(self.camera)
            self.camera = None
        popup = getattr(self, "_photo_popup", None)
        if popup:
            popup.dismiss()

    def private_photo(self, url):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        path = self.state.media_path("photo-" + digest + ".jpg")

        def display(filename):
            image = Image(source=filename, fit_mode="contain")
            popup = Popup(title="Фотография · назад для закрытия", content=image, size_hint=(0.96, 0.9))
            popup.open()
        if path.is_file():
            display(str(path))
        else:
            self.job(lambda: self.api.download_private(url, path), display, "Загружаем фотографию…")

    def _on_keyboard(self, _window, key, *_args):
        if key != 27:
            return False
        if self.busy:
            self.note("Дождитесь завершения текущего запроса")
            return True
        for menu in self._menus:
            if menu.parent:
                menu.dismiss()
                return True
        focused = [widget for widget in self.body.walk() if isinstance(widget, MDTextField) and widget.focus]
        if focused:
            for widget in focused:
                widget.focus = False
            return True
        if self.user and self.page != "home":
            self.home()
            return True
        if not self.user and self.page != "login":
            self.login()
            return True
        return False

    def on_pause(self):
        self._pause_camera_mode = self.page == "scanner" and self.camera is not None
        self._cleanup_page()
        self.model.pause()
        return True

    def on_resume(self):
        self._configure_system_bars()
        if self._pause_camera_mode and self.page == "scanner":
            self.device.camera_permission(self._start_scanner)
        elif self.user and not self.busy:
            # Refresh the current user after returning to foreground; revoked sessions clear cache.
            self.job(self.api.get_me, self._resume_user, "Обновляем сессию…")

    def _resume_user(self, user):
        self.user = user
        self.state.update_user(user)

    def on_stop(self):
        self.stopped = True
        Window.unbind(on_keyboard=self._on_keyboard)
        self._cleanup_page()
        self.device.close()
        self.model.close()
