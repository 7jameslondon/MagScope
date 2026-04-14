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

python-microscope hardware
--------------------------

If your hardware is exposed through `python-microscope <https://python-microscope.org/>`_, MagScope provides optional adapters for focus stages and generic hardware managers.

Install the optional dependency first::

   pip install magscope[python-microscope]

For a microscope Z stage, use :class:`magscope.PythonMicroscopeFocusMotor`. It accepts either a local ``device_factory`` or a remote ``device_uri``. When given a stage device, it will use the ``"z"`` axis by default::

   import magscope
   from magscope import PythonMicroscopeFocusMotor

   scope = magscope.MagScope()
   scope.add_hardware(
       PythonMicroscopeFocusMotor(
           device_uri="PYRO:Stage@127.0.0.1:8001",
           axis_name="z",
           position_scale=1000.0,
       )
   )
   scope.start()

``position_scale`` converts python-microscope stage units into the absolute Z units MagScope uses. For example, set ``position_scale=1000.0`` when the microscope axis reports micrometers but your MagScope workflow should operate in nanometers.

For non-focus devices, subclass :class:`magscope.PythonMicroscopeHardwareManagerBase` to reuse the connection lifecycle while keeping your own telemetry schema and IPC commands::

   from time import time

   import numpy as np
   import magscope
   from magscope import PythonMicroscopeHardwareManagerBase

   class MyMicroscopeHardware(PythonMicroscopeHardwareManagerBase):
       def __init__(self):
           super().__init__(device_uri="PYRO:Light@127.0.0.1:8002")
           self.buffer_shape = (1000, 2)

       def fetch(self):
           power = float(self.microscope_device.power)
           self._buffer.write(np.array([[time(), power]], dtype=float))

This base class supports three ways of supplying the underlying microscope device:

* ``device_factory``: recommended for local devices so construction happens in the child process
* ``device_uri``: recommended for python-microscope device-server deployments
* ``device``: useful when you already have a proxy or device object and know it is safe to share
