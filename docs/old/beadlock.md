# Bead lock manager

The `magscope.beadlock` module implements closed-loop stage adjustments that
keep tracked beads centered inside their regions of interest. It exposes a
single process class, :class:`~magscope.beadlock.BeadLockManager`, which runs in
its own manager process and communicates with the Qt GUI through the shared IPC
channel.

## Responsibilities

`BeadLockManager` extends :class:`~magscope.processes.ManagerProcessBase` and
therefore follows the common manager life cycle (``setup`` → main loop →
``quit``). During :meth:`~magscope.beadlock.BeadLockManager.setup` the manager
reads its initial thresholds from the shared settings dictionary. The
:meth:`~magscope.beadlock.BeadLockManager.do_main_loop` method then performs two
timed control loops on every iteration:

* **XY lock** &ndash; When enabled, :meth:`~magscope.beadlock.BeadLockManager.do_xy_lock`
  checks the most recent tracked position for each bead. Deviations greater
  than one pixel are rounded, limited to ``xy_lock_max``, and sent to
  :class:`~magscope.gui.WindowManager` through a
  :class:`~magscope.utils.Message`. The GUI responds by nudging the ROI so the
  bead recenters gradually rather than in one jump.
* **Z lock** &ndash; A placeholder hook is present via
  :meth:`~magscope.beadlock.BeadLockManager.do_z_lock`. Implementations should
  populate it with autofocus logic that steers the objective toward
  ``z_lock_target`` at a cadence controlled by ``z_lock_interval``.

All IPC methods that can be invoked from the scripting engine are decorated
with :func:`~magscope.utils.registerwithscript`, allowing automated workflows to
turn locking on and off or tune the control parameters.

## State management helpers

Several helper methods manage the book-keeping required to keep XY lock stable:

* :meth:`~magscope.beadlock.BeadLockManager.set_bead_rois` removes any deleted
  bead identifiers from the ``_xy_lock_pending_moves`` queue so stale moves are
  not retried.
* :meth:`~magscope.beadlock.BeadLockManager.remove_bead_from_xy_lock_pending_moves`
  clears the pending flag once the GUI reports a successful move.
* The ``set_*`` accessors (``set_xy_lock_on``, ``set_z_lock_interval``, etc.)
  mirror the GUI inputs and immediately dispatch an IPC message so the window
  reflects the current value.

Together these pieces keep the bead lock manager loosely coupled from the GUI
while still maintaining a responsive feedback loop between tracking results and
stage corrections.
