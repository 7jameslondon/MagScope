<h1 align="center">
<img src="https://raw.githubusercontent.com/7jameslondon/MagScope/refs/heads/master/assets/logo.png" width="300">
</h1><br>

[![PyPi](https://img.shields.io/pypi/v/magscope.svg)](https://pypi.org/project/magscope/)
[![Docs](https://img.shields.io/readthedocs/magscope/latest.svg)](https://magscope.readthedocs.io)
[![Paper](https://img.shields.io/badge/DOI-10.1101/2025.10.31.685671-blue)](https://doi.org/10.1101/2025.10.31.685671)
[![Python package](https://github.com/7jameslondon/MagScope/actions/workflows/python-package.yml/badge.svg)](https://github.com/7jameslondon/MagScope/actions/workflows/python-package.yml)

MagScope is a Python framework for live data acquisition and analysis in magnetic tweezers microscopy.

* GUI (Graphical User Interface)
* No setup required to give it a try
* Easily extended to your setup
* Fast, high-throughput, and high-resolution
* Create simple scripts to automate data-collection and motor movment for long/complex experiments
* CPU or GPU tracking of beads via [MagTrack](https://github.com/7jameslondon/MagTrack)

## ⏳ Install
### Pre-requisites
* Python >=3.11
* [MagTrack](https://github.com/7jameslondon/MagTrack)
* NumPy >=1.26
* SciPy >=1.11.1
* matplotlib
* tifffile
* PyYAML
* PyQt6
* (Optional, but needed for GPU acceleration) CuPy-CUDA12x >=13.0
* Windows or Linux or MacOS (MacOS does not support NVIDIA GPU acceleration)
* MagTrack can run on a CPU or GPU. But GPU execution requires a CUDA-compliant GPU with the CUDA Toolkit installed. This is free and easy to install for most NVIDIA GPUs.

### Instructions
```
pip install magscope[gpu]
```
Or without CuPy
```
pip install magscope
```
Optional: For GPU acceleration on a computer with an NVIDIA CUDA GPU, you may need to install the CUDA Toolkit for CuPy. See details at https://docs.cupy.dev/en/stable/install.html

## ⚒ Usage
Try it as is, includes a simulated camera.
```
import magscope

scope = magscope.MagScope()
scope.start()
```
### More Examples
Coming soon

## 📖 Documentation
View the full documentation at [magscope.readthedocs.io](https://magscope.readthedocs.io)

## 💬 Support
Report issues, make requests, and ask questions on the [GitHub issue tracker](https://github.com/7jameslondon/MagScope/issues)<br>
Or email us at magtrackandmagscope@gmail.com


<br>
<br>
<br>
# OLD
<br>
<br>
<br>

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

## Documentation

In-depth guides, diagrams, and usage examples now live in the
[`docs/`](docs/) directory. Start with the
[MagScope orchestrator overview](docs/scope.md) to learn how `MagScope.start()`
wires together managers, shared memory, and inter-process communication. The
documentation site is powered by MkDocs; see [`docs/index.md`](docs/index.md)
for instructions on building the site locally and contributing new pages.

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

```mermaid
flowchart TD
    subgraph Core Orchestrator
        A[MagScope Main Process]
    end

    subgraph Shared Memory Buffers
        VB[VideoBuffer]
        MB[MatrixBuffer]
    end

    subgraph Manager Processes
        CM[CameraManager]
        BLM[BeadLockManager]
        VPM[VideoProcessorManager]
        SM[ScriptManager]
        WM[WindowManager]
    end

    A -- spawn & configure --> CM
    A -- spawn & configure --> BLM
    A -- spawn & configure --> VPM
    A -- spawn & configure --> SM
    A -- spawn & configure --> WM

    CM -- frames --> VB
    VPM -- processed frames --> VB
    BLM -- bead positions --> MB
    SM -- experiment data --> MB

    VB -. read/update .-> VPM
    VB -. display frames .-> WM
    MB -. analysis results .-> WM

    WM -- GUI commands --> SM
    SM -- IPC messages --> CM
    SM -- IPC messages --> BLM
    SM -- IPC messages --> VPM
```

## Settings

The `settings.py` module provides a convenient location for storing key user
preferences. Two notable parameters are `OBJECTIVE_MAG`, which determines how
pixels are converted to nanometers, and `ROI_WIDTH`, which defines the width of
the region of interest.

## Configuring a Camera

Verify your camera integration by running `test_camera.py` in the `tests`
directory. To add a camera, create a subclass of `CameraABC` (see
`camera.py`) and implement the required attributes and methods. Finally, set
the `ImplementedCamera` variable in `camera.py` to reference your new class.

## Shared-memory data buffers

The ``magscope.datatypes`` module defines the shared-memory-backed buffers that
processes use to exchange data efficiently.

* ``VideoBuffer`` stores image stacks and their capture timestamps. Create it in
  the producer process with the desired shape information and share the
  resulting metadata with consumer processes that instantiate the class with
  ``create=False``.
* ``MatrixBuffer`` stores 2D numeric data such as bead positions or motor
  telemetry. The number of columns is fixed when the buffer is created, while
  the number of rows written at a time can vary up to the buffer capacity.

Both buffers expect locks from ``multiprocessing`` so reads and writes can be
coordinated safely. See ``magscope/datatypes.py`` for detailed docstrings
covering their parameters and usage patterns.

## Force Calibrants (optional)

Provide force calibrants as plain-text files (for example, `force cal.txt`). You
may comment out the header line with `#`. Each subsequent line should map the
motor position in millimeters to the force in piconewtons. Include as many
interpolated data points as possible for the most accurate fit, e.g.:

```
# Motor Position (mm) Force (pN)
1.000 5.000
1.010 5.053
1.020 5.098
1.030 5.156
...
```

## Adding custom hardware

To add hardware, create a subclass of `HardwareManagerBase`.

* Set `buffer_shape` in `__init__`. Each row represents a time point. For
  example, a shape of `(100000, 3)` stores 100,000 time points with three values
  per sample (for example, time, position, and speed).
* Implement `connect`, which should set `self._is_connected` to `True` when the
  connection succeeds.
* Implement `disconnect`.
* Implement `fetch`, which appends an entry to the buffer whenever the
  program automatically polls the device.

## Scripting

MagScope ships with a lightweight scripting runtime that allows you to queue up
GUI interactions and hardware commands for repeatable experiments. A script is
an instance of `magscope.Script` where each call records a step to be executed
by the `ScriptManager` process:

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
* `set_acquisition_mode` – switch between modes such as tracking or video
  recording

See `example_script.py` for a minimal working example.

Expose additional methods to scripts by decorating a manager method with
`@registerwithscript('my_method_name')`. The string you provide becomes the
first argument when adding the step to a script, for example
`script('my_method_name', ...)`.

## Adding a custom process

You can extend `ManagerProcessBase` to create a separate process that manages
logic more complex than a single hardware device. Implement the following
abstract methods:

* `setup` – called when the process starts on its dedicated worker. Initialize
  long-lived resources such as timers or hardware connections here. If no setup
  work is required, use `pass`.
* `do_main_loop` – invoked repeatedly for the lifetime of the process. Place
  autonomous process logic here. If no actions are needed, use `pass`.

## Adding a control panel

Subclass `ControlPanelBase` and implement an `__init__` method to construct the
PyQt6 widgets. The initializer must accept a `manager` argument and pass it to
`super().__init__`. Later, access `self.manager` to invoke `WindowManager`
functions. `ControlPanelBase` derives from `QWidget` and provides a default
`QVBoxLayout`. Replace the layout with `setLayout` if needed, or add elements
via `self.layout().addWidget()` and `self.layout().addLayout()`.

Example:

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

Start by creating a `magscope.Message`. Provide at least two arguments: `to`,
which specifies the destination process (for example `CameraManager` or the
base `ManagerProcessBase` to broadcast to all managers), and `meth`, which is
the uninvoked method object that should be executed (for example
`CameraManager.set_camera_setting`). Do not call the method when constructing
the message. Supply positional or keyword arguments as additional parameters, or
explicitly pass tuples and dictionaries through the `args` and `kwargs`
keywords.

Send the message with `send_ipc()`. To avoid circular imports, perform local
imports of the destination process class immediately before use.

Example:

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

To format the Python files, run the following commands:

```bash
yapf main.py -i
yapf .\magscope\ -i -r
yapf .\tests\ -i -r
```

To install MagTrack during development, run:

```bash
pip install --force-reinstall --no-deps --no-cache-dir '..\MagTrack\magtrack-0.3.2-py3-none-any.whl'
```
