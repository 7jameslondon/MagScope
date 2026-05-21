from __future__ import annotations

from dataclasses import dataclass

from magscope.ipc_commands import Command


# ── Linear Motor (Zaber LSQ) Command Dataclasses ──────────────────────────

@dataclass(frozen=True)
class LinearMove(Command):
    target_mm: float
    speed_mm_s: float


@dataclass(frozen=True)
class LinearJog(Command):
    delta_mm: float
    speed_mm_s: float


@dataclass(frozen=True)
class LinearHome(Command):
    pass


# ── Rotary Motor (Zaber NMS) Command Dataclasses ──────────────────────────

@dataclass(frozen=True)
class RotaryMove(Command):
    target_turns: float
    speed_turns_s: float


@dataclass(frozen=True)
class RotaryJog(Command):
    delta_turns: float
    speed_turns_s: float


# ── Focus Motor (PI E-709) Command Dataclasses ────────────────────────────

@dataclass(frozen=True)
class FocusMove(Command):
    z_nm: float


@dataclass(frozen=True)
class FocusJog(Command):
    delta_nm: float


