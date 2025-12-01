.. _gpu_guide:

Setup GPU Acceleration
======================
This is **optional**. MagScope can work entirely with your computer's CPU. However, adding GPU support can make image processing faster.

Do you have a supported GPU?
----------------------------
To use GPU-acceleration in MagScope you must have a CUDA capable GPU.
Most NVIDIA GPUs are supported. See `this list from NVIDIA <https://developer.nvidia.com/cuda-gpus>`_ to check your specific GPU.

Install the NVIDIA CUDA Toolkit
-------------------------------
You will likely need to install the free CUDA Toolkit. We support versions 11.x, 12.x and 13.x.
Before installing you can check if you already have it installed and the version by running ``nvcc --version`` in a terminal/command prompt.
If that returns an error you do not have the toolkit. Otherwise it will print the version.
If the version does not start with 11, 12 or 13 then you will need to install a newer version.

To install CUDA follow NVIDIA's instructions `CUDA Toolkit <https://developer.nvidia.com/cuda-toolkit>`_.
If you experience issues it may be necessary to update your NVIDIA drivers (see NVIDIA for how to do this).
Or you may need to use an older toolkit that works with your GPU (see `driver version compatibility <https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html>`_).
Keep in mind that MagScope can only support toolkit versions 11, 12, or 13.

Once installed you should restart your computer.
You can check the install worked by checking the version by running ``nvcc --version`` in a terminal/command prompt.

Install MagScope with GPU-acceleration
--------------------------------------
Before trying to install MagScope with GPU support you should have followed the :doc:`getting_started` guide and got MagScope to work with just the CPU.
With your virtual environment active, you can install MagScope along with GPU support using ``pip``.
It is fine if you already have installed magscope.
If you are new to ``pip``, I recommend following `this guide from w3schools <https://www.w3schools.com/python/python_pip.asp>`_.

Install MagTrack using the pip command that matches your CUDA version:

   .. list-table::
      :header-rows: 1

      * - CUDA Toolkit Version
        - Pip command
      * - 11.x
        - ``pip install magtrack[cu11]``
      * - 12.x
        - ``pip install magtrack[cu12]``
      * - 13.x
        - ``pip install magtrack[cu13]``

To check it worked you can run the following in Python:

.. code-block:: python

   import magscope
   magscope.check_cupy()

This will return True if CuPy can reach and use the installed toolkit. Otherwise it is False.
