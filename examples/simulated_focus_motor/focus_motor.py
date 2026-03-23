from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import time
from typing import Final
from warnings import warn

import numpy as np
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton

import magscope
from magscope.datatypes import MatrixBuffer
from magscope.hardware import FocusMotorBase
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import Command, MoveFocusMotorAbsoluteCommand


@dataclass(frozen=True)
class SetSimulatedFocusMotorSpeedCommand(Command):
    speed: float | None = None


@dataclass
class FocusMotorState:
    position: float
    target: float
    speed: float


class SimulatedFocusMotor(FocusMotorBase):
    """Simulated focus/Z motor using the standard FocusMotorBase API."""

    position_min_max: Final[tuple[float, float]] = (-10000.0, 10000.0)
    speed_min_max: Final[tuple[float, float]] = (0.01, 1000.0)

    def __init__(self):
        super().__init__()
        self.fetch_interval = 0.05
        self._state = FocusMotorState(position=0.0, target=0.0, speed=100.0)
        self._last_time = time()

    def connect(self):
        self._is_connected = True
        self._last_time = time()

    def disconnect(self):
        self._is_connected = False

    def move_absolute(self, z: float) -> None:
        self._state.target = float(np.clip(z, *self.position_min_max))

    def get_current_z(self) -> float:
        return self._state.position

    def get_is_moving(self) -> bool:
        return not np.isclose(self._state.position, self._state.target)

    def get_position_limits(self) -> tuple[float, float]:
        return self.position_min_max

    @register_ipc_command(SetSimulatedFocusMotorSpeedCommand)
    def set_speed(self, speed: float | None = None):
        if speed is not None:
            clipped_speed = float(np.clip(speed, *self.speed_min_max))
            self._state.speed = clipped_speed

    def _poll_hardware(self, now: float) -> None:
        dt = now - self._last_time
        self._last_time = now

        if dt <= 0:
            return

        delta = self._state.target - self._state.position
        if np.isclose(delta, 0.0):
            return

        step = np.sign(delta) * min(abs(delta), self._state.speed * dt)
        new_position = self._state.position + step
        new_position = float(np.clip(new_position, *self.position_min_max))

        self._state.position = new_position
        if np.isclose(self._state.position, self._state.target):
            self._state.position = self._state.target


FOCUS_MOTOR_BUFFER_NAME: Final[str] = SimulatedFocusMotor.__name__


class FocusMotorControls(magscope.ControlPanelBase):
    """Simple GUI controls for the simulated focus motor."""

    def __init__(self, manager: magscope.UIManager):
        super().__init__(title="Simulated Focus Motor", manager=manager)

        self._buffer = MatrixBuffer(
            create=False,
            locks=self.manager.locks,
            name=FOCUS_MOTOR_BUFFER_NAME,
        )

        self.position_label = QLabel("Position: --")
        self.position_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout().addWidget(self.position_label)

        self.target_label = QLabel("Target: --")
        self.target_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout().addWidget(self.target_label)

        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target (nm):"))
        self.target_text = QLineEdit("0")
        target_row.addWidget(self.target_text)
        self.layout().addLayout(target_row)

        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed (nm/s):"))
        self.speed_text = QLineEdit("100")
        speed_row.addWidget(self.speed_text)
        self.layout().addLayout(speed_row)

        move_button = QPushButton("Move")
        move_button.clicked.connect(self._send_move_command)
        self.layout().addWidget(move_button)

        self._timer = QTimer()
        self._timer.timeout.connect(self._update_labels)
        self._timer.setInterval(50)
        self._timer.start()

    def _update_labels(self) -> None:
        data = self._buffer.peak_sorted()
        if data.size == 0:
            return

        finite_rows = np.isfinite(data[:, 0])
        if not np.any(finite_rows):
            return

        _, position, target, is_moving_value = data[finite_rows][-1, :]
        is_moving = bool(round(is_moving_value))

        # Update GUI
        moving_suffix = ' (moving)' if is_moving else ''
        self.position_label.setText(f"Position: {position:.3f}{moving_suffix}")
        self.target_label.setText(f"Target: {target:.3f}")

    def _send_move_command(self) -> None:
        target = self._to_float(self.target_text.text())
        speed = self._to_float(self.speed_text.text())

        if target is not None and not (SimulatedFocusMotor.position_min_max[0] <= target <= SimulatedFocusMotor.position_min_max[1]):
            warn(
                f"Target position {target} outside of range {SimulatedFocusMotor.position_min_max}",
            )
            return

        if speed is not None and not (SimulatedFocusMotor.speed_min_max[0] <= speed <= SimulatedFocusMotor.speed_min_max[1]):
            warn(
                f"Speed {speed} outside of range {SimulatedFocusMotor.speed_min_max}",
            )
            return

        if speed is not None:
            self.manager.send_ipc(SetSimulatedFocusMotorSpeedCommand(speed=speed))
        if target is not None:
            self.manager.send_ipc(MoveFocusMotorAbsoluteCommand(z=target))

    @staticmethod
    def _to_float(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None


class FocusMotorPlot(magscope.TimeSeriesPlotBase):
    """Time series plot for the simulated focus motor position and target."""

    def __init__(self, buffer_name: str = FOCUS_MOTOR_BUFFER_NAME):
        super().__init__(buffer_name, "Focus (nm)")
        self.line_position = None
        self.line_target = None

    def setup(self):
        super().setup()
        self.line_position, self.line_target = self.axes.plot([], [], "r", [], [], "g")

    def update(self):
        data = self.buffer.peak_unsorted()
        if data.size == 0:
            return

        t = data[:, 0]
        position = data[:, 1]
        target = data[:, 2]

        selection = np.isfinite(t)
        t = t[selection]
        position = position[selection]
        target = target[selection]

        sort_index = np.argsort(t)
        t = t[sort_index]
        position = position[sort_index]
        target = target[sort_index]

        xmin, xmax = self.parent.limits.get("Time", (None, None))
        ymin, ymax = self.parent.limits.get(self.ylabel, (None, None))

        selection = ((xmin or -np.inf) <= t) & (t <= (xmax or np.inf))
        t = t[selection]
        position = position[selection]
        target = target[selection]

        if xmin is None or xmax is None:
            self.axes.xaxis.set_inverted(False)
        if ymin is None or ymax is None:
            self.axes.yaxis.set_inverted(False)

        timepoints = [datetime.fromtimestamp(t_) for t_ in t]

        self.line_target.set_xdata(timepoints)
        self.line_target.set_ydata(target)
        self.line_position.set_xdata(timepoints)
        self.line_position.set_ydata(position)

        if xmin is not None and xmin == xmax:
            xmax += 1
        if ymin is not None and ymin == ymax:
            ymax += 1

        xmin, xmax = [datetime.fromtimestamp(t_) if t_ else None for t_ in (xmin, xmax)]

        self.axes.autoscale()
        self.axes.autoscale_view()
        self.axes.set_xlim(xmin=xmin, xmax=xmax)
        self.axes.set_ylim(ymin=ymin, ymax=ymax)
        self.axes.relim()


if __name__ == "__main__":
    scope = magscope.MagScope(verbose=True)
    scope.ui_manager.n_windows = 1

    scope.add_hardware(SimulatedFocusMotor())
    scope.add_control(FocusMotorControls, column=0)
    scope.add_timeplot(FocusMotorPlot())

    scope.start()
