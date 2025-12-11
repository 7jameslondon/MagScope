.. _scripting_commands:

Scripting Commands
==================

| This page lists every built-in scriptable command and the corresponding function that is called.
| For example ``Command`` → ``function()``.
| See the :doc:`scripting_guide` guide for how to write and run scripts.

Timing and messaging
--------------------

* :class:`~magscope.ipc_commands.SleepCommand` ``(duration: float)`` → :meth:`~magscope.scripting.ScriptManager.start_sleep`  
  Pause script execution for the given duration in seconds.
* :class:`~magscope.ipc_commands.ShowMessageCommand` ``(text: str, details: str | None = None)`` → :meth:`~magscope.ui.ui.UIManager.print`
  Display an information dialog in the GUI. Optional ``details`` are shown in the dialog's expandable details area.

Acquisition
-----------

* :class:`~magscope.ipc_commands.SetAcquisitionOnCommand` ``(value: bool)`` → :meth:`~magscope.processes.ManagerProcessBase.set_acquisition_on`  
  Enable or disable acquisition and processing across all managers.
* :class:`~magscope.ipc_commands.SetAcquisitionDirOnCommand` ``(value: bool)`` → :meth:`~magscope.processes.ManagerProcessBase.set_acquisition_dir_on`  
  Toggle writing acquisitions to disk. Combine with ``SetAcquisitionDirCommand`` to choose the
  directory.
* :class:`~magscope.ipc_commands.SetAcquisitionDirCommand` ``(value: str | None)`` → :meth:`~magscope.processes.ManagerProcessBase.set_acquisition_dir`  
  Set the directory used to save acquisitions. Pass ``None`` to clear the current directory.
* :class:`~magscope.ipc_commands.SetAcquisitionModeCommand` ``(mode: AcquisitionMode)`` → :meth:`~magscope.processes.ManagerProcessBase.set_acquisition_mode`  
  Switch the acquisition mode. Valid ``AcquisitionMode`` values: ``TRACK``, ``TRACK_AND_CROP_VIDEO``,
  ``TRACK_AND_FULL_VIDEO``, ``CROP_VIDEO``, ``FULL_VIDEO``, ``ZLUT``.

XYZ-Lock
--------

* :class:`~magscope.ipc_commands.ExecuteXYLockCommand` ``(now: float | None = None)`` → :meth:`~magscope.beadlock.BeadLockManager.do_xy_lock`  
  Run a single XY lock pass to re-center bead ROIs based on the latest tracked positions. ``now``
  overrides the timestamp used for rate limiting and is usually left as ``None``.
* :class:`~magscope.ipc_commands.SetXYLockOnCommand` ``(value: bool)`` → :meth:`~magscope.beadlock.BeadLockManager.set_xy_lock_on`  
  Enable or disable continuous XY locking. Enabling resets the cutoff time so only new track data is
  considered.
* :class:`~magscope.ipc_commands.SetXYLockIntervalCommand` ``(value: float)`` → :meth:`~magscope.beadlock.BeadLockManager.set_xy_lock_interval`  
  Set the delay (seconds) between XY lock iterations.
* :class:`~magscope.ipc_commands.SetXYLockMaxCommand` ``(value: float)`` → :meth:`~magscope.beadlock.BeadLockManager.set_xy_lock_max`  
  Limit the maximum per-axis movement applied during XY lock. Values below 1 are clamped up.
* :class:`~magscope.ipc_commands.SetXYLockWindowCommand` ``(value: int)`` → :meth:`~magscope.beadlock.BeadLockManager.set_xy_lock_window`  
  Control how many of the most recent track positions are averaged when computing the XY correction.
  The window is clamped to a minimum of 1 sample.
* :class:`~magscope.ipc_commands.SetZLockOnCommand` ``(value: bool)`` → :meth:`~magscope.beadlock.BeadLockManager.set_z_lock_on`  
  Enable or disable Z locking.
* :class:`~magscope.ipc_commands.SetZLockBeadCommand` ``(value: int)`` → :meth:`~magscope.beadlock.BeadLockManager.set_z_lock_bead`  
  Select the bead id used for Z locking.
* :class:`~magscope.ipc_commands.SetZLockTargetCommand` ``(value: float)`` → :meth:`~magscope.beadlock.BeadLockManager.set_z_lock_target`  
  Set the target Z value for the lock controller. The value should match the units used in the
  active Z-LUT/profile.
* :class:`~magscope.ipc_commands.SetZLockIntervalCommand` ``(value: float)`` → :meth:`~magscope.beadlock.BeadLockManager.set_z_lock_interval`  
  Set the cadence (seconds) between Z lock updates.
* :class:`~magscope.ipc_commands.SetZLockMaxCommand` ``(value: float)`` → :meth:`~magscope.beadlock.BeadLockManager.set_z_lock_max`  
  Limit the maximum Z adjustment applied per update.
