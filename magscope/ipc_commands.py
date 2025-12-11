from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, TYPE_CHECKING

if TYPE_CHECKING:
    from magscope.scripting import ScriptStatus
    from magscope.settings import MagScopeSettings
    from magscope.utils import AcquisitionMode


@dataclass(frozen=True)
class Command:
    """Typed IPC payload sent between processes."""

    # Controls how the command is surfaced while a script runs. Custom commands can
    # override these attributes or expose a ``script_progress_text`` method to
    # provide a user-facing description in the GUI. When ``script_visible`` is
    # False, the scripting UI will skip the step entirely.
    script_visible: ClassVar[bool] = True
    script_progress_text: ClassVar[str | None] = None


@dataclass(frozen=True)
class QuitCommand(Command):
    """Request that all manager processes exit."""


@dataclass(frozen=True)
class SetSettingsCommand(Command):
    settings: "MagScopeSettings"


@dataclass(frozen=True)
class UpdateSettingsCommand(Command):
    settings: "MagScopeSettings"


@dataclass(frozen=True)
class SetAcquisitionOnCommand(Command):
    value: bool

    def script_progress_text(self) -> str:
        return "Start acquisition" if self.value else "Stop acquisition"


@dataclass(frozen=True)
class WaitUntilAcquisitionOnCommand(Command):
    value: bool

    def script_progress_text(self) -> str:
        return "Wait until acquisition is on" if self.value else "Wait until acquisition is off"


@dataclass(frozen=True)
class SetAcquisitionDirOnCommand(Command):
    value: bool

    def script_progress_text(self) -> str:
        return "Enable acquisition directory" if self.value else "Disable acquisition directory"


@dataclass(frozen=True)
class SetAcquisitionModeCommand(Command):
    mode: "AcquisitionMode"

    def script_progress_text(self) -> str:
        return f"Set acquisition mode to {self.mode.name}"


@dataclass(frozen=True)
class SetAcquisitionDirCommand(Command):
    value: str | None

    def script_progress_text(self) -> str:
        return "Clear acquisition directory" if self.value is None else f"Set acquisition directory to {self.value}"


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
class SetSimulatedFocusCommand(Command):
    offset: float


@dataclass(frozen=True)
class UpdateVideoBufferPurgeCommand(Command):
    t: float


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
class UpdateScriptProgressCommand(Command):
    current_step: int
    total_steps: int
    description: str | None


@dataclass(frozen=True)
class ShowMessageCommand(Command):
    text: str
    details: str | None = None

    def script_progress_text(self) -> str:
        return self.text


@dataclass(frozen=True)
class ShowErrorCommand(Command):
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
class RemoveBeadsFromPendingMovesCommand(Command):
    ids: list[int]


@dataclass(frozen=True)
class SetXYLockOnCommand(Command):
    value: bool

    def script_progress_text(self) -> str:
        return "Enable XY lock" if self.value else "Disable XY lock"


@dataclass(frozen=True)
class ExecuteXYLockCommand(Command):
    now: float | None = None

    def script_progress_text(self) -> str:
        return "Execute XY lock adjustment"


@dataclass(frozen=True)
class SetXYLockIntervalCommand(Command):
    value: float

    def script_progress_text(self) -> str:
        return f"Set XY lock interval to {self.value} seconds"


@dataclass(frozen=True)
class SetXYLockMaxCommand(Command):
    value: float

    def script_progress_text(self) -> str:
        return f"Set XY lock max displacement to {self.value}"


@dataclass(frozen=True)
class SetXYLockWindowCommand(Command):
    value: int

    def script_progress_text(self) -> str:
        return f"Set XY lock window to {self.value}"


@dataclass(frozen=True)
class SetZLockOnCommand(Command):
    value: bool

    def script_progress_text(self) -> str:
        return "Enable Z lock" if self.value else "Disable Z lock"


@dataclass(frozen=True)
class SetZLockBeadCommand(Command):
    value: int

    def script_progress_text(self) -> str:
        return f"Set Z lock bead to {self.value}"


@dataclass(frozen=True)
class SetZLockTargetCommand(Command):
    value: float

    def script_progress_text(self) -> str:
        return f"Set Z lock target to {self.value}"


@dataclass(frozen=True)
class SetZLockIntervalCommand(Command):
    value: float

    def script_progress_text(self) -> str:
        return f"Set Z lock interval to {self.value} seconds"


@dataclass(frozen=True)
class SetZLockMaxCommand(Command):
    value: float

    def script_progress_text(self) -> str:
        return f"Set Z lock max displacement to {self.value}"


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

    def script_progress_text(self) -> str:
        return f"Wait for {self.duration} seconds"


@dataclass(frozen=True)
class UpdateWaitingCommand(Command):
    """Signal that a wait condition has been satisfied."""
