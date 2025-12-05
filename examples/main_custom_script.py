from dataclasses import dataclass

import magscope
from magscope.hardware import HardwareManagerBase
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import Command
from magscope.utils import register_script_command


@dataclass(frozen=True)
class HelloCommand(Command):
    name: str


class HelloManager(HardwareManagerBase):
    def connect(self):
        self._is_connected = True

    def disconnect(self):
        self._is_connected = False

    def fetch(self):
        pass

    @register_ipc_command(HelloCommand)
    @register_script_command(HelloCommand)
    def say_hello(self, name: str):
        print(f"Hello {name}", flush=True)


if __name__ == "__main__":
    scope = magscope.MagScope()
    scope.add_hardware(HelloManager())
    scope.start()