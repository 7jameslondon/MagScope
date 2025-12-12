import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

magscope_pkg = types.ModuleType("magscope")
magscope_pkg.__path__ = [str(ROOT / "magscope")]
sys.modules.setdefault("magscope", magscope_pkg)

qt_module = types.ModuleType("PyQt6")
qt_gui_module = types.ModuleType("PyQt6.QtGui")
qt_core_module = types.ModuleType("PyQt6.QtCore")


class _DummyQImage:
    class Format:
        Format_Grayscale8 = object()
        Format_Grayscale16 = object()


class _DummyQSettings:
    _store: dict[str, object] = {}

    def __init__(self, *args, **kwargs):
        self._values = self._store

    def beginGroup(self, _):  # noqa: N802 - Qt naming
        return None

    def contains(self, key: str) -> bool:  # noqa: N802 - Qt naming
        return key in self._values

    def endGroup(self):  # noqa: N802 - Qt naming
        return None

    def remove(self, key: str):  # noqa: N802 - Qt naming
        if key in ("", None):
            self._values.clear()
        else:
            self._values.pop(key, None)

    def setValue(self, key: str, value: object):  # noqa: N802 - Qt naming
        self._values[key] = value

    def sync(self):  # noqa: N802 - Qt naming
        return None

    def value(self, key: str):  # noqa: N802 - Qt naming
        return self._values.get(key)


qt_gui_module.QImage = _DummyQImage
qt_core_module.QSettings = _DummyQSettings
qt_module.QtCore = qt_core_module
qt_module.QtGui = qt_gui_module
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core_module)
sys.modules.setdefault("PyQt6.QtGui", qt_gui_module)

datatypes_spec = importlib.util.spec_from_file_location(
    "magscope.datatypes", ROOT / "magscope" / "datatypes.py"
)
datatypes = importlib.util.module_from_spec(datatypes_spec)
sys.modules["magscope.datatypes"] = datatypes
datatypes_spec.loader.exec_module(datatypes)

utils_spec = importlib.util.spec_from_file_location(
    "magscope.utils", ROOT / "magscope" / "utils.py"
)
utils = importlib.util.module_from_spec(utils_spec)
sys.modules["magscope.utils"] = utils
utils_spec.loader.exec_module(utils)

processes_spec = importlib.util.spec_from_file_location(
    "magscope.processes", ROOT / "magscope" / "processes.py"
)
processes = importlib.util.module_from_spec(processes_spec)
sys.modules["magscope.processes"] = processes
processes_spec.loader.exec_module(processes)
import magscope.ipc_commands as ipc_commands
from magscope.ipc import CommandRegistry, Delivery, UnknownCommandError
from magscope.ipc_commands import LogExceptionCommand, QuitCommand, SetAcquisitionOnCommand


class FakeEvent:
    def __init__(self):
        self._flag = False
        self.set_calls = 0
        self.is_set_calls = 0

    def set(self):
        self._flag = True
        self.set_calls += 1

    def is_set(self):
        self.is_set_calls += 1
        return self._flag


class FakePipe:
    def __init__(self, incoming=None, drain_event=None):
        self.incoming = list(incoming or [])
        self.sent = []
        self.closed = False
        self.poll_calls = 0
        self.recv_calls = 0
        self.drained_messages = []
        self._drain_event = drain_event

    def poll(self):
        self.poll_calls += 1
        return bool(self.incoming)

    def recv(self):
        self.recv_calls += 1
        if not self.incoming:
            raise RuntimeError("No messages available")
        message = self.incoming.pop(0)
        self.drained_messages.append(message)
        if not self.incoming and self._drain_event is not None:
            self._drain_event.set()
        return message

    def send(self, message):
        self.sent.append(message)

    def close(self):
        self.closed = True


class FakeSettings(dict):
    def clone(self):
        return FakeSettings(self)


class DummyProcess(processes.ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self.setup_called = False
        self.main_loop_runs = 0

    def setup(self):
        self.setup_called = True

    def do_main_loop(self):
        self.main_loop_runs += 1
        self._running = False


@pytest.fixture(autouse=True)
def clear_singletons():
    processes.SingletonMeta._instances.clear()
    try:
        yield
    finally:
        processes.SingletonMeta._instances.clear()


@pytest.fixture(autouse=True)
def fake_buffers(monkeypatch):
    created = {"MatrixBuffer": [], "VideoBuffer": []}

    class FakeMatrixBuffer:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            created["MatrixBuffer"].append({"args": args, "kwargs": kwargs})

    class FakeVideoBuffer:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            created["VideoBuffer"].append({"args": args, "kwargs": kwargs})

    class FakeLiveProfileBuffer:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            created.setdefault("LiveProfileBuffer", []).append({"args": args, "kwargs": kwargs})
            locks = kwargs.get("locks", {})
            name = kwargs.get("name", "LiveProfileBuffer")
            FakeMatrixBuffer(create=kwargs.get("create", False), locks=locks, name=name, shape=None)

    monkeypatch.setattr(processes, "LiveProfileBuffer", FakeLiveProfileBuffer)
    monkeypatch.setattr(processes, "MatrixBuffer", FakeMatrixBuffer)
    monkeypatch.setattr(processes, "VideoBuffer", FakeVideoBuffer)
    return created


def test_run_validates_dependencies(fake_buffers):
    proc = DummyProcess()

    proc.locks = {}
    proc._magscope_quitting = FakeEvent()
    with pytest.raises(RuntimeError, match="DummyProcess has no pipe"):
        proc.run()

    pipe = FakePipe()
    proc._pipe = pipe
    proc.locks = None
    with pytest.raises(RuntimeError, match="DummyProcess has no locks"):
        proc.run()

    proc.locks = {}
    proc._magscope_quitting = None
    with pytest.raises(RuntimeError, match="DummyProcess has no magscope_quitting event"):
        proc.run()

    proc._magscope_quitting = FakeEvent()
    proc._pipe = FakePipe()
    proc.locks = {"LiveProfileBuffer": object()}
    with pytest.raises(RuntimeError, match="DummyProcess has no command registry"):
        proc.run()

    registry = CommandRegistry()
    registry.register_manager(proc)
    proc.configure_shared_resources(
        camera_type=None,
        hardware_types={},
        focus_motor_name=None,
        quitting_event=FakeEvent(),
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"LiveProfileBuffer": object()},
        pipe_end=FakePipe(),
        command_registry=registry,
    )
    proc.run()

    assert proc.setup_called
    assert proc.main_loop_runs == 1
    assert proc._pipe.poll_calls == 1
    assert len(fake_buffers["MatrixBuffer"]) == 2
    assert len(fake_buffers["VideoBuffer"]) == 1


def test_receive_ipc_dispatch_and_quit_flag():
    proc = DummyProcess()
    registry = CommandRegistry()
    registry.register_manager(proc)
    quit_event = FakeEvent()
    pipe = FakePipe([
        SetAcquisitionOnCommand(value=False),
        QuitCommand(),
    ])
    proc.configure_shared_resources(
        camera_type=None,
        hardware_types={},
        focus_motor_name=None,
        quitting_event=quit_event,
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"LiveProfileBuffer": object()},
        pipe_end=pipe,
        command_registry=registry,
    )

    proc._acquisition_on = True
    proc.receive_ipc()
    assert proc._acquisition_on is False

    quit_called = []

    def fake_quit():
        quit_called.append(True)

    proc.quit = fake_quit
    assert proc._quit_requested is False
    proc.receive_ipc()
    assert proc._quit_requested is True
    assert quit_called == [True]


def test_receive_ipc_errors_on_unknown_command():
    @dataclass(frozen=True)
    class Unknown(ipc_commands.Command):
        value: int = 0

    proc = DummyProcess()
    registry = CommandRegistry()
    registry.register_manager(proc)
    proc.configure_shared_resources(
        camera_type=None,
        hardware_types={},
        focus_motor_name=None,
        quitting_event=FakeEvent(),
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"LiveProfileBuffer": object()},
        pipe_end=FakePipe([Unknown()]),
        command_registry=registry,
    )

    with pytest.raises(UnknownCommandError):
        proc.receive_ipc()


def test_quit_broadcasts_and_drains_pipe():
    proc = DummyProcess()
    quitting_event = FakeEvent()
    incoming = [SetAcquisitionOnCommand(value=True), SetAcquisitionOnCommand(value=False)]
    pipe = FakePipe(incoming=incoming, drain_event=quitting_event)
    registry = CommandRegistry()
    registry.register_manager(proc)
    proc.configure_shared_resources(
        camera_type=None,
        hardware_types={},
        focus_motor_name=None,
        quitting_event=quitting_event,
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"LiveProfileBuffer": object()},
        pipe_end=pipe,
        command_registry=registry,
    )
    proc._running = True
    proc._quit_requested = False

    proc.quit()

    assert len(pipe.sent) == 1
    broadcast = pipe.sent[0]
    assert isinstance(broadcast, QuitCommand)
    assert pipe.drained_messages == incoming
    assert pipe.closed
    assert proc._pipe is None
    assert quitting_event.set_calls >= 1


def test_run_reports_exception(monkeypatch):
    proc = DummyProcess()
    registry = CommandRegistry()
    registry.register_manager(proc)

    class MagScopeStub:
        def log_exception(self, process_name: str, details: str):
            return None

    registry.register(
        command_type=LogExceptionCommand,
        handler="log_exception",
        owner=MagScopeStub,
        delivery=Delivery.MAG_SCOPE,
        target="MagScope",
    )
    pipe = FakePipe()
    proc.configure_shared_resources(
        camera_type=None,
        hardware_types={},
        focus_motor_name=None,
        quitting_event=FakeEvent(),
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"LiveProfileBuffer": object()},
        pipe_end=pipe,
        command_registry=registry,
    )

    def raising_loop(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(proc, "do_main_loop", types.MethodType(raising_loop, proc))

    with pytest.raises(RuntimeError, match="boom"):
        proc.run()

    assert len(pipe.sent) == 1
    exception_message = pipe.sent[0]
    assert isinstance(exception_message, LogExceptionCommand)
    assert exception_message.process_name == proc.name
    assert "boom" in exception_message.details
