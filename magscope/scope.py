"""Core orchestration for the MagScope application.

This module provides the :class:`MagScope` class, the top-level coordinator that
bootstraps every subsystem required to run the magnetic tweezer and microscopy
stack.  ``MagScope`` is responsible for:

* Instantiating each manager process (camera, bead lock, GUI, scripting, video
  processing, and optional hardware integrations).
* Loading configuration from YAML, sharing it across processes, and creating
  the inter-process communication (IPC) primitives required for collaboration.
* Owning the main event loop that relays :class:`~magscope.utils.Message`
  objects between processes and supervises orderly shutdown.

The class operates as a façade around a fleet of ``multiprocessing``
``Process`` subclasses.  ``MagScope.start`` prepares shared memory buffers,
registers available scripting hooks, and then enters a loop forwarding IPC
messages until a quit command is received.

Example
-------
Run the simulated scope with its default managers::

    >>> from magscope.scope import MagScope
    >>> scope = MagScope()
    >>> scope.start()

For headless automation you can add hardware adapters and GUI panels before
invoking :meth:`MagScope.start`::

    >>> scope.add_hardware(custom_hardware_manager)
    >>> scope.add_control(CustomPanel, column=0)
    >>> scope.start()

``MagScope`` constructs the following high-level pipeline:

``CameraManager`` → ``VideoBuffer`` → ``VideoProcessorManager`` → ``WindowManager``
and
``BeadLockManager`` → ``MatrixBuffer`` → ``WindowManager``

Every manager receives shared locks, pipes, and configuration from the main
process so that real-time video frames, bead tracking data, and scripted events
remain synchronized.
"""

from ctypes import c_uint8
from multiprocessing import Event, freeze_support, Pipe, Lock, Value
import numpy as np
import os
import sys
from typing import TYPE_CHECKING
from warnings import warn
import yaml

from magscope.beadlock import BeadLockManager
from magscope.camera import CameraManager
from magscope.datatypes import MatrixBuffer, VideoBuffer
from magscope.gui import ControlPanelBase, WindowManager, TimeSeriesPlotBase
from magscope.hardware import HardwareManagerBase
from magscope.processes import InterprocessValues, ManagerProcessBase
from magscope.scripting import ScriptManager
from magscope.utils import Message
from magscope.videoprocessing import VideoProcessorManager

if TYPE_CHECKING:
    from multiprocessing.connection import Connection
    from multiprocessing.synchronize import Event as EventType
    from multiprocessing.synchronize import Lock as LockType

class MagScope:
    """Main entry point for coordinating all MagScope subsystems.

    The class holds references to every manager process, shared buffer, and IPC
    primitive used by the application.  ``MagScope`` can be instantiated on its
    own and started immediately, or it can be configured by adding hardware
    managers, GUI controls, or time-series plots before calling
    :meth:`start`.
    """

    def __init__(self):
        self.beadlock_manager = BeadLockManager()
        self.camera_manager = CameraManager()
        self._default_settings_path = os.path.join(os.path.dirname(__file__), 'default_settings.yaml')
        self._hardware: dict[str, HardwareManagerBase] = {}
        self._hardware_buffers: dict[str, MatrixBuffer] = {}
        self.shared_values: InterprocessValues = InterprocessValues()
        self.locks: dict[str, LockType] = {}
        self.lock_names: list[str] = ['ProfilesBuffer', 'TracksBuffer', 'VideoBuffer']
        self.pipes: dict[str, Connection] = {}
        self.processes: dict[str, ManagerProcessBase] = {}
        self.profiles_buffer: MatrixBuffer | None = None
        self._quitting: Event = Event()
        self.quitting_events: dict[str, EventType] = {}
        self._running: bool = False
        self.script_manager = ScriptManager()
        self._settings = self._get_default_settings()
        self._settings_path = 'settings.yaml'
        self.tracks_buffer: MatrixBuffer | None = None
        self.video_buffer: VideoBuffer | None = None
        self.video_processor_manager = VideoProcessorManager()
        self.window_manager = WindowManager()

    def start(self):
        """Launch all managers and enter the main IPC loop.

        The startup sequence performs the following steps:

        1. Collect every manager (built-in and user-supplied hardware) and
           assign them a shared :attr:`processes` mapping for bookkeeping.
        2. Load configuration values, prepare shared memory buffers, locks,
           pipes, and register scriptable methods.
        3. Spawn each manager process and then forward IPC messages until a
           quit signal is observed.

        When a quit message is received the method joins every process before
        returning control to the caller.
        """
        if self._running:
            warn('MagScope is already running')
        self._running = True

        # ===== Collect separate processes in a dictionary =====
        proc_list: list[ManagerProcessBase] = [
            self.script_manager, # ScriptManager must be first in this list for @registerwithscript to work
            self.camera_manager,
            self.beadlock_manager,
            self.video_processor_manager,
            self.window_manager
        ]
        proc_list.extend(self._hardware.values())
        for proc in proc_list:
            self.processes[proc.name] = proc

        # ===== Setup and share resources =====
        freeze_support()  # To prevent recursion in windows executable
        self._load_settings()
        self._setup_shared_resources()
        self._register_script_methods()

        # ===== Start the managers =====
        for proc in self.processes.values():
            proc.start() # calls 'run()'

        # ===== Wait in loop for inter-process messages =====
        print('MagScope main loop starting ...', flush=True)
        while self._running:
            self.receive_ipc()
        print('MagScope main loop ended.', flush=True)

        # ===== End program by joining each process =====
        for name, proc in self.processes.items():
            proc.join()
            print(name, 'ended.', flush=True)

    def receive_ipc(self):
        """Poll every IPC pipe and relay messages between processes."""
        for pipe in self.pipes.values():
            # Check if this pipe has a message
            if not pipe.poll():
                continue

            # Get the message
            message = pipe.recv()

            print(message)

            if type(message) is not Message:
                warn(f'Message is not a Message object: {message}')
                continue

            # Process the message
            if message.to == 'MagScope':
                self._handle_mag_scope_message(message)
            elif message.to == ManagerProcessBase.__name__: # the message is to all processes
                if message.meth == 'quit':
                    print('MagScope quitting ...')
                    self._quitting.set()
                    self._running = False
                for name, pipe2 in self.pipes.items():
                    if self.processes[name].is_alive() and not self.quitting_events[name].is_set():
                        pipe2.send(message)
                        if message.meth == 'quit':
                            while not self.quitting_events[name].is_set():
                                if pipe2.poll():
                                    pipe2.recv()
                if message.meth == 'quit':
                    break
            elif message.to in self.pipes.keys(): # the message is to one process
                if self.processes[message.to].is_alive() and not self.quitting_events[message.to].is_set():
                    self.pipes[message.to].send(message)
            else:
                warn(f'Unknown pipe {message.to} with {message}')

    def _handle_mag_scope_message(self, message: Message) -> None:
        if message.meth == 'log_exception':
            if len(message.args) >= 2:
                proc_name, details = message.args[:2]
            else:
                proc_name, details = ('<unknown>', '')
            print(
                f'[{proc_name}] Unhandled exception in child process:\n{details}',
                file=sys.stderr,
                flush=True,
            )
        else:
            warn(f'Unknown MagScope message {message.meth} with {message.args}')

    def _setup_shared_resources(self):
        """Create and distribute shared locks, pipes, buffers, and metadata."""
        # Create and share: locks, pipes, flags, types, etc.
        camera_type = type(self.camera_manager.camera)
        hardware_types = {name: type(hardware) for name, hardware in self._hardware.items()}
        for name, proc in self.processes.items():
            proc.camera_type = camera_type
            proc.hardware_types = hardware_types
            proc._magscope_quitting = self._quitting
            proc.settings = self._settings
            proc.shared_values = self.shared_values
            self.quitting_events[name] = proc._quitting
        self._setup_pipes()
        self._setup_locks()

        # Create the shared buffers
        self.profiles_buffer = MatrixBuffer(
            create=True,
            locks=self.locks,
            name='ProfilesBuffer',
            shape=(1000, 2+self.settings['bead roi width'])
        )
        self.tracks_buffer = MatrixBuffer(
            create=True,
            locks=self.locks,
            name='TracksBuffer',
            shape=(self._settings['tracks max datapoints'], 7)
        )
        self.video_buffer = VideoBuffer(
            create=True,
            locks=self.locks,
            n_stacks=self._settings['video buffer n stacks'],
            n_images=self._settings['video buffer n images'],
            width=self.camera_manager.camera.width,
            height=self.camera_manager.camera.height,
            bits=np.iinfo(self.camera_manager.camera.dtype).bits
        )
        for name, hardware in self._hardware.items():
            self._hardware_buffers[name] = MatrixBuffer(
                create=True,
                locks=self.locks,
                name=name,
                shape=hardware.buffer_shape
            )

    def _setup_locks(self):
        """Instantiate per-buffer locks and make them available to processes."""
        self.lock_names.extend(self._hardware.keys())
        for name in self.lock_names:
            self.locks[name] = Lock()
        for proc in self.processes.values():
            proc.locks = self.locks

    def _setup_pipes(self):
        """Create duplex pipes that allow processes to exchange messages."""
        for name, proc in self.processes.items():
            pipe = Pipe()
            self.pipes[name] = pipe[0]
            proc._pipe = pipe[1]

    def _register_script_methods(self):
        """Expose manager methods to the scripting subsystem."""
        self.script_manager.script_registry.register_class_methods(ManagerProcessBase)
        for proc in self.processes.values():
            self.script_manager.script_registry.register_class_methods(proc)

    def _get_default_settings(self):
        """Load the project's default YAML configuration shipped with MagScope."""
        with open(self._default_settings_path, 'r') as f:
            settings = yaml.safe_load(f)
        return settings

    def _load_settings(self):
        """Merge user overrides from :attr:`settings_path` into active settings."""
        if not self._settings_path.endswith('.yaml'):
            warn("Settings path must be a .yaml file")
        elif not os.path.exists(self._settings_path):
            warn(f"Settings file {self._settings_path} did not exist. Creating it now.")
            with open(self._settings_path, 'w') as f:
                yaml.dump(self._settings, f)
        else:
            try:
                with open(self._settings_path, 'r') as f:
                    settings = yaml.safe_load(f)
                self._settings.update(settings)
            except yaml.YAMLError as e:
                warn(f"Error loading settings file {self._settings_path}: {e}")

    @property
    def settings_path(self):
        return self._settings_path

    @settings_path.setter
    def settings_path(self, value):
        if self._running:
            warn('MagScope is already running')
        self._settings_path = value

    @property
    def settings(self):
        return self._settings

    @settings.setter
    def settings(self, value):
        self._settings = value
        if self._running:
            for pipe in self.pipes.values():
                pipe.send(Message(ManagerProcessBase, ManagerProcessBase.set_settings, value))

    def add_hardware(self, hardware: HardwareManagerBase):
        """Register a hardware manager so its process launches with MagScope."""
        self._hardware[hardware.name] = hardware

    def add_control(self, control_type: type(ControlPanelBase), column: int):
        """Schedule a GUI control panel to be added when the window manager starts."""
        self.window_manager.controls_to_add.append((control_type, column))

    def add_timeplot(self, plot: TimeSeriesPlotBase):
        """Schedule a time-series plot for inclusion in the GUI at startup."""
        self.window_manager.plots_to_add.append(plot)
