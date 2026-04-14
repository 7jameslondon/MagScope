.. _gpu_guide:

GPU Acceleration
======================
This is **optional**. MagScope can work entirely with your computer's CPU. However, adding GPU support can make image processing faster.

Do you have a supported GPU?
----------------------------
To use GPU-acceleration in MagScope you must have a CUDA-capable GPU and a compatible NVIDIA driver.
Most NVIDIA GPUs are supported. See `this list from NVIDIA <https://developer.nvidia.com/cuda-gpus>`_ to check your specific GPU.

Recommended install
-------------------
Before trying to install MagScope with GPU support you should have followed the :doc:`getting_started` guide and got MagScope to work with just the CPU.
With your virtual environment active, install MagScope first:

.. code-block:: console

   pip install magscope

Then install the CuPy package that matches your CUDA major version, using CuPy's ``[ctk]`` extra:

.. list-table::
   :header-rows: 1

   * - CUDA version
     - Pip command
   * - 12.x
     - ``pip install "cupy-cuda12x[ctk]"``
   * - 13.x
     - ``pip install "cupy-cuda13x[ctk]"``

This is the recommended path for a fresh environment. CuPy will install the CUDA runtime components it needs from PyPI, so you do not usually need a separate system CUDA Toolkit.

Alternative install
-------------------
If you already manage CUDA separately on your system, MagScope also provides optional dependencies that install the matching CuPy wheel:

.. list-table::
   :header-rows: 1

   * - CUDA version
     - Pip command
   * - 12.x
     - ``pip install "magscope[cu12]"``
   * - 13.x
     - ``pip install "magscope[cu13]"``

This path assumes your NVIDIA driver and local CUDA installation are already set up correctly. If you are starting from scratch, prefer the ``[ctk]`` install above.

Supported versions
------------------
MagScope supports CUDA 12.x and 13.x for GPU acceleration.
CUDA 11 support has been removed to match current CuPy support.

If you need to diagnose an existing system CUDA installation, ``nvcc --version`` can still be useful, but it is not required for the recommended ``[ctk]`` workflow.

To check it worked you can run the following in Python:

.. code-block:: python

   import magscope
   print(magscope.check_cupy())

This returns ``True`` if CuPy is available and usable. Otherwise it returns ``False``.
