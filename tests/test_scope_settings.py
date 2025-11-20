import sys
import types

import pytest


class _DummyMeta(type):
    def __getattr__(cls, name):
        return cls

    def __call__(cls, *args, **kwargs):
        return cls


class _Dummy(metaclass=_DummyMeta):
    pass


def _install_pyqt_mocks(monkeypatch):
    dummy = _Dummy()

    def create_module(name):
        module = types.ModuleType(name)
        module.__getattr__ = lambda attr, _dummy=dummy: _dummy
        return module

    qt_gui = create_module("PyQt6.QtGui")
    qt_core = create_module("PyQt6.QtCore")
    qt_widgets = create_module("PyQt6.QtWidgets")

    pyqt6 = types.ModuleType("PyQt6")
    pyqt6.QtGui = qt_gui
    pyqt6.QtCore = qt_core
    pyqt6.QtWidgets = qt_widgets
    pyqt6.__getattr__ = lambda attr, _dummy=dummy: _dummy

    qt_sip = create_module("PyQt6.sip")

    monkeypatch.setitem(sys.modules, "PyQt6", pyqt6)
    monkeypatch.setitem(sys.modules, "PyQt6.QtGui", qt_gui)
    monkeypatch.setitem(sys.modules, "PyQt6.QtCore", qt_core)
    monkeypatch.setitem(sys.modules, "PyQt6.QtWidgets", qt_widgets)
    monkeypatch.setitem(sys.modules, "PyQt6.sip", qt_sip)

    gui_module = types.ModuleType("magscope.gui")
    gui_module.ControlPanelBase = type("ControlPanelBase", (), {})
    gui_module.TimeSeriesPlotBase = type("TimeSeriesPlotBase", (), {})
    gui_module.WindowManager = type("WindowManager", (), {})

    monkeypatch.setitem(sys.modules, "magscope.gui", gui_module)


def test_load_settings_warns_and_skips_empty_file(monkeypatch, tmp_path):
    _install_pyqt_mocks(monkeypatch)
    from magscope.scope import MagScope

    scope = MagScope()
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("")
    original_settings = scope.settings.copy()

    scope.settings_path = str(settings_path)
    with pytest.warns(UserWarning, match="empty.*Skipping merge"):
        scope._load_settings()

    assert scope.settings == original_settings
