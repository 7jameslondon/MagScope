.. _getting_started:

Getting Started
===============

This guide will walk you through installing MagScope, verifying the installation, and running the demo so you can explore the interface in minutes.

(1) Install Python
------------------
MagScope requires Python *3.11 or newer* . I would **recommend using 3.13**.
If you are new to Python, I would recommend following `a guide for beginners such as this one from w3schools <https://www.w3schools.com/python/python_getstarted.asp>`_.
If you do not have Python there are many ways to install it. If you have a preferred method that works for you, feel free to use that.
You can always download Python for free from the official website: `python.org <https://www.python.org/downloads/>`_.

(2) Setup a Virtual Environment
------------------------------
You can run Python directly without a virtual environment, but it is not recommended.
If this is your first time using virtual environments, I recommend following `this guide from w3schools <https://docs.python.org/3/tutorial/venv.html>`_.

You can create a virtual environment using the built-in ``venv`` module.
Create the virtual environment in a new folder and activate it.

(3) Install MagScope
--------------------
With your virtual environment active, you can install the latest version of MagScope using ``pip``.
If you are new to ``pip``, I recommend following `this guide from w3schools <https://www.w3schools.com/python/python_pip.asp>`_.
Run the following command in your terminal or command prompt:

.. code-block:: bash

   pip install magscope

Note: This will install a CPU-only version of MagScope. Once we have that working we will add support for GPU acceleration.

(4) Run MagScope
-----------------

MagScope ships with a simulated camera so you can try the interface without connecting hardware.
To launch the demo run Python, import MagScope, and run the start command:

.. code-block:: python

   python
   import magscope
   magscope.MagScope().start()

This command opens the MagScope window and begins streaming data.

Next Steps
----------

* Read the :doc:`user_guide` for an overview of the interface and built-in tools.

TODO: Add link to GPU install
TODO: Add link to add camera, hardware