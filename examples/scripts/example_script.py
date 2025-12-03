import numpy as np

import magscope
from magscope import AcquisitionMode, Units
from magscope.ipc_commands import (SetAcquisitionModeCommand, SetAcquisitionOnCommand, ShowMessageCommand,
                                   StartSleepCommand)

my_script = magscope.Script()

# Change the acquisition
my_script(StartSleepCommand(2. * Units.sec))
my_script(SetAcquisitionModeCommand(AcquisitionMode.CROP_VIDEO))
my_script(SetAcquisitionOnCommand(False))
my_script(StartSleepCommand(2. * Units.sec))
my_script(SetAcquisitionOnCommand(True))
my_script(SetAcquisitionModeCommand(AcquisitionMode.TRACK))
my_script(StartSleepCommand(2. * Units.sec))

# Use a for loop and Numpy
n = 3
array = np.random.rand(n)
for i in range(n):
    my_script(ShowMessageCommand(f'A random number is: {array[i]}'))
