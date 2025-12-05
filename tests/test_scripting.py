from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1] / "magscope"


def load_magscope_module(qualified_name: str):
    module_path = PACKAGE_DIR / f"{qualified_name.split('.')[-1]}.py"
    spec = importlib.util.spec_from_file_location(qualified_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    if spec.loader is None:  # pragma: no cover - defensive guard
        raise ImportError(f"Cannot load spec for {qualified_name}")
    spec.loader.exec_module(module)
    return module


def test_negative_sleep_reports_error_to_gui():
    qt_module = types.ModuleType("PyQt6")
    qt_gui_module = types.ModuleType("PyQt6.QtGui")
    qt_gui_module.QImage = object
    sys.modules.setdefault("PyQt6", qt_module)
    sys.modules.setdefault("PyQt6.QtGui", qt_gui_module)

    if "magscope" not in sys.modules:
        package = types.ModuleType("magscope")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["magscope"] = package

    ipc_commands = load_magscope_module("magscope.ipc_commands")
    scripting = load_magscope_module("magscope.scripting")

    manager = scripting.ScriptManager()
    sent_commands: list = []
    manager.send_ipc = sent_commands.append  # type: ignore[method-assign]

    manager.start_sleep(-1.0)

    assert manager._script_status == scripting.ScriptStatus.ERROR
    assert not manager._script_waiting
    assert manager._script_sleep_duration is None
    assert any(
        isinstance(command, ipc_commands.ShowErrorCommand)
        and command.text == "Sleep duration must be non-negative"
        for command in sent_commands
    )
