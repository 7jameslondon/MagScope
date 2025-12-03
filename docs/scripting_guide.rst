Scripting IPC Guide
===================

This guide explains how MagScope's scripting subsystem interacts with the IPC
layer. It reflects the updated IPC message schema built around typed command
objects and a centralized registry.

Overview
--------

* User scripts create a :class:`magscope.scripting.Script` instance and record
  each action by instantiating an IPC :class:`magscope.ipc_commands.Command`
  subclass and passing it to the script. Every call is captured as a
  :class:`magscope.scripting.ScriptStep` containing the fully constructed
  command and an optional ``wait`` flag.
* Scriptable methods in managers are opt-in via the
  :func:`magscope.utils.register_script_command` decorator, which pairs each
  script entry point with a concrete IPC :class:`magscope.ipc_commands.Command`
  type (use ``@register_script_command(MyCommand)``). During startup,
  :class:`magscope.scope.MagScope` calls
  :meth:`magscope.scripting.ScriptRegistry.register_class_methods` for each
  manager so the scripting layer knows which methods can be invoked.
* The :class:`magscope.ipc_commands.CommandRegistry` links each scriptable
  method to a concrete :class:`magscope.ipc_commands.Command` dataclass and its
  delivery semantics (direct, broadcast, or MagScope-local). This ensures every
  step maps to a registered IPC payload before execution.

Lifecycle and validation
------------------------

* :class:`magscope.scripting.ScriptManager` owns a compiled script and controls
  its lifecycle through :class:`magscope.scripting.ScriptStatus` values
  (``Empty``, ``Loaded``, ``Running``, ``Paused``, ``Finished``, ``Error``).
* Loading a script dispatches :class:`magscope.ipc_commands.LoadScriptCommand`
  to the manager. The manager executes the file in an isolated namespace,
  extracts the sole ``Script`` instance, and validates each
  :class:`~magscope.scripting.ScriptStep` against both the
  :class:`~magscope.scripting.ScriptRegistry` and the active
  :class:`~magscope.ipc_commands.CommandRegistry`. Argument validation uses the
  command dataclass constructor so scripts must match the IPC payload schema.
* The GUI sends :class:`magscope.ipc_commands.StartScriptCommand`,
  :class:`~magscope.ipc_commands.PauseScriptCommand`, and
  :class:`~magscope.ipc_commands.ResumeScriptCommand` to transition execution
  through the lifecycle states. The manager reports state changes back to the
  GUI via :class:`magscope.ipc_commands.UpdateScriptStatusCommand`.

Dispatching steps
-----------------

* Each :class:`~magscope.scripting.ScriptStep` already holds a concrete command
  instance. :class:`~magscope.scripting.ScriptManager` validates that the
  command type is registered for scripting, checks the active IPC registry for a
  matching handler, and forwards the command over the IPC pipe so the registry
  can route it to the correct process or broadcast destination.
* Steps marked with ``wait=True`` or the special ``StartSleepCommand`` pause
  script advancement until the associated condition is satisfied. Managers emit
  :class:`magscope.ipc_commands.UpdateWaitingCommand` when the wait concludes so
  :class:`~magscope.scripting.ScriptManager` can continue.

Error handling
--------------

* Script parsing and validation errors are logged via the ``scripting`` logger
  with full tracebacks when available. Invalid scripts are rejected before any
  IPC traffic is emitted.
* Runtime exceptions inside :class:`~magscope.scripting.ScriptManager` continue
  to flow through the standard manager error path, which reports details via
  :class:`magscope.ipc_commands.LogExceptionCommand` when IPC is available.
* Missing or unregistered script methods raise explicit validation errors during
  loading, mirroring the IPC command registration semantics and preventing
  undefined messages from being dispatched.
