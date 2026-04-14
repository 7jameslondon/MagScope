from pathlib import Path

from magscope import Script, Units
from magscope.ipc_commands import *


# Update these values for your experiment before loading the script in MagScope.
BASE_SAVE_DIR = Path(r"C:\Users\magne\Desktop\James Data\2026-04-12 James' XY and Z Lock Comparison v2")
MINUTES_PER_PHASE = 180.

XY_LOCK_INTERVAL_SEC = 10
XY_LOCK_MAX = 1
XY_LOCK_WINDOW = 1000

Z_LOCK_BEAD = 0
Z_LOCK_TARGET = 52000  # Set to a float to force a target, or keep None to auto-latch.
Z_LOCK_INTERVAL_SEC = 5
Z_LOCK_MAX = 2
Z_LOCK_WINDOW = 200


script = Script()

seconds_per_phase = MINUTES_PER_PHASE * 60 * Units.sec
phase_directories = {
    "both_xy_z": BASE_SAVE_DIR / "XYZ",
    "xy_only": BASE_SAVE_DIR / "XY",
    "z_only": BASE_SAVE_DIR / "Z",
    "neither": BASE_SAVE_DIR / "None",
}

for directory in phase_directories.values():
    directory.mkdir(parents=True, exist_ok=True)


script.append(SetXYLockIntervalCommand(value=XY_LOCK_INTERVAL_SEC))
script.append(SetXYLockMaxCommand(value=XY_LOCK_MAX))
script.append(SetXYLockWindowCommand(value=XY_LOCK_WINDOW))

script.append(SetZLockBeadCommand(value=Z_LOCK_BEAD))
script.append(SetZLockTargetCommand(value=Z_LOCK_TARGET))
script.append(SetZLockIntervalCommand(value=Z_LOCK_INTERVAL_SEC))
script.append(SetZLockMaxCommand(value=Z_LOCK_MAX))
script.append(SetZLockWindowCommand(value=Z_LOCK_WINDOW))

script.append(SetAcquisitionDirCommand(value=str(phase_directories["both_xy_z"])))
script.append(SetAcquisitionDirOnCommand(value=True))
script.append(SetXYLockOnCommand(value=True))
script.append(SetZLockOnCommand(value=True))
script.append(SleepCommand(seconds_per_phase))

script.append(SetAcquisitionDirCommand(value=str(phase_directories["z_only"])))
script.append(SetAcquisitionDirOnCommand(value=True))
script.append(SetXYLockOnCommand(value=False))
script.append(SetZLockOnCommand(value=True))
script.append(SleepCommand(seconds_per_phase))

script.append(SetAcquisitionDirCommand(value=str(phase_directories["xy_only"])))
script.append(SetAcquisitionDirOnCommand(value=True))
script.append(SetXYLockOnCommand(value=True))
script.append(SetZLockOnCommand(value=False))
script.append(SleepCommand(seconds_per_phase))

script.append(SetAcquisitionDirCommand(value=str(phase_directories["neither"])))
script.append(SetAcquisitionDirOnCommand(value=True))
script.append(SetXYLockOnCommand(value=False))
script.append(SetZLockOnCommand(value=False))
script.append(SleepCommand(seconds_per_phase))

script.append(SetAcquisitionDirOnCommand(value=False))