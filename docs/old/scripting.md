# Scripting engine

Automation in MagScope is powered by the `magscope.scripting` module. It records
user-authored procedures, validates them against registered manager methods, and
plays the sequence back in its own manager process.

## Building scripts

Scripts are composed by instantiating :class:`~magscope.scripting.Script` and
calling it with the name of a registered method plus any positional or keyword
arguments. Each call appends a tuple of ``(method_name, args, kwargs)`` to the
script's ``steps`` list. Scripts are often created from Python files that import
`Script`, populate it, and then hand it to :class:`~magscope.scope.MagScope`.

## Registering callable steps

The :class:`~magscope.scripting.ScriptRegistry` discovers scriptable methods by
inspecting classes for the :func:`~magscope.utils.registerwithscript` decorator.
It stores a mapping from the exported script name (``meth._script_str``) to the
implementing class and attribute name. Validation via
:meth:`~magscope.scripting.ScriptRegistry.check_script` ensures that every step
references a known method, that arguments bind cleanly to the function
signature, and that reserved keywords like ``wait`` are booleans.

## Managing execution

:class:`~magscope.scripting.ScriptManager` runs as a standard
:class:`~magscope.processes.ManagerProcessBase`. It tracks the current index,
status, and wait conditions of the active script:

* :meth:`~magscope.scripting.ScriptManager.start_script` resets the index and
  transitions to ``RUNNING`` if a script is loaded.
* The main loop executes steps one at a time, pausing when a step requested
  ``wait`` or :meth:`~magscope.scripting.ScriptManager.sleep` was called.
* :meth:`~magscope.scripting.ScriptManager.pause_script`,
  :meth:`~magscope.scripting.ScriptManager.resume_script`, and
  :meth:`~magscope.scripting.ScriptManager.stop_script` update
  :class:`~magscope.scripting.ScriptStatus` to reflect the user's intent.

Errors encountered during playback set the status to ``ERROR`` and emit a
:class:`~magscope.utils.Message` so the GUI can display the traceback. Successful
completion switches the status to ``FINISHED`` and clears any pending wait
requests.
