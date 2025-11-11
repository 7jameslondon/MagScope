.. _user_guide:

User Guide
==========

This guide focuses on launching the MagScope application and interacting with its graphical interface.

Launching the Demo GUI
----------------------

MagScope includes a simulated camera so the interface can be explored without laboratory hardware. Launch the demo from a Python interpreter by running::

   import magscope

   scope = magscope.MagScope()
   scope.start()

This creates the default window layout and begins streaming data from the built-in demo pipeline.

GUI
---

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Open-Close_Panel_v1.gif
   :alt: Demonstration of opening and closing a GUI panel in MagScope
   :align: center

Panels for each set of controls can be hidden or revealed by clicking on the panel's title.

What to Explore Next
--------------------

* **Scripting** – Placeholder for documenting how to automate experiments from the GUI.
* **Hardware integration** – Placeholder for instructions on connecting real microscopes, stages, or cameras.
* **Data export** – Placeholder for explaining how to save measurements captured through the interface.
