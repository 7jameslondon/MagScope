import magscope


def main() -> None:
    scope = magscope.MagScope(verbose=True)
    scope.ui_manager.n_windows = 1
    scope.start()


if __name__ == "__main__":
    main()
