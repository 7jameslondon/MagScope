from dataclasses import dataclass
import importlib
import logging
from pathlib import Path
import sys
import types

import numpy as np
import pytest

from magscope.ipc import CommandRegistry, Delivery, register_ipc_command
from magscope.ipc_commands import Command, QuitCommand, SetSettingsCommand, StartupReadyCommand


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


class ValueBox:
    def __init__(self, value):
        self.value = value


def load_scope_with_stubs(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]

    package = types.ModuleType("magscope")
    package.__path__ = [str(repo_root / "magscope")]
    monkeypatch.setitem(sys.modules, "magscope", package)

    class StubLogger:
        def __init__(self):
            self.debug_calls = []
            self.info_calls = []
            self.warning_calls = []

        def isEnabledFor(self, _level):
            return True

        def debug(self, *args, **kwargs):
            self.debug_calls.append((args, kwargs))

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


# ---------------------------------------------------------------------------
# Scope lifecycle methods
# ---------------------------------------------------------------------------

def test_read_command_returns_none_when_pipe_empty(scope_module):
    scope = make_scope(scope_module)
    pipe = DummyPipe(messages=[])
    assert scope._read_command(pipe) is None


def test_read_command_returns_none_for_non_command(scope_module):
    scope = make_scope(scope_module)
    pipe = DummyPipe(messages=["not_a_command"])
    assert scope._read_command(pipe) is None


def test_read_command_returns_valid_command(scope_module):
    scope = make_scope(scope_module)
    command = QuitCommand()
    pipe = DummyPipe(messages=[command])
    result = scope._read_command(pipe)
    assert result is command


def test_coerce_settings_from_dict(scope_module):
    scope = make_scope(scope_module)
    result = scope._coerce_settings({"ROI": 64})
    assert result["ROI"] == 64


def test_coerce_settings_from_magscope_settings_obj(scope_module):
    from magscope.settings import MagScopeSettings
    scope = make_scope(scope_module)
    settings = MagScopeSettings({"ROI": 32})
    result = scope._coerce_settings(settings)
    assert result["ROI"] == 32
    assert result is not settings  # cloned


def test_setup_command_registry_is_idempotent(scope_module):
    scope = make_scope(scope_module)
    scope._setup_command_registry()
    assert scope._command_registry_initialized is True
    scope._setup_command_registry()


def test_reset_camera_health_logging_state(scope_module, monkeypatch):
    scope = make_scope(scope_module)

    class FakeCameraTotalFrames:
        value = 42

    scope.shared_values = type("StubSV", (), {"camera_total_frames": FakeCameraTotalFrames})()
    monkeypatch.setattr("time.monotonic", lambda: 100.0)

    scope._reset_camera_health_logging_state()
    assert scope._last_camera_health_sample_time == 100.0
    assert scope._last_camera_health_frame_count == 42


def test_dispatch_mag_scope_command_calls_handler(scope_module):
    scope = make_scope(scope_module)
    calls = []

    @dataclass(frozen=True)
    class ScopeCommand(Command):
        value: int

    class FakeSpec:
        handler = "my_handler"

    scope.my_handler = lambda **kw: calls.append(kw)
    scope._dispatch_mag_scope_command(ScopeCommand(value=5), FakeSpec)
    assert calls == [{"value": 5}]


def test_dispatch_mag_scope_command_missing_handler_raises(scope_module):
    scope = make_scope(scope_module)

    @dataclass(frozen=True)
    class ScopeCommand(Command):
        value: int

    class FakeSpec:
        handler = "nonexistent"

    with pytest.raises(RuntimeError, match="No MagScope handler"):
        scope._dispatch_mag_scope_command(ScopeCommand(value=5), FakeSpec)


def test_mark_running_sets_flag(scope_module):
    scope = make_scope(scope_module)
    scope._running = False
    assert scope._mark_running() is True
    assert scope._running is True


def test_mark_running_rejects_if_already_running(scope_module):
    scope = make_scope(scope_module)
    scope._running = True
    assert scope._mark_running() is False


def test_ensure_not_terminated_raises_if_terminated(scope_module):
    scope = make_scope(scope_module)
    scope._terminated = True
    with pytest.raises(RuntimeError, match="already been stopped"):
        scope._ensure_not_terminated()


def test_ensure_not_terminated_passes_when_active(scope_module):
    scope = make_scope(scope_module)
    scope._terminated = False
    scope._ensure_not_terminated()


def test_set_verbose_logging_toggles_level(scope_module):
    scope = make_scope(scope_module)
    scope.set_verbose_logging(True)
    assert scope._log_level == 20  # INFO
    scope.set_verbose_logging(False)
    assert scope._log_level == 30  # WARNING


def test_add_control_appends_to_ui_manager_list(scope_module):
    scope = make_scope(scope_module)
    scope.ui_manager.controls_to_add = []
    scope.add_control(str, 0)
    assert scope.ui_manager.controls_to_add == [(str, 0)]


def test_add_timeplot_appends_to_ui_manager_list(scope_module):
    scope = make_scope(scope_module)
    scope.ui_manager.plots_to_add = []
    scope.add_timeplot("fake_plot")
    assert scope.ui_manager.plots_to_add == ["fake_plot"]


def test_stop_broadcasts_quit_and_joins(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope._running = True
    scope._terminated = False
    scope.pipes = {}
    scope.processes = {"test": DummyProcess(alive=True)}
    scope.quitting_events = {"test": DummyEvent()}

    handle_calls = []
    monkeypatch.setattr(scope, "_handle_broadcast_command", lambda cmd: handle_calls.append(cmd))
    monkeypatch.setattr(scope, "_join_processes", lambda: None)
    monkeypatch.setattr(scope, "_mark_terminated", lambda: None)

    scope.stop()
    assert len(handle_calls) == 1
    assert isinstance(handle_calls[0], QuitCommand)


def test_stop_warns_when_not_running(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope._running = False
    scope._terminated = False
    scope.stop()


def test_print_ipc_commands_property_setter_guard(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope._running = True
    scope.print_ipc_commands = True
    assert scope._print_ipc_commands is False  # unchanged because running

    scope._running = False
    scope.print_ipc_commands = True
    assert scope._print_ipc_commands is True


def test_print_script_commands_property_setter_guard(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope._running = True
    scope.print_script_commands = True
    assert scope._print_script_commands is False

    scope._running = False
    scope.print_script_commands = True
    assert scope._print_script_commands is True


def test_print_command_property_getters(scope_module):
    scope = make_scope(scope_module)

    scope._print_ipc_commands = True
    scope._print_script_commands = True

    assert scope.print_ipc_commands is True
    assert scope.print_script_commands is True


def test_settings_property_getter_returns_current_settings(scope_module):
    scope = make_scope(scope_module)

    assert scope.settings is scope._settings


def test_settings_setter_saves_without_broadcast_when_not_running(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope._running = False
    saved = []
    broadcasted = []

    monkeypatch.setattr(
        scope_module.MagScopeSettings,
        "save_to_qsettings",
        lambda settings: saved.append(settings),
    )
    monkeypatch.setattr(scope, "_handle_broadcast_command", broadcasted.append)

    scope.settings = {"ROI": 64}

    assert scope.settings["ROI"] == 64
    assert saved == [scope.settings]
    assert broadcasted == []


def test_settings_setter_broadcasts_when_running(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope._running = True
    broadcasted = []

    monkeypatch.setattr(scope_module.MagScopeSettings, "save_to_qsettings", lambda _settings: None)
    monkeypatch.setattr(scope, "_handle_broadcast_command", broadcasted.append)

    scope.settings = {"ROI": 80}

    assert len(broadcasted) == 1
    assert isinstance(broadcasted[0], SetSettingsCommand)
    assert broadcasted[0].settings["ROI"] == 80


def test_update_settings_broadcasts_set_settings_command(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope._running = True
    broadcasted = []

    monkeypatch.setattr(scope_module.MagScopeSettings, "save_to_qsettings", lambda _settings: None)
    monkeypatch.setattr(scope, "_handle_broadcast_command", broadcasted.append)

    scope.update_settings({"ROI": 96})

    assert len(broadcasted) == 1
    assert isinstance(broadcasted[0], SetSettingsCommand)
    assert broadcasted[0].settings["ROI"] == 96


def test_handle_broadcast_set_settings_command_updates_local_settings(scope_module):
    scope = make_scope(scope_module)

    class SettingsOwner:
        def set_settings(self, settings) -> None:
            pass

    scope.command_registry.register(
        command_type=SetSettingsCommand,
        handler="set_settings",
        owner=SettingsOwner,
        delivery=Delivery.BROADCAST,
        target="ManagerProcessBase",
    )
    settings = scope_module.MagScopeSettings({"ROI": 64})

    should_break = scope._handle_broadcast_command(SetSettingsCommand(settings=settings))

    assert should_break is False
    assert scope.settings["ROI"] == 64
    assert scope.settings is not settings


def test_route_command_unknown_pipe_warns(scope_module):
    scope = make_scope(scope_module)

    @dataclass(frozen=True)
    class DirectCommand(Command):
        pass

    class Owner:
        def handle_direct(self) -> None:
            pass

    scope.command_registry.register(
        command_type=DirectCommand,
        handler="handle_direct",
        owner=Owner,
        delivery=Delivery.DIRECT,
        target="missing",
    )

    with pytest.warns(UserWarning, match="Unknown pipe missing"):
        routed = scope._route_command(DirectCommand())

    assert routed is False


def test_process_command_delegates_to_route_command(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    command = QuitCommand()
    routed = []

    monkeypatch.setattr(scope, "_route_command", lambda command: routed.append(command) or True)

    assert scope._process_command(command) is True
    assert routed == [command]


def test_receive_ipc_idles_when_no_command(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope.pipes = {"worker": DummyPipe(messages=[])}
    health_checks = []
    idle_sleeps = []

    monkeypatch.setattr(scope, "_check_startup_splash_timeout", lambda: None)
    monkeypatch.setattr(scope, "_log_camera_health_if_due", lambda: health_checks.append(True))
    monkeypatch.setattr(scope, "_sleep_when_idle", lambda: idle_sleeps.append(True))

    scope.receive_ipc()

    assert health_checks == [True]
    assert idle_sleeps == [True]


def test_receive_ipc_breaks_after_processed_command_requests_stop(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    first_command = QuitCommand()
    second_command = QuitCommand()
    scope.pipes = {
        "first": DummyPipe(messages=[first_command]),
        "second": DummyPipe(messages=[second_command]),
    }
    processed = []

    monkeypatch.setattr(scope, "_check_startup_splash_timeout", lambda: None)
    monkeypatch.setattr(scope, "_log_camera_health_if_due", lambda: None)
    monkeypatch.setattr(scope, "_process_command", lambda command: processed.append(command) or True)

    scope.receive_ipc()

    assert processed == [first_command]


def test_start_startup_splash_launches_process(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    close_event = DummyEvent()
    created_processes = []

    def run_startup_splash(_close_event):
        pass

    splash_module = types.ModuleType("magscope.startup_splash")
    splash_module.run_startup_splash = run_startup_splash
    monkeypatch.setitem(sys.modules, "magscope.startup_splash", splash_module)

    class FakeProcess:
        def __init__(self, *, target, args, name):
            self.target = target
            self.args = args
            self.name = name
            self.started = False
            created_processes.append(self)

        def start(self):
            self.started = True

        def is_alive(self):
            return False

    monkeypatch.setattr(scope_module, "Event", lambda: close_event)
    monkeypatch.setattr(scope_module, "Process", FakeProcess)
    monkeypatch.setattr(scope_module.time, "monotonic", lambda: 100.0)
    scope._startup_splash_timeout_seconds = 12.5

    scope._start_startup_splash()

    assert len(created_processes) == 1
    assert created_processes[0].target is run_startup_splash
    assert created_processes[0].args == (close_event,)
    assert created_processes[0].name == "MagScopeStartupSplash"
    assert created_processes[0].started is True
    assert scope._startup_splash_deadline == 112.5
    assert scope._startup_splash_close_event is close_event
    assert scope._startup_splash_process is created_processes[0]
    assert scope._startup_splash_waiting_for_ui_ready is True


def test_start_startup_splash_skips_when_existing_process_alive(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    splash_module = types.ModuleType("magscope.startup_splash")
    splash_module.run_startup_splash = lambda _close_event: None
    monkeypatch.setitem(sys.modules, "magscope.startup_splash", splash_module)

    class AliveProcess:
        def is_alive(self):
            return True

    existing_process = AliveProcess()
    scope._startup_splash_process = existing_process
    monkeypatch.setattr(
        scope_module,
        "Process",
        lambda *args, **kwargs: pytest.fail("startup splash process should not be recreated"),
    )

    scope._start_startup_splash()

    assert scope._startup_splash_process is existing_process


def test_stop_startup_splash_terminates_after_join_timeout(scope_module):
    scope = make_scope(scope_module)
    close_event = DummyEvent()

    class SlowProcess:
        def __init__(self):
            self.alive = True
            self.join_timeouts = []
            self.terminated = False

        def join(self, timeout=None):
            self.join_timeouts.append(timeout)

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

    process = SlowProcess()
    scope._startup_splash_close_event = close_event
    scope._startup_splash_process = process
    scope._startup_splash_deadline = 123.0
    scope._startup_splash_waiting_for_ui_ready = True

    scope._stop_startup_splash()

    assert close_event.is_set()
    assert process.terminated is True
    assert process.join_timeouts == [5, 1]
    assert scope._startup_splash_deadline is None
    assert scope._startup_splash_close_event is None
    assert scope._startup_splash_process is None
    assert scope._startup_splash_waiting_for_ui_ready is False


def test_dismiss_startup_splash_noop_when_not_pending(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope._startup_splash_waiting_for_ui_ready = False

    monkeypatch.setattr(
        scope,
        "_stop_startup_splash",
        lambda: pytest.fail("splash should not be stopped when it is not pending"),
    )

    scope._dismiss_startup_splash_if_pending()


def test_check_startup_splash_timeout_waits_before_deadline(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    dismissed = []
    scope._startup_splash_waiting_for_ui_ready = True
    scope._startup_splash_deadline = 20.0

    monkeypatch.setattr(scope_module.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(scope, "_dismiss_startup_splash_if_pending", lambda: dismissed.append(True))

    scope._check_startup_splash_timeout()

    assert dismissed == []
    assert scope._startup_splash_deadline == 20.0


def test_log_camera_health_skips_when_logger_disabled(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope.video_buffer = object()
    seen_levels = []

    monkeypatch.setattr(scope_module.logger, "isEnabledFor", lambda level: seen_levels.append(level) or False)
    monkeypatch.setattr(
        scope,
        "_reset_camera_health_logging_state",
        lambda: pytest.fail("camera health state should not reset when logging is disabled"),
    )

    scope._log_camera_health_if_due()

    assert seen_levels == [logging.INFO]


def test_log_camera_health_resets_when_state_uninitialized(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope.video_buffer = object()
    resets = []

    monkeypatch.setattr(scope_module.logger, "isEnabledFor", lambda _level: True)
    monkeypatch.setattr(scope, "_reset_camera_health_logging_state", lambda: resets.append(True))

    scope._next_camera_health_log_deadline = None
    scope._last_camera_health_sample_time = None

    scope._log_camera_health_if_due()

    assert resets == [True]


def test_log_camera_health_skips_before_deadline(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope.video_buffer = object()
    scope._next_camera_health_log_deadline = 20.0
    scope._last_camera_health_sample_time = 10.0
    scope_module.logger.info_calls.clear()

    monkeypatch.setattr(scope_module.logger, "isEnabledFor", lambda _level: True)
    monkeypatch.setattr(scope_module.time, "monotonic", lambda: 15.0)

    scope._log_camera_health_if_due()

    assert scope_module.logger.info_calls == []


def test_log_camera_health_logs_summary_since_last_frame(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope.shared_values = types.SimpleNamespace(
        camera_total_frames=ValueBox(70),
        camera_last_frame_timestamp=ValueBox(990.0),
        camera_consecutive_timeouts=ValueBox(3),
        camera_queue_full_events=ValueBox(4),
    )
    scope.video_buffer = types.SimpleNamespace(get_level=lambda: 0.25)
    scope._last_camera_health_sample_time = 100.0
    scope._last_camera_health_frame_count = 10
    scope._next_camera_health_log_deadline = 150.0
    scope._camera_health_log_interval_seconds = 60.0
    scope_module.logger.info_calls.clear()

    monkeypatch.setattr(scope_module.logger, "isEnabledFor", lambda _level: True)
    monkeypatch.setattr(scope_module.time, "monotonic", lambda: 160.0)
    monkeypatch.setattr(scope_module.time, "time", lambda: 1000.0)

    scope._log_camera_health_if_due()

    args, kwargs = scope_module.logger.info_calls[-1]
    assert args[0].startswith("Camera health")
    assert args[1] == pytest.approx(1.0)
    assert args[2] == 70
    assert args[3] == "10.00s since last frame"
    assert args[4] == 3
    assert args[5] == 4
    assert args[6] == pytest.approx(25.0)
    assert kwargs == {}
    assert scope._last_camera_health_sample_time == 160.0
    assert scope._last_camera_health_frame_count == 70
    assert scope._next_camera_health_log_deadline == 220.0


def test_log_camera_health_logs_when_no_frames_received(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope.shared_values = types.SimpleNamespace(
        camera_total_frames=ValueBox(0),
        camera_last_frame_timestamp=ValueBox(0.0),
        camera_consecutive_timeouts=ValueBox(0),
        camera_queue_full_events=ValueBox(0),
    )
    scope.video_buffer = types.SimpleNamespace(get_level=lambda: 0.0)
    scope._last_camera_health_sample_time = 10.0
    scope._last_camera_health_frame_count = 0
    scope._next_camera_health_log_deadline = 20.0
    scope_module.logger.info_calls.clear()

    monkeypatch.setattr(scope_module.logger, "isEnabledFor", lambda _level: True)
    monkeypatch.setattr(scope_module.time, "monotonic", lambda: 20.0)

    scope._log_camera_health_if_due()

    args, _kwargs = scope_module.logger.info_calls[-1]
    assert args[3] == "no frames received yet"


def test_drain_child_pipes_after_quit_drains_alive_non_quitting_processes(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    live_pipe = DummyPipe()
    dead_pipe = DummyPipe()
    quitting_pipe = DummyPipe()
    live_event = DummyEvent()
    dead_event = DummyEvent()
    quitting_event = DummyEvent(set_flag=True)
    drained = []

    scope.pipes = {"live": live_pipe, "dead": dead_pipe, "quitting": quitting_pipe}
    scope.processes = {
        "live": DummyProcess(alive=True),
        "dead": DummyProcess(alive=False),
        "quitting": DummyProcess(alive=True),
    }
    scope.quitting_events = {
        "live": live_event,
        "dead": dead_event,
        "quitting": quitting_event,
    }
    monkeypatch.setattr(
        scope_module,
        "drain_pipe_until_quit",
        lambda pipe, event: drained.append((pipe, event)),
    )

    scope._drain_child_pipes_after_quit()

    assert drained == [(live_pipe, live_event)]


def test_add_hardware_registers_plain_hardware(scope_module):
    scope = make_scope(scope_module)
    hardware = scope_module.HardwareManagerBase()
    hardware.name = "PlainHardware"

    scope.add_hardware(hardware)

    assert scope._hardware == {"PlainHardware": hardware}
    assert QuitCommand in scope.command_registry.handlers_for_target("PlainHardware")


def test_create_shared_buffers_creates_hardware_buffers(scope_module):
    scope = make_scope(scope_module)
    hardware = scope_module.HardwareManagerBase()
    hardware.name = "DemoHardware"
    hardware.buffer_shape = (2, 3)
    scope._hardware = {hardware.name: hardware}

    scope._setup_locks()
    scope._create_shared_buffers()

    hardware_buffer = scope._hardware_buffers["DemoHardware"]
    assert hardware_buffer.kwargs["name"] == "DemoHardware"
    assert hardware_buffer.kwargs["shape"] == (2, 3)


def test_setup_pipes_stores_parent_ends(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    parent_ends = {"worker": DummyPipe()}
    child_ends = {"worker": DummyPipe()}

    monkeypatch.setattr(scope_module, "create_pipes", lambda processes: (parent_ends, child_ends))
    scope.processes = {"worker": object()}

    assert scope._setup_pipes() is child_ends
    assert scope.pipes is parent_ends


def test_setup_shared_resources_runs_configuration_then_buffer_creation(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    calls = []

    monkeypatch.setattr(scope, "_configure_processes_with_shared_resources", lambda: calls.append("configure"))
    monkeypatch.setattr(scope, "_create_shared_buffers", lambda: calls.append("buffers"))

    scope._setup_shared_resources()

    assert calls == ["configure", "buffers"]


def test_configure_processes_assigns_shared_resources(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    hardware = scope_module.HardwareManagerBase()
    hardware.name = "DemoHardware"
    scope._hardware = {hardware.name: hardware}
    child_pipe = DummyPipe()

    class RecordingProcess:
        name = "worker"

        def __init__(self):
            self._quitting = DummyEvent()
            self.kwargs = None

        @property
        def quitting_event(self):
            return self._quitting

        def configure_shared_resources(self, **kwargs):
            self.kwargs = kwargs
            self._quitting = kwargs["quitting_event"]

    process = RecordingProcess()
    scope.processes = {process.name: process}
    monkeypatch.setattr(scope, "_setup_pipes", lambda: {process.name: child_pipe})
    monkeypatch.setattr(scope, "_setup_locks", lambda: scope.locks.update({"VideoBuffer": object()}))

    scope._configure_processes_with_shared_resources()

    kwargs = process.kwargs

    assert kwargs["camera_type"] is type(scope.camera_manager.camera)
    assert kwargs["hardware_types"] == {"DemoHardware": type(hardware)}
    assert kwargs["quitting_event"] is scope._quitting
    assert kwargs["settings"] is not scope.settings
    assert kwargs["settings"]["ROI"] == scope.settings["ROI"]
    assert kwargs["shared_values"] is scope.shared_values
    assert kwargs["locks"] is scope.locks
    assert kwargs["pipe_end"] is child_pipe
    assert kwargs["command_registry"] is scope.command_registry
    assert scope.quitting_events == {process.name: scope._quitting}


def test_register_script_methods_registers_base_and_processes(scope_module):
    scope = make_scope(scope_module)
    scope.processes = {
        "CameraManager": scope.camera_manager,
        "UIManager": scope.ui_manager,
    }
    scope.script_manager.script_registry.registered.clear()

    scope._register_script_methods()

    assert scope.script_manager.script_registry.registered == [
        scope_module.ManagerProcessBase,
        scope.camera_manager,
        scope.ui_manager,
    ]


def test_print_registered_commands_outputs_registered_handlers(scope_module, capsys):
    scope = make_scope(scope_module)

    scope.print_registered_commands()

    output = capsys.readouterr().out
    assert "MagScope:" in output
    assert "StartupReadyCommand -> MAG_SCOPE to MagScope via startup_ready" in output
    assert "QuitCommand -> BROADCAST to BROADCAST via quit" in output


def test_print_registered_script_commands_outputs_registered_methods(scope_module, capsys):
    scope = make_scope(scope_module)

    @dataclass(frozen=True)
    class ExampleScriptCommand(Command):
        pass

    scope.script_manager.script_registry._methods = {
        ExampleScriptCommand: types.SimpleNamespace(cls_name="Worker", meth_name="run"),
    }

    scope.print_registered_script_commands()

    output = capsys.readouterr().out
    assert "Script commands:" in output
    assert "ExampleScriptCommand -> Worker.run" in output


def test_initialize_shared_state_runs_setup_steps(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    calls = []

    monkeypatch.setattr(scope_module, "freeze_support", lambda: calls.append("freeze"))
    monkeypatch.setattr(scope, "_setup_command_registry", lambda: calls.append("registry"))
    monkeypatch.setattr(scope, "_setup_shared_resources", lambda: calls.append("resources"))
    monkeypatch.setattr(scope, "_register_script_methods", lambda: calls.append("scripts"))

    scope._initialize_shared_state()

    assert calls == ["freeze", "registry", "resources", "scripts"]


def test_start_managers_starts_each_process(scope_module):
    scope = make_scope(scope_module)
    scope.processes = {
        "CameraManager": scope.camera_manager,
        "UIManager": scope.ui_manager,
    }

    scope._start_managers()

    assert scope.camera_manager.start_called is True
    assert scope.ui_manager.start_called is True


def test_main_ipc_loop_resets_health_and_receives_until_stopped(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope._running = True
    calls = []

    monkeypatch.setattr(scope, "_reset_camera_health_logging_state", lambda: calls.append("reset"))

    def receive_once():
        calls.append("receive")
        scope._running = False

    monkeypatch.setattr(scope, "receive_ipc", receive_once)

    scope._main_ipc_loop()
    assert calls == ["reset", "receive"]


def test_join_processes_joins_each_process_and_logs(scope_module):
    scope = make_scope(scope_module)
    scope.processes = {
        "CameraManager": scope.camera_manager,
        "UIManager": scope.ui_manager,
    }
    scope_module.logger.info_calls.clear()

    scope._join_processes()
    assert scope.camera_manager.join_called is True
    assert scope.ui_manager.join_called is True
    assert (("%s ended.", "CameraManager"), {}) in scope_module.logger.info_calls
    assert (("%s ended.", "UIManager"), {}) in scope_module.logger.info_calls


def test_start_returns_in_child_process(scope_module, monkeypatch):
    scope = make_scope(scope_module)

    monkeypatch.setattr(scope_module, "freeze_support", lambda: None)
    monkeypatch.setattr(scope_module, "current_process", lambda: types.SimpleNamespace(name="SpawnProcess-1"))
    monkeypatch.setattr(
        scope,
        "_ensure_not_terminated",
        lambda: pytest.fail("child-process guard should return before startup checks"),
    )

    scope.start()

    assert scope._running is False


def test_start_returns_when_mark_running_rejects_start(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    calls = []

    monkeypatch.setattr(scope_module, "freeze_support", lambda: calls.append("freeze"))
    monkeypatch.setattr(scope, "_ensure_not_terminated", lambda: calls.append("ensure"))
    monkeypatch.setattr(scope, "_apply_logging_preferences", lambda: calls.append("logging"))
    monkeypatch.setattr(scope, "_mark_running", lambda: calls.append("mark") or False)
    monkeypatch.setattr(
        scope,
        "_start_startup_splash",
        lambda: pytest.fail("startup should stop before splash launch"),
    )

    scope.start()

    assert calls == ["freeze", "ensure", "logging", "mark"]


def test_start_prints_script_commands_in_print_only_mode(scope_module, monkeypatch):
    scope = make_scope(scope_module)
    scope.print_script_commands = True
    calls = []

    monkeypatch.setattr(scope, "_start_startup_splash", lambda: calls.append("start_splash"))
    monkeypatch.setattr(scope, "_stop_startup_splash", lambda: calls.append("stop_splash"))
    monkeypatch.setattr(scope, "_collect_processes", lambda: calls.append("collect"))
    monkeypatch.setattr(scope, "print_registered_script_commands", lambda: calls.append("print_scripts"))

    scope.start()

    assert calls == ["collect", "print_scripts"]
    assert scope._running is False
