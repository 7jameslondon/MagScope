"""Core orchestration for the MagScope application.

``MagScope`` is the parent process that builds every other manager process,
connects them with shared resources, and relays inter-process messages until
shutdown. Its responsibilities span the full application lifetime:

* Loading YAML configuration, merging user overrides, and distributing the
  result to each process.
* Constructing manager processes (camera, bead lock, GUI, scripting, video
  processing, and optional hardware integrations) and wiring them to shared
  locks, buffers, and IPC pipes.
* Running the main IPC loop that forwards typed IPC commands between processes
  and supervises orderly shutdown.

``MagScope.start`` prepares the shared resources, registers scriptable hooks,
starts each process, and then loops until a quit command is received.

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

``CameraManager`` → ``VideoBuffer`` → ``VideoProcessorManager`` → ``UIManager``
and
``BeadLockManager`` → ``MatrixBuffer`` → ``UIManager``

Every manager receives shared locks, pipes, and configuration from the main
process so that real-time video frames, bead tracking data, and scripted events
remain synchronized.
"""

import logging
import sys
import time
from multiprocessing import Event, Lock, current_process, freeze_support
from multiprocessing.connection import Connection
from typing import TYPE_CHECKING
from warnings import warn

import numpy as np

from magscope._logging import configure_logging, get_logger
from magscope.beadlock import BeadLockManager
from magscope.camera import CameraManager
from magscope.datatypes import LiveProfileBuffer, MatrixBuffer, VideoBuffer
from magscope.ui import ControlPanelBase, TimeSeriesPlotBase, UIManager
from magscope.hardware import HardwareManagerBase
from magscope.ipc import (
    broadcast_command,
    command_kwargs,
    CommandRegistry,
    create_pipes,
    Delivery,
    drain_pipe_until_quit,
    register_ipc_command,
)
from magscope.ipc_commands import Command, LogExceptionCommand, QuitCommand, SetSettingsCommand, UpdateSettingsCommand
from magscope.processes import InterprocessValues, ManagerProcessBase, SingletonMeta
from magscope.settings import MagScopeSettings
from magscope.scripting import ScriptManager
from magscope.videoprocessing import VideoProcessorManager

logger = get_logger("scope")

if TYPE_CHECKING:
    from multiprocessing.synchronize import Event as EventType
    from multiprocessing.synchronize import Lock as LockType


class MagScope(metaclass=SingletonMeta):
    """Coordinate MagScope managers, shared resources, and IPC.

    ``MagScope`` owns references to every manager process, shared buffer, and
    IPC primitive used by the application. Instances can be customized by
    adding hardware managers, GUI controls, or time-series plots before calling
    :meth:`start`. Once started, the instance supervises manager lifetimes,
    relays messages, and coordinates shutdown when a quit signal is broadcast
    over the IPC bus. The orchestrator is a singleton: attempts to construct a
    second instance raise ``TypeError``. Each instance is single-use: calling
    :meth:`start` while an instance is already running logs a warning, and
    invoking :meth:`start` after the instance has quit raises an error. Use
    :meth:`stop` to request a graceful shutdown; it blocks until all managers
    acknowledge the quit sequence and exit.
    """

    def __init__(
        self,
        *,
        verbose: bool = False,
        print_ipc_commands: bool = False,
        print_script_commands: bool = False,
    ):
        self.beadlock_manager = BeadLockManager()
        self.camera_manager = CameraManager()
        self.video_processor_manager = VideoProcessorManager()
        self.ui_manager = UIManager()
        self.script_manager = ScriptManager()

        self._hardware: dict[str, HardwareManagerBase] = {}
        self._hardware_buffers: dict[str, MatrixBuffer] = {}
        self.processes: dict[str, ManagerProcessBase] = {}
        self.command_registry: CommandRegistry = CommandRegistry()

        self.locks: dict[str, LockType] = {}
        self.lock_names: list[str] = ['LiveProfileBuffer', 'TracksBuffer', 'VideoBuffer']
        self.pipes: dict[str, Connection] = {}
        self.quitting_events: dict[str, EventType] = {}
        self.shared_values: InterprocessValues = InterprocessValues()
        self._quitting: Event = Event()

        self._settings = MagScopeSettings()

        self._running: bool = False
        self._log_level = logging.INFO if verbose else logging.WARNING

        self._command_registry_initialized: bool = False
        self._print_ipc_commands = print_ipc_commands
        self._print_script_commands = print_script_commands

        self._terminated: bool = False

        self.live_profile_buffer: LiveProfileBuffer | None = None
        self.tracks_buffer: MatrixBuffer | None = None
        self.video_buffer: VideoBuffer | None = None
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
        freeze_support()

        if current_process().name != "MainProcess":
            logger.debug(
                "MagScope.start called in a child process; skipping initialization to "
                "avoid duplicate startup during multiprocessing spawn."
            )
            return

        self._ensure_not_terminated()
        self._apply_logging_preferences()

        if not self._mark_running():
            return

        self._collect_processes()

        if self._print_ipc_commands or self._print_script_commands:
            if self._print_ipc_commands:
                self.print_registered_commands()
            if self._print_script_commands:
                self.print_registered_script_commands()
            self._running = False
            return

        self._initialize_shared_state()
        self._start_managers()
        self._main_ipc_loop()
        self._join_processes()
        self._mark_terminated()

    def stop(self) -> None:
        """Request a graceful shutdown and wait for every manager to exit.

        ``stop`` mirrors a quit request sent from any manager process: it
        broadcasts a quit message, drains outstanding IPC, and blocks until all
        managers have joined. After ``stop`` completes the instance is
        permanently terminated and cannot be restarted.
        """

        self._ensure_not_terminated()
        if not self._running:
            warn('MagScope is not running')
            return

        quit_command = QuitCommand()
        self._handle_broadcast_command(quit_command)
        self._join_processes()
        self._mark_terminated()

    def add_hardware(self, hardware: HardwareManagerBase):
        """Register a hardware manager so its process launches with MagScope."""
        self._hardware[hardware.name] = hardware
        self.command_registry.register_manager(hardware)

    def add_control(self, control_type: type(ControlPanelBase), column: int):
        """Schedule a GUI control panel to be added when the UI manager starts."""
        self.ui_manager.controls_to_add.append((control_type, column))

    def add_timeplot(self, plot: TimeSeriesPlotBase):
        """Schedule a time-series plot for inclusion in the GUI at startup."""
        self.ui_manager.plots_to_add.append(plot)

    @property
    def window_manager(self) -> UIManager:
        """Backward-compatible alias for :attr:`ui_manager`."""

        return self.ui_manager

    @window_manager.setter
    def window_manager(self, manager: UIManager) -> None:
        self.ui_manager = manager

    @property
    def print_ipc_commands(self) -> bool:
        """Return whether :meth:`start` should print IPC commands and exit early."""

        return self._print_ipc_commands

    @print_ipc_commands.setter
    def print_ipc_commands(self, enabled: bool) -> None:
        if self._running:
            warn('MagScope is already running')
            return
        self._print_ipc_commands = enabled

    @property
    def print_script_commands(self) -> bool:
        """Return whether :meth:`start` should print script commands and exit early."""

        return self._print_script_commands

    @print_script_commands.setter
    def print_script_commands(self, enabled: bool) -> None:
        if self._running:
            warn('MagScope is already running')
            return
        self._print_script_commands = enabled

    def _coerce_settings(self, value: MagScopeSettings | dict) -> MagScopeSettings:
        if isinstance(value, MagScopeSettings):
            return value.clone()
        return MagScopeSettings(value)

    @property
    def settings(self):
        return self._settings

    @settings.setter
    def settings(self, value):
        self._settings = self._coerce_settings(value)
        if self._running:
            command = SetSettingsCommand(settings=self._settings.clone())
            self._handle_broadcast_command(command)

    @classmethod
    def _reset_singleton_for_testing(cls) -> None:
        """Clear the singleton registry so tests can create fresh instances."""

        instances = getattr(type(cls), '_instances', None)
        if isinstance(instances, dict):
            instances.pop(cls, None)

    def set_verbose_logging(self, enabled: bool = True) -> None:
        """Toggle informational console output for MagScope internals."""

        self._log_level = logging.INFO if enabled else logging.WARNING
        configure_logging(level=self._log_level)

    def _mark_running(self) -> bool:
        """Mark the orchestrator as running if it is not already active."""

        if self._running:
            warn('MagScope is already running')
            return False

        self._running = True
        return True

    def _ensure_not_terminated(self) -> None:
        """Prevent reusing a MagScope instance after it has been stopped."""

        if self._terminated:
            raise RuntimeError('MagScope has already been stopped and cannot be restarted')

    def _apply_logging_preferences(self) -> None:
        """Apply the current verbosity preference to the logging system."""
        configure_logging(level=self._log_level)

    def _mark_terminated(self) -> None:
        """Record that this MagScope instance has finished its lifecycle."""

        self._terminated = True

    def _collect_processes(self) -> None:
        """Assemble the ordered list of manager processes to supervise.

        ScriptManager must remain first so that the ``@register_script_command``
        decorator binds correctly before other managers start.
        """
        proc_list: list[ManagerProcessBase] = [
            # ScriptManager must be first in this list for @register_script_command to work
            self.script_manager,
            self.camera_manager,
            self.beadlock_manager,
            self.video_processor_manager,
            self.ui_manager,
        ]
        proc_list.extend(self._hardware.values())

        self.processes = {}
        for proc in proc_list:
            self.processes[proc.name] = proc

        self._command_registry_initialized = False

    def _setup_command_registry(self) -> None:
        """Register all command handlers and validate destinations."""
        if self._command_registry_initialized:
            return

        self.command_registry.register_object(self, target='MagScope')
        for proc in self.processes.values():
            self.command_registry.register_manager(proc)
        self.command_registry.validate_targets(self.processes)
        self._command_registry_initialized = True

    def print_registered_commands(self) -> None:
        """Print the registered IPC commands without launching managers."""

        if not self.processes:
            self._collect_processes()

        self._setup_command_registry()

        targets = sorted({*self.processes.keys(), 'MagScope'})
        for target in targets:
            specs = self.command_registry.handlers_for_target(target)
            if not specs:
                continue
            print(f'{target}:', file=sys.stdout)
            for command_type in sorted(specs.keys(), key=lambda c: c.__name__):
                spec = specs[command_type]
                destination = spec.target if spec.delivery != Delivery.BROADCAST else 'BROADCAST'
                print(
                    f'  {command_type.__name__} -> {spec.delivery.name} to {destination} via {spec.handler}',
                    file=sys.stdout,
                )

    def print_registered_script_commands(self) -> None:
        """Print the registered script commands without launching managers."""

        if not self.processes:
            self._collect_processes()

        self._setup_command_registry()
        self._register_script_methods()

        registrations = self.script_manager.script_registry._methods
        if not registrations:
            return

        print('Script commands:', file=sys.stdout)
        for command_type in sorted(registrations.keys(), key=lambda c: c.__name__):
            registration = registrations[command_type]
            print(
                f'  {command_type.__name__} -> {registration.cls_name}.{registration.meth_name}',
                file=sys.stdout,
            )

    def _initialize_shared_state(self) -> None:
        """Load configuration and prepare shared resources for all managers."""
        freeze_support()  # To prevent recursion in windows executable
        self._setup_command_registry()
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
        """Poll every IPC pipe once and relay any commands that arrive."""
        handled_command = False
        for pipe in self.pipes.values():
            command = self._read_command(pipe)
            if command is None:
                continue

            handled_command = True
            if self._process_command(command):
                break

        if not handled_command:
            self._sleep_when_idle()

    def _read_command(self, pipe: Connection) -> Command | object | None:
        """Retrieve a command from ``pipe`` if one is waiting."""

        if not pipe.poll():
            return None

        command = pipe.recv()
        logger.info('%s', command)
        if not isinstance(command, Command):
            warn(f'IPC payload is not a Command: {command}')
            return None

        return command

    def _process_command(self, command: Command) -> bool:
        """Route a valid command and indicate whether the IPC loop should break."""

        return self._route_command(command)

    def _route_command(self, command: Command) -> bool:
        """Dispatch a command based on its destination.

        Returns ``True`` when the IPC loop should stop iterating over the
        current set of pipes (for example, immediately after handling a quit
        broadcast). This mirrors the previous behavior of breaking out of the
        ``receive_ipc`` loop once a quit command has been processed.
        """
        spec = self.command_registry.route_for(command)
        if spec.delivery == Delivery.MAG_SCOPE:
            self._dispatch_mag_scope_command(command, spec)
        elif spec.delivery == Delivery.BROADCAST:
            if self._handle_broadcast_command(command, spec):
                return True
        elif spec.target in self.pipes:  # the command is to one process
            if self.processes[spec.target].is_alive() and not self.quitting_events[spec.target].is_set():
                self.pipes[spec.target].send(command)
        else:
            warn(f'Unknown pipe {spec.target} for {command}')

        return False

    def _dispatch_mag_scope_command(self, command: Command, spec) -> None:
        """Handle commands destined for the MagScope orchestrator."""

        handler = getattr(self, spec.handler, None)
        if handler is None:
            raise RuntimeError(f'No MagScope handler for {type(command).__name__}')
        handler(**command_kwargs(command))

    def _handle_broadcast_command(self, command: Command, spec=None) -> bool:
        """Broadcast a command to all processes and handle quit semantics.

        Returns ``True`` when the caller should stop processing the current
        IPC loop (e.g., after handling a quit command).
        """
        if spec is None:
            spec = self.command_registry.route_for(command)

        if isinstance(command, SetSettingsCommand):
            self._settings = self._coerce_settings(command.settings)

        if isinstance(command, QuitCommand):
            logger.info('MagScope quitting ...')
            self._quitting.set()
            self._running = False

        broadcast_command(
            command,
            pipes=self.pipes,
            processes=self.processes,
            quitting_events=self.quitting_events,
        )

        if isinstance(command, QuitCommand):
            self._drain_child_pipes_after_quit()
            return True

        return False

    @register_ipc_command(LogExceptionCommand, delivery=Delivery.MAG_SCOPE, target='MagScope')
    def log_exception(self, process_name: str, details: str) -> None:
        """Surface an exception raised in a managed process."""

        print(
            f'[{process_name}] Unhandled exception in child process:\n{details}',
            file=sys.stderr,
            flush=True,
        )

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
                settings=self._settings.clone(),
                shared_values=self.shared_values,
                locks=self.locks,
                pipe_end=child_pipes[name],
                command_registry=self.command_registry,
            )
            self.quitting_events[name] = proc.quitting_event

    def _create_shared_buffers(self):
        """Instantiate shared memory buffers used throughout the application."""
        self.live_profile_buffer = LiveProfileBuffer(
            create=True,
            locks=self.locks,
            profile_capacity=2560,
        )
        self.tracks_buffer = MatrixBuffer(
            create=True,
            locks=self.locks,
            name='TracksBuffer',
            shape=(self._settings['tracks max datapoints'], 7),
        )
        self.video_buffer = VideoBuffer(
            create=True,
            locks=self.locks,
            n_stacks=self._settings['video buffer n stacks'],
            n_images=self._settings['video buffer n images'],
            width=self.camera_manager.camera.width,
            height=self.camera_manager.camera.height,
            bits=np.iinfo(self.camera_manager.camera.dtype).bits,
        )
        for name, hardware in self._hardware.items():
            self._hardware_buffers[name] = MatrixBuffer(
                create=True,
                locks=self.locks,
                name=name,
                shape=hardware.buffer_shape,
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

    @register_ipc_command(UpdateSettingsCommand, delivery=Delivery.MAG_SCOPE, target='MagScope')
    def update_settings(self, settings: MagScopeSettings | dict) -> None:
        """Replace the active settings and broadcast them to all managers."""

        self.settings = settings
