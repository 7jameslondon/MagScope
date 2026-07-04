from magscope import Script
from motors.zaber_lsq import LinearMoveCommand, LinearJogCommand, LinearHomeCommand
from motors.zaber_nms import RotaryMoveCommand, RotaryJogCommand
from focus.pi_e709 import FocusMoveCommand, FocusJogCommand

script = Script()

# Linear LSQ: absolute move to 5 mm at 2 mm/s, wait for completion
script.append(LinearMoveCommand(target_mm=5.0, speed_mm_s=2.0, wait_until_done=True))

# Linear LSQ: jog +1 mm at 1 mm/s, no wait
script.append(LinearJogCommand(delta_mm=1.0, speed_mm_s=1.0))

# Linear LSQ: home at 0.5 mm/s, wait
script.append(LinearHomeCommand(speed_mm_s=0.5, wait_until_done=True))

# Rotary NMS: move to 2 turns at 0.5 turns/s, wait
script.append(RotaryMoveCommand(target_turns=2.0, speed_turns_s=0.5, wait_until_done=True))

# Rotary NMS: jog +0.5 turns at 0.5 turns/s
script.append(RotaryJogCommand(delta_turns=0.5, speed_turns_s=0.5, wait_until_done=True))

# Focus: move to 5000 nm, wait for completion
script.append(FocusMoveCommand(z_nm=5000.0, wait_until_done=True))

# Focus: jog -1000 nm
script.append(FocusJogCommand(delta_nm=-1000.0, wait_until_done=True))
