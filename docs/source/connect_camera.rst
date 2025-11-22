.. _connect_camera:

Connect Your Camera
===================

MagScope ships with a simulated camera so you can explore the interface immediately, but you can swap in a real camera by providing a small adapter class. This guide shows how to implement a :class:`~magscope.camera.CameraBase` subclass for your hardware and register it with MagScope before launching the GUI.

Note: You may connect your camera to the computer with a simple interface like a USB port. In that case you are just using a camera.
But in many cases cameras are connected through a specialized frame grabber. This is a card added to the computer to support a special connection interface.
In that case you are really trying to connect MagScope to the frame grabber but for the purpose of this tutorial we will just refer to everything as the camera.

0. Test your camera with Python
-------------------------------

Before you try to get your camera to work with MagScope you should just try to create a minimal test of it working with Python.
Many manufacturers will provide Python bindings for free. This will include a guide on how to get your specific camera working.
For example:

- `Hamamatsu <https://www.hamamatsu.com/us/en/product/cameras/software/driver-software/dcam-sdk4.html>`_
- `Basler <https://github.com/basler/pypylon>`_
- `Allied Vision <https://docs.alliedvision.com/Vimba_DeveloperGuide/pythonAPIManual.html>`_
- `ThorLabs <https://www.thorlabs.com/software_pages/ViewSoftwarePage.cfm?Code=ThorCam>`_

Alternatively, you can use third party libraries. Many of these will connect to a wide variety of scientific cameras.
For example:

- `Harvesters <https://harvesters.readthedocs.io/>`_ This work with any camera that supports GenTL.
- `PyLabLib <https://pylablib.readthedocs.io>`_

1. Implement a camera adapter
-----------------------------

Once you can get your camera to connect through any Python library you can use that to connect it to MagScope.
Every camera must subclass :class:`~magscope.camera.CameraBase`.
At minimum, define the following attributes on the class:

* ``width`` and ``height``: image dimensions in pixels
* ``dtype``: a ``numpy`` integer dtype (``np.uint8``, ``np.uint16``, ``np.uint32``, or ``np.uint64``)
* ``bits``: the number of meaningful bits per pixel (must fit inside the dtype)
* ``nm_per_px``: nanometers represented by each pixel **with out any magnification**
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
