"""Start MagScope with safe motor integration enabled.

No homing, zeroing, or movement commands are issued at startup.
"""

import magscope

from egrabber_camera_loader import load_egrabber_camera_class
from magscope_motors import configure_scope_with_motors
from scope_config import load_core_settings, load_motors_settings

EGrabberCamera = load_egrabber_camera_class()


def build_scope() -> magscope.MagScope:
    scope = magscope.MagScope()
    core_settings = load_core_settings()
    if core_settings is not None:
        scope.settings = core_settings
    if EGrabberCamera is not None:
        scope.camera_manager.camera = EGrabberCamera()
    configure_scope_with_motors(
        scope,
        control_column=1,
        add_plots=True,
        motors_settings=load_motors_settings(),
    )
    return scope


if __name__ == "__main__":
    build_scope().start()
