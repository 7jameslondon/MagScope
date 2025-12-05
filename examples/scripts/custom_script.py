import magscope
from examples.custom_hello import HelloCommand

script = magscope.Script()
script.append(HelloCommand("Jamie"))
