import importlib.util
import sys
import types
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


class _DummyQImage:
    class Format:
        Format_Grayscale8 = object()
        Format_Grayscale16 = object()


qt_gui_module.QImage = _DummyQImage
qt_module.QtGui = qt_gui_module
sys.modules.setdefault("PyQt6", qt_module)
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
Message = utils.Message

processes_spec = importlib.util.spec_from_file_location(
    "magscope.processes", ROOT / "magscope" / "processes.py"
)
processes = importlib.util.module_from_spec(processes_spec)
sys.modules["magscope.processes"] = processes
processes_spec.loader.exec_module(processes)


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
    proc.locks = {"ProfilesBuffer": object()}
    proc.run()

    assert proc.setup_called
    assert proc.main_loop_runs == 1
    assert proc._pipe.poll_calls == 1
    assert len(fake_buffers["MatrixBuffer"]) == 2
    assert len(fake_buffers["VideoBuffer"]) == 1


def test_receive_ipc_dispatch_and_quit_flag():
    proc = DummyProcess()
    proc._pipe = FakePipe([
        Message("MagScope", "set_acquisition_on", False),
        Message("MagScope", "unknown_method"),
        Message("MagScope", "quit"),
    ])

    proc._acquisition_on = True
    proc.receive_ipc()
    assert proc._acquisition_on is False

    with pytest.warns(UserWarning, match="Function 'unknown_method' not found"):
        proc.receive_ipc()

    quit_called = []

    def fake_quit():
        quit_called.append(True)

    proc.quit = fake_quit
    assert proc._quit_requested is False
    proc.receive_ipc()
    assert proc._quit_requested is True
    assert quit_called == [True]


def test_quit_broadcasts_and_drains_pipe():
    proc = DummyProcess()
    quitting_event = FakeEvent()
    proc._magscope_quitting = quitting_event
    incoming = [Message("MagScope", "noop"), Message("MagScope", "noop2")]
    pipe = FakePipe(incoming=incoming, drain_event=quitting_event)
    proc._pipe = pipe
    proc.locks = {"ProfilesBuffer": object()}
    proc._running = True
    proc._quit_requested = False

    proc.quit()

    assert len(pipe.sent) == 1
    broadcast = pipe.sent[0]
    assert broadcast.to == "ManagerProcessBase"
    assert broadcast.meth == "quit"
    assert pipe.drained_messages == incoming
    assert pipe.closed
    assert proc._pipe is None
    assert quitting_event.set_calls >= 1


def test_run_reports_exception(monkeypatch):
    proc = DummyProcess()
    proc._pipe = FakePipe()
    proc.locks = {"ProfilesBuffer": object()}
    proc._magscope_quitting = FakeEvent()

    def raising_loop(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(proc, "do_main_loop", types.MethodType(raising_loop, proc))

    sent_messages = []

    def capture_send(self, message):
        sent_messages.append(message)

    monkeypatch.setattr(proc, "send_ipc", types.MethodType(capture_send, proc))

    with pytest.raises(RuntimeError, match="boom"):
        proc.run()

    assert len(sent_messages) == 1
    exception_message = sent_messages[0]
    assert exception_message.to == "MagScope"
    assert exception_message.meth == "log_exception"
    assert exception_message.args[0] == proc.name
    assert "boom" in exception_message.args[1]
