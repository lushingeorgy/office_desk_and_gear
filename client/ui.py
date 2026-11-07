"""Persistent navigation and lightweight, frame-safe page transitions."""
from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.properties import BooleanProperty, NumericProperty
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scrollview import ScrollView
from kivy.effects.scroll import ScrollEffect
from kivy.uix.widget import Widget
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDIcon

from client.registration import ACCENT, BACKGROUND, MUTED, SURFACE, TEXT, label
from client.theme import SURFACE_RAISED


class NavigationItem(ButtonBehavior, MDBoxLayout):
    """A stable touch target with both an icon and a readable caption."""

    def __init__(self, title, icon, **kwargs):
        super().__init__(orientation="vertical", padding=(0, dp(6)), spacing=dp(2),
                         radius=[dp(10)], **kwargs)
        self.caption = label(title, 20, 11, MUTED)
        self.caption.halign = "center"
        self.symbol = MDIcon(icon=icon, theme_icon_color="Custom", icon_color=MUTED)
        mark = AnchorLayout()
        mark.add_widget(self.symbol)
        self.add_widget(mark)
        self.add_widget(self.caption)
        self.active = False

    def select(self, active):
        self.active = active
        self.symbol.icon_color = self.caption.text_color = ACCENT if active else MUTED
        self.md_bg_color = SURFACE_RAISED if active else (0, 0, 0, 0)


class PageView(ScrollView):
    interactive = BooleanProperty(False)

    def __init__(self, **kwargs):
        super().__init__(do_scroll_x=False, bar_width=dp(2), effect_cls=ScrollEffect,
                         always_overscroll=False, **kwargs)
        self.anchor = AnchorLayout(anchor_y="top", padding=(dp(8), dp(12)), size_hint_y=None)
        self.body = MDBoxLayout(orientation="vertical", adaptive_height=True,
                                spacing=dp(14), size_hint_x=None)
        self.anchor.add_widget(self.body)
        self.add_widget(self.anchor)
        self.body.bind(height=self.relayout)
        self.bind(size=self.relayout)

    def relayout(self, *_):
        self.body.width = max(0, min(dp(620), self.width - dp(16)))
        self.anchor.height = max(self.height, self.body.height + dp(24))

    def on_touch_down(self, touch):
        # Block touches while staging without applying disabled colors to the
        # outgoing form or scheduling Material color changes on the new one.
        if not self.interactive and self.collide_point(*touch.pos):
            return True
        return super().on_touch_down(touch)


class PageHost(FloatLayout):
    """Keep the outgoing page until Kivy has laid out the incoming tree.

    Opacity animation avoids the full-screen FBOs of ScreenManager shader
    transitions. At most two page trees are retained, including on rapid taps.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current = None
        self._ready = None

    def stage(self):
        self.cancel_pending()
        for child in self.children[:]:
            Animation.cancel_all(child, "opacity")
            if child is not self.current:
                self.remove_widget(child)
        if self.current:
            self.current.opacity = 1
            self.current.interactive = False
        page = PageView(opacity=0, pos_hint={"x": 0, "y": 0})
        self.add_widget(page)
        # Two clock turns let nested adaptive layouts and Material properties
        # settle before any of the new content becomes visible.
        self._ready = Clock.schedule_once(lambda _dt: self._settle(page), 0)
        return page

    def _settle(self, page):
        page.relayout()
        self._ready = Clock.schedule_once(lambda _dt: self._reveal(page), 0)

    def _reveal(self, page):
        self._ready = None
        old, self.current = self.current, page
        page.interactive = True
        if old:
            fade = Animation(opacity=0, duration=0.16, t="out_quad")
            fade.bind(on_complete=lambda *_: self.remove_widget(old) if old.parent is self else None)
            fade.start(old)
        Animation(opacity=1, duration=0.20, t="out_quad").start(page)

    def cancel_pending(self):
        if self._ready:
            self._ready.cancel()
            self._ready = None

    def clear(self):
        self.cancel_pending()
        for child in self.children[:]:
            Animation.cancel_all(child)
        self.clear_widgets()
        self.current = None


class LoadingLine(Widget):
    """Small custom indeterminate indicator; no spinner or layout resizing."""

    phase = NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(size_hint_y=None, height=dp(3), **kwargs)
        with self.canvas:
            Color(*SURFACE)
            self.track = RoundedRectangle(radius=[dp(2)])
            Color(*ACCENT)
            self.segment = RoundedRectangle(radius=[dp(2)])
        self.bind(pos=self._draw, size=self._draw, phase=self._draw)
        self._draw()

    def _draw(self, *_):
        self.track.pos, self.track.size = self.pos, self.size
        width = self.width * 0.28
        self.segment.pos = (self.x + (self.width - width) * self.phase, self.y)
        self.segment.size = (width, self.height)

    def start(self):
        self.stop()
        self.opacity = 1
        animation = (Animation(phase=1, duration=0.85, t="in_out_sine") +
                     Animation(phase=0, duration=0.85, t="in_out_sine"))
        animation.repeat = True
        animation.start(self)

    def stop(self):
        Animation.cancel_all(self, "phase")
        self.opacity = 0
        self.phase = 0


class StartupScreen(MDBoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", md_bg_color=BACKGROUND, **kwargs)
        center = AnchorLayout()
        content = MDBoxLayout(orientation="vertical", size_hint=(None, None),
                              size=(dp(288), dp(300)), spacing=dp(12))
        mark = AnchorLayout(size_hint_y=None, height=dp(128))
        mark.add_widget(MDIcon(icon="desk", theme_icon_color="Custom", icon_color=ACCENT,
                               theme_font_size="Custom", font_size=dp(64)))
        content.add_widget(mark)
        title = label("Office Desk & Gear", 36, 23, TEXT)
        title.halign = "center"
        content.add_widget(title)
        subtitle = label("Ваше рабочее пространство", 26, 14, MUTED)
        subtitle.halign = "center"
        content.add_widget(subtitle)
        self.progress = LoadingLine()
        content.add_widget(self.progress)
        caption = label("Готовим всё к работе…", 30, 12, MUTED)
        caption.halign = "center"
        content.add_widget(caption)
        center.add_widget(content)
        self.add_widget(center)
        self.progress.start()

    def on_touch_down(self, touch):
        return True

    def dismiss(self):
        self.progress.stop()
        animation = Animation(opacity=0, duration=0.22, t="out_quad")
        animation.bind(on_complete=lambda *_: self.parent.remove_widget(self) if self.parent else None)
        animation.start(self)
