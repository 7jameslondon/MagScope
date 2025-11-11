.. _user_guide:

User Guide
==========

This guide focuses on launching the MagScope application and interacting with its graphical interface.

.. contents::
   :local:
   :depth: 2

Prerequisites
-------------

MagScope targets Python 3.11 or newer and depends on PyQt6 for the desktop interface. A CUDA-enabled GPU is optional for acceleration, but not required for basic GUI use. Install the package with::

   pip install magscope

Launching the Demo GUI
----------------------

MagScope includes a simulated camera so the interface can be explored without laboratory hardware. Launch the demo from a Python interpreter by running::

   import magscope

   scope = magscope.MagScope()
   scope.start()

This creates the default window layout and begins streaming data from the built-in demo pipeline. The demo starts in a single-window configuration, so closing that window exits the session.

Command-Line Script Entry Points
--------------------------------

For a script-driven workflow, the repository includes helpers that mirror the interactive example above:

* ``main.py`` starts MagScope with the default configuration::

     python main.py

* ``simulated_scope.py`` explicitly loads the ``DummyBeadCamera`` so the GUI always shows simulated imagery::

     python simulated_scope.py

  The script mirrors the manual steps of instantiating ``MagScope``, swapping in ``DummyBeadCamera``, and starting the application loop.

GUI
---

.. image:: ../assets/Open-Close Panel v1.gif
   :alt: Demonstration of opening and closing a GUI panel in MagScope
   :align: center

The GUI presents panels for camera feeds, plots, and controls. Use the window manager buttons to reveal or hide panels as needed.

What to Explore Next
--------------------

* **Scripting** – Placeholder for documenting how to automate experiments from the GUI.
* **Hardware integration** – Placeholder for instructions on connecting real microscopes, stages, or cameras.
* **Data export** – Placeholder for explaining how to save measurements captured through the interface.

Troubleshooting
---------------

* **Display issues** – Placeholder for guidance on installing the system dependencies required by PyQt6 (such as ``libGL`` on Linux).
* **Missing panels** – Placeholder for steps to restore default layouts if panels are accidentally closed.

