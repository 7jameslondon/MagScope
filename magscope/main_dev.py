import magscope

if __name__ == "__main__":
    scope = magscope.MagScope(verbose=True)
    scope.window_manager.n_windows = 1
    scope.start()