from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from time import time

import numpy as np
import yaml

from magscope.beadlock import BeadLockManager

from .commands import MoveObjectiveRelativeCommand
from .control_panel import (
    ForceCalibrationControlPanel,
    ForceMotorPlot,
    LinearMotorControlPanel,
    LinearMotorPlot,
    RotaryMotorControlPanel,
    RotaryMotorPlot,
    ObjectiveMotorControlPanel,
    ObjectiveMotorPlot,
)
from .manager import MotorManager


class MotorAwareBeadLockManager(BeadLockManager):
    """Use motor-safe objective move commands for Z-lock."""

    def do_z_lock(self, now=None):
        if now is None:
            now = time()
        self._z_lock_last_time = now

        tracks = self.tracks_buffer.peak_unsorted().copy()
        if tracks.size == 0:
            return

        bead_mask = tracks[:, 4] == float(self.z_lock_bead)
        valid = bead_mask & np.isfinite(tracks[:, 0]) & np.isfinite(tracks[:, 3])
        if not np.any(valid):
            return

        bead_rows = tracks[valid]
        latest_row = bead_rows[np.argmax(bead_rows[:, 0])]
        measured_z_nm = float(latest_row[3])

        if self.z_lock_target is None:
            self.z_lock_target = measured_z_nm
            return

        delta_nm = float(self.z_lock_target) - measured_z_nm
        max_step_nm = abs(float(self.z_lock_max))
        if max_step_nm > 0:
            delta_nm = float(np.clip(delta_nm, -max_step_nm, max_step_nm))

        if abs(delta_nm) < 1e-9:
            return

        command = MoveObjectiveRelativeCommand(
            delta_nm=delta_nm,
            speed_nm_s=None,
            source="z_lock",
        )
        self.send_ipc(command)


def _coerce_motors_settings(
    motors_settings: Mapping[str, object] | str | Path | None,
) -> dict[str, object]:
    if motors_settings is None:
        return {}
    if isinstance(motors_settings, Mapping):
        return dict(motors_settings)
    target = Path(motors_settings)
    if not target.exists():
        return {}
    with target.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Motor settings file {target} must be a YAML mapping")
    wrapped = data.get("motors")
    if isinstance(wrapped, dict):
        return dict(wrapped)
    return dict(data)


def _legacy_scope_motors_settings(scope) -> dict[str, object]:
    settings = getattr(scope, "settings", None)
    if settings is None:
        return {}
    getter = getattr(settings, "get", None)
    if not callable(getter):
        return {}
    legacy = getter("motors", {})
    if isinstance(legacy, Mapping):
        return dict(legacy)
    return {}


def configure_scope_with_motors(
    scope,
    *,
    control_column: int = 1,
    add_plots: bool = True,
    motors_settings: Mapping[str, object] | str | Path | None = None,
) -> MotorManager | None:
    """Attach the motor extension to a MagScope instance."""
    resolved_settings = _coerce_motors_settings(motors_settings)
    if not resolved_settings:
        resolved_settings = _legacy_scope_motors_settings(scope)
    if not bool(resolved_settings.get("enabled", True)):
        return None

    if not isinstance(scope.beadlock_manager, MotorAwareBeadLockManager):
        try:
            scope.beadlock_manager = MotorAwareBeadLockManager()
        except TypeError:
            # Singleton already exists in this process; keep the existing manager if it matches.
            if not isinstance(scope.beadlock_manager, MotorAwareBeadLockManager):
                raise

    try:
        motor_manager = MotorManager(motors_settings=resolved_settings)
    except TypeError:
        existing = getattr(scope, "_hardware", {}).get("MotorManager")
        if not isinstance(existing, MotorManager):
            raise
        motor_manager = existing
    motor_manager.set_external_motors_settings(resolved_settings)

    scope.add_hardware(motor_manager)
    scope.add_control(LinearMotorControlPanel, column=control_column)
    scope.add_control(ForceCalibrationControlPanel, column=control_column)
    scope.add_control(RotaryMotorControlPanel, column=control_column)
    scope.add_control(ObjectiveMotorControlPanel, column=control_column)

    if add_plots:
        scope.add_timeplot(ForceMotorPlot())
        scope.add_timeplot(LinearMotorPlot())
        scope.add_timeplot(RotaryMotorPlot())
        scope.add_timeplot(ObjectiveMotorPlot())

    return motor_manager
