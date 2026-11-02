"""Render the Android presplash from the same native UI as the startup screen."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_HOME", str(ROOT / "tests" / "_kivy_home"))
os.environ["KIVY_METRICS_DENSITY"] = "2"

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivymd.app import MDApp
from client.registration import BACKGROUND
from client.ui import StartupScreen


class PresplashRenderer(MDApp):
    def build(self):
        self.theme_cls.theme_style_switch_animation = False
        self.theme_cls.theme_style = "Dark"
        Window.size = (dp(360), dp(640))
        Window.clearcolor = BACKGROUND
        return StartupScreen()

    def on_start(self):
        Clock.schedule_once(lambda _dt: Clock.schedule_once(self.render, 0.4), 0)

    def render(self, _dt):
        self.root.progress.stop()
        # The animated indicator belongs to the live startup; the native splash
        # remains static while Android loads the Python runtime.
        self.root.export_to_png(str(ROOT / "client" / "assets" / "presplash.png"))
        self.stop()


if __name__ == "__main__":
    PresplashRenderer().run()
