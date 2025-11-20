import importlib
import sys
import types
import warnings
from pathlib import Path

import pytest


def load_scope_with_stubs(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]

    package = types.ModuleType("magscope")
    package.__path__ = [str(repo_root / "magscope")]
    monkeypatch.setitem(sys.modules, "magscope", package)

    class StubLogger:
        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

    logging_module = types.ModuleType("magscope._logging")
    logging_module.configure_logging = lambda *args, **kwargs: None
    logging_module.get_logger = lambda *args, **kwargs: StubLogger()
    monkeypatch.setitem(sys.modules, "magscope._logging", logging_module)

    class StubManagerProcessBase:
        def __init__(self):
            self._quitting = types.SimpleNamespace(set=lambda: None, is_set=lambda: False)

        def is_alive(self):
            return True

    stub_classes = {
        "beadlock": {"BeadLockManager": type("BeadLockManager", (), {})},
        "camera": {"CameraManager": type("CameraManager", (), {})},
        "datatypes": {
            "MatrixBuffer": type("MatrixBuffer", (), {}),
            "VideoBuffer": type("VideoBuffer", (), {}),
        },
        "gui": {
            "ControlPanelBase": type("ControlPanelBase", (), {}),
            "TimeSeriesPlotBase": type("TimeSeriesPlotBase", (), {}),
            "WindowManager": type("WindowManager", (), {}),
        },
        "hardware": {"HardwareManagerBase": type("HardwareManagerBase", (), {})},
        "processes": {
            "InterprocessValues": type("InterprocessValues", (), {}),
            "ManagerProcessBase": StubManagerProcessBase,
        },
        "scripting": {"ScriptManager": type("ScriptManager", (), {})},
        "videoprocessing": {"VideoProcessorManager": type("VideoProcessorManager", (), {})},
    }

    for module_name, attributes in stub_classes.items():
        module = types.ModuleType(f"magscope.{module_name}")
        for attr_name, attr_value in attributes.items():
            setattr(module, attr_name, attr_value)
        monkeypatch.setitem(sys.modules, f"magscope.{module_name}", module)

    utils_module = types.ModuleType("magscope.utils")

    class StubMessage:
        def __init__(self, to, meth, *args, **kwargs):
            self.to = to
            self.meth = meth
            self.args = args
            self.kwargs = kwargs

    utils_module.Message = StubMessage
    monkeypatch.setitem(sys.modules, "magscope.utils", utils_module)

    if "magscope.scope" in sys.modules:
        del sys.modules["magscope.scope"]
    return importlib.import_module("magscope.scope"), StubMessage


class DummyPipe:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.sent = []

    def poll(self):
        return bool(self.messages)

    def recv(self):
        return self.messages.pop(0)

    def send(self, message):
        self.sent.append(message)


class AliveProcess:
    def __init__(self, alive=True):
        self._alive = alive

    def is_alive(self):
        return self._alive


class FakeEvent:
    def __init__(self, set_flag=False):
        self._set = set_flag

    def is_set(self):
        return self._set


def test_receive_ipc_accepts_message_subclass(monkeypatch):
    scope_module, base_message_cls = load_scope_with_stubs(monkeypatch)

    class SubclassedMessage(base_message_cls):
        pass

    message = SubclassedMessage("proc2", "do_thing")
    incoming = DummyPipe([message])
    outgoing = DummyPipe()

    scope = scope_module.MagScope.__new__(scope_module.MagScope)
    scope.pipes = {"proc1": incoming, "proc2": outgoing}
    alive_proc = AliveProcess()
    scope.processes = {"proc1": alive_proc, "proc2": alive_proc}
    scope.quitting_events = {"proc1": FakeEvent(), "proc2": FakeEvent()}

    with warnings.catch_warnings(record=True) as caught_warnings:
        scope.receive_ipc()

    assert outgoing.sent == [message]
    assert caught_warnings == []
