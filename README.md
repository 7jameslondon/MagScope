## Settings
The settings.py module is a quick an easy place to store important user settings.
Of notes is the OBJECTIVE_MAG setting which will evvect the coversion of pixels to nanometers.
As well as the ROI_WIDTH.

## Setting up the Camera
Test your camera by running the test_camera.py test in the \test directory.
To setup a camera you must create a new subclass of CameraABC (see camera.py) and implement the required attributes and methods.
You must then set the variable ImplmentedCamera in camera.py to the name of your camera.

## Force Calibrants (optional)
The force calibrant should be a text file (example "force cal.txt"). The header line can be commented out with a '#'.
Otherwise, the file should contain a list relating the motor position in mm and the force in pN.
The more data points the better. Data points should be interpolated from a fit.\
Example:\
\# Motor Position (mm) Force (pN)\
1.000 5.000\
1.010 5.053\
1.020 5.098\
1.030 5.156\
...

## Adding your own hardware
To add hardware create a subclass of `HardwareManagerBase`.
* Set `buffer_shape` in the `__init__`. This will store data (if any needs storing) from the device.
It should each row will be an time point. So a shape `(100000,3)` would be 100000 timepoints with 3 values.
For example this might be a motor that stores the 3 values at each time point such as time, position, speed.
* Implement `connect` which should set `self._is_connected` to `True` when succsefuly connected.
* Implement `disconnect`
* Implement `fetch` which add an entry to the buffer when automatically called by the program.

## Scripting
Valid functions:
* 'print' - Print a message to the GUI
* 'sleep' - Do nothing for a fixed amount of seconds
* 'set_acquisition_on' - Whether frames are sent for processing
* 'set_acquisition_dir' - The directory to save data to
* 'set_acquisition_dir_on' - Whether to save data
* 'set_acquisition_mode' - Set the mode such as tracking or video recording

An example script is included exxample_script.py

You can add your own methods to the scripting system with a decorator.
`@registerwithscript(func_str)` where `func_str` is the first argument when calling a function in a script.

## Adding your own process
You can extened the `ManagerProcessBase` to create a seperate process to manage something more
complex then just hardware. To do so you will need to implment the following abstract methods:
* 'setup' - this gets called when the process is started on a seperate processor. This is a good place to initate
complex objects like timers or connections to hardware. If you do not need to do anything here the just `pass`.
* 'do_main_loop' - this is repeatly called in the process as fast as possible. This is where all the stuff your process
does by itself should happen. If you do not need to do anything here the just `pass`.

## Development
To format the python files run 
``` yapf main.py -i ```, 
``` yapf .\magscope\ -i -r ``` and
``` yapf .\tests\ -i -r ```

To install Magtrack during development: ``` pip install --force-reinstall --no-deps --no-cache-dir '..\MagTrack\magtrack-0.3.2-py3-none-any.whl'```