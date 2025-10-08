## Project Overview

MagScope is a modular control and analysis environment for magnetic tweezer
and microscopy experiments. It coordinates camera acquisition, bead tracking,
and hardware automation so researchers can run reproducible experiments from a
single desktop application. The toolkit is built to be extended – new cameras,
actuators, and analysis routines can plug into the same orchestration layer
without rewriting the core system.

**Key features**

* Multi-process managers for the camera, bead locking, video processing, GUI,
  and scripting keep latency low while sharing data through high-performance
  buffers.
* Shared-memory `VideoBuffer` and `MatrixBuffer` structures make it easy to
  stream image stacks and time-series telemetry between producers and
  consumers.
* A lightweight scripting runtime allows repeatable experiment protocols and
  automated GUI interactions.
* Extensible hardware and control panel base classes simplify adding custom
  instruments or user interface panels.

**High-level architecture**

At runtime `MagScope` instantiates manager processes for each subsystem,
including the `CameraManager`, `BeadLockManager`, `VideoProcessorManager`,
`ScriptManager`, and `WindowManager`. The core `MagScope` orchestrator loads
settings, allocates shared locks and buffers, and wires up inter-process pipes
before launching the managers. Managers exchange work and status updates via a
message-passing API and shared memory, while the GUI presents controls built on
`ControlPanelBase` widgets and time-series plots. Hardware integrations derive
from `HardwareManagerBase`, letting custom devices participate in the same
event loop and scripting hooks.

## Quick Start Guide

Use the following condensed checklist when you simply want to get the simulated
scope running end-to-end:

1. **Install prerequisites** – Python 3.10+, git, and a terminal for your
   operating system.
2. **Clone the repository**:

   ```bash
   git clone https://github.com/your-org/MagScope.git
   cd MagScope
   ```

3. **Create and activate a virtual environment**:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   ```

4. **Install dependencies**:

   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

5. **Run the simulated scope**:

   ```bash
   python simulated_scope.py
   ```

When the GUI appears you should see the dummy camera streaming frames. Close the
window or press `Ctrl+C` in the terminal when you are finished.

## Detailed Setup Guide

The sections below expand each quick start step with more background
information, helpful links, and troubleshooting tips for developers who are new
to Python tooling.

### 0. Install the core tooling (Python, git, and a terminal)

If you are brand new to Python development, start by installing the latest
Python 3.10+ release from the
[official downloads page](https://www.python.org/downloads/). During the Windows
installer flow make sure the "Add python.exe to PATH" option is selected so that
the `python` command is available in Command Prompt and PowerShell. You will
also need a terminal (macOS Terminal, Windows Terminal, or any Linux shell) and
[git](https://git-scm.com/downloads) to clone the project repository. The
[Python beginner's guide to software engineering](https://docs.python.org/3/faq/programming.html)
explains how these pieces fit together if you need more background.

Once Python and git are installed, clone the repository and change into the
project directory:

```bash
git clone https://github.com/your-org/MagScope.git
cd MagScope
```

If you prefer a graphical interface, GitHub Desktop and GitKraken both provide
point-and-click workflows that accomplish the same clone and checkout steps.

### 1. Create an isolated Python environment

MagScope targets Python 3.10 or newer. From the project root create and activate
a virtual environment so the project's dependencies stay separate from other
Python work. If you are new to the concept, the
[official venv tutorial](https://docs.python.org/3/tutorial/venv.html) provides a
gentle walkthrough.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows use `python -m venv .venv` followed by `.venv\Scripts\activate`. You
can confirm activation by checking that your shell prompt is prefixed with
`(.venv)` and that `which python` (macOS/Linux) or `where python` (Windows)
points to the `.venv` directory.

### 2. Install the dependencies

With the virtual environment activated, install the project requirements using
`pip`, Python's package installer. If you have never used `pip` before, the
[official user guide](https://pip.pypa.io/en/stable/user_guide/) explains the
basics and common troubleshooting steps.

Install the core requirements with pip:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

The list includes optional GPU-accelerated packages such as CuPy. If you do not
have a compatible CUDA toolkit available, comment out or remove the CuPy line
and install the remainder of the requirements. The
[Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/installing-packages/)
has additional background if you want to learn more about how Python packages
are managed.

### 3. Run the simulated scope end-to-end

When everything looks correct, start the simulated scope. The repository ships
with a dummy camera so you can exercise the full user interface without
connecting lab hardware. Launch it with:

```bash
python simulated_scope.py
```

This command starts `MagScope`, wires the `DummyBeadCamera` into the
`CameraManager`, and opens the Qt-based GUI. You should see the interface begin
streaming mock camera frames within a few seconds. If the window does not
appear, double-check that the virtual environment is active and that the
required Qt libraries were installed during the dependency step. When you are
ready to connect real instruments you can instead run `python main.py` and
configure the hardware classes described later in this document.

## Settings
The settings.py module is a quick an easy place to store important user settings.
Of notes is the OBJECTIVE_MAG setting which will evvect the coversion of pixels to nanometers.
As well as the ROI_WIDTH.

## Setting up the Camera
Test your camera by running the test_camera.py test in the \test directory.
To setup a camera you must create a new subclass of CameraABC (see camera.py) and implement the required attributes and methods.
You must then set the variable ImplmentedCamera in camera.py to the name of your camera.

## Shared memory data buffers
The ``magscope.datatypes`` module contains the shared-memory backed buffers that
processes use to exchange data efficiently.

* ``VideoBuffer`` stores image stacks and their capture timestamps. Create it in
  the producer process with the desired shape information and share the
  resulting metadata with consumer processes that instantiate the class with
  ``create=False``.
* ``MatrixBuffer`` stores 2D numeric data such as bead positions or motor
  telemetry. The number of columns is fixed when the buffer is created, while
  the number of rows written at a time can vary up to the buffer capacity.

Both buffers expect locks from ``multiprocessing`` to be passed in so reads
and writes can be coordinated safely. See ``magscope/datatypes.py`` for detailed
docstrings covering their parameters and usage patterns.

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
MagScope ships with a lightweight scripting runtime that allows you to queue
up GUI interactions and hardware commands for repeatable experiments. A script
is simply an instance of `magscope.Script` where each call records a step to be
executed by the `ScriptManager` process:

```python
import magscope

script = magscope.Script()
script('set_acquisition_mode', magscope.AcquisitionMode.CROP_VIDEO)
script('sleep', 2.0)  # wait for 2 seconds before running the next command
script('print', 'Ready for capture!')
```

Save the script to a `.py` file and load it from the GUI to run it. The manager
validates each step to ensure the referenced method exists and that the
provided arguments match the registered callable.

Built-in scriptable functions include:

* `print` – display a message in the GUI log
* `sleep` – pause script execution for a fixed number of seconds
* `set_acquisition_on` – toggle processing of incoming frames
* `set_acquisition_dir` – choose the directory used to save acquisitions
* `set_acquisition_dir_on` – enable or disable saving data to disk
* `set_acquisition_mode` – switch between modes such as tracking or video recording

See `example_script.py` for a minimal working example.

You can expose additional methods to scripts by decorating a manager method
with `@registerwithscript('my_method_name')`. The string you provide becomes
the first argument used when adding the step to a script, e.g.
`script('my_method_name', ...)`.

## Adding your own process
You can extened the `ManagerProcessBase` to create a seperate process to manage something more
complex then just hardware. To do so you will need to implment the following abstract methods:
* 'setup' - this gets called when the process is started on a seperate processor. This is a good place to initate
complex objects like timers or connections to hardware. If you do not need to do anything here the just `pass`.
* 'do_main_loop' - this is repeatly called in the process as fast as possible. This is where all the stuff your process
does by itself should happen. If you do not need to do anything here the just `pass`.

## Adding a Control Panel
Extend a `ControlPanelBase` and implment a `__init__` method to create the controls with PyQt6.
The `__init__` must take a manager argument to be passed to its super. This can be accessed
as `self.manager` later to call `WindowManger` functions. The `ControlPanelBase` is a QWidget which
by defualt contains a `QVBoxLayout`. This can layout can be changed using `setLayout` in the `__init__`.
Elements can be added to the layout with `self.layout().addWidget()` or `self.layout().addLayout()`.

Example
```
import magscope

class MyNewControlPanel(magscope.ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='New Panel')
        self.layout().addWidget(QLabel('This is my new panel'))
        
        row = QHBoxLayout()
        self.layout().addLayout(row)
        
        row.addWidget(QLabel('A Button'))
        button = QPushButton('Press Me')
        button.clicked.connect(self.button_callback)
        row.addWidget(button)
        
    def button_callback(self):
        print('The button was pressed')
```

## Sending interprocess calls (IPC)
First create a `magscope.Message`. The message takes at least two arguments. The first is `to`
which is the destination process such as `CameraManager` or if you want it to go to all
processes use the base class `ManagerProcessBase`. The second argument is `meth` the method
of the destinatino process that should be called such as `CameraManager.set_camera_setting`.
The method should be the method object it self such as `CameraManager.set_camera_setting`. It
should not be called in the message such as it should NOT be `CameraManager.set_camera_setting()`.
If the method will need to recive argument or keyword arguments those can be provided 
next as regular arguments or keyword arguements. Or they can be explicitly provided as a keyword argument
`tuple` and `dict` for `args` and `kwargs` respectivly.

Second send the message by calling `send_ipc()`.

Also it is often easiest to avoid circular imports by locally importing the destination process
class right before it is needed.

Example
```
import magscope

class MyProcesses(magscope.ManagerProcessBase):
    def send_camera_setting(self, setting_name, setting_value):
        message = magscope.Message(
            to=magscope.CameraManager,
            meth=magscope.CameraManager.set_camera_setting,
            args=(setting_name, setting_value),
        )
        self.send_ipc(message)
```

## Development
To format the python files run 
``` yapf main.py -i ```, 
``` yapf .\magscope\ -i -r ``` and
``` yapf .\tests\ -i -r ```

To install Magtrack during development: ``` pip install --force-reinstall --no-deps --no-cache-dir '..\MagTrack\magtrack-0.3.2-py3-none-any.whl'```