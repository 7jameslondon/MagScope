""" 
The main file for the Fishel Lab MagScope setup. This includes an EGrabber camera, a Zaber LSQ linear magnet motor, and a Zaber NMS rotary magnet motor. 

For PI and Zaber motor support, make sure to install the following packages:
pip install PIPython zaber-motion

For the camera install the wheel package from the EGrabber website: https://www.egrabber.com/downloads/
pip install GrabberCamera-1.0.0-py3-none-any.whl
"""

import magscope

from cameras.camera_egrabber import EGrabberCamera
from motors.zaber_lsq import ZaberLsqMotor, ZaberLsqControls, ZaberLsqPositionPlot
from motors.zaber_nms import ZaberNmsMotor, ZaberNmsControls, ZaberNmsPositionPlot
from focus.pi_e709 import PiE709FocusMotor, PiE709Controls, PiE709FocusPlot

if __name__ == "__main__":
    scope = magscope.MagScope()

    # Fishel Lab Camera
    scope.camera_manager.camera = EGrabberCamera()

    # Fishel Lab Zaber LSQ Motor (Linear Magnet Motor)
    scope.add_hardware(ZaberLsqMotor())
    scope.add_control(ZaberLsqControls, column=3)
    scope.add_timeplot(ZaberLsqPositionPlot())

    # Fishel Lab Zaber NMS Motor (Rotary Magnet Motor)
    scope.add_hardware(ZaberNmsMotor())
    scope.add_control(ZaberNmsControls, column=3)
    scope.add_timeplot(ZaberNmsPositionPlot())

    # Fishel Lab PI E709 Focus (Objective Focus)
    scope.add_hardware(PiE709FocusMotor())
    scope.add_control(PiE709Controls, column=3)
    scope.add_timeplot(PiE709FocusPlot())

    scope.start()