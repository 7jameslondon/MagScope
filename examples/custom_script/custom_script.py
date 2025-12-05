""" custom_script.py """
import magscope
from custom_command import HelloCommand

script = magscope.Script()
script.append(HelloCommand("Jamie"))
script.append(HelloCommand("Abhishek"))
script.append(HelloCommand("Teague"))
