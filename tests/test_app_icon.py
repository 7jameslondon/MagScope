import sys
import types
from pathlib import Path

import pytest

from magscope import app_icon


class FakePixmap:
    def __init__(self, *args, null=False):
        self._null = null
        self.scaled_calls = []

    def isNull(self):
        return self._null

    def scaled(self, size, aspect_mode, transform_mode):
        self.scaled_calls.append((size, aspect_mode, transform_mode))
        return self

    def toImage(self):
        class FakeImage:
            def toHICON(self):
                return 42
        return FakeImage()


class FakeQIcon:
    def __init__(self):
        self.pixmaps = []

    def addPixmap(self, pixmap):
        self.pixmaps.append(pixmap)


class FakeCtypesLong:
    value = 0


def _make_fake_windll():
    windll = types.SimpleNamespace()
    shell32 = types.SimpleNamespace()
    user32 = types.SimpleNamespace()

    setter_calls = []
    send_msg_calls = []
    set_class_calls = []

    def fake_setter(app_id):
        setter_calls.append(app_id)
        return FakeCtypesLong()

    def fake_send_message(hwnd, msg, wparam, lparam):
        send_msg_calls.append((hwnd, msg, wparam, lparam))
        return FakeCtypesLong()

    def fake_set_class_long(hwnd, index, icon):
        set_class_calls.append((hwnd, index, icon))
        return FakeCtypesLong()

    fake_setter.argtypes = None
    fake_setter.restype = FakeCtypesLong
    shell32.SetCurrentProcessExplicitAppUserModelID = fake_setter

    fake_send_message.argtypes = None
    fake_send_message.restype = FakeCtypesLong
    user32.SendMessageW = fake_send_message

    fake_set_class_long.argtypes = None
    fake_set_class_long.restype = FakeCtypesLong
    user32.SetClassLongW = fake_set_class_long
    user32.SetClassLongPtrW = fake_set_class_long

    windll.shell32 = shell32
    windll.user32 = user32
    return windll, setter_calls, send_msg_calls, set_class_calls


class TestSetWindowsAppUserModelId:
    def test_returns_early_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert app_icon.set_windows_app_user_model_id() is None

    def test_calls_ctypes_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        windll, setter_calls, _, _ = _make_fake_windll()
        monkeypatch.setattr(app_icon.ctypes, "windll", windll, raising=False)

        app_icon.set_windows_app_user_model_id("TestApp")

        assert len(setter_calls) == 1
        assert setter_calls[0] == "TestApp"

    def test_silently_handles_ctypes_error_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(app_icon.ctypes, "windll", None)
        assert app_icon.set_windows_app_user_model_id() is None


class TestApplyWindowsNativeWindowIcon:
    def test_returns_early_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")

        class FakeWindow:
            def winId(self):
                return 123

        assert app_icon.apply_windows_native_window_icon(FakeWindow()) is None


class TestLoadAppIcon:
    def test_loads_icon_from_assets(self, monkeypatch):
        monkeypatch.setattr(sys.modules["PyQt6.QtGui"], "QPixmap", FakePixmap)
        monkeypatch.setattr(sys.modules["PyQt6.QtGui"], "QIcon", FakeQIcon)

        class FakeAsFile:
            def __init__(self, resource):
                self._resource = resource

            def __enter__(self):
                return Path("fake_icon.svg")

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(app_icon.resources, "as_file", FakeAsFile)

        icon = app_icon.load_app_icon()
        assert isinstance(icon, FakeQIcon)
        assert len(icon.pixmaps) > 0


class TestLoadWindowsHicon:
    def test_returns_zero_when_pixmap_is_null(self, monkeypatch):
        monkeypatch.setattr(app_icon, "QPixmap", FakePixmap, raising=False)

        class MissingResource:
            def joinpath(self, *parts):
                class Missing:
                    def is_file(self):
                        return False
                    def is_dir(self):
                        return False
                return Missing()

        monkeypatch.setattr(app_icon.resources, "files", lambda pkg: MissingResource())

        result = app_icon._load_windows_hicon("missing.svg", 32)
        assert result == 0

    def test_returns_hicon_when_pixmap_valid(self, monkeypatch):
        monkeypatch.setattr(sys.modules["PyQt6.QtGui"], "QPixmap", FakePixmap)

        class FakeAsFile:
            def __init__(self, resource):
                pass

            def __enter__(self):
                return Path("fake_icon.svg")

            def __exit__(self, *args):
                pass

        monkeypatch.setattr(app_icon.resources, "as_file", FakeAsFile)

        result = app_icon._load_windows_hicon("app_icon_taskbar.svg", 32)
        assert result == 42


class TestSetWindowsClassIcon:
    def test_uses_set_class_long_when_ptr_size_equals_long(self, monkeypatch):
        windll, _, _, set_class_calls = _make_fake_windll()
        monkeypatch.setattr(app_icon.ctypes, "windll", windll, raising=False)

        original_sizeof = app_icon.ctypes.sizeof

        def fake_sizeof(t):
            if t is app_icon.ctypes.c_void_p:
                return app_icon.ctypes.sizeof(app_icon.ctypes.c_long)
            return original_sizeof(t)

        monkeypatch.setattr(app_icon.ctypes, "sizeof", fake_sizeof)

        app_icon._set_windows_class_icon(1, 2, 3)
        assert len(set_class_calls) == 1

    def test_uses_set_class_long_ptr_when_ptr_size_differs(self, monkeypatch):
        windll, _, _, set_class_calls = _make_fake_windll()
        monkeypatch.setattr(app_icon.ctypes, "windll", windll, raising=False)

        original_sizeof = app_icon.ctypes.sizeof

        def fake_sizeof(t):
            if t is app_icon.ctypes.c_void_p:
                return 99
            return original_sizeof(t)

        monkeypatch.setattr(app_icon.ctypes, "sizeof", fake_sizeof)

        app_icon._set_windows_class_icon(1, 2, 3)
        assert len(set_class_calls) == 1
