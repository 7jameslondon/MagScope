"""Utilities for registering and executing scripted automation flows.

This module provides the runtime that powers MagScope's lightweight
automation system. Users describe a sequence of actions in a script file
by instantiating :class:`Script` and adding IPC :class:`Command` instances.
The resulting steps are validated against :class:`ScriptRegistry` to ensure
that each call is valid before being executed by :class:`ScriptManager`.

Only methods decorated with :func:`register_script_command` are exposed to the
script environment. Script execution runs in its own manager process and
communicates with other parts of the application through the standard IPC
mechanism.
"""

from dataclasses import dataclass
from enum import StrEnum
from time import time
import traceback
from typing import Callable, Iterable

from magscope._logging import get_logger
from magscope.ipc import UnknownCommandError, register_ipc_command
from magscope.ipc_commands import (Command, LoadScriptCommand, PauseScriptCommand, ResumeScriptCommand,
                                   ShowErrorCommand, SleepCommand, StartScriptCommand,
                                   UpdateScriptStatusCommand, UpdateWaitingCommand)
from magscope.processes import ManagerProcessBase
from magscope.utils import register_script_command


logger = get_logger("scripting")


@dataclass(frozen=True)
class ScriptStep:
    """Structured representation of a single scripted action."""

    command: Command
    wait: bool = False


@dataclass(frozen=True)
class ScriptCommandRegistration:
    """Metadata binding a script-visible method to its IPC command type."""

    cls_name: str
    meth_name: str
    command_type: type[Command]
    callable: Callable


class Script:
    """Container that records the steps of a user-authored script."""

    def __init__(self):
        # Each step is stored as a :class:`ScriptStep` so that the manager can
        # replay the actions later.
        self.steps: list[ScriptStep] = []

    def append(self, command: Command, *, wait: bool = False):
        """Append an IPC command to the script."""

        if not isinstance(command, Command):
            raise TypeError(f"Script steps must be IPC commands, got {type(command).__name__}")
        if not isinstance(wait, bool):
            raise ValueError(f"Argument 'wait' must be a boolean. Got {wait}")

        self.steps.append(ScriptStep(command=command, wait=wait))


class ScriptRegistry:
    """Tracks scriptable methods that managers expose to the scripting API."""

    avoided_names = ['sentinel', 'send_ipc']

    def __init__(self):
        # Mapping of command type -> registered command spec
        self._methods: dict[type[Command], ScriptCommandRegistration] = {}

    def __call__(self, command_type: type[Command]) -> "ScriptCommandRegistration":
        """Return the registered callable for ``command_type``.

        Raises:
            ValueError: If ``command_type`` has not been registered.
        """

        if command_type not in self._methods:
            raise ValueError(f"Script command {command_type.__name__} is not registered.")
        return self._methods[command_type]

    def register_class_methods(self, cls):
        """Inspect ``cls`` for scriptable methods and add them to the registry."""

        target_cls = cls if isinstance(cls, type) else cls.__class__
        cls_name = self.get_class_name(cls)
        for registration in self._collect_script_registrations(target_cls):
            if registration.command_type in self._methods:
                existing = self._methods[registration.command_type]
                if (existing.cls_name == registration.cls_name
                        and existing.meth_name == registration.meth_name):
                    continue
                raise ValueError(
                    f"Script command {registration.command_type.__name__} for {cls_name}.{registration.meth_name} "
                    f"is already registered with {existing.cls_name}.{existing.meth_name}."
                )

            self._methods[registration.command_type] = registration

    def check_script(self, script: Iterable[ScriptStep], *, command_registry=None):
        """Validate a compiled script before it is executed.

        Checks include verifying that the method exists, arguments bind against
        the callable signature, and that reserved flags such as ``wait`` have
        the correct types. When ``command_registry`` is provided, the command must
        also map to a registered IPC handler so that ScriptManager can dispatch
        it.
        """

        for step in script:
            if not isinstance(step.command, Command):
                raise TypeError(
                    f"Script contains a non-command step of type {type(step.command).__name__}"
                )

            if not isinstance(step.wait, bool):
                raise ValueError(f"Argument 'wait' must be a boolean. Got {step.wait}")

            registration = self._methods.get(type(step.command))
            if registration is None:
                raise ValueError(
                    f"Script contains an unknown command: {type(step.command).__name__}"
                )

            if command_registry is not None:
                try:
                    command = command_registry.command_for_handler(registration.cls_name, registration.meth_name)
                except UnknownCommandError as exc:
                    raise ValueError(
                        f"No IPC command registered for {registration.cls_name}.{registration.meth_name} "
                        f"(command {registration.command_type.__name__})"
                    ) from exc
                if command is not registration.command_type:
                    raise ValueError(
                        f"Script command {registration.command_type.__name__} maps to {registration.cls_name}.{registration.meth_name} "
                        f"but IPC registry maps that handler to {command.__name__}."
                    )

    @staticmethod
    def _collect_script_registrations(cls):
        """Yield scriptable methods declared on ``cls`` and its bases."""

        seen: set[str] = set()
        for base in cls.mro():
            for meth_name, meth in base.__dict__.items():
                if meth_name in seen or meth_name in ScriptRegistry.avoided_names:
                    continue

                if not getattr(meth, "_scriptable", False):
                    continue

                command_type = getattr(meth, "_script_command_type", None)
                if command_type is None:
                    raise ValueError(
                        f"Script method {cls.__name__}.{meth_name} is missing its IPC command mapping"
                    )

                seen.add(meth_name)
                yield ScriptCommandRegistration(
                    cls_name=ScriptRegistry.get_class_name(base),
                    meth_name=meth_name,
                    command_type=command_type,
                    callable=meth,
                )

    @staticmethod
    def get_class_name(cls):
        """Return the class name for a class or instance."""

        if isinstance(cls, type):
            return cls.__name__
        else:
            return cls.__class__.__name__


class ScriptStatus(StrEnum):
    """Lifecycle stages of a script managed by :class:`ScriptManager`."""

    EMPTY = 'Empty'
    LOADED = 'Loaded'
    RUNNING = 'Running'
    PAUSED = 'Paused'
    FINISHED = 'Finished'
    ERROR = 'Error'


class ScriptManager(ManagerProcessBase):
    """Process that coordinates script execution and forwards IPC messages."""

    def __init__(self):
        super().__init__()
        self._script: list[ScriptStep] = []
        self._script_index: int = 0
        self._script_length: int = 0
        self.script_registry = ScriptRegistry()
        self._script_status: ScriptStatus = ScriptStatus.EMPTY
        self._script_waiting: bool = False
        self._script_sleep_duration: float | None = None
        self._script_sleep_start: float = 0

    def setup(self):
        """Initialise process state.

        Currently no special setup is required, but the hook is retained for
        symmetry with other :class:`ManagerProcessBase` implementations.
        """

        pass

    def do_main_loop(self):
        """Main loop executed by the process infrastructure."""

        if self._script_status == ScriptStatus.RUNNING:
            # Check if were waiting on a previous step to finish
            if self._script_waiting:
                if self._script_sleep_duration is not None:
                    self._do_sleep()
                return

            # Execute next step in script
            self._execute_script_step(self._script[self._script_index])

            # Increment index
            self._script_index += 1

            # Check if script is finished
            if self._script_index >= self._script_length:
                self._set_script_status(ScriptStatus.FINISHED)

    @register_ipc_command(StartScriptCommand)
    def start_script(self):
        """Start the currently loaded script from the beginning."""

        if self._script_status == ScriptStatus.EMPTY:
            logger.warning('Cannot start script. A script is not loaded.')
            return
        if self._script_status == ScriptStatus.ERROR:
            logger.warning('Cannot start script. The previously loaded script failed.')
            return
        elif self._script_status == ScriptStatus.RUNNING:
            logger.warning('Cannot start script. The script is already running.')
            return

        self._script_index = 0
        self._set_script_status(ScriptStatus.RUNNING)

    @register_ipc_command(PauseScriptCommand)
    def pause_script(self):
        """Pause the running script."""

        if self._script_status != ScriptStatus.RUNNING:
            logger.warning('Cannot pause script. A script is not running.')
            return
        self._set_script_status(ScriptStatus.PAUSED)

    @register_ipc_command(ResumeScriptCommand)
    def resume_script(self):
        """Resume a script that was previously paused."""

        if self._script_status != ScriptStatus.PAUSED:
            logger.warning('Cannot resume script. The script is not paused.')
            return
        self._set_script_status(ScriptStatus.RUNNING)

    @register_ipc_command(LoadScriptCommand)
    def load_script(self, path):
        """Load and validate a script from ``path``.

        The script file is executed in an isolated namespace. Exactly one
        :class:`Script` instance must be created in that file; its recorded
        steps are copied locally after validation.
        """

        if self._script_status == ScriptStatus.RUNNING:
            logger.warning('Cannot load script while a script is running.')
            return

        self._script = []
        status = ScriptStatus.EMPTY
        error_message: str | None = None
        error_details: str | None = None

        if path:
            namespace = {}
            try:
                with open(path, 'r') as f:
                    exec(
                        f.read(),
                        {
                            "__file__": path,
                        },
                        namespace,
                    )
            except Exception:  # noqa
                error_message = "An error occurred while loading a script."
                error_details = traceback.format_exc()
                logger.error(error_message)
                logger.error(error_details)
                status = ScriptStatus.ERROR
            else:
                n_scripts_found = 0
                script = None
                for item in namespace.values():
                    if isinstance(item, Script):
                        script = item.steps  # noqa: retain type narrow
                        n_scripts_found += 1
                if n_scripts_found == 0:
                    error_message = "No Script instance found in script file."
                    logger.error(error_message)
                    status = ScriptStatus.ERROR
                elif n_scripts_found > 1:
                    error_message = "Multiple Script instances found in script file."
                    logger.error(error_message)
                    status = ScriptStatus.ERROR
                else:
                    # Check the script is valid
                    try:
                        self.script_registry.check_script(script, command_registry=self._command_registry)
                    except Exception as e:
                        error_message = 'Script is invalid. No script loaded.'
                        error_details = str(e)
                        logger.error('%s Error: %s', error_message, e)
                        status = ScriptStatus.ERROR
                    else:
                        self._script = script
                        status = ScriptStatus.LOADED

        self._script_length = len(self._script)
        self._script_waiting = False
        self._script_index = 0
        self._script_sleep_duration = None
        self._script_sleep_start = 0
        self._set_script_status(status)

        if error_message is not None:
            self.send_ipc(ShowErrorCommand(text=error_message, details=error_details))

    def _execute_script_step(self, step: ScriptStep):
        """Dispatch a single script step to its owning manager."""

        if self._command_registry is None:
            raise RuntimeError("ScriptManager cannot dispatch commands without a registry")

        registration = self.script_registry(type(step.command))

        if step.wait:
            self._script_waiting = True

        if isinstance(step.command, SleepCommand):
            self._script_waiting = True

        command_type = self._command_registry.command_for_handler(
            registration.cls_name, registration.meth_name
        )
        if command_type is not registration.command_type:
            raise UnknownCommandError(
                f"Script command {registration.command_type.__name__} expected {registration.cls_name}.{registration.meth_name} "
                f"but registry mapped to {command_type.__name__}"
            )

        self.send_ipc(step.command)

    @register_ipc_command(UpdateWaitingCommand)
    def update_waiting(self):
        """Let the script resume after waiting for a previous step to finish."""
        self._script_waiting = False

    @register_ipc_command(SleepCommand)
    @register_script_command(SleepCommand)
    def start_sleep(self, duration: float):
        """Pause the script for ``duration`` seconds."""
        if duration < 0:
            raise ValueError("Sleep duration must be non-negative")

        self._script_sleep_duration = duration
        self._script_sleep_start = time()
        self._script_waiting = True

        if duration == 0:
            self._do_sleep()

    def _do_sleep(self):
        """Check whether the scripted sleep period has elapsed."""
        if time() - self._script_sleep_start >= self._script_sleep_duration:
            self._script_sleep_duration = None
            self.update_waiting()

    def _set_script_status(self, status):
        """Notify the GUI that the script status has changed."""
        self._script_status = status
        command = UpdateScriptStatusCommand(status=status)
        self.send_ipc(command)
