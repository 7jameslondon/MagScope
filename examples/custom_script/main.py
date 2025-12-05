""" main.py """
from custom_command import HelloManager
import magscope

scope = magscope.MagScope()
scope.add_hardware(HelloManager())
scope.start()
