from __future__ import annotations

from abc import abstractmethod, ABC, ABCMeta
from multiprocessing import Process, Event
from typing import TYPE_CHECKING
from warnings import warn

from magscope.datatypes import VideoBuffer, MatrixBuffer
from magscope.utils import AcquisitionMode, Message, registerwithscript

if TYPE_CHECKING:
    from multiprocessing.connection import Connection
    from multiprocessing.synchronize import Event as EventType
    from multiprocessing.synchronize import Lock as LockType
    from multiprocessing.sharedctypes import Synchronized
    ValueTypeUI8 = Synchronized[int]
    from magscope.camera import CameraBase
    from magscope.hardware import HardwareManagerBase


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
        self._bead_rois: dict[int, tuple[int, int, int, int]] = {} # x0 x1 y0 y1
        self._camera_type: type[CameraBase] | None = None
        self._hardware_types: dict[str, type[HardwareManagerBase]] = {}
        self._locks: dict[str, LockType] | None = None
        self._magscope_quitting: EventType | None = None
        self._name: str = type(self).__name__ # Read-only
        self._pipe: Connection | None = None # Pipe back to the 'MagScope' for inter-process communication
        self._quitting: EventType = Event()
        self._quit_requested: bool = False # A flag to prevent repeated calls to 'quit()' after one process asks the others to quit
        self._running: bool = False
        self._settings = None
        self._tracks_buffer: MatrixBuffer | None = None
        self._video_buffer: VideoBuffer | None = None
        self._video_process_flag: ValueTypeUI8 | None = None

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
        if self._pipe is None:
            raise RuntimeError(f'{self.name} has no pipe')
        if self._locks is None:
            raise RuntimeError(f'{self.name} has no locks')
        if self._magscope_quitting is None:
            raise RuntimeError(f'{self.name} has no magscope_quitting event')

        self._video_buffer = VideoBuffer(
            create=False,
            locks=self._locks)
        self._tracks_buffer = MatrixBuffer(
            create=False,
            locks=self._locks,
            name='TracksBuffer')

        self.setup()

        print(f'{self.name} is running')
        self._running = True

        while self._running:
            self.do_main_loop()
            self.check_pipe()

    @abstractmethod
    def setup(self):
        pass

    @abstractmethod
    def do_main_loop(self):
        """ Main loop for the process """
        pass

    def quit(self):
        """ Shutdown the process (and ask the other processes to quit too) """
        self._quitting.set()
        self._running = False
        if not self._quit_requested:
            message = Message(ManagerProcessBase, ManagerProcessBase.quit)
            self.send(message)
        if self._pipe:
            while not self._magscope_quitting.is_set():
                if self._pipe.poll():
                    self._pipe.recv()
            self._pipe.close()
            self._pipe = None
        print(f'{self.name} quit')

    def send(self, message: Message):
        if self._pipe and not self._magscope_quitting.is_set():
            self._pipe.send(message)

    def check_pipe(self):
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

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        raise AttributeError("This property is read-only.")

    @property
    def locks(self):
        return self._locks

    @locks.setter
    def locks(self, value):
        raise AttributeError("This property is read-only.")

    def _set_locks(self, locks: dict[str, LockType]):
        self._locks = locks

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
        self._bead_rois = value

    def set_settings(self, settings: dict):
        self._settings = settings