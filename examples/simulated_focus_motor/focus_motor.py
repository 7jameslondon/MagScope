from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import TYPE_CHECKING, Final

import numpy as np

from magscope.hardware import HardwareManagerBase
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import Command, SetSimulatedFocusCommand

if TYPE_CHECKING:
    from magscope.camera import DummyCameraBeads


@dataclass(frozen=True)
class MoveFocusMotorCommand(Command):
    target: float | None = None
    speed: float | None = None


@dataclass
class FocusMotorState:
    position: float
    target: float
    speed: float


class SimulatedFocusMotor(HardwareManagerBase):
    """Simulated focus/Z motor that publishes telemetry and adjusts camera focus."""

    position_min_max: Final[tuple[float, float]] = (-10.0, 10.0)
    speed_min_max: Final[tuple[float, float]] = (0.01, 50.0)

    def __init__(self):
        super().__init__()
        self.buffer_shape = (1000, 3)
        self.fetch_interval = 0.05
        self._state = FocusMotorState(position=0.0, target=0.0, speed=1.0)
        self._last_time = time()
        self._last_written = 0.0
        self._last_sent_focus: float | None = None

    def connect(self):
        self._is_connected = True
        self._update_camera_focus(force=True)

    def disconnect(self):
        self._is_connected = False

    def fetch(self):
        now = time()
        moved = self._advance_motion(now)

        if moved:
            self._update_camera_focus()

        if (now - self._last_written) >= self.fetch_interval or moved:
            self._last_written = now
            self._buffer.write(
                np.array([[now, self._state.position, self._state.target]], dtype=float)
            )

    @register_ipc_command(MoveFocusMotorCommand)
    def move(self, target: float | None = None, speed: float | None = None):
        if target is not None:
            clipped_target = float(np.clip(target, *self.position_min_max))
            self._state.target = clipped_target
        if speed is not None:
            clipped_speed = float(np.clip(speed, *self.speed_min_max))
            self._state.speed = clipped_speed

    def _advance_motion(self, now: float) -> bool:
        dt = now - self._last_time
        self._last_time = now

        if dt <= 0:
            return False

        delta = self._state.target - self._state.position
        if np.isclose(delta, 0.0):
            return False

        step = np.sign(delta) * min(abs(delta), self._state.speed * dt)
        new_position = self._state.position + step
        new_position = float(np.clip(new_position, *self.position_min_max))

        moved = not np.isclose(new_position, self._state.position)
        self._state.position = new_position
        if np.isclose(self._state.position, self._state.target):
            self._state.position = self._state.target
        return moved

    def _update_camera_focus(self, *, force: bool = False):
        from magscope.camera import DummyCameraBeads

        if self.camera_type is None or not issubclass(self.camera_type, DummyCameraBeads):
            return

        if force or self._last_sent_focus is None or not np.isclose(
            self._state.position, self._last_sent_focus
        ):
            self._last_sent_focus = self._state.position
            self.send_ipc(SetSimulatedFocusCommand(offset=self._state.position))
