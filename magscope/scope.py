"""Core orchestration for the MagScope application.

``MagScope`` is the top-level coordinator responsible for preparing shared
resources (configuration, shared-memory buffers, locks, and IPC pipes),
constructing manager processes, and relaying messages between them until
shutdown. Its duties include:

* Instantiating manager processes (camera, bead lock, GUI, scripting, video
  processing, and optional hardware integrations).
* Loading configuration from YAML, merging user overrides, and distributing the
  result to each process.
* Owning the main event loop that forwards :class:`~magscope.utils.Message`
  objects and supervises orderly shutdown.

The orchestrator runs as a façade around a fleet of ``multiprocessing``
``Process`` subclasses.  ``MagScope.start`` prepares shared resources,
registers available scripting hooks, starts each process, and then loops until
a quit command is received.

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

import logging
import os
import sys
import time
from multiprocessing import Event, Lock, freeze_support
from multiprocessing.connection import Connection
from typing import TYPE_CHECKING
from warnings import warn

import numpy as np
import yaml

from magscope._logging import configure_logging, get_logger
from magscope.beadlock import BeadLockManager
from magscope.camera import CameraManager
from magscope.datatypes import MatrixBuffer, VideoBuffer
from magscope.gui import ControlPanelBase, TimeSeriesPlotBase, WindowManager
from magscope.hardware import HardwareManagerBase
from magscope.ipc import broadcast_message, create_pipes, drain_pipe_until_quit
from magscope.processes import InterprocessValues, ManagerProcessBase
from magscope.scripting import ScriptManager
from magscope.utils import Message
from magscope.videoprocessing import VideoProcessorManager

logger = get_logger("scope")

if TYPE_CHECKING:
    from multiprocessing.synchronize import Event as EventType
    from multiprocessing.synchronize import Lock as LockType

class MagScope:
    """Coordinate MagScope managers, shared resources, and IPC.

    ``MagScope`` owns references to every manager process, shared buffer, and
    IPC primitive used by the application. Instances can be customized by
    adding hardware managers, GUI controls, or time-series plots before calling
    :meth:`start`. Once started, the instance supervises manager lifetimes until
    it receives a quit signal broadcast over the IPC bus.
    """

    def __init__(self, *, verbose: bool = False):
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
        self._log_level = logging.INFO if verbose else logging.WARNING
        configure_logging(level=self._log_level)

    def set_verbose_logging(self, enabled: bool = True) -> None:
        """Toggle informational console output for MagScope internals."""

        self._log_level = logging.INFO if enabled else logging.WARNING
        configure_logging(level=self._log_level)

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
        self._apply_logging_preferences()

        if not self._mark_running():
            return

        self._collect_processes()
        self._initialize_shared_state()
        self._start_managers()
        self._main_ipc_loop()
        self._join_processes()

    def _mark_running(self) -> bool:
        """Mark the orchestrator as running if it is not already active."""

        if self._running:
            warn('MagScope is already running')
            return False

        self._running = True
        return True

    def _apply_logging_preferences(self) -> None:
        """Apply the current verbosity preference to the logging system."""
        configure_logging(level=self._log_level)

    def _collect_processes(self) -> None:
        """Assemble the ordered list of manager processes to supervise.

        ScriptManager must remain first so that the ``@registerwithscript``
        decorator binds correctly before other managers start.
        """
        proc_list: list[ManagerProcessBase] = [
            self.script_manager,  # ScriptManager must be first in this list for @registerwithscript to work
            self.camera_manager,
            self.beadlock_manager,
            self.video_processor_manager,
            self.window_manager,
        ]
        proc_list.extend(self._hardware.values())

        self.processes = {}
        for proc in proc_list:
            self.processes[proc.name] = proc

    def _initialize_shared_state(self) -> None:
        """Load configuration and prepare shared resources for all managers."""
        freeze_support()  # To prevent recursion in windows executable
        self._load_settings()
        self._setup_shared_resources()
        self._register_script_methods()

    def _start_managers(self) -> None:
        """Start each manager process."""
        for proc in self.processes.values():
            proc.start()  # calls 'run()'

    def _main_ipc_loop(self) -> None:
        """Forward IPC messages until a quit request is observed."""
        logger.info('MagScope main loop starting ...')
        while self._running:
            self.receive_ipc()
        logger.info('MagScope main loop ended.')

    def _join_processes(self) -> None:
        """Join every managed process once shutdown has been requested."""
        for name, proc in self.processes.items():
            proc.join()
            logger.info('%s ended.', name)

    def receive_ipc(self):
        """Poll every IPC pipe once and relay any messages that arrive."""
        handled_message = False
        for _name, pipe in self.pipes.items():
            # Check if this pipe has a message
            if not pipe.poll():
                continue

            # Get the message
            message = pipe.recv()

            handled_message = True

            logger.info('%s', message)

            if not isinstance(message, Message):
                warn(f'Message is not a Message object: {message}')
                continue

            if self._route_message(message):
                break

        if not handled_message:
            self._sleep_when_idle()

    def _route_message(self, message: Message) -> bool:
        """Dispatch a message based on its destination.

        Returns ``True`` when the IPC loop should stop iterating over the
        current set of pipes (for example, immediately after handling a quit
        broadcast). This mirrors the previous behavior of breaking out of the
        ``receive_ipc`` loop once a quit message has been processed.
        """
        if message.to == 'MagScope':
            self._handle_mag_scope_message(message)
        elif message.to == ManagerProcessBase.__name__:  # the message is to all processes
            if self._handle_broadcast_message(message):
                return True
        elif message.to in self.pipes:  # the message is to one process
            if self.processes[message.to].is_alive() and not self.quitting_events[message.to].is_set():
                self.pipes[message.to].send(message)
        else:
            warn(f'Unknown pipe {message.to} with {message}')

        return False

    def _handle_mag_scope_message(self, message: Message) -> None:
        """Handle messages whose destination is the MagScope orchestrator."""

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

    def _handle_broadcast_message(self, message: Message) -> bool:
        """Broadcast a message to all processes and handle quit semantics.

        Returns ``True`` when the caller should stop processing the current
        IPC loop (e.g., after handling a quit message).
        """
        if message.meth == 'quit':
            logger.info('MagScope quitting ...')
            self._quitting.set()
            self._running = False

        broadcast_message(
            message,
            pipes=self.pipes,
            processes=self.processes,
            quitting_events=self.quitting_events,
        )

        if message.meth == 'quit':
            self._drain_child_pipes_after_quit()
            return True

        return False

    def _sleep_when_idle(self) -> None:
        """Throttle the IPC loop when no messages were processed."""

        time.sleep(0.001)

    def _drain_child_pipes_after_quit(self) -> None:
        """Drain child pipes until they acknowledge the quit event."""

        for name, pipe in self.pipes.items():
            if self.processes[name].is_alive() and not self.quitting_events[name].is_set():
                drain_pipe_until_quit(pipe, self.quitting_events[name])

    def _setup_shared_resources(self):
        """Create and distribute shared locks, pipes, buffers, and metadata."""
        self._configure_processes_with_shared_resources()
        self._create_shared_buffers()

    def _configure_processes_with_shared_resources(self):
        """Share locks, pipes, and configuration with each process.

        This step must occur before any manager processes are started so they can
        inherit references to shared multiprocessing primitives.
        """
        camera_type = type(self.camera_manager.camera)
        hardware_types = {name: type(hardware) for name, hardware in self._hardware.items()}
        child_pipes = self._setup_pipes()
        self._setup_locks()
        for name, proc in self.processes.items():
            proc.configure_shared_resources(
                camera_type=camera_type,
                hardware_types=hardware_types,
                quitting_event=self._quitting,
                settings=self._settings,
                shared_values=self.shared_values,
                locks=self.locks,
                pipe_end=child_pipes[name],
            )
            self.quitting_events[name] = proc.quitting_event

    def _create_shared_buffers(self):
        """Instantiate shared memory buffers used throughout the application."""
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
        lock_targets = list(dict.fromkeys([*self.lock_names, *self._hardware.keys()]))
        self.lock_names = lock_targets
        for name in self.lock_names:
            self.locks[name] = Lock()

    def _setup_pipes(self) -> dict[str, Connection]:
        """Create duplex pipes that allow processes to exchange messages."""
        parent_ends, child_ends = create_pipes(self.processes)
        self.pipes = parent_ends
        return child_ends

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
                if settings is None:
                    warn(f"Settings file {self._settings_path} is empty. Skipping merge.")
                    return
                if not isinstance(settings, dict):
                    warn(
                        f"Settings file {self._settings_path} must contain a YAML mapping. "
                        "Skipping merge."
                    )
                    return
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
