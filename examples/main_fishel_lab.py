import magscope

from cameras.camera_egrabber import EGrabberCamera
from motors.zaber_lsq import ZaberLsqMotor, ZaberLsqControls, ZaberLsqPositionPlot

if __name__ == "__main__":
    scope = magscope.MagScope()

    # Fishel Lab Camera
    scope.camera_manager.camera = EGrabberCamera()

    # Fishel Lab Zaber LSQ Motor (Linear Magnet Motor)
    scope.add_hardware(ZaberLsqMotor())
    scope.add_control(ZaberLsqControls, column=3)
    scope.add_timeplot(ZaberLsqPositionPlot())

    scope.start()