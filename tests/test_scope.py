from dataclasses import dataclass
import importlib
from pathlib import Path
import sys
import types

import numpy as np
import pytest

from magscope.ipc import CommandRegistry, Delivery, register_ipc_command
from magscope.ipc_commands import Command, QuitCommand, StartupReadyCommand


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
        def __init__(self):
            self.info_calls = []
            self.warning_calls = []

        def info(self, *args, **kwargs):
            self.info_calls.append((args, kwargs))

        def warning(self, *args, **kwargs):
            self.warning_calls.append((args, kwargs))

    stub_logger = StubLogger()

    logging_module = types.ModuleType("magscope._logging")
    logging_module.configure_logging = lambda *args, **kwargs: None
    logging_module.get_logger = lambda *args, **kwargs: stub_logger
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

        @register_ipc_command(QuitCommand, delivery=Delivery.BROADCAST, target="ManagerProcessBase")
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
        dtype = np.uint8

    class MatrixBuffer:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            self.name = kwargs.get("name", type(self).__name__)
            self.shape = kwargs.get("shape")
            self.nbytes = 0 if self.shape is None else self.shape[0] * self.shape[1] * 8

    class BeadRoiBuffer:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class VideoBuffer:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

    class LiveProfileBuffer:
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

    class UIManager(StubManagerProcessBase):
        def __init__(self):
            super().__init__(name="UIManager")
            self.controls_to_add = []
            self.plots_to_add = []

    class ZLUTGenerationManager(StubManagerProcessBase):
        def __init__(self):
            super().__init__(name="ZLUTGenerationManager")

    class ScriptManager(StubManagerProcessBase):
        def __init__(self):
            super().__init__(name="ScriptManager")
            self.script_registry = StubScriptRegistry()

    class HardwareManagerBase(StubManagerProcessBase):
        pass

    class FocusMotorBase(HardwareManagerBase):
        pass

    class InterprocessValues:
        pass

    stub_modules = {
        "magscope.beadlock": {"BeadLockManager": BeadLockManager},
        "magscope.camera": {"CameraManager": CameraManager},
        "magscope.datatypes": {
            "BeadRoiBuffer": BeadRoiBuffer,
            "LiveProfileBuffer": LiveProfileBuffer,
            "MatrixBuffer": MatrixBuffer,
            "VideoBuffer": VideoBuffer,
        },
        "magscope.ui": {
            "ControlPanelBase": type("ControlPanelBase", (), {}),
            "TimeSeriesPlotBase": type("TimeSeriesPlotBase", (), {}),
            "UIManager": UIManager,
        },
        "magscope.hardware": {
            "FocusMotorBase": FocusMotorBase,
            "HardwareManagerBase": HardwareManagerBase,
        },
        "magscope.processes": {
            "InterprocessValues": InterprocessValues,
            "ManagerProcessBase": StubManagerProcessBase,
            "SingletonMeta": StubSingletonMeta,
            "SingletonABCMeta": StubSingletonMeta,
        },
        "magscope.scripting": {"ScriptManager": ScriptManager},
        "magscope.videoprocessing": {"VideoProcessorManager": VideoProcessorManager},
        "magscope.zlut_generation": {"ZLUTGenerationManager": ZLUTGenerationManager},
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


def test_startup_ready_stops_startup_splash(scope_module):
    scope = make_scope(scope_module)

    scope._startup_splash_deadline = 123.0
    scope._startup_splash_waiting_for_ui_ready = True

    scope.startup_ready(process_name="UIManager")

    assert scope._startup_splash_deadline is None
    assert scope._startup_splash_waiting_for_ui_ready is False


def test_log_exception_from_ui_stops_startup_splash(scope_module, capsys):
    scope = make_scope(scope_module)

    scope._startup_splash_deadline = 123.0
    scope._startup_splash_waiting_for_ui_ready = True

    scope.log_exception(process_name="UIManager", details="boom")

    captured = capsys.readouterr()

    assert "[UIManager] Unhandled exception in child process:\nboom" in captured.err
    assert scope._startup_splash_deadline is None
    assert scope._startup_splash_waiting_for_ui_ready is False


def test_log_exception_from_non_ui_stops_startup_splash(scope_module, capsys):
    scope = make_scope(scope_module)

    scope._startup_splash_deadline = 123.0
    scope._startup_splash_waiting_for_ui_ready = True

    scope.log_exception(process_name="CameraManager", details="boom")

    captured = capsys.readouterr()

    assert "[CameraManager] Unhandled exception in child process:\nboom" in captured.err
    assert scope._startup_splash_deadline is None
    assert scope._startup_splash_waiting_for_ui_ready is False


def test_sleep_when_idle_dismisses_timed_out_startup_splash(scope_module, monkeypatch):
    scope = make_scope(scope_module)

    scope._startup_splash_deadline = 10.0
    scope._startup_splash_waiting_for_ui_ready = True

    monkeypatch.setattr(scope_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(scope_module.time, "sleep", lambda _: None)

    scope._sleep_when_idle()

    assert scope._startup_splash_deadline is None
    assert scope._startup_splash_waiting_for_ui_ready is False


def test_sleep_when_idle_keeps_completed_startup_splash_closed(scope_module, monkeypatch):
    scope = make_scope(scope_module)

    scope._startup_splash_deadline = None
    scope._startup_splash_waiting_for_ui_ready = False

    monkeypatch.setattr(scope_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(scope_module.time, "sleep", lambda _: None)

    scope._sleep_when_idle()

    assert scope._startup_splash_deadline is None
    assert scope._startup_splash_waiting_for_ui_ready is False


def test_receive_ipc_dismisses_timed_out_startup_splash_while_busy(scope_module, monkeypatch):
    scope = make_scope(scope_module)

    scope._startup_splash_deadline = 10.0
    scope._startup_splash_waiting_for_ui_ready = True
    scope.pipes = {"UIManager": DummyPipe([QuitCommand()])}

    processed = []

    monkeypatch.setattr(scope_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(
        scope,
        "_process_command",
        lambda command: processed.append(command) or False,
    )

    scope.receive_ipc()

    assert processed == [QuitCommand()]
    assert scope._startup_splash_deadline is None
    assert scope._startup_splash_waiting_for_ui_ready is False


def test_start_launches_and_cleans_up_splash(scope_module, monkeypatch):
    scope = make_scope(scope_module)

    calls = []

    monkeypatch.setattr(scope, "_start_startup_splash", lambda: calls.append("start_splash"))
    monkeypatch.setattr(scope, "_stop_startup_splash", lambda: calls.append("stop_splash"))
    monkeypatch.setattr(scope, "_collect_processes", lambda: calls.append("collect"))
    monkeypatch.setattr(scope, "_initialize_shared_state", lambda: calls.append("init"))
    monkeypatch.setattr(scope, "_start_managers", lambda: calls.append("start_managers"))
    monkeypatch.setattr(scope, "_join_processes", lambda: calls.append("join"))

    def fake_loop():
        calls.append("loop")
        scope._running = False

    monkeypatch.setattr(scope, "_main_ipc_loop", fake_loop)

    scope.start()

    assert calls == [
        "start_splash",
        "collect",
        "init",
        "start_managers",
        "loop",
        "join",
        "stop_splash",
    ]
    assert scope._terminated is True


def test_start_skips_splash_for_print_only_mode(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope.print_ipc_commands = True

    calls = []

    monkeypatch.setattr(scope, "_start_startup_splash", lambda: calls.append("start_splash"))
    monkeypatch.setattr(scope, "_stop_startup_splash", lambda: calls.append("stop_splash"))
    monkeypatch.setattr(scope, "_collect_processes", lambda: calls.append("collect"))
    monkeypatch.setattr(scope, "print_registered_commands", lambda: calls.append("print_ipc"))

    scope.start()

    assert calls == ["collect", "print_ipc"]


def test_magscope_is_singleton(scope_module):
    scope_module.MagScope()

    with pytest.raises(TypeError):
        scope_module.MagScope()

    scope_module.MagScope._reset_singleton_for_testing()


def test_collect_processes_includes_zlut_generation_manager(scope_module):
    scope = scope_module.MagScope()

    scope._collect_processes()

    assert list(scope.processes) == [
        'ScriptManager',
        'CameraManager',
        'BeadLockManager',
        'VideoProcessorManager',
        'ZLUTGenerationManager',
        'UIManager',
    ]
    assert 'ZLUTSweepDataset' in scope.lock_names

    scope_module.MagScope._reset_singleton_for_testing()


def test_create_shared_buffers_logs_tracks_buffer_size(scope_module):
    scope = make_scope(scope_module)
    scope._setup_locks()
    scope_module.logger.info_calls.clear()

    scope._create_shared_buffers()

    assert (
        (
            'Creating %s with shape %s and size %s MB',
            'TracksBuffer',
            scope.tracks_buffer.shape,
            scope.tracks_buffer.nbytes / 1e6,
        ),
        {},
    ) in scope_module.logger.info_calls


def test_add_hardware_rejects_multiple_focus_motors(scope_module):
    scope = make_scope(scope_module)
    FocusMotorBase = scope_module.FocusMotorBase

    class PrimaryFocusMotor(FocusMotorBase):
        pass

    class SecondaryFocusMotor(FocusMotorBase):
        pass

    primary = PrimaryFocusMotor()
    primary.name = 'PrimaryFocusMotor'
    secondary = SecondaryFocusMotor()
    secondary.name = 'SecondaryFocusMotor'

    scope.add_hardware(primary)

    with pytest.raises(ValueError, match='supports only one FocusMotorBase'):
        scope.add_hardware(secondary)
