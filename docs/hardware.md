# Hardware manager base class

The `magscope.hardware` module defines :class:`~magscope.hardware.HardwareManagerBase`,
the foundation for integrating auxiliary hardware such as force sensors or
motion controllers. It combines :class:`~magscope.processes.ManagerProcessBase`
with an abstract hardware API and enforces a singleton per concrete manager so
that multiple processes do not fight over the same device.

## Lifecycle hooks

When MagScope spawns a hardware manager process it calls
:meth:`~magscope.hardware.HardwareManagerBase.setup`, which attaches to the
shared :class:`~magscope.datatypes.MatrixBuffer` reserved for that manager's
telemetry. After setup the inherited main loop simply calls
:meth:`~magscope.hardware.HardwareManagerBase.fetch` every iteration. Subclasses
implement ``fetch`` to pull new samples from the device and append them to the
matrix buffer along with timestamps.

Managers must also implement :meth:`connect` and :meth:`disconnect` to open and
close the physical connection, and they should set ``self._is_connected`` as
appropriate. The base class ensures that :meth:`disconnect` is called from the
:meth:`quit` handler so devices shut down cleanly even if another process asks
MagScope to exit.

## Buffer configuration

Each hardware manager can override ``self.buffer_shape`` to describe the number
of rows cached locally before shipping them to shared memory. The default shape
of ``(1000, 2)`` suits two-channel telemetry such as ``(timestamp, value)``
traces, but subclasses may choose higher dimensional layouts.
