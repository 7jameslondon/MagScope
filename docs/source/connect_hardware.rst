.. _connect_hardware:

Connect Your Hardware
=====================

This guide is to help you get hardware such as motors, pumps, light sources, ect. connected to MagScope.
For help with your camera see :doc:`connect_camera`.

Hardware managers run in their own MagScope process and typically subclass
``magscope.HardwareManagerBase``. Add your hardware before starting the scope::

    import magscope

    scope = magscope.MagScope()
    scope.add_hardware(MyHardwareManager())
    scope.start()

Focus motor integration
-----------------------

For Z-sweep and related workflows, subclass ``magscope.FocusMotorBase``. This
standardizes the focus-motor contract used by MagScope while leaving the
device-specific motion code in your subclass.

Implement these methods:

- ``move_absolute(z: float) -> None``
- ``get_current_z() -> float``
- ``get_is_moving() -> bool``
- ``get_position_limits() -> tuple[float, float]``

You may also override:

- ``is_at_target(tolerance: float | None = None) -> bool``

``FocusMotorBase.is_at_target()`` provides a default target-reached check based
on the commanded target and a tolerance. The right tolerance depends on your
motor's real positioning accuracy, so you may need to adjust the class
attribute used by the default implementation or override ``is_at_target()`` in
your subclass when the hardware exposes a better native "at target" signal or
needs more complex logic.

``FocusMotorBase`` writes telemetry rows to its hardware buffer as
``[timestamp, current_z, target_z, is_at_target]`` and handles the generic
``MoveFocusMotorAbsoluteCommand`` IPC command for you.

See ``examples/focus/simulated_focus_motor.py`` for a complete simulated
focus motor that follows this pattern and also drives the simulated camera
focus when ``DummyCameraBeads`` is active.
