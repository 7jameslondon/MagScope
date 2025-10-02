from math import copysign, isnan
import numpy as np
from time import time

from magscope.gui import WindowManager
from magscope.processes import ManagerProcessBase
from magscope.utils import Message, registerwithscript


class BeadLockManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()

        # XY-Lock Properties
        self.xy_lock_on: bool = False
        self.xy_lock_interval: float
        self.xy_lock_max: float
        self._xy_lock_last_time: float = 0.
        self._xy_lock_pending_moves: list[int] = []

        # Z-Lock Properties
        self.z_lock_on: bool = False
        self.z_lock_bead: int = 0
        self.z_lock_target: float | None = None
        self.z_lock_interval: float
        self.z_lock_max: float
        self._z_lock_last_time: float = 0.

    def setup(self):
        self.xy_lock_interval = self.settings['xy-lock default interval']
        self.xy_lock_max = self.settings['xy-lock default max']
        self.z_lock_interval = self.settings['z-lock default interval']
        self.z_lock_max = self.settings['z-lock default max']

    def do_main_loop(self):
        # XY-Lock Enabled
        if self.xy_lock_on:
            # Timer
            if (now:=time()) - self._xy_lock_last_time > self.xy_lock_interval:
                self.do_xy_lock(now=now)

        # Z-Lock Enabled
        if self.z_lock_on:
            # Timer
            if (now:=time()) - self._z_lock_last_time > self.z_lock_interval:
                self.do_z_lock(now=now)

    @registerwithscript('do_xy_lock')
    def do_xy_lock(self, now=None):
        """ Centers the bead-rois based on their tracked position """

        # Gather information
        width = self.settings['bead roi width']
        half_width = width // 2
        tracks = self.tracks_buffer.peak_unsorted().copy()
        if now is None: now = time()
        self._xy_lock_last_time = now

        # For each bead calculate if/how much to move
        for id, roi in self.bead_rois.items():

            # Get the track for this bead
            track = tracks[tracks[:, 4] == id, :]

            # Check there is track data
            if track.shape[0] == 0:
                continue

            # Get the latest position
            idx = np.argmax(track[:, 0])
            t, x, y, roi_x, roi_y = track[idx, [0, 1, 2, 5, 6]].tolist()

            # Check the position is valid
            if isnan(t) or isnan(x) or isnan(y):
                continue

            # Check the position was recent
            if now - t > 3*self.xy_lock_interval:
                continue

            # Check the bead-roi is current
            if roi[0] != int(roi_x) or roi[2] != int(roi_y):
                continue

            # Check the bead started the last move
            if id in self._xy_lock_pending_moves:
                continue

            # Calculate the move
            nm_per_px = self.camera_type.nm_per_px / self.settings['magnification']
            dx = (x / nm_per_px) - half_width - roi_x
            dy = (y / nm_per_px) - half_width - roi_y
            if abs(dx) <= 1:
                dx = 0.
            if abs(dy) <= 1:
                dy = 0.
            dx = round(dx)
            dy = round(dy)

            # Limit movement to the maximum threshold
            dx = copysign(min(abs(dx), self.xy_lock_max), dx)
            dy = copysign(min(abs(dy), self.xy_lock_max), dy)

            # Move the bead as needed
            if abs(dx) > 0. or abs(dy) > 0.:
                self._xy_lock_pending_moves.append(id)
                message = Message(
                    to=WindowManager,
                    meth=WindowManager.move_bead,
                    args=(id, dx, dy)
                )
                self.send_ipc(message)

    @registerwithscript('do_z_lock')
    def do_z_lock(self, now=None):
        # Gather information
        if now is None: now = time()
        self._z_lock_last_time = now

        raise NotImplementedError

    def set_bead_rois(self, value: dict[int, tuple[int, int, int, int]]):
        super().set_bead_rois(value)

        # Check if any of the beads have been deleted
        keys = list(self._xy_lock_pending_moves) # copy
        for id in keys:
            if id not in self.bead_rois:
                self._xy_lock_pending_moves.pop(id)

    def remove_bead_from_xy_lock_pending_moves(self, id: int):
        if id in self._xy_lock_pending_moves:
            self._xy_lock_pending_moves.remove(id)

    @registerwithscript('set_xy_lock_on')
    def set_xy_lock_on(self, value: bool):
        self.xy_lock_on = value

        from magscope.gui import WindowManager
        message = Message(
            to=WindowManager,
            meth=WindowManager.update_xy_lock_enabled,
            args=(value,)
        )
        self.send_ipc(message)

    @registerwithscript('set_xy_lock_interval')
    def set_xy_lock_interval(self, value: float):
        self.xy_lock_interval = value

        from magscope.gui import WindowManager
        message = Message(
            to=WindowManager,
            meth=WindowManager.update_xy_lock_interval,
            args=(value,)
        )
        self.send_ipc(message)

    @registerwithscript('set_xy_lock_max')
    def set_xy_lock_max(self, value: float):
        value = max(1, round(value))
        self.xy_lock_max = value

        from magscope.gui import WindowManager
        message = Message(
            to=WindowManager,
            meth=WindowManager.update_xy_lock_max,
            args=(value,)
        )
        self.send_ipc(message)

    @registerwithscript('set_z_lock_on')
    def set_z_lock_on(self, value: bool):
        self.z_lock_on = value

        from magscope.gui import WindowManager
        message = Message(
            to=WindowManager,
            meth=WindowManager.update_z_lock_enabled,
            args=(value,)
        )
        self.send_ipc(message)

    @registerwithscript('set_z_lock_bead')
    def set_z_lock_bead(self, value: int):
        value = int(value)
        self.z_lock_bead = value

        from magscope.gui import WindowManager
        message = Message(
            to=WindowManager,
            meth=WindowManager.update_z_lock_bead,
            args=(value,)
        )
        self.send_ipc(message)

    @registerwithscript('set_z_lock_target')
    def set_z_lock_target(self, value: float):
        self.z_lock_target = value

        from magscope.gui import WindowManager
        message = Message(
            to=WindowManager,
            meth=WindowManager.update_z_lock_target,
            args=(value,)
        )
        self.send_ipc(message)

    @registerwithscript('set_z_lock_interval')
    def set_z_lock_interval(self, value: float):
        self.z_lock_interval = value

        from magscope.gui import WindowManager
        message = Message(
            to=WindowManager,
            meth=WindowManager.update_z_lock_interval,
            args=(value,)
        )
        self.send_ipc(message)

    @registerwithscript('set_z_lock_max')
    def set_z_lock_max(self, value: float):
        self.z_lock_max = value

        from magscope.gui import WindowManager
        message = Message(
            to=WindowManager,
            meth=WindowManager.update_z_lock_max,
            args=(value,)
        )
        self.send_ipc(message)