from __future__ import annotations

from dataclasses import dataclass

from magscope.ipc_commands import Command


@dataclass(frozen=True)
class SetMotorArmedCommand(Command):
    value: bool


@dataclass(frozen=True)
class ConnectMotorsCommand(Command):
    """Connect all configured motors."""


@dataclass(frozen=True)
class DisconnectMotorsCommand(Command):
    """Disconnect all configured motors."""


@dataclass(frozen=True)
class StopAllMotorsCommand(Command):
    """Immediately stop all motor motion."""


@dataclass(frozen=True)
class MoveObjectiveRelativeCommand(Command):
    delta_nm: float
    speed_nm_s: float | None = None
    source: str = "ui"


@dataclass(frozen=True)
class MoveObjectiveAbsoluteCommand(Command):
    position_nm: float
    speed_nm_s: float | None = None
    source: str = "ui"


@dataclass(frozen=True)
class MoveLinearRelativeCommand(Command):
    delta_mm: float
    speed_mm_s: float | None = None
    source: str = "ui"


@dataclass(frozen=True)
class MoveLinearAbsoluteCommand(Command):
    position_mm: float
    speed_mm_s: float | None = None
    source: str = "ui"


@dataclass(frozen=True)
class MoveRotaryRelativeCommand(Command):
    delta_turns: float
    speed_turns_s: float | None = None
    source: str = "ui"


@dataclass(frozen=True)
class MoveRotaryAbsoluteCommand(Command):
    position_turns: float
    speed_turns_s: float | None = None
    source: str = "ui"


@dataclass(frozen=True)
class SetSessionSafetyWindowCommand(Command):
    objective_nm: float | None = None
    linear_mm: float | None = None
    rotary_turns: float | None = None
    enabled: bool | None = None


@dataclass(frozen=True)
class UpdateMotorStatusCommand(Command):
    axis: str
    timestamp: float
    actual_position: float
    target_position: float
    velocity: float | None = None
    connected: bool = False
    armed: bool = False


@dataclass(frozen=True)
class UpdateMotorFaultCommand(Command):
    axis: str
    timestamp: float
    reason: str
    requested_target: float | None = None


@dataclass(frozen=True)
class LoadForceCalibrantCommand(Command):
    path: str
    source: str = "ui"


@dataclass(frozen=True)
class UnloadForceCalibrantCommand(Command):
    source: str = "ui"


@dataclass(frozen=True)
class MoveLinearToForceCommand(Command):
    force_pn: float
    speed_mm_s: float | None = None
    source: str = "ui"


@dataclass(frozen=True)
class RunLinearForceRampCommand(Command):
    start_pn: float
    stop_pn: float
    rate_pn_s: float
    speed_mm_s: float | None = None
    source: str = "ui"


@dataclass(frozen=True)
class SetLinearUiSpeedCommand(Command):
    speed_mm_s: float | None = None


@dataclass(frozen=True)
class UpdateForceCalibrantStatusCommand(Command):
    loaded: bool
    path: str | None = None
    message: str = ""
    force_min_pn: float | None = None
    force_max_pn: float | None = None
