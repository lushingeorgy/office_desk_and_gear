import threading
from functools import partial

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.utils import get_color_from_hex, platform
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonIcon, MDButtonText
from kivymd.uix.label import MDIcon, MDLabel
from kivymd.uix.menu import MDDropdownMenu
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.textfield import MDTextField, MDTextFieldHintText, MDTextFieldTrailingIcon

from client.services.api_client import ApiClient, ApiError

BACKGROUND = get_color_from_hex("#0C1017")
SURFACE = get_color_from_hex("#172234")
BORDER = get_color_from_hex("#3C4A60")
TEXT = get_color_from_hex("#EEF2F8")
MUTED = get_color_from_hex("#9CAAC0")
ACCENT = get_color_from_hex("#8BADFF")
ERROR = get_color_from_hex("#F1959B")
SUCCESS = get_color_from_hex("#88CCAC")
JOB_ROLES = {
    "Сотрудник": "employee",
    "Офис-менеджер": "office_manager",
    "IT-администратор": "it_admin",
}


def label(text, height, size=14, color=TEXT):
    return MDLabel(text=text, size_hint_y=None, height=dp(height),
                   theme_font_size="Custom", font_size=sp(size),
                   theme_text_color="Custom", text_color=color)


class RegistrationScreen(MDScreen):
    def __init__(self, api=None, **kwargs):
        super().__init__(md_bg_color=BACKGROUND, **kwargs)
        self.api = api or ApiClient()
        self._stopped = False
        self._worker = None
        self._focus_event = None
        self.scroll = MDScrollView(do_scroll_x=False, bar_width=dp(2))
        self.anchor = AnchorLayout(anchor_y="top", padding=dp(24), size_hint_y=None)
        self.form = MDBoxLayout(orientation="vertical", adaptive_height=True, size_hint_x=None)
        self.anchor.add_widget(self.form)
        self.scroll.add_widget(self.anchor)
        self.add_widget(self.scroll)

        brand = MDBoxLayout(spacing=dp(12), size_hint_y=None, height=dp(48))
        mark = MDBoxLayout(size_hint=(None, None), size=(dp(48), dp(48)),
                           md_bg_color=SURFACE, radius=[dp(12)], padding=dp(12))
        mark.add_widget(MDIcon(icon="office-building-outline", theme_icon_color="Custom",
                               icon_color=ACCENT, pos_hint={"center_x": 0.5, "center_y": 0.5}))
        brand.add_widget(mark)
        brand_text = MDBoxLayout(orientation="vertical", padding=(0, dp(4)))
        brand_text.add_widget(label("Office Desk & Gear", 22, 16))
        brand_text.add_widget(label("Рабочее пространство", 18, 12, MUTED))
        brand.add_widget(brand_text)
        self.form.add_widget(brand)
        self.form.add_widget(MDBoxLayout(size_hint_y=None, height=dp(24)))
        self.form.add_widget(label("РЕГИСТРАЦИЯ СОТРУДНИКА", 18, 11, ACCENT))
        self.form.add_widget(label("Создать аккаунт", 44, 28))
        self.form.add_widget(label("Заполните данные рабочего профиля.", 40, 14, MUTED))
        self.form.add_widget(MDBoxLayout(size_hint_y=None, height=dp(12)))

        fields_box = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(20))
        self.fields = {}
        for key, title in (("full_name", "ФИО"), ("job_title", "Должность"), ("email", "Рабочая почта"),
                           ("password", "Пароль"), ("confirm_password", "Повторите пароль")):
            secret = key in {"password", "confirm_password"}
            field = MDTextField(
                MDTextFieldHintText(text=title, text_color_normal=MUTED, text_color_focus=ACCENT),
                mode="outlined", size_hint_y=None, height=dp(56), radius=[dp(10)],
                multiline=False, write_tab=False, password=secret,
                input_type="mail" if key == "email" else "text",
                keyboard_suggestions=not secret and key != "email",
                theme_text_color="Custom", text_color_normal=TEXT, text_color_focus=TEXT,
                theme_line_color="Custom", line_color_normal=BORDER, line_color_focus=ACCENT,
                error_color=ERROR,
            )
            field.bind(text=self._edited, focus=self._on_focus,
                       on_text_validate=partial(self._next_field, len(self.fields)))
            self.fields[key] = field
            if key == "job_title":
                field.readonly = True
                field.keyboard_mode = "managed"
                field.add_widget(MDTextFieldTrailingIcon(icon="chevron-down"))
                field.bind(on_touch_down=self._job_touch)
            fields_box.add_widget(field)
        self.job_menu = MDDropdownMenu(
            caller=self.fields["job_title"], position="bottom", md_bg_color=SURFACE,
            items=[{"text": title, "height": dp(56), "on_release": partial(self.select_job, title)}
                   for title in JOB_ROLES],
        )
        self.form.add_widget(fields_box)
        self.form.add_widget(label("Пароль — не менее 6 символов", 30, 12, MUTED))

        self.password_label = MDButtonText(text="Показать пароли", theme_text_color="Custom", text_color=MUTED)
        self.password_icon = MDButtonIcon(icon="eye-outline", theme_icon_color="Custom", icon_color=MUTED)
        self.password_toggle = MDButton(self.password_icon, self.password_label, style="text",
                                        size_hint_y=None, height=dp(48))
        self.password_toggle.bind(on_release=self._toggle_password)
        self.form.add_widget(self.password_toggle)
        self.form.add_widget(MDBoxLayout(size_hint_y=None, height=dp(8)))

        self.submit_label = MDButtonText(text="Создать аккаунт", theme_text_color="Custom",
                                         text_color=BACKGROUND, pos_hint={"center_x": 0.5, "center_y": 0.5})
        self.submit_button = MDButton(self.submit_label, style="filled", theme_width="Custom",
                                       size_hint=(1, None), height=dp(52), radius=[dp(12)],
                                       theme_bg_color="Custom", md_bg_color=ACCENT)
        self.submit_button.bind(on_release=self.submit)
        self.form.add_widget(self.submit_button)
        self.status = label("", 56, 13, MUTED)
        self.status.bind(texture_size=lambda item, size: setattr(item, "height", max(dp(56), size[1] + dp(16))))
        self.form.add_widget(self.status)
        self.bind(size=self._layout)
        self.form.bind(height=self._layout)
        self._layout()

    def _layout(self, *_args):
        self.form.width = max(0, min(dp(440), self.width - dp(48)))
        self.anchor.height = max(self.height, self.form.height + dp(48))

    def _edited(self, field, _text):
        field.error = False

    def _job_touch(self, field, touch):
        if field.disabled or not field.collide_point(*touch.pos) or getattr(touch, "is_mouse_scrolling", False):
            return False
        self.open_jobs()
        return True

    def open_jobs(self, *_args):
        if self._stopped or self.fields["job_title"].disabled or self.job_menu.parent:
            return
        for field in self.fields.values():
            field.focus = False
        self.scroll.scroll_to(self.fields["job_title"], padding=dp(24), animate=False)
        self.job_menu.width = self.fields["job_title"].width
        self.job_menu.open()

    def select_job(self, title, *_args):
        if title in JOB_ROLES:
            self.fields["job_title"].text = title
            self.job_menu.dismiss()

    def _on_focus(self, field, focused):
        if self._focus_event:
            self._focus_event.cancel()
        if focused:
            self._focus_event = Clock.schedule_once(partial(self._reveal_field, field), 0.2)

    def _reveal_field(self, field, _dt):
        if not self._stopped and field.focus:
            self.scroll.scroll_to(field, padding=dp(24), animate=False)

    def _next_field(self, index, *_args):
        fields = list(self.fields.values())
        fields[index].focus = False
        if index + 1 < len(fields):
            if fields[index + 1] is self.fields["job_title"]:
                Clock.schedule_once(self.open_jobs)
            else:
                Clock.schedule_once(lambda _dt: setattr(fields[index + 1], "focus", True))
        else:
            self.submit()

    def _toggle_password(self, *_args):
        hidden = not self.fields["password"].password
        for name in ("password", "confirm_password"):
            self.fields[name].password = hidden
        self.password_label.text = "Показать пароли" if hidden else "Скрыть пароли"
        self.password_icon.icon = "eye-outline" if hidden else "eye-off-outline"

    def _set_busy(self, busy):
        self.submit_button.disabled = busy
        self.submit_label.text = "Создание аккаунта…" if busy else "Создать аккаунт"
        self.password_toggle.disabled = busy
        for field in self.fields.values():
            field.disabled = busy
            if busy:
                field.focus = False

    def _show_error(self, message, names):
        self.status.text_color = ERROR
        self.status.text = message
        for name in names:
            self.fields[name].error = True
        self.fields[names[0]].focus = True

    def submit(self, *_args):
        if self.submit_button.disabled or self._stopped:
            return
        values = {name: field.text if name in {"password", "confirm_password"} else field.text.strip()
                  for name, field in self.fields.items()}
        missing = [name for name, value in values.items() if not value]
        if missing:
            self._show_error("Повторите пароль" if missing == ["confirm_password"] else "Заполните все поля", missing)
            return
        if values["job_title"] not in JOB_ROLES:
            self._show_error("Выберите должность из списка", ["job_title"])
            return
        if len(values["password"]) < 6:
            self._show_error("Пароль должен содержать не менее 6 символов", ["password"])
            return
        if values["password"] != values.pop("confirm_password"):
            self._show_error("Пароли не совпадают", ["confirm_password"])
            return
        values["role"] = JOB_ROLES[values["job_title"]]
        self._set_busy(True)
        self.status.text_color = MUTED
        self.status.text = "Сохраняем данные вашего профиля…"
        self._worker = threading.Thread(target=self._register, args=(values,), daemon=True)
        self._worker.start()

    def _register(self, values):
        try:
            user = self.api.register(**values)
            message, success = f"Регистрация завершена. Добро пожаловать, {user['full_name']}!", True
        except ApiError as error:
            message, success = str(error), False
        except Exception:
            message, success = "Не удалось обработать ответ сервера", False
        if not self._stopped:
            Clock.schedule_once(partial(self._finish, message, success))

    def _finish(self, message, success, _dt):
        if self._stopped:
            return
        self._set_busy(False)
        self.status.text_color = SUCCESS if success else ERROR
        self.status.text = message
        if success:
            for field in self.fields.values():
                field.text = ""
                field.error = False
            for name in ("password", "confirm_password"):
                self.fields[name].password = True
            self.password_label.text = "Показать пароли"
            self.password_icon.icon = "eye-outline"
        Clock.schedule_once(lambda _dt: self.scroll.scroll_to(self.submit_button, padding=dp(24), animate=False))

    def close(self):
        self._stopped = True
        self.job_menu.dismiss()
        if self._focus_event:
            self._focus_event.cancel()


class OfficeDeskGearApp(MDApp):
    title = "Office Desk & Gear"

    def build(self):
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Blue"
        Window.clearcolor = BACKGROUND

        Window.softinput_mode = "below_target"
        if platform not in {"android", "ios"}:
            Window.size = (dp(400), dp(840))
            Window.minimum_width, Window.minimum_height = dp(320), dp(480)
        return RegistrationScreen()

    def on_start(self):
        Window.bind(on_keyboard=self._on_keyboard)

    def _on_keyboard(self, _window, key, *_args):
        if key == 27:
            if self.root.job_menu.parent:
                self.root.job_menu.dismiss()
                return True
            focused = [field for field in self.root.fields.values() if field.focus]
            if focused:
                for field in focused:
                    field.focus = False
                return True
        return False

    def on_stop(self):
        Window.unbind(on_keyboard=self._on_keyboard)
        self.root.close()
