from magscope import Script
from magscope.ipc_commands import AddRandomBeadsCommand, ShowMessageCommand

script = Script()
script.append(AddRandomBeadsCommand(count=100, seed=7))
