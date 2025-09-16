import numpy as np

import magscope
from magscope import Units, AcquisitionMode

my_script = magscope.Script()
my_script('set_acquisition_mode', AcquisitionMode.CROP_VIDEO)
my_script('set_acquisition_on', True)
my_script('set_acquisition_dir_on', True)
my_script('set_acquisition_dir', r'C:\Users\lond11\Documents\MagScope and MagTrack\temp')