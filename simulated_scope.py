import magscope
from magscope.camera import DummyBeadCamera

if __name__ == "__main__":
    scope = magscope.MagScope()
    scope.camera_manager.camera = DummyBeadCamera()
    scope.window_manager.n_windows = 1
    scope.start()