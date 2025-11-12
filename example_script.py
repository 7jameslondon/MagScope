import numpy as np

import magscope
from magscope import Units, AcquisitionMode

my_script = magscope.Script()

# Change the acquisition
my_script('sleep', 2.*Units.sec)
my_script('set_acquisition_mode', AcquisitionMode.CROP_VIDEO)
my_script('set_acquisition_on', False)
my_script('sleep', 2.*Units.sec)
my_script('set_acquisition_on', True)
my_script('set_acquisition_mode', AcquisitionMode.TRACK)
my_script('sleep', 2.*Units.sec)

# Use a for loop and Numpy
n = 3
array = np.random.rand(n)
for i in range(n):
    my_script('print', f'A random number is: {array[i]}')