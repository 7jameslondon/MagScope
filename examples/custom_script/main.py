""" main.py """
from custom_command import HelloManager
import magscope


def main() -> None:
    scope = magscope.MagScope()
    scope.add_hardware(HelloManager())
    scope.start()


if __name__ == "__main__":
    main()
