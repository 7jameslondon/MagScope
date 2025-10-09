# Utility helpers

The `magscope.utils` module gathers small helper types that are shared across
multiple subsystems.

## Messaging

:class:`~magscope.utils.Message` is the lightweight envelope passed over IPC
pipes. It stores the destination class name, method name, positional arguments,
and keyword arguments in a serialisable form so that managers and the main
:class:`~magscope.scope.MagScope` router can communicate without tight coupling.

## Enumerations

Two enums describe global state:

* :class:`~magscope.utils.AcquisitionMode` enumerates the major acquisition
  workflows (tracking, cropped video, full video, and ZLUT generation).
* :class:`~magscope.utils.PoolVideoFlag` reports the state of the GPU processing
  pool so producers know when frames are being processed, finished, or ready for
  reuse.

## Image helpers

Functions such as :func:`~magscope.utils.crop_stack_to_rois` and
:func:`~magscope.utils.numpy_type_to_qt_image_type` adapt numpy arrays to the
needs of downstream subsystems. The first extracts rectangular ROIs out of a 3-D
stack, while the second maps numpy dtypes to :class:`PyQt6.QtGui.QImage` pixel
formats. :func:`~magscope.utils.date_timestamp_str` formats timestamps into the
human-readable filenames used when saving recordings.

## Units and scripting decorator

:class:`~magscope.utils.Units` exposes convenient SI prefixes (``um``, ``mN``,
``ms``, etc.) that scripts can reference. The
:func:`~magscope.utils.registerwithscript` decorator tags manager methods as
scriptable by recording metadata about their owning class and exported script
name. :class:`~magscope.scripting.ScriptRegistry` uses this metadata when wiring
the scripting API.
