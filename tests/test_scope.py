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

    class StubSingletonMeta(type):
        _instances = {}

        def __call__(cls, *args, **kwargs):
            if cls in cls._instances:
                raise TypeError("MagScope is a singleton")
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
            return instance

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
            "SingletonMeta": StubSingletonMeta,
            "SingletonABCMeta": StubSingletonMeta,
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
    module = importlib.import_module("magscope.scope")
    module.MagScope._reset_singleton_for_testing()
    return module, StubMessage


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


def test_start_warns_when_already_running(monkeypatch):
    scope_module, _ = load_scope_with_stubs(monkeypatch)
    scope = scope_module.MagScope()

    calls: list[str] = []

    def recorder(label):
        def _inner():
            calls.append(label)
        return _inner

    monkeypatch.setattr(scope, "_collect_processes", recorder("collect"))
    monkeypatch.setattr(scope, "_initialize_shared_state", recorder("init"))
    monkeypatch.setattr(scope, "_start_managers", recorder("start"))
    monkeypatch.setattr(scope, "_main_ipc_loop", recorder("loop"))
    monkeypatch.setattr(scope, "_join_processes", recorder("join"))

    scope._running = True

    with warnings.catch_warnings(record=True) as caught_warnings:
        scope.start()

    assert any("already running" in str(item.message) for item in caught_warnings)
    assert calls == []


def test_start_raises_after_stop(monkeypatch):
    scope_module, _ = load_scope_with_stubs(monkeypatch)
    scope = scope_module.MagScope()

    def fake_loop():
        scope._running = False

    monkeypatch.setattr(scope, "_collect_processes", lambda: None)
    monkeypatch.setattr(scope, "_initialize_shared_state", lambda: None)
    monkeypatch.setattr(scope, "_start_managers", lambda: None)
    monkeypatch.setattr(scope, "_main_ipc_loop", fake_loop)
    monkeypatch.setattr(scope, "_join_processes", lambda: None)

    scope.start()
    assert scope._terminated is True

    with pytest.raises(RuntimeError):
        scope.start()


def test_stop_broadcasts_quit_and_joins(monkeypatch):
    scope_module, base_message_cls = load_scope_with_stubs(monkeypatch)
    scope = scope_module.MagScope()

    monkeypatch.setattr(scope_module.ManagerProcessBase, "quit", "quit", raising=False)

    sent_messages: list[base_message_cls] = []
    joined: list[bool] = []

    def fake_broadcast(message):
        sent_messages.append(message)
        scope._running = False

    def fake_join():
        joined.append(True)

    scope._running = True

    monkeypatch.setattr(scope, "_handle_broadcast_message", fake_broadcast)
    monkeypatch.setattr(scope, "_join_processes", fake_join)

    scope.stop()

    assert sent_messages
    assert sent_messages[0].meth == "quit"
    assert joined == [True]
    assert scope._terminated is True


def test_magscope_is_singleton(monkeypatch):
    scope_module, _ = load_scope_with_stubs(monkeypatch)
    scope_module.MagScope()

    with pytest.raises(TypeError):
        scope_module.MagScope()

    scope_module.MagScope._reset_singleton_for_testing()
