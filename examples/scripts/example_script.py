import numpy as np

from magscope import AcquisitionMode, Script, Units
from magscope.ipc_commands import (
    SetAcquisitionModeCommand,
    SetAcquisitionOnCommand,
    ShowMessageCommand,
    SleepCommand,
)

my_script = Script()

# Change the acquisition
my_script(SleepCommand(2. * Units.sec))
my_script(SetAcquisitionModeCommand(AcquisitionMode.CROP_VIDEO))
my_script(SetAcquisitionOnCommand(False))
my_script(SleepCommand(2. * Units.sec))
my_script(SetAcquisitionOnCommand(True))
my_script(SetAcquisitionModeCommand(AcquisitionMode.TRACK))
my_script(SleepCommand(2. * Units.sec))

# Use a for loop and Numpy
n = 3
array = np.random.rand(n)
for i in range(n):
    my_script(ShowMessageCommand(f'A random number is: {array[i]}'))
