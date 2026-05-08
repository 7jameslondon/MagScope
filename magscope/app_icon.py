import ctypes
from importlib import resources
import sys
from typing import Any


APP_USER_MODEL_ID = "MagScope.MagScope.Desktop"
TASKBAR_ICON_RESOURCE = "app_icon_taskbar.svg"
WINDOW_ICON_RESOURCE = "app_icon_window.svg"

_ICON_PIXMAP_SIZES = (
    (16, WINDOW_ICON_RESOURCE),
    (24, WINDOW_ICON_RESOURCE),
    (32, WINDOW_ICON_RESOURCE),
    (48, TASKBAR_ICON_RESOURCE),
    (64, TASKBAR_ICON_RESOURCE),
    (128, TASKBAR_ICON_RESOURCE),
    (256, TASKBAR_ICON_RESOURCE),
)
_WINDOWS_ICON_HANDLES: list[int] = []

_WM_SETICON = 0x0080
_ICON_SMALL = 0
_ICON_BIG = 1
_GCLP_HICON = -14
_GCLP_HICONSM = -34


def set_windows_app_user_model_id(app_id: str = APP_USER_MODEL_ID) -> None:
    """Set the Windows app identity used for taskbar grouping and icons."""

    if sys.platform != "win32":
        return

    try:
        setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        setter(app_id)
    except (AttributeError, OSError, ValueError):
        return


def load_app_icon():
    """Build the MagScope Qt icon with simple small sizes and richer large sizes."""

    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QIcon, QPixmap

    icon = QIcon()
    source_pixmaps: dict[str, QPixmap] = {}

    for size, resource_name in _ICON_PIXMAP_SIZES:
        if resource_name not in source_pixmaps:
            resource = resources.files("magscope").joinpath("assets", resource_name)
            if not resource.is_file():
                continue
            with resources.as_file(resource) as icon_path:
                source_pixmaps[resource_name] = QPixmap(str(icon_path))

        source_pixmap = source_pixmaps[resource_name]
        if source_pixmap.isNull():
            continue

        pixmap = source_pixmap.scaled(
            QSize(size, size),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        icon.addPixmap(pixmap)

    return icon


def apply_windows_native_window_icon(window: Any) -> None:
    """Apply native Windows small and taskbar icons to an existing Qt window."""

    if sys.platform != "win32":
        return

    try:
        hwnd = int(window.winId())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return
    if hwnd <= 0:
        return

    small_icon = _load_windows_hicon(WINDOW_ICON_RESOURCE, 32)
    big_icon = _load_windows_hicon(TASKBAR_ICON_RESOURCE, 256)
    if small_icon == 0 or big_icon == 0:
        return

    _set_windows_window_icon(hwnd, _ICON_SMALL, small_icon)
    _set_windows_window_icon(hwnd, _ICON_BIG, big_icon)
    _set_windows_class_icon(hwnd, _GCLP_HICONSM, small_icon)
    _set_windows_class_icon(hwnd, _GCLP_HICON, big_icon)

    # Windows keeps references to these HICONs after WM_SETICON/ClassLongPtr.
    # Retain them for the process lifetime so taskbar icons do not dangle.
    _WINDOWS_ICON_HANDLES.extend((small_icon, big_icon))


def _load_windows_hicon(resource_name: str, size: int) -> int:
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QPixmap

    resource = resources.files("magscope").joinpath("assets", resource_name)
    if not resource.is_file():
        return 0

    with resources.as_file(resource) as icon_path:
        pixmap = QPixmap(str(icon_path))
    if pixmap.isNull():
        return 0

    pixmap = pixmap.scaled(
        QSize(size, size),
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return int(pixmap.toImage().toHICON())


def _set_windows_window_icon(hwnd: int, icon_type: int, hicon: int) -> None:
    from ctypes import wintypes

    send_message = ctypes.windll.user32.SendMessageW
    send_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send_message.restype = wintypes.LPARAM
    send_message(hwnd, _WM_SETICON, icon_type, hicon)


def _set_windows_class_icon(hwnd: int, index: int, hicon: int) -> None:
    from ctypes import wintypes

    if ctypes.sizeof(ctypes.c_void_p) == ctypes.sizeof(ctypes.c_long):
        set_class_long = ctypes.windll.user32.SetClassLongW
        set_class_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        set_class_long.restype = ctypes.c_long
    else:
        set_class_long = ctypes.windll.user32.SetClassLongPtrW
        set_class_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_class_long.restype = ctypes.c_ssize_t
    set_class_long(hwnd, index, hicon)
