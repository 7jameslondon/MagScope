.. _getting_started:

Getting Started
===============

This guide walks through installing MagScope, verifying the installation, and running the demo microscope so you can explore the
interface in minutes.

.. contents::
   :local:
   :depth: 2

System Requirements
-------------------

MagScope targets Python 3.11 or newer on Windows, macOS, and Linux. The desktop application relies on `PyQt6 <https://pypi.org/project/PyQt6/>`_,
so make sure your environment can install Qt dependencies (on Linux, that usually means system packages such as ``libxcb`` and ``libGL``).
A CUDA-enabled GPU can accelerate certain processing pipelines but is **not** required to run the demo.

Install MagScope
----------------

You can install MagScope from the Python Package Index (PyPI) with ``pip``. Create and activate a virtual environment if desired, then run::

   pip install magscope

If you are working from a cloned repository and want an editable install for development, run::

   pip install -e .[dev]

This command installs MagScope along with the development extras defined in ``pyproject.toml``.

Verify the Installation
-----------------------

Launch Python and confirm that MagScope can be imported::

   python - <<'PY'
   import magscope
   print(magscope.__version__)
   PY

You should see the installed version printed in the terminal. If the import fails, double-check that your virtual environment is activated and that
``pip`` installed MagScope into the same interpreter.

Run the Demo Application
------------------------

MagScope ships with a simulated camera so you can try the interface without connecting hardware. After installing the package, launch the demo GUI with::

   python -m magscope.demo

This command opens the MagScope window and begins streaming data from the built-in ``DummyBeadCamera``. Close the window to exit the application.

Next Steps
----------

* Read the :doc:`user_guide` for an overview of the interface and built-in tools.
* Explore the ``example_script.py`` in the repository to see how to embed MagScope in automation scripts.
* When you are ready to integrate laboratory hardware, consult the customization and developer guides (coming soon).
