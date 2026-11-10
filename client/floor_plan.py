"""One complete floor with a separate, always reachable floor selector."""
from functools import partial

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.effects.scroll import ScrollEffect
from kivy.metrics import dp, sp
from kivy.uix.scrollview import ScrollView
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDButton, MDButtonText, MDIconButton
from kivymd.uix.gridlayout import MDGridLayout

from client.registration import ACCENT, BACKGROUND, MUTED, SURFACE, label


class FloorPlan(MDBoxLayout):
    def __init__(self, desks, selected_floor, status, open_desk, on_floor, **kwargs):
        super().__init__(spacing=dp(8), size_hint_y=None, **kwargs)
        self.desks = desks
        self.floors = sorted({desk["floor"] for desk in desks if desk.get("floor") is not None})
        self.status, self.open_desk, self.on_floor = status, open_desk, on_floor
        self.selected_floor = selected_floor if selected_floor in self.floors else self.floors[0]
        self.plan_scroll = ScrollView(do_scroll_x=False, effect_cls=ScrollEffect)
        self.content = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(8))
        self.plan_scroll.add_widget(self.content)
        self.add_widget(self.plan_scroll)
        self.rail = MDBoxLayout(orientation="vertical", size_hint_x=None, width=dp(56), spacing=dp(6))
        self.add_widget(self.rail)
        self.up = self._arrow("chevron-up", 1)
        self.rail.add_widget(self.up)
        numbers = ScrollView(do_scroll_x=False, effect_cls=ScrollEffect)
        self.floor_scroll = numbers
        self.numbers = MDBoxLayout(orientation="vertical", adaptive_height=True, spacing=dp(6))
        numbers.add_widget(self.numbers)
        self.rail.add_widget(numbers)
        self.floor_buttons = {}
        for floor in reversed(self.floors):
            button = self._button(str(floor), partial(self.select_floor, floor), dp(48))
            button._floor = floor
            self.floor_buttons[floor] = button
            self.numbers.add_widget(button)
        self.down = self._arrow("chevron-down", -1)
        self.rail.add_widget(self.down)
        self._resize()
        self.bind(width=self._resize)
        self.select_floor(self.selected_floor)

    def _resize(self, *_):
        self.height = max(dp(300), min(dp(540), Window.height * .60))

    @staticmethod
    def _button(caption, callback, height, color=ACCENT):
        text = MDButtonText(text=caption, theme_text_color="Custom", text_color=color,
                            theme_font_size="Custom", font_size=sp(13), halign="center")
        button = MDButton(text, style="text", theme_width="Custom", size_hint=(1, None),
                          height=height, theme_bg_color="Custom", md_bg_color=SURFACE,
                          radius=[dp(10)])
        text.adaptive_size = False
        text.bind(texture_size=lambda w, size: setattr(w, "height", size[1]))
        def fit_caption(*_):
            text.width = max(dp(1), button.width - dp(8))
            text.center_x = button.width / 2
        button.bind(width=fit_caption)
        text.bind(x=fit_caption)
        fit_caption()
        button.bind(on_release=lambda *_: callback())
        return button

    def _arrow(self, icon, direction):
        button = MDIconButton(icon=icon, size_hint=(None, None), size=(dp(48), dp(48)),
                              pos_hint={"center_x": .5}, theme_icon_color="Custom", icon_color=ACCENT)
        button.bind(on_release=lambda *_: self.move_floor(direction))
        return button

    def move_floor(self, direction):
        index = self.floors.index(self.selected_floor) + direction
        if 0 <= index < len(self.floors):
            self.select_floor(self.floors[index])

    def select_floor(self, floor):
        self.selected_floor = floor
        self.on_floor(floor)
        self.content.clear_widgets()
        items = [desk for desk in self.desks if desk.get("floor") == floor]
        self.content.add_widget(label(f"Этаж {floor} · мест: {len(items)}", 38, 16, ACCENT))
        sections = {}
        for desk in items:
            sections.setdefault(desk.get("section"), []).append(desk)
        for section, desks in sections.items():
            self.content.add_widget(label(f"Секция {section if section is not None else '—'}", 32, 13, MUTED))
            grid = MDGridLayout(cols=2, adaptive_height=True, spacing=dp(6))
            for desk in desks:
                _, color, availability = self.status(desk)
                caption = {"free": "Свободен", "occupied": "Занят", "unavailable": "Недоступен"}[availability]
                button = self._button(f"Стол {desk['number']}\n{caption}", partial(self.open_desk, desk), dp(80), color)
                button.md_bg_color = (*color[:3], .10)
                button._desk_id, button._availability = desk["id"], availability
                grid.add_widget(button)
            self.content.add_widget(grid)
        for number, button in self.floor_buttons.items():
            selected = number == floor
            button.md_bg_color = ACCENT if selected else SURFACE
            button.children[0].text_color = BACKGROUND if selected else ACCENT
        self.up.disabled = floor == self.floors[-1]
        self.down.disabled = floor == self.floors[0]
        self.plan_scroll.scroll_y = 1
        Clock.schedule_once(lambda _dt: self.floor_scroll.scroll_to(self.floor_buttons[floor], animate=False), 0)
