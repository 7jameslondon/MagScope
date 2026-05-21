from __future__ import annotations

from magscope.ipc import register_ipc_command
from magscope.utils import register_script_command

from examples.focus.pi_e709 import PiE709FocusMotor
from examples.motors.zaber_lsq import ZaberLsqMotor
from examples.motors.zaber_nms import ZaberNmsMotor
from examples.scripts.fishel_lab_custom_commands import (
    FocusJog,
    FocusMove,
    LinearHome,
    LinearJog,
    LinearMove,
    RotaryJog,
    RotaryMove,
)


class ScriptableLsqMotor(ZaberLsqMotor):

    def __init__(self):
        super().__init__()
        self.name = ZaberLsqMotor.__name__

    @register_ipc_command(LinearMove)
    @register_script_command(LinearMove)
    def handle_linear_move(self, target_mm: float, speed_mm_s: float):
        self.handle_move_absolute(target_mm=target_mm, speed_mm_s=speed_mm_s)

    @register_ipc_command(LinearJog)
    @register_script_command(LinearJog)
    def handle_linear_jog(self, delta_mm: float, speed_mm_s: float):
        self.handle_jog_relative(delta_mm=delta_mm, speed_mm_s=speed_mm_s)

    @register_ipc_command(LinearHome)
    @register_script_command(LinearHome)
    def handle_linear_home(self):
        self.handle_home()


class ScriptableNmsMotor(ZaberNmsMotor):

    def __init__(self):
        super().__init__()
        self.name = ZaberNmsMotor.__name__

    @register_ipc_command(RotaryMove)
    @register_script_command(RotaryMove)
    def handle_rotary_move(self, target_turns: float, speed_turns_s: float):
        self.handle_move_absolute(target_turns=target_turns, speed_turns_s=speed_turns_s)

    @register_ipc_command(RotaryJog)
    @register_script_command(RotaryJog)
    def handle_rotary_jog(self, delta_turns: float, speed_turns_s: float):
        self.handle_jog_relative(delta_turns=delta_turns, speed_turns_s=speed_turns_s)


class ScriptableFocusMotor(PiE709FocusMotor):

    def __init__(self):
        super().__init__()
        self.name = PiE709FocusMotor.__name__

    @register_ipc_command(FocusMove)
    @register_script_command(FocusMove)
    def handle_focus_move(self, z_nm: float):
        self.handle_move_absolute(z=z_nm)

    @register_ipc_command(FocusJog)
    @register_script_command(FocusJog)
    def handle_focus_jog(self, delta_nm: float):
        self.handle_jog(delta_nm=delta_nm)
