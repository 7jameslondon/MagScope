from abc import ABC, abstractmethod
from dataclasses import dataclass

from magscope.datatypes import MatrixBuffer
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import (
    FocusMoveCommand,
    RequestFocusStatusCommand,
    UpdateFocusStatusCommand,
)
from magscope.processes import FocusStatus, ManagerProcessBase, SingletonABCMeta


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
        (January 1, 1970, 00:00:00 UTC)
        """


@dataclass(frozen=True)
class FocusLimits:
    minimum: float
    maximum: float


class FocusMotorBase(HardwareManagerBase, ABC):
    """Base class for user-provided focus/Z motors.

    Subclasses must implement absolute positioning along with limit reporting.
    Each instance publishes its latest position and limits via
    :class:`UpdateFocusStatusCommand` so other managers (e.g., Z-lock) can
    react to changes in motor state.
    """

    def __init__(self):
        super().__init__()
        self._last_reported_status: FocusStatus | None = None

    @abstractmethod
    def move_to(self, position: float) -> None:
        """Move the focus axis to ``position`` in nanometers."""

    @abstractmethod
    def get_position(self) -> float:
        """Return the current focus position in nanometers."""

    @abstractmethod
    def get_limits(self) -> FocusLimits:
        """Return the minimum and maximum reachable positions."""

    def _apply_speed(self, speed: float) -> None:
        """Optionally adjust the motor speed. Subclasses may override."""

    @register_ipc_command(FocusMoveCommand)
    def handle_focus_move(self, position: float, speed: float | None = None) -> None:
        """Move the motor to ``position`` and publish the new status."""

        limits = self.get_limits()
        clipped_position = min(max(position, limits.minimum), limits.maximum)
        if speed is not None:
            self._apply_speed(speed)
        self.move_to(clipped_position)
        self._publish_focus_status()

    @register_ipc_command(RequestFocusStatusCommand)
    def handle_focus_status_request(self) -> None:
        """Broadcast the current position and limits."""

        self._publish_focus_status(force=True)

    def _publish_focus_status(self, *, force: bool = False) -> None:
        """Send a status update if it changed or if ``force`` is True."""

        limits = self.get_limits()
        status = FocusStatus(
            position=self.get_position(),
            min_position=limits.minimum,
            max_position=limits.maximum,
        )

        if not force and status == self._last_reported_status:
            return

        self._last_reported_status = status
        command = UpdateFocusStatusCommand(
            position=status.position,
            min_position=status.min_position,
            max_position=status.max_position,
        )
        self.send_ipc(command)
