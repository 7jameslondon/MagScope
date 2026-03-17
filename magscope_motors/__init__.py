"""Motor extension package for MagScope.

PI objective support requires the `pipython` package when `test_mode` is false.
"""

from importlib import import_module

_EXPORTS = {
    "configure_scope_with_motors": ("magscope_motors.beadlock_ext", "configure_scope_with_motors"),
    "MotorAwareBeadLockManager": ("magscope_motors.beadlock_ext", "MotorAwareBeadLockManager"),
    "MotorControlPanel": ("magscope_motors.control_panel", "MotorControlPanel"),
    "ForceCalibrationControlPanel": ("magscope_motors.control_panel", "ForceCalibrationControlPanel"),
    "ObjectiveMotorControlPanel": ("magscope_motors.control_panel", "ObjectiveMotorControlPanel"),
    "LinearMotorControlPanel": ("magscope_motors.control_panel", "LinearMotorControlPanel"),
    "RotaryMotorControlPanel": ("magscope_motors.control_panel", "RotaryMotorControlPanel"),
    "ObjectiveMotorPlot": ("magscope_motors.control_panel", "ObjectiveMotorPlot"),
    "ForceMotorPlot": ("magscope_motors.control_panel", "ForceMotorPlot"),
    "LinearMotorPlot": ("magscope_motors.control_panel", "LinearMotorPlot"),
    "RotaryMotorPlot": ("magscope_motors.control_panel", "RotaryMotorPlot"),
    "MotorManager": ("magscope_motors.manager", "MotorManager"),
    "ForceCalibrantModel": ("magscope_motors.force_calibration", "ForceCalibrantModel"),
    "ForceCalibrantError": ("magscope_motors.force_calibration", "ForceCalibrantError"),
    "FaultCode": ("magscope_motors.safety", "FaultCode"),
    "SafetyGuard": ("magscope_motors.safety", "SafetyGuard"),
}

__all__ = list(_EXPORTS.keys())


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    return getattr(module, attr_name)
