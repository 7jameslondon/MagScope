from __future__ import annotations

from dataclasses import dataclass

from magscope import Script
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import Command, SleepCommand, UpdateWaitingCommand
from magscope.utils import register_script_command

from focus.pi_e709 import PiE709FocusMotor
from motors.zaber_lsq import ZaberLsqMotor
from motors.zaber_nms import ZaberNmsMotor


# ── Linear Motor (Zaber LSQ) Command Dataclasses ──────────────────────────

@dataclass(frozen=True)
class LinearMoveCommand(Command):
    target_mm: float
    speed_mm_s: float


@dataclass(frozen=True)
class LinearJogCommand(Command):
    delta_mm: float
    speed_mm_s: float


@dataclass(frozen=True)
class LinearHomeCommand(Command):
    pass


# ── Rotary Motor (Zaber NMS) Command Dataclasses ──────────────────────────

@dataclass(frozen=True)
class RotaryMoveCommand(Command):
    target_turns: float
    speed_turns_s: float


@dataclass(frozen=True)
class RotaryJogCommand(Command):
    delta_turns: float
    speed_turns_s: float


# ── Focus Motor (PI E-709) Command Dataclasses ────────────────────────────

@dataclass(frozen=True)
class FocusMoveCommand(Command):
    z_nm: float


@dataclass(frozen=True)
class FocusJogCommand(Command):
    delta_nm: float


# ── Scriptable Manager Subclasses ──────────────────────────────────────────

class ScriptableLsqMotor(ZaberLsqMotor):

    @register_ipc_command(LinearMoveCommand)
    @register_script_command(LinearMoveCommand)
    def handle_linear_move(self, target_mm: float, speed_mm_s: float):
        self.handle_move_absolute(target_mm=target_mm, speed_mm_s=speed_mm_s)

    @register_ipc_command(LinearJogCommand)
    @register_script_command(LinearJogCommand)
    def handle_linear_jog(self, delta_mm: float, speed_mm_s: float):
        self.handle_jog_relative(delta_mm=delta_mm, speed_mm_s=speed_mm_s)

    @register_ipc_command(LinearHomeCommand)
    @register_script_command(LinearHomeCommand)
    def handle_linear_home(self):
        self.handle_home()


class ScriptableNmsMotor(ZaberNmsMotor):

    @register_ipc_command(RotaryMoveCommand)
    @register_script_command(RotaryMoveCommand)
    def handle_rotary_move(self, target_turns: float, speed_turns_s: float):
        self.handle_move_absolute(target_turns=target_turns, speed_turns_s=speed_turns_s)

    @register_ipc_command(RotaryJogCommand)
    @register_script_command(RotaryJogCommand)
    def handle_rotary_jog(self, delta_turns: float, speed_turns_s: float):
        self.handle_jog_relative(delta_turns=delta_turns, speed_turns_s=speed_turns_s)


class ScriptableFocusMotor(PiE709FocusMotor):

    @register_ipc_command(FocusMoveCommand)
    @register_script_command(FocusMoveCommand)
    def handle_focus_move(self, z_nm: float):
        self.handle_move_absolute(z=z_nm)

    @register_ipc_command(FocusJogCommand)
    @register_script_command(FocusJogCommand)
    def handle_focus_jog(self, delta_nm: float):
        self.handle_jog(delta_nm=delta_nm)
