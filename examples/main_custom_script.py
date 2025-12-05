import sys

from examples.custom_hello import HelloManager
import magscope


if __name__ == "__main__":
    sys.modules.setdefault("examples.main_custom_script", sys.modules[__name__])

    scope = magscope.MagScope()
    scope.add_hardware(HelloManager())
    scope.start()
