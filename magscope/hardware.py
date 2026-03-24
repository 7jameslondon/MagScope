from abc import ABC, abstractmethod
from time import time
from warnings import warn

import numpy as np

from magscope.datatypes import MatrixBuffer
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import (
    MoveFocusMotorAbsoluteCommand,
    ReportFocusMotorLimitsCommand,
    RequestFocusMotorLimitsCommand,
    SetSimulatedFocusCommand,
)
from magscope.processes import ManagerProcessBase, SingletonABCMeta


class HardwareManagerBase(ManagerProcessBase, ABC, metaclass=SingletonABCMeta):
    def __init__(self):
        super().__init__()
        self.buffer_shape = (1000, 2)
        self._buffer: MatrixBuffer | None = None
        self._is_connected: bool = False

    def setup(self):
        self._buffer = MatrixBuffer(
            create=False,
            locks=self.locks,
            name=self.name,
        )

    def do_main_loop(self):
        self.fetch()

    def quit(self):
        super().quit()
        self.disconnect()

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def fetch(self):
        """
        Checks if the hardware has new data.

        If the hardware has new data, then it stores the
        data and timestamp in the matrix buffer (self._buffer).

        The timestamp should be the seconds since the unix epoch:
        (January 1, 1970, 00:00:00 UTC) """


class FocusMotorBase(HardwareManagerBase, ABC, metaclass=SingletonABCMeta):
    """Base class for absolute-Z focus motors used by MagScope.

    Subclasses provide the device-specific motion primitives while this base
    class standardizes polling, telemetry buffering, and the optional bridge to
    the simulated camera. The hardware matrix buffer stores rows as
    ``[timestamp, current_z, target_z, is_moving]`` where ``is_moving`` is
    encoded as ``0.0`` or ``1.0``.
    """

    def __init__(self):
        super().__init__()
        self.buffer_shape = (100000, 4)
        self.fetch_interval = 0.05
        self._target_z = 0.0
        self._last_written = 0.0
        self._last_sent_focus: float | None = None
        self._last_state: tuple[float, float, bool] | None = None

    def setup(self):
        super().setup()
        self.connect()
        current_z = float(self.get_current_z())
        self._target_z = current_z
        self._write_state(time(), current_z, force=True)

    def fetch(self):
        if not self._is_connected:
            return

        now = time()
        self._poll_hardware(now)
        current_z = float(self.get_current_z())
        is_moving = bool(self.get_is_moving())
        state = (current_z, self._target_z, is_moving)
        moved = self._last_state is None or not np.allclose(state[:2], self._last_state[:2])
        motion_changed = self._last_state is None or is_moving != self._last_state[2]

        if moved:
            self._update_simulated_camera_focus(current_z)

        if motion_changed or moved or (now - self._last_written) >= self.fetch_interval:
            self._write_state(now, current_z)

    @register_ipc_command(MoveFocusMotorAbsoluteCommand)
    def handle_move_absolute(self, z: float):
        z_min, z_max = self.get_position_limits()
        clipped_z = float(np.clip(z, z_min, z_max))
        if not np.isclose(clipped_z, z):
            warn(
                f'{self.name} clipped requested z {z} to {clipped_z} within '
                f'limits {(z_min, z_max)}'
            )
        self._target_z = clipped_z
        self.move_absolute(clipped_z)
        self._write_state(time(), float(self.get_current_z()), force=True)

    @register_ipc_command(RequestFocusMotorLimitsCommand)
    def report_focus_motor_limits(self) -> None:
        z_min, z_max = self.get_position_limits()
        self.send_ipc(ReportFocusMotorLimitsCommand(z_min=float(z_min), z_max=float(z_max)))

    def get_target_z(self) -> float:
        return self._target_z

    def is_in_position(self, tolerance: float = 1e-6) -> bool:
        return (
            not self.get_is_moving()
            and abs(float(self.get_current_z()) - self._target_z) <= tolerance
        )

    def _write_state(self, timestamp: float, current_z: float, *, force: bool = False) -> None:
        if self._buffer is None:
            raise RuntimeError(f'{self.name} has no hardware buffer')

        is_moving = bool(self.get_is_moving())
        row = np.array(
            [[timestamp, current_z, self._target_z, float(is_moving)]],
            dtype=float,
        )
        if force or not np.isclose(current_z, self._target_z):
            self._update_simulated_camera_focus(current_z, force=force)
        self._buffer.write(row)
        self._last_written = timestamp
        self._last_state = (current_z, self._target_z, is_moving)

    def _poll_hardware(self, now: float) -> None:
        """Allow subclasses to advance device state before telemetry is sampled."""

    def _update_simulated_camera_focus(self, current_z: float, *, force: bool = False) -> None:
        from magscope.camera import DummyCameraBeads

        if self.camera_type is None or not issubclass(self.camera_type, DummyCameraBeads):
            return

        if force or self._last_sent_focus is None or not np.isclose(current_z, self._last_sent_focus):
            self._last_sent_focus = current_z
            self.send_ipc(SetSimulatedFocusCommand(offset=current_z))

    @abstractmethod
    def move_absolute(self, z: float) -> None:
        """Command the motor to move to an absolute Z position."""

    @abstractmethod
    def get_current_z(self) -> float:
        """Return the motor's reported current Z position."""

    @abstractmethod
    def get_is_moving(self) -> bool:
        """Return the motor's reported moving state."""

    @abstractmethod
    def get_position_limits(self) -> tuple[float, float]:
        """Return the allowed absolute Z limits for this motor."""
