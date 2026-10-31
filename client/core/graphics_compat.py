"""Compatibility for KivyMD 2.0's Material 3 ripple on Adreno drivers."""
from pathlib import Path


def initialize_ripple_shader_at_creation():
    """Keep the ripple, supplying its shader when the FBO is constructed.

    KivyMD 2.0 first creates an FBO with Kivy's default shader, then replaces
    its fragment shader. This can leave invalid GL state on Adreno and crash
    native drawing on a real touch. Kivy's own fix recommends passing ``fs``
    into the Fbo constructor: https://github.com/kivy/kivy/pull/8098.
    Apply before constructing any Material widgets, including menu rows.
    """
    from kivy.graphics import ClearBuffers, ClearColor, Color, Fbo, Rectangle
    from kivymd.uix.behaviors import ripple_behavior

    if getattr(ripple_behavior.M3CommonRipple.init_fbos, "_odg_shader_at_creation", False):
        return

    def init_fbos(self):
        self._phase = 0.0
        self.ripple_pos = (0, 0)
        if ripple_behavior.RIPPLE_FS_STRING is None:
            ripple_behavior.RIPPLE_FS_STRING = "$HEADER$\n" + Path(
                ripple_behavior.M3_RIPPLE_FS
            ).read_text(encoding="utf-8")
        self.fbo = Fbo(size=self.size, group="m3_ripple_behavior",
                       fs=ripple_behavior.RIPPLE_FS_STRING)
        with self.fbo:
            ClearColor(0, 0, 0, 0)
            ClearBuffers()
            Color(1, 1, 1, 1)
            self.rect = Rectangle(pos=(0, 0), size=self.size)

    init_fbos._odg_shader_at_creation = True
    ripple_behavior.M3CommonRipple.init_fbos = init_fbos
