from math import copysign
from time import time

import numpy as np

from magscope.ipc import register_ipc_command
from magscope.ipc_commands import (ExecuteXYLockCommand, MoveBeadsCommand,
                                   RemoveBeadFromPendingMovesCommand,
                                   RemoveBeadsFromPendingMovesCommand, SetXYLockIntervalCommand,
                                   SetXYLockMaxCommand, SetXYLockOnCommand, SetXYLockWindowCommand,
                                   SetZLockBeadCommand, SetZLockIntervalCommand, SetZLockMaxCommand,
                                   SetZLockOnCommand, SetZLockTargetCommand,
                                   UpdateXYLockEnabledCommand, UpdateXYLockIntervalCommand,
                                   UpdateXYLockMaxCommand, UpdateXYLockWindowCommand,
                                   UpdateZLockBeadCommand, UpdateZLockEnabledCommand,
                                   UpdateZLockIntervalCommand, UpdateZLockMaxCommand,
                                   UpdateZLockTargetCommand)
from magscope.processes import ManagerProcessBase
from magscope.utils import register_script_command


class BeadLockManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()

        # XY-Lock Properties
        self.xy_lock_on: bool = False
        self.xy_lock_interval: float
        self.xy_lock_max: float
        self.xy_lock_window: int
        self._xy_lock_last_time: float = 0.
        self._xy_lock_global_cutoff: float = 0.
        self._xy_lock_bead_cutoff: dict[int, float] = {}
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
        window_default = self.settings.get('xy-lock default window', 1)
        self.xy_lock_window = max(1, int(window_default))
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

    @register_ipc_command(ExecuteXYLockCommand)
    @register_script_command(ExecuteXYLockCommand)
    def do_xy_lock(self, now=None):
        """ Centers the bead-rois based on their tracked position """

        # Gather information
        width = self.settings['bead roi width']
        half_width = width // 2
        tracks = self.tracks_buffer.peak_unsorted().copy()
        if now is None: now = time()
        self._xy_lock_last_time = now

        # For each bead calculate if/how much to move
        moves_to_send: list[tuple[int, int, int]] = []
        for id, roi in self.bead_rois.items():

            # Get the track for this bead
            track = tracks[tracks[:, 4] == id, :]

            # Check there is track data
            if track.shape[0] == 0:
                continue

            # Filter to valid positions for this ROI
            position_mask =  ~np.isnan(track[:, [0, 1, 2]]).any(axis=1)
            valid_track = track[position_mask]

            cutoff = max(
                self._xy_lock_global_cutoff,
                self._xy_lock_bead_cutoff.get(id, 0.),
            )
            time_mask = valid_track[:, 0] >= cutoff
            valid_track = valid_track[time_mask]

            if valid_track.shape[0] == 0:
                continue

            # Use the most recent valid positions
            order = np.argsort(valid_track[:, 0])[::-1]
            recent_track = valid_track[order[: self.xy_lock_window]]
            _, xs, ys, *_ = recent_track.T
            x = float(np.mean(xs))
            y = float(np.mean(ys))

            # Check the bead started the last move
            if id in self._xy_lock_pending_moves:
                continue

            # Calculate the move
            nm_per_px = self.camera_type.nm_per_px / self.settings['magnification']
            dx = (x / nm_per_px) - half_width - roi[0]
            dy = (y / nm_per_px) - half_width - roi[2]
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
                moves_to_send.append((id, int(dx), int(dy)))

        if moves_to_send:
            self._xy_lock_pending_moves.extend([id for id, _, _ in moves_to_send])
            command = MoveBeadsCommand(moves=moves_to_send)
            self.send_ipc(command)

    def do_z_lock(self, now=None):
        # Gather information
        if now is None: now = time()
        self._z_lock_last_time = now

        raise NotImplementedError

    def set_bead_rois(self, value: dict[int, tuple[int, int, int, int]]):
        previous_bead_rois = getattr(self, 'bead_rois', {}).copy()
        super().set_bead_rois(value)

        # Check if any of the beads have been deleted
        keys = list(self._xy_lock_pending_moves) # copy
        for id in keys:
            if id not in self.bead_rois:
                self._xy_lock_pending_moves.pop(id)

        # Remove any bead-specific cutoffs for deleted beads
        bead_cutoff_ids = list(self._xy_lock_bead_cutoff)
        for bead_id in bead_cutoff_ids:
            if bead_id not in self.bead_rois:
                self._xy_lock_bead_cutoff.pop(bead_id, None)

        now = time()
        for bead_id, roi in self.bead_rois.items():
            previous_roi = previous_bead_rois.get(bead_id)
            if previous_roi == roi:
                continue

            if bead_id in self._xy_lock_pending_moves:
                continue

            self._xy_lock_bead_cutoff[bead_id] = now

    @register_ipc_command(RemoveBeadFromPendingMovesCommand)
    def remove_bead_from_xy_lock_pending_moves(self, id: int):
        if id in self._xy_lock_pending_moves:
            self._xy_lock_pending_moves.remove(id)

    @register_ipc_command(RemoveBeadsFromPendingMovesCommand)
    def remove_beads_from_xy_lock_pending_moves(self, ids: list[int]):
        if not ids:
            return

        pending_set = set(ids)
        self._xy_lock_pending_moves = [
            bead_id for bead_id in self._xy_lock_pending_moves if bead_id not in pending_set
        ]

    @register_ipc_command(SetXYLockOnCommand)
    @register_script_command(SetXYLockOnCommand)
    def set_xy_lock_on(self, value: bool):
        self.xy_lock_on = value
        self._xy_lock_global_cutoff = time()

        command = UpdateXYLockEnabledCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetXYLockIntervalCommand)
    @register_script_command(SetXYLockIntervalCommand)
    def set_xy_lock_interval(self, value: float):
        self.xy_lock_interval = value

        command = UpdateXYLockIntervalCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetXYLockMaxCommand)
    @register_script_command(SetXYLockMaxCommand)
    def set_xy_lock_max(self, value: float):
        value = max(1, round(value))
        self.xy_lock_max = value

        command = UpdateXYLockMaxCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetXYLockWindowCommand)
    @register_script_command(SetXYLockWindowCommand)
    def set_xy_lock_window(self, value: int):
        self.xy_lock_window = max(1, int(value))

        command = UpdateXYLockWindowCommand(value=self.xy_lock_window)
        self.send_ipc(command)

    @register_ipc_command(SetZLockOnCommand)
    @register_script_command(SetZLockOnCommand)
    def set_z_lock_on(self, value: bool):
        self.z_lock_on = value

        command = UpdateZLockEnabledCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetZLockBeadCommand)
    @register_script_command(SetZLockBeadCommand)
    def set_z_lock_bead(self, value: int):
        value = int(value)
        self.z_lock_bead = value

        command = UpdateZLockBeadCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetZLockTargetCommand)
    @register_script_command(SetZLockTargetCommand)
    def set_z_lock_target(self, value: float):
        self.z_lock_target = value

        command = UpdateZLockTargetCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetZLockIntervalCommand)
    @register_script_command(SetZLockIntervalCommand)
    def set_z_lock_interval(self, value: float):
        self.z_lock_interval = value

        command = UpdateZLockIntervalCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetZLockMaxCommand)
    @register_script_command(SetZLockMaxCommand)
    def set_z_lock_max(self, value: float):
        self.z_lock_max = value

        command = UpdateZLockMaxCommand(value=value)
        self.send_ipc(command)
