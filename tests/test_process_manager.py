import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from time import time

import numpy as np
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
hardware_spec = importlib.util.spec_from_file_location(
    "magscope.hardware", ROOT / "magscope" / "hardware.py"
)
hardware = importlib.util.module_from_spec(hardware_spec)
sys.modules["magscope.hardware"] = hardware
hardware_spec.loader.exec_module(hardware)
import magscope.ipc_commands as ipc_commands
from magscope.ipc import CommandRegistry, Delivery, UnknownCommandError
from magscope.ipc_commands import (
    LogExceptionCommand,
    QuitCommand,
    ReportFocusMotorLimitsCommand,
    SetAcquisitionOnCommand,
    SetSimulatedFocusCommand,
)


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
    created = {"BeadRoiBuffer": [], "MatrixBuffer": [], "VideoBuffer": []}

    class FakeBeadRoiBuffer:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            created["BeadRoiBuffer"].append({"args": args, "kwargs": kwargs})

        def get_beads(self):
            return np.zeros((0,), dtype=np.uint32), np.zeros((0, 4), dtype=np.uint32)

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
    monkeypatch.setattr(processes, "BeadRoiBuffer", FakeBeadRoiBuffer)
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
    proc.locks = {"BeadRoiBuffer": object(), "LiveProfileBuffer": object()}
    with pytest.raises(RuntimeError, match="DummyProcess has no command registry"):
        proc.run()

    registry = CommandRegistry()
    registry.register_manager(proc)
    proc.configure_shared_resources(
        camera_type=None,
        hardware_types={},
        quitting_event=FakeEvent(),
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"BeadRoiBuffer": object(), "LiveProfileBuffer": object()},
        pipe_end=FakePipe(),
        command_registry=registry,
    )
    proc.run()

    assert proc.setup_called
    assert proc.main_loop_runs == 1
    assert proc._pipe.poll_calls == 1
    assert len(fake_buffers["BeadRoiBuffer"]) == 1
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
        quitting_event=quit_event,
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"BeadRoiBuffer": object(), "LiveProfileBuffer": object()},
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
        quitting_event=FakeEvent(),
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"BeadRoiBuffer": object(), "LiveProfileBuffer": object()},
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
        quitting_event=quitting_event,
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"BeadRoiBuffer": object(), "LiveProfileBuffer": object()},
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
        quitting_event=FakeEvent(),
        settings=FakeSettings(),
        shared_values=processes.InterprocessValues(),
        locks={"BeadRoiBuffer": object(), "LiveProfileBuffer": object()},
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


class FakeHardwareBuffer:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.rows = []
        self._unread_rows = []

    def write(self, row):
        copied = np.array(row, copy=True)
        self.rows.append(copied)
        self._unread_rows.append(copied)

    def read(self):
        if not self._unread_rows:
            return np.empty((0, 0), dtype=float)
        unread = np.vstack(self._unread_rows)
        self._unread_rows.clear()
        return unread


class DummyFocusMotor(hardware.FocusMotorBase):
    def __init__(self):
        super().__init__()
        self.position = 1.5
        self.target = 1.5
        self.moving = False

    def connect(self):
        self._is_connected = True

    def disconnect(self):
        self._is_connected = False

    def move_absolute(self, z: float) -> None:
        self.target = z
        self.moving = True

    def get_current_z(self) -> float:
        return self.position

    def get_is_moving(self) -> bool:
        return self.moving

    def get_position_limits(self) -> tuple[float, float]:
        return (0.0, 10.0)

    def _poll_hardware(self, now: float) -> None:
        if self.moving:
            self.position = self.target
            self.moving = False


def test_focus_motor_base_setup_writes_initial_state(monkeypatch):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None

    motor.setup()

    assert motor._is_connected is True
    assert isinstance(motor._buffer, FakeHardwareBuffer)
    assert len(motor._buffer.rows) == 1
    np.testing.assert_allclose(motor._buffer.rows[0][0, 1:], [1.5, 1.5, 1.0])


def test_focus_motor_base_clips_move_and_records_polled_state(monkeypatch):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor.setup()

    motor.handle_move_absolute(25.0)

    assert motor.get_target_z() == pytest.approx(10.0)
    assert motor.target == pytest.approx(10.0)
    assert motor.is_at_target() is False
    np.testing.assert_allclose(motor._buffer.rows[-1][0, 1:], [1.5, 10.0, 0.0])

    motor.fetch()

    assert motor.is_at_target() is True
    assert len(motor._buffer.rows) >= 3
    np.testing.assert_allclose(motor._buffer.rows[-1][0, 1:], [10.0, 10.0, 1.0])


def test_focus_motor_base_reports_position_limits():
    motor = DummyFocusMotor()
    sent_commands = []
    motor.send_ipc = sent_commands.append

    motor.report_focus_motor_limits()

    assert sent_commands == [ReportFocusMotorLimitsCommand(z_min=0.0, z_max=10.0)]


def test_hardware_manager_saves_to_device_file_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor._acquisition_dir = str(tmp_path)
    motor._acquisition_dir_on = True
    motor.setup()

    motor.do_main_loop()

    save_path = tmp_path / f"{motor.name}.txt"
    assert save_path.exists()
    saved = np.loadtxt(save_path)
    saved = np.atleast_2d(saved)
    assert saved.shape == (1, 4)
    np.testing.assert_allclose(saved[:, 1:], [[1.5, 1.5, 1.0]])


def test_hardware_manager_appends_only_new_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor._acquisition_dir = str(tmp_path)
    motor._acquisition_dir_on = True
    motor.setup()

    motor.do_main_loop()
    motor.handle_move_absolute(4.0)
    motor.do_main_loop()

    save_path = tmp_path / f"{motor.name}.txt"
    saved = np.loadtxt(save_path)
    saved = np.atleast_2d(saved)
    assert saved.shape == (3, 4)
    np.testing.assert_allclose(saved[:, 1:], [[1.5, 1.5, 1.0], [1.5, 4.0, 0.0], [4.0, 4.0, 1.0]])


def test_hardware_manager_does_not_save_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor._acquisition_dir = str(tmp_path)
    motor._acquisition_dir_on = False
    motor.setup()

    motor.do_main_loop()

    assert not (tmp_path / f"{motor.name}.txt").exists()


def test_hardware_manager_quit_calls_disconnect():
    class DisconnectTrackingMotor(DummyFocusMotor):
        def __init__(self):
            super().__init__()
            self.disconnect_called = False

        def disconnect(self):
            self.disconnect_called = True
            super().disconnect()

    motor = DisconnectTrackingMotor()
    motor.connect()
    assert motor._is_connected is True

    sent_commands = []
    motor.send_ipc = sent_commands.append
    motor.quit()

    assert motor.disconnect_called is True
    assert motor._is_connected is False


def test_hardware_manager_save_skips_empty_buffer(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor._acquisition_dir = str(tmp_path)
    motor._acquisition_dir_on = True
    motor.setup()

    motor._buffer.read = lambda: np.empty((0, 0), dtype=float)
    motor._save_pending_data_if_enabled()

    assert not (tmp_path / f"{motor.name}.txt").exists()


def test_hardware_manager_save_skips_all_nan_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor._acquisition_dir = str(tmp_path)
    motor._acquisition_dir_on = True
    motor.setup()

    motor._buffer.read()

    nan_row = np.array([[np.nan, np.nan, np.nan, np.nan]], dtype=float)
    motor._buffer.write(nan_row)
    motor._save_pending_data_if_enabled()

    assert not (tmp_path / f"{motor.name}.txt").exists()


def test_hardware_manager_save_header_appears_only_on_new_file(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor._acquisition_dir = str(tmp_path)
    motor._acquisition_dir_on = True
    motor.setup()

    motor._save_pending_data_if_enabled()

    save_path = tmp_path / f"{motor.name}.txt"
    raw = save_path.read_text(encoding="utf-8")
    assert raw.startswith("# timestamp value_1 value_2 value_3")

    motor._save_pending_data_if_enabled()
    raw_after = save_path.read_text(encoding="utf-8")
    count = raw_after.count("# timestamp value_1 value_2 value_3")
    assert count == 1


def test_hardware_manager_save_filepath_raises_without_acquisition_dir():
    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor._acquisition_dir = None

    with pytest.raises(RuntimeError, match="has no acquisition directory configured"):
        motor._hardware_save_filepath()

    motor._acquisition_dir = ""
    with pytest.raises(RuntimeError, match="has no acquisition directory configured"):
        motor._hardware_save_filepath()


def test_hardware_manager_save_header_single_column():
    motor = DummyFocusMotor()

    original_shape = motor.buffer_shape
    motor.buffer_shape = (1000, 1)
    header = motor._hardware_save_header()
    assert header == "timestamp"

    motor.buffer_shape = (1000, 3)
    header = motor._hardware_save_header()
    assert header == "timestamp value_1 value_2"

    motor.buffer_shape = original_shape


def test_hardware_manager_save_does_not_write_when_acquisition_dir_off(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor._acquisition_dir = str(tmp_path)
    motor._acquisition_dir_on = False
    motor.setup()

    motor.do_main_loop()

    save_path = tmp_path / f"{motor.name}.txt"
    assert not save_path.exists()


def test_focus_motor_fetch_returns_early_when_disconnected(monkeypatch):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor.setup()
    motor.disconnect()

    original_pos = motor.position
    motor.fetch()
    assert motor.position == original_pos


def test_focus_motor_is_at_target_with_explicit_tolerance():
    motor = DummyFocusMotor()
    motor.position = 5.0
    motor.moving = False

    motor._target_z = 5.0
    assert motor.is_at_target() is True
    assert motor.is_at_target(tolerance=1.0) is True

    motor.position = 5.02
    assert motor.is_at_target(tolerance=1.0) is True
    assert motor.is_at_target(tolerance=0.001) is False

    motor.position = 25.0
    assert motor.is_at_target() is False
    assert motor.is_at_target(tolerance=100.0) is True


def test_focus_motor_is_at_target_returns_false_when_moving():
    motor = DummyFocusMotor()
    motor.position = 5.0
    motor.moving = True
    motor._target_z = 5.0

    assert motor.is_at_target() is False


def test_focus_motor_write_state_raises_without_buffer():
    motor = DummyFocusMotor()
    motor._buffer = None

    with pytest.raises(RuntimeError, match="has no hardware buffer"):
        motor._write_state(0.0, 0.0)


def test_focus_motor_update_simulated_camera_focus_sends_command(monkeypatch):
    sent_commands = []

    class StubDummyCameraBeads:
        pass

    camera_module_stub = types.ModuleType("magscope.camera")
    camera_module_stub.DummyCameraBeads = StubDummyCameraBeads
    sys.modules["magscope.camera"] = camera_module_stub

    motor = DummyFocusMotor()
    motor.camera_type = StubDummyCameraBeads
    motor.send_ipc = sent_commands.append

    hardware.FocusMotorBase._update_simulated_camera_focus(motor, 3.0, force=True)

    assert len(sent_commands) == 1
    assert sent_commands[0].offset == 3.0

    hardware.FocusMotorBase._update_simulated_camera_focus(motor, 3.0, force=False)

    assert len(sent_commands) == 1

    hardware.FocusMotorBase._update_simulated_camera_focus(motor, 5.0, force=False)

    assert len(sent_commands) == 2
    assert sent_commands[1].offset == 5.0

    del sys.modules["magscope.camera"]


def test_focus_motor_fetch_skips_write_when_not_moved_and_interval_not_elapsed(monkeypatch):
    monkeypatch.setattr(hardware, "MatrixBuffer", FakeHardwareBuffer)

    motor = DummyFocusMotor()
    motor.locks = {motor.name: object()}
    motor.camera_type = None
    motor.setup()

    motor.position = 1.5
    motor._last_written = time() + 9999

    rows_before = len(motor._buffer.rows)
    motor.fetch()
    assert len(motor._buffer.rows) == rows_before


# ---------------------------------------------------------------------------
# send_ipc / receive_ipc error paths
# ---------------------------------------------------------------------------

def test_send_ipc_raises_without_command_registry():
    from magscope.ipc_commands import SleepCommand
    proc = DummyProcess()
    proc._command_registry = None
    with pytest.raises(RuntimeError, match="cannot send IPC without a command registry"):
        proc.send_ipc(SleepCommand(duration=1.0))


def test_send_ipc_raises_without_magscope_quitting():
    from magscope.ipc_commands import SleepCommand
    proc = DummyProcess()
    proc._command_registry = CommandRegistry()
    proc._magscope_quitting = None
    with pytest.raises(RuntimeError, match="has no magscope_quitting"):
        proc.send_ipc(SleepCommand(duration=1.0))


def test_receive_ipc_non_command_warns(monkeypatch):
    warnings_log = []
    monkeypatch.setattr(processes, "warn", lambda msg: warnings_log.append(msg))

    class PollingPipe(FakePipe):
        def poll(self):
            return bool(getattr(self, "incoming", None))

    proc = DummyProcess()
    pipe = PollingPipe()
    pipe.incoming = ["just a string"]
    proc._pipe = pipe
    proc._command_registry = CommandRegistry()
    proc.receive_ipc()
    assert len(warnings_log) >= 1


def test_bead_rois_property_converts_arrays_to_dict():
    import numpy as np
    proc = DummyProcess()
    proc._bead_roi_ids = np.asarray([1, 2], dtype=np.uint32)
    proc._bead_roi_values = np.asarray([[0, 10, 0, 10], [10, 20, 10, 20]], dtype=np.uint32)
    result = proc.bead_rois
    assert result == {1: (0, 10, 0, 10), 2: (10, 20, 10, 20)}


def test_refresh_bead_roi_cache_no_buffer():
    import numpy as np
    proc = DummyProcess()
    proc.bead_roi_buffer = None
    proc._refresh_bead_roi_cache()
    assert len(proc._bead_roi_ids) == 0
    assert len(proc._bead_roi_values) == 0


def test_singleton_meta_rejects_second_instance():
    from magscope.processes import SingletonMeta

    called = 0

    class DoubleCheck(metaclass=SingletonMeta):
        def __init__(self):
            nonlocal called
            called += 1

    first = DoubleCheck()
    assert called == 1
    with pytest.raises(TypeError, match="Cannot create another instance"):
        DoubleCheck()
    assert called == 1
    SingletonMeta._instances.clear()


def test_quitting_event_property():
    from multiprocessing import Event
    proc = DummyProcess()
    e = Event()
    proc._quitting = e
    assert proc.quitting_event is e


def test_quit_raises_when_no_magscope_quitting():
    proc = DummyProcess()
    proc._magscope_quitting = None
    proc._pipe = FakePipe()
    proc._quit_requested = True
    proc._quitting = FakeEvent()
    with pytest.raises(RuntimeError, match="has no magscope_quitting"):
        proc.quit()


def test_run_already_running_warns(monkeypatch):
    warnings_log = []
    monkeypatch.setattr(processes, "warn", lambda msg: warnings_log.append(msg))
    proc = DummyProcess()
    proc._running = True
    proc.run()
    assert len(warnings_log) >= 1
