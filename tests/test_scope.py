from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
import types

import pytest

from magscope.ipc_commands import Command, CommandRegistry, Delivery, QuitCommand, command_handler


class DummyEvent:
    def __init__(self, set_flag: bool = False):
        self._set = set_flag

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True


class DummyPipe:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.sent = []

    def poll(self) -> bool:
        return bool(self.messages)

    def recv(self):
        return self.messages.pop(0)

    def send(self, message) -> None:
        self.sent.append(message)


class DummyProcess:
    def __init__(self, alive: bool = True):
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


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

    class StubSingletonMeta(type):
        _instances = {}

        def __call__(cls, *args, **kwargs):
            if cls in cls._instances:
                raise TypeError("MagScope is a singleton")
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
            return instance

    class StubManagerProcessBase(metaclass=StubSingletonMeta):
        def __init__(self, *, name=None, alive: bool = True):
            self.name = name or type(self).__name__
            self._alive = alive
            self._quitting = DummyEvent()
            self._command_registry: CommandRegistry | None = None
            self._command_handlers: dict[type[Command], str] = {}
            self.pipe_end = None
            self.start_called = False
            self.join_called = False

        @property
        def quitting_event(self):
            return self._quitting

        def configure_shared_resources(
            self,
            *,
            camera_type,
            hardware_types,
            quitting_event,
            settings,
            shared_values,
            locks,
            pipe_end,
            command_registry,
        ) -> None:
            self.pipe_end = pipe_end
            self._quitting = quitting_event
            self._command_registry = command_registry
            self._command_handlers = {
                command_type: spec.handler
                for command_type, spec in command_registry.handlers_for_target(self.name).items()
            }

        def is_alive(self) -> bool:
            return self._alive

        def start(self) -> None:
            self.start_called = True

        def join(self) -> None:
            self.join_called = True

        @command_handler(QuitCommand, delivery=Delivery.BROADCAST, target="ManagerProcessBase")
        def quit(self) -> None:
            self._quitting.set()

    class StubScriptRegistry:
        def __init__(self):
            self.registered = []

        def register_class_methods(self, cls) -> None:
            self.registered.append(cls)

    class StubCamera:
        width = 1
        height = 1
        dtype = types.SimpleNamespace(bits=8)

    class MatrixBuffer:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class VideoBuffer:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class BeadLockManager(StubManagerProcessBase):
        def __init__(self):
            super().__init__(name="BeadLockManager")

    class CameraManager(StubManagerProcessBase):
        def __init__(self):
            super().__init__(name="CameraManager")
            self.camera = StubCamera()

    class VideoProcessorManager(StubManagerProcessBase):
        def __init__(self):
            super().__init__(name="VideoProcessorManager")

    class WindowManager(StubManagerProcessBase):
        def __init__(self):
            super().__init__(name="WindowManager")
            self.controls_to_add = []
            self.plots_to_add = []

    class ScriptManager(StubManagerProcessBase):
        def __init__(self):
            super().__init__(name="ScriptManager")
            self.script_registry = StubScriptRegistry()

    class HardwareManagerBase(StubManagerProcessBase):
        pass

    class InterprocessValues:
        pass

    stub_modules = {
        "magscope.beadlock": {"BeadLockManager": BeadLockManager},
        "magscope.camera": {"CameraManager": CameraManager},
        "magscope.datatypes": {"MatrixBuffer": MatrixBuffer, "VideoBuffer": VideoBuffer},
        "magscope.gui": {
            "ControlPanelBase": type("ControlPanelBase", (), {}),
            "TimeSeriesPlotBase": type("TimeSeriesPlotBase", (), {}),
            "WindowManager": WindowManager,
        },
        "magscope.hardware": {"HardwareManagerBase": HardwareManagerBase},
        "magscope.processes": {
            "InterprocessValues": InterprocessValues,
            "ManagerProcessBase": StubManagerProcessBase,
            "SingletonMeta": StubSingletonMeta,
            "SingletonABCMeta": StubSingletonMeta,
        },
        "magscope.scripting": {"ScriptManager": ScriptManager},
        "magscope.videoprocessing": {"VideoProcessorManager": VideoProcessorManager},
    }

    for module_name, attributes in stub_modules.items():
        module = types.ModuleType(module_name)
        for attr_name, attr_value in attributes.items():
            setattr(module, attr_name, attr_value)
        monkeypatch.setitem(sys.modules, module_name, module)

    if "magscope.scope" in sys.modules:
        del sys.modules["magscope.scope"]
    module = importlib.import_module("magscope.scope")
    module.MagScope._reset_singleton_for_testing()
    return module


@pytest.fixture
def scope_module(monkeypatch):
    module = load_scope_with_stubs(monkeypatch)
    yield module
    module.MagScope._reset_singleton_for_testing()
    sys.modules.pop("magscope.scope", None)


def make_scope(scope_module):
    scope = scope_module.MagScope()
    scope.command_registry = CommandRegistry()
    scope.pipes = {}
    scope.processes = {}
    scope.quitting_events = {}
    return scope


def test_route_mag_scope_command_invokes_handler(scope_module, monkeypatch):
    scope = make_scope(scope_module)

    handled = []

    def handler(self, payload: str):
        handled.append(payload)

    monkeypatch.setattr(scope_module.MagScope, "handle_example_command", handler, raising=False)

    @dataclass(frozen=True)
    class ExampleCommand(Command):
        payload: str

    scope.command_registry.register(
        command_type=ExampleCommand,
        handler="handle_example_command",
        owner=scope_module.MagScope,
        delivery=Delivery.MAG_SCOPE,
        target="MagScope",
    )

    routed = scope._route_command(ExampleCommand(payload="payload"))

    assert routed is False
    assert handled == ["payload"]


def test_route_direct_command_sends_when_process_alive(scope_module):
    scope = make_scope(scope_module)
    pipe = DummyPipe()
    scope.pipes = {"worker": pipe}
    scope.processes = {"worker": DummyProcess(alive=True)}
    scope.quitting_events = {"worker": DummyEvent()}

    @dataclass(frozen=True)
    class DirectCommand(Command):
        value: int

    class Owner:
        def handle_direct(self, value: int) -> None:
            pass

    scope.command_registry.register(
        command_type=DirectCommand,
        handler="handle_direct",
        owner=Owner,
        delivery=Delivery.DIRECT,
        target="worker",
    )

    routed = scope._route_command(DirectCommand(value=5))

    assert routed is False
    assert pipe.sent == [DirectCommand(value=5)]


def test_broadcast_command_skips_quitting_or_dead_processes(scope_module):
    scope = make_scope(scope_module)
    pipe_live = DummyPipe()
    pipe_dead = DummyPipe()
    pipe_quitting = DummyPipe()

    @dataclass(frozen=True)
    class BroadcastCommand(Command):
        pass

    class Owner:
        def fan_out(self) -> None:
            pass

    scope.command_registry.register(
        command_type=BroadcastCommand,
        handler="fan_out",
        owner=Owner,
        delivery=Delivery.BROADCAST,
        target="ManagerProcessBase",
    )

    scope.pipes = {"live": pipe_live, "dead": pipe_dead, "quitting": pipe_quitting}
    scope.processes = {
        "live": DummyProcess(alive=True),
        "dead": DummyProcess(alive=False),
        "quitting": DummyProcess(alive=True),
    }
    scope.quitting_events = {
        "live": DummyEvent(),
        "dead": DummyEvent(),
        "quitting": DummyEvent(set_flag=True),
    }

    routed = scope._route_command(BroadcastCommand())

    assert routed is False
    assert len(pipe_live.sent) == 1
    assert isinstance(pipe_live.sent[0], BroadcastCommand)
    assert pipe_dead.sent == []
    assert pipe_quitting.sent == []


def test_broadcast_quit_sets_quitting_and_stops_loop(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    pipe = DummyPipe()
    scope.pipes = {"worker": pipe}
    scope.processes = {"worker": DummyProcess(alive=True)}
    scope.quitting_events = {"worker": DummyEvent()}
    scope._quitting = DummyEvent()
    scope._running = True

    class Owner:
        def quit(self) -> None:
            pass

    scope.command_registry.register(
        command_type=QuitCommand,
        handler="quit",
        owner=Owner,
        delivery=Delivery.BROADCAST,
        target="ManagerProcessBase",
    )

    drained = []
    monkeypatch.setattr(scope, "_drain_child_pipes_after_quit", lambda: drained.append(True))

    routed = scope._route_command(QuitCommand())

    assert routed is True
    assert scope._running is False
    assert scope._quitting.is_set()
    assert pipe.sent == [QuitCommand()]
    assert drained == [True]


def test_magscope_is_singleton(scope_module):
    scope_module.MagScope()

    with pytest.raises(TypeError):
        scope_module.MagScope()

    scope_module.MagScope._reset_singleton_for_testing()
