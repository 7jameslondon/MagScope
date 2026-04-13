from math import copysign
from time import time
from warnings import warn

import numpy as np

from magscope.datatypes import MatrixBuffer
from magscope.hardware import FocusMotorBase
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import (
    ExecuteXYLockCommand,
    MoveFocusMotorAbsoluteCommand,
    MoveBeadsCommand,
    RemoveBeadFromPendingMovesCommand,
    RemoveBeadsFromPendingMovesCommand,
    SetXYLockIntervalCommand,
    SetXYLockMaxCommand,
    SetXYLockOnCommand,
    SetXYLockWindowCommand,
    SetZLockBeadCommand,
    SetZLockIntervalCommand,
    SetZLockMaxCommand,
    SetZLockOnCommand,
    SetZLockTargetCommand,
    SetZLockWindowCommand,
    UpdateXYLockEnabledCommand,
    UpdateXYLockIntervalCommand,
    UpdateXYLockMaxCommand,
    UpdateXYLockWindowCommand,
    UpdateZLockBeadCommand,
    UpdateZLockEnabledCommand,
    UpdateZLockIntervalCommand,
    UpdateZLockMaxCommand,
    UpdateZLockTargetCommand,
    UpdateZLockWindowCommand,
)
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
        self._xy_lock_last_time: float = 0.0
        self._xy_lock_global_cutoff: float = 0.0
        self._xy_lock_bead_cutoff: dict[int, float] = {}
        self._xy_lock_pending_moves: list[int] = []

        # Z-Lock Properties
        self.z_lock_on: bool = False
        self.z_lock_bead: int = 0
        self.z_lock_target: float | None = None
        self.z_lock_interval: float
        self.z_lock_max: float
        self.z_lock_window: int
        self._z_lock_last_time: float = 0.0
        self._z_lock_global_cutoff: float = 0.0
        self._z_lock_expected_focus_target: float | None = None
        self._z_lock_last_focus_target: float | None = None
        self._focus_motor_name: str | None = None
        self._focus_buffer: MatrixBuffer | None = None

    def setup(self):
        self.xy_lock_interval = self.settings['xy-lock default interval']
        self.xy_lock_max = self.settings['xy-lock default max']
        window_default = self.settings.get('xy-lock default window', 1)
        self.xy_lock_window = max(1, int(window_default))
        self.z_lock_interval = self.settings['z-lock default interval']
        self.z_lock_max = self.settings['z-lock default max']
        z_window_default = self.settings.get('z-lock default window', 1)
        self.z_lock_window = max(1, int(z_window_default))

    def do_main_loop(self):
        # XY-Lock Enabled
        if self.xy_lock_on:
            # Timer
            if (now := time()) - self._xy_lock_last_time > self.xy_lock_interval:
                self.do_xy_lock(now=now)

        # Z-Lock Enabled
        if self.z_lock_on:
            # Timer
            if (now := time()) - self._z_lock_last_time > self.z_lock_interval:
                self.do_z_lock(now=now)

    @register_ipc_command(ExecuteXYLockCommand)
    @register_script_command(ExecuteXYLockCommand)
    def do_xy_lock(self, now=None):
        """ Centers the bead-rois based on their tracked position """

        # Gather information
        width = self.settings['ROI']
        half_width = width // 2
        tracks = self.tracks_buffer.peak_unsorted().copy()
        if now is None:
            now = time()
        self._xy_lock_last_time = now
        bead_ids, bead_rois = self.get_cached_bead_rois()

        # For each bead calculate if/how much to move
        moves_to_send: list[tuple[int, int, int]] = []
        for bead_id, roi in zip(bead_ids.tolist(), bead_rois, strict=False):

            # Get the track for this bead
            track = tracks[tracks[:, 4] == bead_id, :]

            # Check there is track data
            if track.shape[0] == 0:
                continue

            # Filter to valid positions for this ROI
            position_mask = ~np.isnan(track[:, [0, 1, 2]]).any(axis=1)
            valid_track = track[position_mask]

            cutoff = max(
                self._xy_lock_global_cutoff,
                self._xy_lock_bead_cutoff.get(bead_id, 0.),
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
            if bead_id in self._xy_lock_pending_moves:
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
            if abs(dx) > 0 or abs(dy) > 0:
                moves_to_send.append((bead_id, int(dx), int(dy)))

        if moves_to_send:
            self._xy_lock_pending_moves.extend([id for id, _, _ in moves_to_send])
            command = MoveBeadsCommand(moves=moves_to_send)
            self.send_ipc(command)

    def do_z_lock(self, now=None):
        if now is None:
            now = time()
        self._z_lock_last_time = now

        focus_state = self._latest_focus_state()
        if focus_state is not None:
            self._update_z_lock_cutoff_for_external_focus_change(focus_state, now)

        tracked_z = self._averaged_bead_z(self.z_lock_bead, self.z_lock_window)
        if tracked_z is None:
            return

        if self.z_lock_target is None:
            self.set_z_lock_target(tracked_z)
            return

        if focus_state is None:
            return

        current_focus_z, _target_focus_z, is_at_target = focus_state
        if not is_at_target:
            return

        correction = float(self.z_lock_target - tracked_z)
        max_step = abs(float(self.z_lock_max))
        if max_step <= 0:
            return
        correction = float(np.clip(correction, -max_step, max_step))
        if np.isclose(correction, 0.0):
            return

        new_target = float(current_focus_z + correction)
        self._z_lock_expected_focus_target = new_target
        self._advance_z_lock_cutoff(now)
        self.send_ipc(MoveFocusMotorAbsoluteCommand(z=new_target))

    def _averaged_bead_z(self, bead_id: int, window: int) -> float | None:
        if self.tracks_buffer is None:
            return None

        tracks = self.tracks_buffer.peak_unsorted()
        if tracks.size == 0:
            return None

        finite_rows = np.isfinite(tracks[:, [0, 3, 4]]).all(axis=1)
        written_rows = tracks[:, 0] > 0.0
        bead_rows = tracks[:, 4] == bead_id
        cutoff_rows = tracks[:, 0] >= self._z_lock_global_cutoff
        valid_rows = finite_rows & written_rows & bead_rows & cutoff_rows
        if not np.any(valid_rows):
            return None

        bead_tracks = tracks[valid_rows]
        order = np.argsort(bead_tracks[:, 0])[::-1]
        recent_track = bead_tracks[order[: max(1, int(window))]]
        return float(np.mean(recent_track[:, 3]))

    def _latest_focus_state(self) -> tuple[float, float, bool] | None:
        focus_buffer = self._focus_matrix_buffer()
        if focus_buffer is None:
            return None

        data = focus_buffer.peak_sorted()
        if data.size == 0:
            return None

        finite_rows = np.isfinite(data[:, 0])
        if not np.any(finite_rows):
            return None

        _timestamp, current_z, target_z, is_at_target = data[finite_rows][-1, :]
        return float(current_z), float(target_z), bool(round(is_at_target))

    def _advance_z_lock_cutoff(self, now: float | None = None) -> None:
        self._z_lock_global_cutoff = time() if now is None else float(now)

    def _update_z_lock_cutoff_for_external_focus_change(
        self,
        focus_state: tuple[float, float, bool],
        now: float,
    ) -> None:
        _current_focus_z, focus_target_z, _is_at_target = focus_state

        if self._z_lock_expected_focus_target is not None and np.isclose(
            focus_target_z, self._z_lock_expected_focus_target
        ):
            self._z_lock_last_focus_target = focus_target_z
            return

        if self._z_lock_last_focus_target is None:
            self._z_lock_last_focus_target = focus_target_z
            return

        if np.isclose(focus_target_z, self._z_lock_last_focus_target):
            return

        self._z_lock_expected_focus_target = None
        self._z_lock_last_focus_target = focus_target_z
        self._advance_z_lock_cutoff(now)

    def _focus_matrix_buffer(self) -> MatrixBuffer | None:
        self._focus_motor_name = self._focus_motor_name or self._discover_focus_motor_name()
        if self._focus_motor_name is None:
            return None

        if self._focus_buffer is None:
            self._focus_buffer = MatrixBuffer(
                create=False,
                locks=self.locks,
                name=self._focus_motor_name,
            )
        return self._focus_buffer

    def _discover_focus_motor_name(self) -> str | None:
        focus_motor_names: list[str] = []
        for name, hardware_type in self.hardware_types.items():
            try:
                if issubclass(hardware_type, FocusMotorBase):
                    focus_motor_names.append(name)
            except TypeError:
                continue

        if len(focus_motor_names) == 1:
            return focus_motor_names[0]

        if len(focus_motor_names) > 1:
            warn('Z-lock requires exactly one registered FocusMotorBase hardware manager.')
        return None

    def refresh_bead_rois(self):
        previous_bead_rois = self.bead_rois
        super().refresh_bead_rois()
        current_bead_rois = self.bead_rois
        active_ids = set(current_bead_rois)

        # Check if any of the beads have been deleted
        self._xy_lock_pending_moves = [
            bead_id for bead_id in self._xy_lock_pending_moves if bead_id in active_ids
        ]

        # Remove any bead-specific cutoffs for deleted beads
        bead_cutoff_ids = list(self._xy_lock_bead_cutoff)
        for bead_id in bead_cutoff_ids:
            if bead_id not in current_bead_rois:
                self._xy_lock_bead_cutoff.pop(bead_id, None)

        now = time()
        for bead_id, roi in current_bead_rois.items():
            previous_roi = previous_bead_rois.get(bead_id)
            if previous_roi == roi:
                continue

            if bead_id in self._xy_lock_pending_moves:
                continue

            self._xy_lock_bead_cutoff[bead_id] = now

            if bead_id == self.z_lock_bead:
                self._advance_z_lock_cutoff(now)

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
        if value <= 0:
            return
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
        self._advance_z_lock_cutoff()
        self._z_lock_expected_focus_target = None

        command = UpdateZLockEnabledCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetZLockBeadCommand)
    @register_script_command(SetZLockBeadCommand)
    def set_z_lock_bead(self, value: int):
        value = int(value)
        self.z_lock_bead = value
        self._advance_z_lock_cutoff()

        command = UpdateZLockBeadCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetZLockTargetCommand)
    @register_script_command(SetZLockTargetCommand)
    def set_z_lock_target(self, value: float):
        self.z_lock_target = value
        self._advance_z_lock_cutoff()

        command = UpdateZLockTargetCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetZLockIntervalCommand)
    @register_script_command(SetZLockIntervalCommand)
    def set_z_lock_interval(self, value: float):
        if value <= 0:
            return
        self.z_lock_interval = value
        self._advance_z_lock_cutoff()

        command = UpdateZLockIntervalCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetZLockMaxCommand)
    @register_script_command(SetZLockMaxCommand)
    def set_z_lock_max(self, value: float):
        self.z_lock_max = value
        self._advance_z_lock_cutoff()

        command = UpdateZLockMaxCommand(value=value)
        self.send_ipc(command)

    @register_ipc_command(SetZLockWindowCommand)
    @register_script_command(SetZLockWindowCommand)
    def set_z_lock_window(self, value: int):
        self.z_lock_window = max(1, int(value))
        self._advance_z_lock_cutoff()

        command = UpdateZLockWindowCommand(value=self.z_lock_window)
        self.send_ipc(command)
