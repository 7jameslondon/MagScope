.. _user_guide:

User Guide
==========

This guide explains how to launch MagScope and use its interface. If you have not already read the :doc:`getting_started`.

Launching the Demo GUI
----------------------

MagScope includes a simulated camera so the interface can be explored without laboratory hardware. Launch the demo from a Python interpreter by running::

   import magscope

   scope = magscope.MagScope()
   scope.start()

This creates the default window layout and begins streaming data from the built-in demo pipeline.

You can only launch MagScope once.
After launching it if you want to start again you must close it and delete the instance (delete ``scope`` in the example).

To close MagScope you can close any of the windows, you might need to wait up to a couple of minutes for it to finish.
Or you call :py:meth:`MagScope.stop <magscope.scope.MagScope.stop>`.

Windows and Multiple Screens
----------------------------

It is often easier to see everything in MagScope with multiple screens.
By default MagScope will try to detect how many screens your computer has and place one Window in full screen on each.
Alternatively, you can specify the number of windows between 1-3 using the following::

   import magscope

   scope = magscope.MagScope()
   scope.window_manager.n_windows = 1
   scope.start()

.. list-table::
   :widths: 15 85
   :header-rows: 0

   * - One Screen
     - .. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/One-Window_v1.jpg
        :width: 200px

   * - Two Screens
     - .. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Two-Windows-a_v1.jpg
        :width: 200px
       .. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Two-Windows-b_v1.jpg
        :width: 200px

   * - Three Screens
     - .. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Three-Windows-a_v1.jpg
        :width: 200px
       .. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Three-Windows-c_v1.jpg
        :width: 200px
       .. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Three-Windows-b_v1.jpg
        :width: 200px

Live Video Viewer
--------------

MagScope automatically launches with a live video feed. You can zoom by scrolling in and out with a mouse wheel.

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Video_Viewer_v1.gif
   :alt: Demonstation of the live video feed (video viewer)
   :align: center

Control Panels
--------------

Panels for each set of controls can be hidden or revealed by clicking on the panel's title.

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Open-Close_Panel_v1.gif
   :alt: Demonstration of opening and closing a GUI panel in MagScope
   :align: center

Panels can move arranged by dragging them by the top-right corner. If space permits a new column can be added.

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Move-Panel_v1.gif
   :alt: Demonstration of moving a GUI panel in MagScope
   :align: center

The interface can be reset to the default arrangement by clicking the "Reset the GUI" button in the top-left corner of the window with the control panels.

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Reset-GUI_v1.jpg
   :alt: The "Reset the GUI" button
   :align: center

Status Panel
-------

??????????

Bead Selection
-------
.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Selecting_Beads_v1.gif
   :alt: Demonstation of beads being added, moved and removed
   :align: center

Instructions and some controls for selecting bead ROIs can be found in the "Bead Selection" panel.
To **add** a bead ROI click on the live video feed. A bead ROI will be created centered on your cursor.
You can **move** the ROI by dragging the ROI.
You can **remove** a bead by right-clicking the ROI.

.. image:: https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/Bead_Selection_Panel_v1.jpg
   :alt: The bead selection panel
   :align: center

Each bead ROI will be assigned an ID number in the corner of the ROI.
The ID number always increases to prevent mixing up beads.

To clear all beads and reset the ID number count to 0 click the "Remove All Beads" button in the "Bead Selection" panel.

During an experiment you may want to lock the beads so you do not accidentally add/move/remove any of the ROIs.
You can do this by click the 🔓 button on the "Bead Selection" panel.
This will only affect user interactions (it will not effect the XY-Lock).
You can click the button again to unlock.

Plot Settings
-------

??????????

Camera Settings
-------

??????????

Acquisition (Saving Data)
-------

??????????

Histogram
-------

??????????

Radial Profile Monitor
-------

??????????

Z-LUT
-------

??????????

Z-LUT Generator
-------

??????????

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


Scripting
---------

To learn how to use scripts to automate tasks in MagScope read the :doc:`scripting_guide` guide.
