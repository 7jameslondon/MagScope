from __future__ import annotations
from abc import ABC, ABCMeta, abstractmethod
import sys
import traceback
from ctypes import c_uint8
from multiprocessing import Event, Process, Value
from typing import TYPE_CHECKING
from warnings import warn

from magscope.datatypes import MatrixBuffer, VideoBuffer
from magscope.utils import AcquisitionMode, Message, registerwithscript

if TYPE_CHECKING:
    from multiprocessing.connection import Connection
    from multiprocessing.synchronize import Event as EventType
    from multiprocessing.synchronize import Lock as LockType
    from multiprocessing.sharedctypes import Synchronized
    ValueTypeUI8 = Synchronized[int]
    from magscope.camera import CameraBase
    from magscope.hardware import HardwareManagerBase


class InterprocessValues:
    def __init__(self):
        self.video_process_busy_count: ValueTypeUI8 = Value(c_uint8, 0)
        self.video_process_flag: ValueTypeUI8 = Value(c_uint8, 0)


class SingletonMeta(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        else:
            # Raise an exception if a second instance is attempted
            raise TypeError(f"Cannot create another instance of {cls.__name__}. This is a Singleton class.")
        return cls._instances[cls]


class SingletonABCMeta(ABCMeta, SingletonMeta):
    pass


class ManagerProcessBase(Process, ABC, metaclass=SingletonABCMeta):
    """ Abstract base class for processes in the MagScope

        Subclass requirements:
        * Each subclass should have a unique name.
        * There should only be one instance of each subclass (singleton).
        * The class name is used for consistent inter-process identification.
    """
    def __init__(self):
        # Note: Some setup/initialization will be at the beginning of the 'run()' method
        super().__init__()
        self._acquisition_on: bool = True
        self._acquisition_dir: str | None = None
        self._acquisition_dir_on: bool = False
        self._acquisition_mode: AcquisitionMode = AcquisitionMode.TRACK
        self.bead_rois: dict[int, tuple[int, int, int, int]] = {} # x0 x1 y0 y1
        self.camera_type: type[CameraBase] | None = None
        self.hardware_types: dict[str, type[HardwareManagerBase]] = {}
        self.locks: dict[str, LockType] | None = None
        self._magscope_quitting: EventType | None = None
        self.name: str = type(self).__name__ # Read-only
        self._pipe: Connection | None = None # Pipe back to the 'MagScope' for inter-process communication
        self.profiles_buffer: MatrixBuffer | None = None
        self._quitting: EventType = Event()
        self._quit_requested: bool = False # A flag to prevent repeated calls to 'quit()' after one process asks the others to quit
        self._running: bool = False
        self.settings = None
        self.tracks_buffer: MatrixBuffer | None = None
        self.video_buffer: VideoBuffer | None = None
        self.shared_values: InterprocessValues | None = None

    def run(self):
        """ Start the process when 'start()' is called

            run should create a loop that calls '_check_pipe()' last
            Example:
                while self._running:
                    # do other stuff
                    self._check_pipe() # should be done last
        """
        if self._running:
            warn(f'{self.name} is already running')
            return
        print(f'{self.name} is starting', flush=True)
        self._running = True

        try:
            if self._pipe is None:
                raise RuntimeError(f'{self.name} has no pipe')
            if self.locks is None:
                raise RuntimeError(f'{self.name} has no locks')
            if self._magscope_quitting is None:
                raise RuntimeError(f'{self.name} has no magscope_quitting event')

            self.profiles_buffer = MatrixBuffer(
                create=False,
                locks=self.locks,
                name='ProfilesBuffer',
            )
            self.tracks_buffer = MatrixBuffer(
                create=False,
                locks=self.locks,
                name='TracksBuffer',
            )
            self.video_buffer = VideoBuffer(
                create=False,
                locks=self.locks,
            )

            self.setup()

            while self._running:
                self.do_main_loop()
                self.receive_ipc()
        except Exception as exc:
            self._running = False
            self._report_exception(exc)
            raise

    @abstractmethod
    def setup(self):
        pass

    @abstractmethod
    def do_main_loop(self):
        pass

    def quit(self):
        """ Shutdown the process (and ask the other processes to quit too) """
        self._quitting.set()
        self._running = False
        if not self._quit_requested:
            message = Message(ManagerProcessBase, ManagerProcessBase.quit)
            self.send_ipc(message)
        if self._pipe:
            while not self._magscope_quitting.is_set():
                if self._pipe.poll():
                    self._pipe.recv()
            self._pipe.close()
            self._pipe = None
        print(f'{self.name} quit', flush=True)

    def send_ipc(self, message: Message):
        if self._pipe and not self._magscope_quitting.is_set():
            self._pipe.send(message)

    def receive_ipc(self):
        # Check pipe for new messages
        if self._pipe is None or not self._pipe.poll():
            return

        # Get the message
        message = self._pipe.recv()

        # Special case: if the message is 'quit'
        # then set a flag to prevent this message repeating
        if message.meth == 'quit':
            self._quit_requested = True

        # Dispatch the message
        if hasattr(self, message.meth):
            getattr(self, message.meth)(*message.args, **message.kwargs)
        else:
            warn(f"Function '{message.meth}' not found in {self.name}")

    @registerwithscript('set_acquisition_dir')
    def set_acquisition_dir(self, value: str):
        self._acquisition_dir = value

    @registerwithscript('set_acquisition_dir_on')
    def set_acquisition_dir_on(self, value: bool):
        self._acquisition_dir_on = value

    @registerwithscript('set_acquisition_mode')
    def set_acquisition_mode(self, mode: AcquisitionMode):
        self._acquisition_mode = mode

    @registerwithscript('set_acquisition_on')
    def set_acquisition_on(self, value: bool):
        self._acquisition_on = value

    def set_bead_rois(self, value: dict[int, tuple[int, int, int, int]]):
        self.bead_rois = value

    def set_settings(self, settings: dict):
        self.settings = settings

    def _report_exception(self, exc: BaseException) -> None:
        error_details = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        error_message = f"{self.name} encountered an unhandled exception:\n{error_details}"
        print(error_message, file=sys.stderr, flush=True)
        try:
            self.send_ipc(Message('MagScope', 'log_exception', self.name, error_details))
        except Exception:
            # The IPC pipe may already be unavailable; ensure we still surface the error locally.
            pass
