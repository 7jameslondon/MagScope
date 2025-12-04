from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magscope.scripting import ScriptStatus
    from magscope.utils import AcquisitionMode


@dataclass(frozen=True)
class Command:
    """Typed IPC payload sent between processes."""


@dataclass(frozen=True)
class QuitCommand(Command):
    """Request that all manager processes exit."""


@dataclass(frozen=True)
class SetSettingsCommand(Command):
    settings: dict


@dataclass(frozen=True)
class SetAcquisitionOnCommand(Command):
    value: bool


@dataclass(frozen=True)
class SetAcquisitionDirOnCommand(Command):
    value: bool


@dataclass(frozen=True)
class SetAcquisitionModeCommand(Command):
    mode: "AcquisitionMode"


@dataclass(frozen=True)
class SetAcquisitionDirCommand(Command):
    value: str | None


@dataclass(frozen=True)
class SetBeadRoisCommand(Command):
    value: dict[int, tuple[int, int, int, int]]


@dataclass(frozen=True)
class LogExceptionCommand(Command):
    process_name: str
    details: str


@dataclass(frozen=True)
class UpdateCameraSettingCommand(Command):
    name: str
    value: str


@dataclass(frozen=True)
class UpdateVideoBufferPurgeCommand(Command):
    t: float


@dataclass(frozen=True)
class MoveBeadCommand(Command):
    id: int
    dx: int
    dy: int


@dataclass(frozen=True)
class MoveBeadsCommand(Command):
    moves: list[tuple[int, int, int]]


@dataclass(frozen=True)
class UpdateXYLockEnabledCommand(Command):
    value: bool


@dataclass(frozen=True)
class UpdateXYLockIntervalCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateXYLockMaxCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateXYLockWindowCommand(Command):
    value: int


@dataclass(frozen=True)
class UpdateZLockEnabledCommand(Command):
    value: bool


@dataclass(frozen=True)
class UpdateZLockBeadCommand(Command):
    value: int


@dataclass(frozen=True)
class UpdateZLockTargetCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateZLockIntervalCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateZLockMaxCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateScriptStatusCommand(Command):
    status: "ScriptStatus"


@dataclass(frozen=True)
class ShowMessageCommand(Command):
    text: str
    details: str | None = None


@dataclass(frozen=True)
class UpdateZLUTMetadataCommand(Command):
    filepath: str | None = None
    z_min: float | None = None
    z_max: float | None = None
    step_size: float | None = None
    profile_length: int | None = None


@dataclass(frozen=True)
class LoadZLUTCommand(Command):
    filepath: str


@dataclass(frozen=True)
class UnloadZLUTCommand(Command):
    """Clear the currently loaded Z-LUT."""


@dataclass(frozen=True)
class RemoveBeadFromPendingMovesCommand(Command):
    id: int


@dataclass(frozen=True)
class SetXYLockOnCommand(Command):
    value: bool


@dataclass(frozen=True)
class ExecuteXYLockCommand(Command):
    now: float | None = None


@dataclass(frozen=True)
class SetXYLockIntervalCommand(Command):
    value: float


@dataclass(frozen=True)
class SetXYLockMaxCommand(Command):
    value: float


@dataclass(frozen=True)
class SetXYLockWindowCommand(Command):
    value: int


@dataclass(frozen=True)
class SetZLockOnCommand(Command):
    value: bool


@dataclass(frozen=True)
class SetZLockBeadCommand(Command):
    value: int


@dataclass(frozen=True)
class SetZLockTargetCommand(Command):
    value: float


@dataclass(frozen=True)
class SetZLockIntervalCommand(Command):
    value: float


@dataclass(frozen=True)
class SetZLockMaxCommand(Command):
    value: float


@dataclass(frozen=True)
class GetCameraSettingCommand(Command):
    name: str


@dataclass(frozen=True)
class SetCameraSettingCommand(Command):
    name: str
    value: str


@dataclass(frozen=True)
class LoadScriptCommand(Command):
    path: str


@dataclass(frozen=True)
class StartScriptCommand(Command):
    """Start the currently loaded script."""


@dataclass(frozen=True)
class PauseScriptCommand(Command):
    """Pause the running script."""


@dataclass(frozen=True)
class ResumeScriptCommand(Command):
    """Resume a paused script."""


@dataclass(frozen=True)
class SleepCommand(Command):
    duration: float


@dataclass(frozen=True)
class UpdateWaitingCommand(Command):
    """Signal that a wait condition has been satisfied."""
