"""
The main file for the Fishel Lab MagScope setup. This includes an EGrabber camera, a Zaber LSQ linear magnet motor, and a Zaber NMS rotary magnet motor. 

For PI and Zaber motor support, make sure to install the following packages:
pip install PIPython zaber-motion

For the camera install the wheel package from the EGrabber website: https://www.egrabber.com/downloads/
pip install GrabberCamera-1.0.0-py3-none-any.whl
"""

from pathlib import Path
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

import magscope

from examples.cameras.camera_egrabber import EGrabberCamera
from examples.focus.pi_e709 import PiE709Controls, PiE709FocusPlot
from examples.motors.zaber_lsq import ZaberLsqControls, ZaberLsqPositionPlot
from examples.motors.zaber_nms import ZaberNmsControls, ZaberNmsPositionPlot
from examples.scripts.fishel_lab_scriptable_hardware import (
    ScriptableFocusMotor,
    ScriptableLsqMotor,
    ScriptableNmsMotor,
)

if __name__ == "__main__":
    scope = magscope.MagScope(verbose=True)

    # Fishel Lab Camera
    scope.camera_manager.camera = EGrabberCamera()

    # Fishel Lab Zaber LSQ Motor (Linear Magnet Motor)
    scope.add_hardware(ScriptableLsqMotor())
    scope.add_control(ZaberLsqControls, column=3)
    scope.add_timeplot(ZaberLsqPositionPlot())

    # Fishel Lab Zaber NMS Motor (Rotary Magnet Motor)
    scope.add_hardware(ScriptableNmsMotor())
    scope.add_control(ZaberNmsControls, column=3)
    scope.add_timeplot(ZaberNmsPositionPlot())

    # Fishel Lab PI E709 Focus (Objective Focus)
    scope.add_hardware(ScriptableFocusMotor())
    scope.add_control(PiE709Controls, column=3)
    scope.add_timeplot(PiE709FocusPlot())

    scope.start()
