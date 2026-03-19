from magscope import Script
from magscope.ipc_commands import AddRandomBeadsCommand

script = Script()
script.append(AddRandomBeadsCommand(count=1000, seed=None))
