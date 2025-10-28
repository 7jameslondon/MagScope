# Camera subsystem

The `magscope.camera` module provides the abstractions that let MagScope talk to
imaging hardware. It defines both the runtime process that orchestrates
acquisition and the base classes that concrete camera drivers inherit from.

## `CameraManager`

:class:`~magscope.camera.CameraManager` extends
:class:`~magscope.processes.ManagerProcessBase` and owns an instance of a
:class:`~magscope.camera.CameraBase` subclass. During
:meth:`~magscope.camera.CameraManager.setup` it attempts to connect to the camera
and push the initial setting values to the GUI. The main loop then:

1. Coordinates with the video-processing pool through the shared
   :class:`~magscope.utils.PoolVideoFlag`, releasing buffers when processing
   finishes or when the acquisition is idle.
2. Monitors :class:`~magscope.datatypes.VideoBuffer` usage so it can purge frames
   when only a single stack remains free. The GUI receives a notification via
   :class:`~magscope.utils.Message` whenever a purge occurs.
3. Polls :meth:`~magscope.camera.CameraBase.fetch` for newly captured frames and
   writes them to the shared video buffer.

Convenience helpers :meth:`~magscope.camera.CameraManager.get_camera_setting`
and :meth:`~magscope.camera.CameraManager.set_camera_setting` bridge camera
settings to :class:`~magscope.gui.WindowManager`, which keeps the on-screen
controls synchronized with the hardware state.

## `CameraBase`

Custom camera drivers subclass :class:`~magscope.camera.CameraBase` and
implement four abstract methods: :meth:`connect`, :meth:`fetch`,
:meth:`release`, and :meth:`set_setting`. The base class enforces a minimal set
of attributes (geometry, dtype, pixel size, and a ``settings`` list that must
include ``'framerate'``) and validates that bit depth and numpy dtype agree.
Common conveniences include:

* ``self.video_buffer`` &ndash; set during :meth:`connect` so drivers can write image
  stacks directly into shared memory.
* ``self.camera_buffers`` &ndash; optional queue for SDK-provided buffers that need
  explicit release calls.
* ``__getitem__`` and ``__setitem__`` &ndash; wrappers that route to
  :meth:`get_setting`/:meth:`set_setting` for dictionary-like access.

## Built-in dummy cameras

Two lightweight drivers, :class:`~magscope.camera.DummyCamera` and
:class:`~magscope.camera.DummyCameraFast`, provide synthetic data streams for
development. They generate random image bytes at configurable frame rates,
maintain estimated FPS counters, and expose ``framerate``, ``exposure``, and
``gain`` knobs. :class:`~magscope.camera.DummyBeadCamera` loads a precomputed
half Z-LUT to simulate tethered beads with photophysical noise. These drivers
illustrate how real hardware integrations should feed bytes into the video
buffer, keep track of SDK buffers, and surface settings through the manager.
