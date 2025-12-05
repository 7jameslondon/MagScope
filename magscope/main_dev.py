import magscope
from magscope import Command, register_script_command
from dataclasses import dataclass

@dataclass(frozen=True)
class HelloCommand(Command):
    name: str

@register_script_command(HelloCommand)
def say_hello(name: str):
    print(f"Hello {name}!")

scope = magscope.MagScope(verbose=True)
scope.start()