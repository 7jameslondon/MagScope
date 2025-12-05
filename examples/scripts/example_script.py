import numpy as np

from magscope import AcquisitionMode, Script, Units
from magscope.ipc_commands import *

my_script = Script()

# Change the acquisition
my_script.append(SleepCommand(2.0 * Units.sec))
my_script.append(SetAcquisitionModeCommand(AcquisitionMode.CROP_VIDEO))
my_script.append(SetAcquisitionOnCommand(False))
my_script.append(SleepCommand(2.0 * Units.sec))
my_script.append(SetAcquisitionOnCommand(True))
my_script.append(SetAcquisitionModeCommand(AcquisitionMode.TRACK))
my_script.append(SleepCommand(2.0 * Units.sec))

# Use a for loop and Numpy
n = 3
array = np.random.rand(n)
for i in range(n):
    my_script.append(ShowMessageCommand(f'A random number is: {array[i]}'))
