from __future__ import annotations

from dataclasses import dataclass

from magscope.ipc_commands import Command


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
    rate_pn_s: float | None = None
    source: str = "ui"


@dataclass(frozen=True)
class RunLinearForceRampCommand(Command):
    start_pn: float
    stop_pn: float
    rate_pn_s: float
    source: str = "ui"


@dataclass(frozen=True)
class UpdateForceCalibrantStatusCommand(Command):
    loaded: bool
    path: str | None = None
    message: str = ""
    force_min_pn: float | None = None
    force_max_pn: float | None = None


@dataclass(frozen=True)
class ForceMoveCommand(Command):
    force_pn: float
    rate_pn_s: float | None = None
    wait_until_done: bool = False


@dataclass(frozen=True)
class ForceRampCommand(Command):
    start_pn: float
    stop_pn: float
    rate_pn_s: float
    wait_until_done: bool = True
