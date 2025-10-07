"""Utilities for registering and executing scripted automation flows.

This module provides the runtime that powers MagScope's lightweight
automation system. Users describe a sequence of actions in a script file
by instantiating :class:`Script` and calling registered methods on it. The
resulting steps are validated against :class:`ScriptRegistry` to ensure
that each call is valid before being executed by :class:`ScriptManager`.

Only methods decorated with :func:`registerwithscript` are exposed to the
script environment. Script execution runs in its own manager process and
communicates with other parts of the application through the standard IPC
mechanism.
"""

from enum import StrEnum
import inspect
from time import time
import traceback
from typing import Callable
from warnings import warn

from magscope.processes import ManagerProcessBase
from magscope.utils import Message, registerwithscript


class Script:
    """Container that records the steps of a user-authored script."""

    def __init__(self):
        # Each step is stored as ``(method_name, args, kwargs)`` so that the
        # manager can replay the actions later.
        self.steps: list[tuple[str, tuple, dict]] = []

    def __call__(self, meth: str, *args, **kwargs):
        """Append a method invocation to the script."""
        self.steps.append((meth, args, kwargs))


class ScriptRegistry:
    """Tracks scriptable methods that managers expose to the scripting API."""

    avoided_names = ['sentinel', 'send_ipc']

    def __init__(self):
        # Mapping of script-name -> (class name, attribute name, bound/unbound callable)
        self._methods: dict[str, tuple[str, str, Callable]] = {}

    def __call__(self, meth_str: str) -> tuple[str, str, Callable]:
        """Return the registered callable for ``meth_str``.

        Raises:
            ValueError: If ``meth_str`` has not been registered.
        """

        if meth_str not in self._methods:
            raise ValueError(f"Script method {meth_str} is not registered.")
        return self._methods[meth_str]

    def register_class_methods(self, cls):
        """Inspect ``cls`` for scriptable methods and add them to the registry."""

        cls_name = self.get_class_name(cls)
        meth_names = dir(cls)
        for meth_name in meth_names:
            # Skip some special methods
            if meth_name in self.avoided_names:
                continue

            # Check if the method was decorated for registration
            meth = getattr(cls, meth_name)
            if not hasattr(meth, "_scriptable") or not meth._scriptable:
                continue

            # Check if it is a subclass only inheriting the registration
            if cls_name != meth._script_cls:
                continue

            # Check the script method name is unique
            if meth_name in self._methods:
                cls_name_reg, meth_name_reg, _ = self._methods[meth_name]
                raise ValueError(
                    f"Script method {meth_name} for {cls_name}.{meth_name} is already registered with {cls_name_reg}.{meth_name_reg}.")

            # Add method to registry
            self._methods[meth._script_str] = (cls_name, meth_name, meth)

    def check_script(self, script: list[tuple[str, tuple, dict]]):
        """Validate a compiled script before it is executed.

        Checks include verifying that the method exists, arguments bind against
        the callable signature, and that reserved keywords such as ``wait`` have
        the correct types.
        """

        for step in script:
            step_meth: str = step[0]
            step_args: tuple = step[1]
            step_kwargs: dict = step[2]

            if step_meth not in self._methods:
                raise ValueError(f"Script contains an unknown method: {step_meth}")

            if wait := step_kwargs.get('wait', False):
                if not isinstance(wait, bool):
                    raise ValueError(f"Argument 'wait' must be a boolean. Got {wait}")

            # Test is the method will be called with the correct arguments
            cls_name, meth_name, meth = self._methods[step_meth]
            try:
                if inspect.ismethod(meth):
                    inspect.signature(meth).bind(*step_args, **step_kwargs)
                else:
                    # "None" is used in place of "self" for unbound functions of class methods
                    inspect.signature(meth).bind(None, *step_args, **step_kwargs)
            except TypeError as e:
                raise TypeError(f"Invalid arguments for {meth.__name__} to call {cls_name}.{meth_name}: {e}")

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
        self._script: list[tuple[str, tuple, dict]] = []
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

    def start_script(self):
        """Start the currently loaded script from the beginning."""

        if self._script_status == ScriptStatus.EMPTY:
            warn('Cannot start script. A script is not loaded.')
            return
        elif self._script_status == ScriptStatus.RUNNING:
            warn('Cannot start script. The script is already running.')
            return

        self._script_index = 0
        self._set_script_status(ScriptStatus.RUNNING)

    def pause_script(self):
        """Pause the running script."""

        if self._script_status != ScriptStatus.RUNNING:
            warn('Cannot pause script. A script is not running.')
            return
        self._set_script_status(ScriptStatus.PAUSED)

    def resume_script(self):
        """Resume a script that was previously paused."""

        if self._script_status != ScriptStatus.PAUSED:
            warn('Cannot resume script. The script is not paused.')
            return
        self._set_script_status(ScriptStatus.RUNNING)

    def load_script(self, path):
        """Load and validate a script from ``path``.

        The script file is executed in an isolated namespace. Exactly one
        :class:`Script` instance must be created in that file; its recorded
        steps are copied locally after validation.
        """

        if self._script_status == ScriptStatus.RUNNING:
            warn('Cannot load script while a script is running.')
            return

        self._script = []
        status = ScriptStatus.EMPTY

        if path:
            namespace = {}
            try:
                with open(path, 'r') as f:
                    exec(f.read(), {}, namespace)
            except Exception:  # noqa
                warn(f"An error occurred while loading a script:\n")
                warn(traceback.format_exc())
            else:
                n_scripts_found = 0
                script = None
                for item in namespace.values():
                    if isinstance(item, Script):
                        script = item.steps  # noqa: retain type narrow
                        n_scripts_found += 1
                if n_scripts_found == 0:
                    warn("No Script instance found in script file.")
                elif n_scripts_found > 1:
                    warn("Multiple Script instances found in script file.")
                else:
                    # Check the script is valid
                    try:
                        self.script_registry.check_script(script)
                    except Exception as e:
                        warn(f'Script is invalid. No script loaded. Error: {e}')
                    else:
                        self._script = script
                        status = ScriptStatus.LOADED

        self._script_length = len(self._script)
        self._script_waiting = False
        self._script_index = 0
        self._set_script_status(status)

    def _execute_script_step(self, step: tuple[str, tuple, dict]):
        """Dispatch a single script step to its owning manager."""

        step_name: str = step[0]
        step_args: tuple = step[1]
        step_kwargs: dict = step[2]

        cls_name, meth_name, meth = self.script_registry(step_name)

        if wait := step_kwargs.get('wait', False):
            self._script_waiting = wait

        # Special case
        if step_name == 'sleep':
            self._script_waiting = True

        message = Message(cls_name, meth_name, *step_args, **step_kwargs)
        self.send_ipc(message)

    def update_waiting(self):
        """Let the script resume after waiting for a previous step to finish."""
        self._script_waiting = False

    @registerwithscript('sleep')
    def start_sleep(self, duration: float):
        """Pause the script for ``duration`` seconds."""
        self._script_sleep_duration = duration
        self._script_sleep_start = time()

    def _do_sleep(self):
        """Check whether the scripted sleep period has elapsed."""
        if time() - self._script_sleep_start >= self._script_sleep_duration:
            self._script_sleep_duration = None
            self.update_waiting()

    def _set_script_status(self, status):
        """Notify the GUI that the script status has changed."""
        # local import to avoid circular imports
        from magscope.gui import WindowManager
        self._script_status = status
        message = Message(
            to=WindowManager,
            meth=WindowManager.update_script_status,
            args=(status,)
        )
        self.send_ipc(message)
