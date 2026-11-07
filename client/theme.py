"""Shared neutral palette, with color reserved for availability and feedback."""
from kivy.utils import get_color_from_hex


BACKGROUND = get_color_from_hex("#121212")
SURFACE = get_color_from_hex("#1E1E1E")
SURFACE_RAISED = get_color_from_hex("#292929")
BORDER = get_color_from_hex("#484848")
TEXT = get_color_from_hex("#F5F5F5")
MUTED = get_color_from_hex("#B3B3B3")
ACCENT = get_color_from_hex("#FFFFFF")
ERROR = get_color_from_hex("#F28B82")
SUCCESS = get_color_from_hex("#77D5A0")


def configure_theme(theme):
    """Apply the same neutral roles to built-in Material controls and our UI.

    Material regenerates its scheme during app startup. Its completion hook
    keeps menus, selection controls and text-field defaults in this palette.
    """
    theme.theme_style_switch_animation = False
    theme.dynamic_color = False
    theme.theme_style = "Dark"
    theme.primary_palette = "Gray"
    theme.dynamic_scheme_name = "MONOCHROME"

    def apply_roles():
        roles = {
            "backgroundColor": BACKGROUND,
            "onBackgroundColor": TEXT,
            "primaryColor": ACCENT,
            "onPrimaryColor": BACKGROUND,
            "primaryContainerColor": SURFACE_RAISED,
            "onPrimaryContainerColor": TEXT,
            "secondaryColor": TEXT,
            "onSecondaryColor": BACKGROUND,
            "secondaryContainerColor": SURFACE_RAISED,
            "onSecondaryContainerColor": TEXT,
            "tertiaryColor": TEXT,
            "onTertiaryColor": BACKGROUND,
            "tertiaryContainerColor": SURFACE_RAISED,
            "onTertiaryContainerColor": TEXT,
            "surfaceColor": SURFACE,
            "surfaceDimColor": BACKGROUND,
            "surfaceBrightColor": SURFACE_RAISED,
            "surfaceContainerLowestColor": BACKGROUND,
            "surfaceContainerLowColor": SURFACE,
            "surfaceContainerColor": SURFACE,
            "surfaceContainerHighColor": SURFACE_RAISED,
            "surfaceContainerHighestColor": SURFACE_RAISED,
            "surfaceVariantColor": SURFACE_RAISED,
            "surfaceTintColor": ACCENT,
            "onSurfaceColor": TEXT,
            "onSurfaceVariantColor": MUTED,
            "outlineColor": BORDER,
            "outlineVariantColor": BORDER,
            "errorColor": ERROR,
            "onErrorColor": BACKGROUND,
            "rippleColor": (*ACCENT[:3], 0.12),
        }
        for name, color in roles.items():
            setattr(theme, name, color)

    theme.on_colors = apply_roles
    theme.set_colors()
