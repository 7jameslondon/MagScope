"""Shared UI color constants."""

from magscope.settings import DEFAULT_GUI_ACCENT_COLOR, normalize_hex_color

ACCENT_COLOR = DEFAULT_GUI_ACCENT_COLOR
APP_BACKGROUND_COLOR = '#121212'
PANEL_BACKGROUND_COLOR = '#1d1d1d'
PANEL_BACKGROUND_RGB = (29, 29, 29)

_current_accent_color = ACCENT_COLOR


def get_accent_color() -> str:
    return _current_accent_color


def set_accent_color(color: str) -> str:
    global _current_accent_color
    _current_accent_color = normalize_hex_color(color)
    return _current_accent_color
