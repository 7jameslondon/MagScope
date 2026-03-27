import magscope


def main() -> None:
    scope = magscope.MagScope(verbose=True)
    scope.camera_manager.camera = magscope.camera.DummyCameraNoise()
    scope.ui_manager.n_windows = 1
    scope.start()


if __name__ == "__main__":
    main()
