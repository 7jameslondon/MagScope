import ctypes
from importlib import resources
import sys


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
