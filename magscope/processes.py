from warnings import warn
from multiprocessing import Process, Event
from typing import TYPE_CHECKING

from magscope import AcquisitionMode, Message, VideoBuffer, MatrixBuffer

if TYPE_CHECKING:
    from multiprocessing.connection import Connection
    from multiprocessing.synchronize import Event as EventType
    from multiprocessing.synchronize import Lock as LockType
    from multiprocessing.sharedctypes import Synchronized
    ValueTypeUI8 = Synchronized[int]
    from magscope.camera import CameraABC

class SingletonMeta(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        else:
            # Raise an exception if a second instance is attempted
            raise TypeError(f"Cannot create another instance of {cls.__name__}. This is a Singleton class.")
        return cls._instances[cls]

class ManagerProcess(Process, metaclass=SingletonMeta):
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
        self._camera_type: type[CameraABC] | None = None
        self._locks: dict[str, LockType] | None = None
        self._name: str = type(self).__name__ # Read-only
        self._pipe: Connection | None = None # Pipe back to the 'MagScope' for inter-process communication
        self._bead_rois: dict[int, tuple[int, int, int, int]] = {} # x0 x1 y0 y1
        self._running: bool = False
        self._quitting: EventType = Event()
        self._magscope_quitting: EventType | None = None
        self._quit_requested: bool = False # A flag to prevent repeated calls to 'quit()' after one process asks the others to quit
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

        print(f'{self.name} is running')
        self._running = True

    def quit(self):
        """ Shutdown the process (and ask the other processes to quit too) """
        self._quitting.set()
        self._running = False
        if not self._quit_requested:
            message = Message(ManagerProcess, ManagerProcess.quit)
            self._send(message)
        if self._pipe:
            while not self._magscope_quitting.is_set():
                if self._pipe.poll():
                    self._pipe.recv()
            self._pipe.close()
            self._pipe = None
        print(f'{self.name} quit')

    def _send(self, message: Message):
        if self._pipe and not self._magscope_quitting.is_set():
            self._pipe.send(message)

    def _check_pipe(self):
        # Check pipe for new messages
        if self._pipe is None or not self._pipe.poll():
            return

        # Get the message
        message = self._pipe.recv()

        # Special case: if the message is 'quit'
        # then set a flag to prevent this message repeating
        if message.func == 'quit':
            self._quit_requested = True

        # Dispatch the message
        if hasattr(self, message.func):
            getattr(self, message.func)(*message.args, **message.kwargs)
        else:
            warn(f"Function '{message.func}' not found in {self.name}")

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        raise AttributeError("This property is read-only.")

    def set_acquisition_dir(self, value: str):
        self._acquisition_dir = value

    def set_acquisition_dir_on(self, value: bool):
        self._acquisition_dir_on = value

    def set_acquisition_mode(self, mode: AcquisitionMode):
        self._acquisition_mode = mode

    def set_acquisition_on(self, value: bool):
        self._acquisition_on = value

    def set_bead_rois(self, value: dict[int, tuple[int, int, int, int]]):
        self._bead_rois = value

    def set_settings(self, settings: dict):
        self._settings = settings