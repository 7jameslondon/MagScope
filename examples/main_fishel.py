import magscope

from cameras.camera_egrabber import EGrabberCamera

if __name__ == "__main__":
    scope = magscope.MagScope()
    scope.camera_manager.camera = EGrabberCamera()
    scope.start()