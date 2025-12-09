.. _connect_camera:

Connect Your Camera
===================

MagScope ships with a simulated camera so you can explore the interface immediately, but you can swap in a real camera by providing a small adapter class. This guide shows how to implement a :class:`~magscope.camera.CameraBase` subclass for your hardware and register it with MagScope before launching the GUI.

**Do you have a frame grabber?**
Cameras can be connected to a computer through either a standard built-in interface or through a frame grabber.
If you have a frame grabber then you are really connecting MagScope with your frame grabber which is then interfacing with your camera.
For the purpose of this tutorial we will just refer to everything as the camera. Make sure you know what you have before starting this tutorial.

0. Test your camera with Python
-------------------------------

Before you try to get your camera to work with MagScope you should just try to create a minimal test of it working with Python in general.
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

* ``width`` and ``height``: Number of pixels in each dimension.
* ``bits``: The number bits per pixel. Most cameras generate 8, 10, 12, or 16-bits.
* ``dtype``: This needs to be ``numpy`` integer dtype (``numpy.uint8``, ``numpy.uint16``, ``numpy.uint32``, or ``numpy.uint64``).
  It needs to be just large enough to fit the number of bits your camera generates.
  For example if your camera generates 8-bit data then this should be ``numpy.uint8``.
  If it is 12-bit then ``numpy.uint16``.
* ``nm_per_px``: The width of a pixel in nanometers **with out any magnification**. This is usually between 1000nm-10000nm.
* ``settings``: list of setting names; must include ``"framerate"`` so the GUI can display and edit it

Implement the methods below to bridge between the device SDK and MagScope’s shared buffers:

* ``connect(video_buffer)``: Open the hardware connection, allocate any SDK buffers, and stash ``video_buffer`` for later writes
* ``fetch()``: Pull the next frame from the device into ``video_buffer`` using ``self.video_buffer.write_frame(...)``
* ``release()``: Return SDK buffers or handles after frames have been consumed. Not all cameras will need this.
* ``__getitem__``/``__setitem__``: Read and update entries in ``settings`` so the GUI can synchronize values

A minimal skeleton that wraps a vendor SDK might look like::

   import numpy as np
   import fake_sdk # you will need to replace this with a real SDK for your camera
   from magscope.camera import CameraBase

   class MyLabCamera(CameraBase):
       width = 2048
       height = 1024
       bits = 12
       dtype = np.uint16
       nm_per_px = 5000
       settings = ["framerate", "exposure"]

       def __init__(self):
           super().__init__()
           self._sdk = fake_sdk.SDK()
           self._settings = {"framerate": 30, "exposure": 10.0}

       def connect(self, video_buffer):
           self.video_buffer = video_buffer
           self._sdk = self._sdk.connect_to_camera()
           self.is_connected = True

       def fetch(self):
           image, timestamp = self._sdk.get_frame()
           self.video_buffer.write_image_and_timestamp(image, timestamp)

       def release(self):
           pass

       def __getitem__(self, name):
           return self._settings[name]

       def __setitem__(self, name, value):
           self._settings[name] = value
           self._sdk.update_setting(name, value)

**For examples with real cameras** take a look at the `examples/cameras folder on GitHub <https://github.com/7jameslondon/MagScope/tree/master/examples/cameras>`_.

2. Register the camera before starting MagScope
-----------------------------------------------

Instantiate your adapter and assign it to the camera manager prior to calling :py:meth:`magscope.scope.MagScope.start`::

   import magscope
   from lab_camera import MyLabCamera

   scope = magscope.MagScope()
   scope.camera_manager.camera = MyLabCamera()
   scope.start()

During startup the camera manager calls :py:meth:`magscope.camera.CameraBase.connect` and immediately publishes all entries in ``settings`` to the GUI, so ensure your adapter populates defaults before ``start()`` runs.

3. Validate the connection
--------------------------

* Watch the console for warnings; if :py:meth:`magscope.camera.CameraBase.connect` raises an exception MagScope will stay in simulation mode and report the error.
* Confirm that the GUI reflects any custom settings you exposed in ``settings`` and that adjusting them updates your device through ``__setitem__``.
