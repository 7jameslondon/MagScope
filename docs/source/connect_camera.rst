.. _connect_camera:

Connect Your Camera
===================

MagScope ships with a simulated camera so you can explore the interface immediately, but you can swap in a real camera by providing a small adapter class. This guide shows how to implement a :class:`~magscope.camera.CameraBase` subclass for your hardware and register it with MagScope before launching the GUI.

1. Implement a camera adapter
-----------------------------

Every camera must subclass :class:`~magscope.camera.CameraBase` and expose immutable metadata plus a handful of lifecycle methods. At minimum, define the following attributes on the class:

* ``width`` and ``height``: image dimensions in pixels
* ``dtype``: a ``numpy`` integer dtype (``np.uint8``, ``np.uint16``, ``np.uint32``, or ``np.uint64``)
* ``bits``: the number of meaningful bits per pixel (must fit inside the dtype)
* ``nm_per_px``: nanometers represented by each pixel
* ``settings``: list of setting names; must include ``"framerate"`` so the GUI can display and edit it

Implement the methods below to bridge between the device SDK and MagScope’s shared buffers:

* ``connect(video_buffer)``: open the hardware connection, allocate any SDK buffers, and stash ``video_buffer`` for later writes
* ``fetch()``: pull the next frame from the device into ``video_buffer`` using ``self.video_buffer.write_frame(...)``
* ``release()`` and ``release_all()``: return SDK buffers or handles after frames have been consumed
* ``__getitem__``/``__setitem__``: read and update entries in ``settings`` so the GUI can synchronize values

A minimal skeleton that wraps a vendor SDK might look like::

   import numpy as np
   from magscope.camera import CameraBase

   class LabCamera(CameraBase):
       width = 2048
       height = 2048
       dtype = np.uint16
       bits = 12
       nm_per_px = 100
       settings = ["framerate", "exposure_ms"]

       def __init__(self):
           super().__init__()
           self._sdk = None
           self._settings = {"framerate": 30, "exposure_ms": 10.0}

       def connect(self, video_buffer):
           self.video_buffer = video_buffer
           self._sdk = connect_to_camera()
           self.is_connected = True

       def fetch(self):
           frame = self._sdk.get_frame()
           self.video_buffer.write_frame(frame)

       def release(self):
           self._sdk.release_frame()

       def release_all(self):
           self._sdk.shutdown()
           self.is_connected = False

       def __getitem__(self, name):
           return self._settings[name]

       def __setitem__(self, name, value):
           self._settings[name] = value
           self._sdk.update_setting(name, value)

2. Register the camera before starting MagScope
-----------------------------------------------

Instantiate your adapter and assign it to the camera manager prior to calling :py:meth:`magscope.scope.MagScope.start`::

   import magscope
   from lab_camera import LabCamera

   scope = magscope.MagScope()
   scope.camera_manager.camera = LabCamera()
   scope.start()

During startup the camera manager calls :py:meth:`magscope.camera.CameraBase.connect` and immediately publishes all entries in ``settings`` to the GUI, so ensure your adapter populates defaults before ``start()`` runs.

3. Validate the connection
--------------------------

* Watch the console for warnings; if :py:meth:`magscope.camera.CameraBase.connect` raises an exception MagScope will stay in simulation mode and report the error.
* Confirm that the GUI reflects any custom settings you exposed in ``settings`` and that adjusting them updates your device through ``__setitem__``.
* If the camera stream overruns the buffer, the camera manager will purge frames to keep acquisition alive. Consider matching ``framerate`` and exposure to the processing throughput of your system.
