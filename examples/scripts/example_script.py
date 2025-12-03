import numpy as np

import magscope
from magscope import AcquisitionMode, Units
from magscope.ipc_commands import (SetAcquisitionModeCommand, SetAcquisitionOnCommand, ShowMessageCommand,
                                   StartSleepCommand)

my_script = magscope.Script()

# Change the acquisition
my_script.append(StartSleepCommand(2. * Units.sec))
my_script.append(SetAcquisitionModeCommand(AcquisitionMode.CROP_VIDEO))
my_script.append(SetAcquisitionOnCommand(False))
my_script.append(StartSleepCommand(2. * Units.sec))
my_script.append(SetAcquisitionOnCommand(True))
my_script.append(SetAcquisitionModeCommand(AcquisitionMode.TRACK))
my_script.append(StartSleepCommand(2. * Units.sec))

# Use a for loop and Numpy
n = 3
array = np.random.rand(n)
for i in range(n):
    my_script.append(ShowMessageCommand(f'A random number is: {array[i]}'))
