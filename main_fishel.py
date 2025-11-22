import magscope

import camera_egrabber

if __name__ == "__main__":
    scope = magscope.MagScope()
    scope.camera_manager.camera = camera_egrabber.EGrabberCamera()
    scope.start()