"""Offline floor-plan checks, rendered at an exact logical phone width.

No account, API, or database is used. Run with the client Python environment.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_HOME", str(ROOT / "tests" / "_kivy_home"))
os.environ.setdefault("KIVY_METRICS_DENSITY", "1")
os.environ.setdefault("KIVY_METRICS_FONTSCALE", "1")
OUTPUT = ROOT / "tests" / "artifacts" / "floor-plan"
OUTPUT.mkdir(parents=True, exist_ok=True)

from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from client.app import OfficeDeskGearApp
from client.floor_plan import FloorPlan
from client.registration import BACKGROUND
from client.theme import configure_theme


def desk(number, floor=2, section=1):
    return {"id": number, "number": 978700 + number, "floor": floor, "section": section,
            "is_free": number % 3 == 0, "is_operational": number % 3 != 2}


def check_loading():
    filters = dict(floor="2", section="", sockets="4", features="", search="", sort="number",
                   order="asc", date="2026-11-12", start_time="10:30", end_time="12:00")
    api = SimpleNamespace(last_meta={}, get_availability=Mock(return_value=[]))
    values = {}
    owner = SimpleNamespace(filters=filters, map_mode=True, api=api, desk_results=None,
                            _display_desks=lambda _data: None,
                            _desk_interval=lambda f: OfficeDeskGearApp._desk_interval(None, f),
                            job=lambda fetch, *_a, **_kw: values.update(fetch()))
    desks = [desk(n) for n in range(420)]
    api.get_desks = Mock(side_effect=[desks[:200], desks[200:400], desks[400:]])
    OfficeDeskGearApp._load_desks(owner)
    assert len(values["desks"]) == 420
    assert [c.kwargs["offset"] for c in api.get_desks.call_args_list] == [0, 200, 400]
    assert all(c.kwargs["floor"] is None and c.kwargs["start_at"].endswith("10:30:00")
               and c.kwargs["end_at"].endswith("12:00:00") for c in api.get_desks.call_args_list)

    def strict_then_soft(**args):
        api.last_meta = {"x-partial-match": "true"} if args["offset"] else {}
        return desks[:200]
    api.get_desks = Mock(side_effect=strict_then_soft)
    OfficeDeskGearApp._load_desks(owner)
    assert len(values["desks"]) == 200 and values["partial"] is None

    def softened(**args):
        api.last_meta = {"x-partial-match": "true"} if args["offset"] == 0 else {}
        return desks[:200] if args["offset"] == 0 else desks[200:220]
    api.get_desks = Mock(side_effect=softened)
    OfficeDeskGearApp._load_desks(owner)
    assert len(values["desks"]) == 220 and values["partial"] == "true"
    assert api.get_desks.call_args_list[1].kwargs["sockets"] is None
    api.last_meta = {}
    api.get_desks = Mock(return_value=[])
    OfficeDeskGearApp._load_desks(owner)
    assert values["desks"] == []
    print("PASS: complete API loading, interval, empty results and consistent partial matches", flush=True)


class PhoneLayoutCheck(MDApp):
    def build(self):
        from client.core.graphics_compat import initialize_ripple_shader_at_creation
        initialize_ripple_shader_at_creation()
        configure_theme(self.theme_cls)
        check_loading()
        self.errors, self.opened, self.selected = [], [], []
        # Explicit root size also works when a remote Windows desktop refuses
        # to resize a hidden native window. dp(320) is the width being checked.
        root = MDBoxLayout(orientation="vertical", size_hint=(None, None), size=(dp(320), dp(640)),
                           padding=dp(12), md_bg_color=BACKGROUND)
        self.fixture = [desk(n, section=n // 15 + 1) for n in range(35)] + [desk(90, 0), desk(91, 9)]
        self.plan = FloorPlan(self.fixture, 2, OfficeDeskGearApp._desk_status,
                              self.opened.append, self.selected.append)
        root.add_widget(self.plan)
        return root

    def on_start(self):
        self.steps = [self.initial, self.scroll_end, self.open_last, self.floors, self.wider, self.finish]
        Clock.schedule_once(lambda _dt: Clock.schedule_once(lambda _dt: Clock.schedule_once(self.step, .7), 0), 0)

    def step(self, _dt):
        try:
            self.steps.pop(0)()
            if self.steps:
                Clock.schedule_once(self.step, .4)
        except Exception:
            self.errors.append(traceback.format_exc())
            print(self.errors[-1], flush=True)
            self.root.export_to_png(str(OUTPUT / "failure.png"))
            self.stop()

    def initial(self):
        p = self.plan
        print(f"Layout: root={self.root.size}, plan={p.size}, viewport={p.plan_scroll.size}, rail={p.rail.size}", flush=True)
        assert self.root.width == dp(320) and p.right <= self.root.right
        assert p.plan_scroll.right <= p.rail.x
        assert len([w for w in p.content.walk() if hasattr(w, "_desk_id")]) == 35
        for button in p.floor_buttons.values():
            assert button.width >= dp(44) and button.height >= dp(44)
            assert abs(button.children[0].center_x - button.width / 2) < 1
        for button in p.content.walk():
            if hasattr(button, "_desk_id"):
                assert button.width >= dp(88), (self.root.size, p.size, p.plan_scroll.size,
                                                button.size, button.size_hint, dp(1))
                assert button.children[0].texture_size[1] <= button.height
        self.rail_position = p.rail.pos[:]
        self.root.export_to_png(str(OUTPUT / "phone-320-top.png"))
        p.plan_scroll.scroll_y = 0
        print("PASS: 320dp phone layout, 35 desks across three sections, labels and touch targets", flush=True)

    def scroll_end(self):
        assert self.plan.rail.pos == self.rail_position
        self.root.export_to_png(str(OUTPUT / "phone-320-bottom.png"))

    def open_last(self):
        button = next(w for w in self.plan.content.walk() if getattr(w, "_desk_id", None) == 34)
        button.dispatch("on_release")
        assert self.opened == [self.fixture[34]]
        print("PASS: final desk after 20 opens; floor controls stay visible during scrolling", flush=True)

    def floors(self):
        p = self.plan
        p.up.dispatch("on_release")
        assert p.selected_floor == 9 and p.up.disabled
        p.move_floor(1)
        assert p.selected_floor == 9
        p.down.dispatch("on_release")
        assert p.selected_floor == 2
        p.floor_buttons[0].dispatch("on_release")
        assert p.selected_floor == 0 and p.down.disabled
        p.move_floor(-1)
        assert p.selected_floor == 0
        p.floor_buttons[2].dispatch("on_release")
        assert p.plan_scroll.scroll_y == 1 and self.selected[-1] == 2
        print("PASS: floor numbers, up/down, nonconsecutive floors, floor zero and boundaries", flush=True)

    def wider(self):
        self.root.width = dp(420)

    def finish(self):
        assert self.plan.rail.x >= self.plan.plan_scroll.right
        self.root.export_to_png(str(OUTPUT / "phone-420.png"))
        print("PASS: 420dp phone layout; all floor-plan checks", flush=True)
        self.stop()


if __name__ == "__main__":
    app = PhoneLayoutCheck()
    app.run()
    sys.exit(bool(app.errors))
