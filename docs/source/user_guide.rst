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

Windows and Multiple Screens
--------------

It is often easier to see everything in MagScope with multiple screens.
By default MagScope will try to detect how many screens your computer has and place one Window in full screen on each.
Alternatively, you can specify the number of windows between 1-3 using the following::

   import magscope

   scope = magscope.MagScope()
   scope.window_manager.n_windows = 1
   scope.start()


Control Panels
--------------

Panels for each set of controls can be hidden or revealed by clicking on the panel's title.

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Open-Close_Panel_v1.gif
   :alt: Demonstration of opening and closing a GUI panel in MagScope
   :align: center

Scripting
---------
MagScope comes with a lightweight scripting runtime that allows you to queue up GUI interactions and hardware commands for repeatable experiments.
A script is an instance of ``magscope.Script`` where each call records a step to be executed.

First, create a script following the details below.
An `example script is available <https://github.com/7jameslondon/MagScope/blob/master/example_script.py>`_.
Second, load the script by clicking "Load" and selecting the Python script.
Once loaded the Scripting panel should say "Loaded".
Third, click "Start".
You can pause your script while it is running.
Or once it is "Finished" you can run it again by clicking "Start".

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Scripting_Panel_v1.jpg
         :alt: Screenshot of the Scripting panel.

Writing your own script
""""""""""""""""""""""""

To start a script create a new Python file (example: ``a_script.py``).
Then import magscope and create script instance.
The instance can be called anything (it does not need to be called "my_script").
Example::
   import magscope
   my_script = magscope.Script()

To add a step to the script you just need to call it with atleast one argument.
The first argument should be the name of the scriptable function you want to call.
Latter arguments should be anything you need to pass to that function.
For example we can call the ``sleep`` command to pause our script for 5 seconds like this::
   my_script('sleep', 5)


XY-Lock
-------
**Once:** Keeping your beads in the center of the ROI improves tracking accuracy.
It can be annoying to move each bead to the center of the ROI.
When a bead is being tracked MagScope can move the ROI so that the bead is in the exact center.
Just open the XY-Lock panel and click the "Once" button.

**Automatic:** When you are running a long experiment you might notice some drift in your stage/sample over time.
You can have MagScope periodically run the centering routine by checking the "Enabled" checkbox.
You can control the frequency of updates and set a maximum distance to move at a time.

The bead XY-positions are relative to the top-left of the camera's field of view.
Therefore, moving the ROI does not affect the bead's detected position, unless it is near the edge of the ROI.
Generally you should not see any "jumps" in the beads position when using the XY-Lock.

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/XY-Lock_Panel_v1.jpg
         :alt: Screenshot of the XY-Lock panel.
         :height: 166px
.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/XY-Lock_Bead_v1.gif
         :alt: Demonstration of a bead being centered in a ROI.
         :height: 166px

Z-Lock
------
Z-Lock seems similar to XY-Lock but works a little different.
Z-Lock takes control of the piezo "Z" or "focusing" motor to keep one bead at a specific Z value.
**You must have a Z-LUT loaded and the selected bead's current focus must be within the range of the Z-LUT.**
**You must have your piezo motor controlled by MagScope to use this feature.**
Connecting hardware is covered in the :ref:`custom_guide`.

Z-Lock has five settings which must be set before the Z-Lock will take affect:

* Enabled - Whether the Z-Lock is active.
* Bead - Which bead ROI will be kept in focus. This should generally be a reference bead.
* Target - The Z-value that the selected bead will be maintained at.
* Interval - The frequency with which the difference between the target value and current value will be checked/adjusted.
* Max - An upper limit you can set. The Z-Lock will not move more than this amount at a given time. If the Z-Look keep over shooting the target try decreasing this value.

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Z-Lock_Panel_v1.jpg
         :alt: Screenshot of the Z-Lock panel.
         :align: center

Coming Soon
--------------------

* **Hardware integration** – Placeholder for instructions on connecting real microscopes, stages, or cameras.
* **Data export** – Placeholder for explaining how to save measurements captured through the interface.
