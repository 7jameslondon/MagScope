.. _getting_started:

Getting Started
===============

This guide will walk you through installing MagScope, verifying the installation, and running the demo so you can explore the interface in minutes.

(1) Install Python
------------------
MagScope requires Python *3.11 or newer*. I would **recommend using 3.13**.
If you are new to Python, I would recommend following `a guide for beginners such as this one from w3schools <https://www.w3schools.com/python/python_getstarted.asp>`_.

There are many ways to install Python. If you have a preferred method that works for you, feel free to use that.
You can always download Python for free from the official website: `python.org <https://www.python.org/downloads/>`_.

(2) Setup a Virtual Environment
------------------------------
You can run MagScope in Python directly without a virtual environment, but it is not recommended.
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

Note: This will install a CPU-only version of MagScope. Once you have that working, you can add support for GPU acceleration.

(4) Run MagScope
-----------------

MagScope ships with a simulated camera so you can try the interface without connecting hardware.
To launch the demo run Python, import MagScope, and call the ``start`` method:

.. code-block:: python

   import magscope
   scope = magscope.MagScope()
   scope.start()

This command launches MagScope and begins streaming data.

Next Steps
----------
* If you have a NVIDIA GPU you can read the :doc:`gpu_guide` guide for information on adding GPU acceleration for image processing.
* To learn how to use MagScope read the :doc:`user_guide`.
* To add your own camera, motor or other hardware see our guides in :doc:`index`.

Support
-------

| You can report issues and make requests on the `GitHub issue tracker <https://github.com/7jameslondon/MagScope/issues>`_.
| Having trouble? Need help? Have suggestions? Want to contribute?
| Email us at magtrackandmagscope@gmail.com
