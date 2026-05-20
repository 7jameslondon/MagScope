from fishel_lab_custom_commands import *

script = Script()

# Linear motor — absolute move at 2 mm/s to 10 mm
script.append(LinearMove(target_mm=10.0, speed_mm_s=2.0))

# Linear motor — relative jog +5 mm at 2 mm/s
script.append(LinearJog(delta_mm=5.0, speed_mm_s=2.0))

# Linear motor — home
script.append(LinearHome())

# Rotary motor — absolute move to 0.25 turns at 0.5 turns/s
script.append(RotaryMove(target_turns=0.25, speed_turns_s=0.5))

# Rotary motor — relative jog -0.1 turns at 0.5 turns/s
script.append(RotaryJog(delta_turns=-0.1, speed_turns_s=0.5))

# Focus motor — absolute move to 5000 nm at default speed
script.append(FocusMove(z_nm=5000.0))

# Focus motor — relative jog -100 nm at default speed
script.append(FocusJog(delta_nm=-100.0))
