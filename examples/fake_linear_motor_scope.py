from dataclasses import dataclass
from datetime import datetime
from math import copysign
from pathlib import Path
from time import time
from warnings import warn

import matplotlib
import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout
from scipy.interpolate import PchipInterpolator

import magscope
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import Command

FORCE_CALIBRATION_PATH = Path(__file__).with_name("force_calibrant.txt")


class ForceCalibration:
    def __init__(self):
        # Load in data
        self.data = np.loadtxt(FORCE_CALIBRATION_PATH)

        # Sort data to be ascending
        idx = np.argsort(self.data[:, 0])
        self.data = self.data[idx, :]

        # Interpolate
        self.motor2force = PchipInterpolator(self.data[:, 0],
                                             self.data[:, 1],
                                             extrapolate=False)

        self.force2motor = PchipInterpolator(self.data[:, 1],
                                             self.data[:, 0],
                                             extrapolate=False)


force_calibration = ForceCalibration()


@dataclass(frozen=True)
class MoveLinearMotorCommand(Command):
    target: float | None = None
    speed: float | None = None


@dataclass(frozen=True)
class MoveLinearMotorForceCommand(Command):
    target_force: float | None = None
    speed: float | None = None


@dataclass(frozen=True)
class ForceRampCommand(Command):
    a_force: float | None = None
    b_force: float | None = None
    rate: float | None = None
    direction: int | None = None


class FakeLinearMotor(magscope.HardwareManagerBase):
    position_min_max = (0, 34.5)
    speed_min_max = (0.1, 10.0)
    def __init__(self):
        super().__init__()

        self.buffer_shape = (100000, 4)
        self.last_fetch = 0
        self.fetch_interval = 0.01
        self.force_calibration = force_calibration
        self.target = 0.0
        self.speed = 1.0

        self._fake_position = 0.0
        self._fake_speed = 1.0
        self._fake_moving = False
        self._fake_target = 0.0
        self._fake_last_time = time()
        self._fake_pvt = None
        self.fake_pvt_on = False

    def connect(self):
        self._is_connected = True

    def disconnect(self):
        self._is_connected = False

    def fetch(self):
        self._do_fake_move() # Only needed to simulate motor movement

        if (now := time()) - self.last_fetch > self.fetch_interval:
            self.last_fetch = now
            row = np.array([[now, self._fake_position, self.target, self.speed]])
            self._buffer.write(row)

    @register_ipc_command(MoveLinearMotorCommand)
    def move(self, target=None, speed=None):
        if target is not None:
            self.target = target
            self._fake_target = target
            self._fake_moving = True
            self._fake_last_time = time()

        if speed is not None:
            self.speed = speed
            self._fake_speed = speed

        self.fake_pvt_on = False

    @register_ipc_command(MoveLinearMotorForceCommand)
    def move_force(self, target_force=None, speed=None):
        if target_force is not None:
            fake_target = self.force_calibration.force2motor(target_force)
            if np.isnan(fake_target):
                return
            self.target = fake_target
            self._fake_target = fake_target
            self._fake_moving = True
            self._fake_last_time = time()

        if speed is not None:
            self.speed = speed
            self._fake_speed = speed

        self.fake_pvt_on = False

    @register_ipc_command(ForceRampCommand)
    def force_ramp(self, a_force=None, b_force=None, rate=None, direction=None):
        if direction == 1:
            start = a_force
            stop = b_force
        else:
            start = b_force
            stop = a_force

        f = np.linspace(start, stop, num=100)
        dt = abs(stop - start) / rate / (100 - 1)
        p = self.force_calibration.force2motor(f)
        if np.all(np.logical_not(np.isfinite(p))):
            return
        v = abs(np.diff(p)) / dt
        v = np.insert(v, 0, self.speed)
        dt = np.ones_like(v) * dt
        pvt = np.hstack((p[:, np.newaxis], v[:, np.newaxis], dt[:, np.newaxis]))

        self.target = p[-1]
        self.fake_pvt_on = True
        self._fake_moving = True
        self._fake_pvt = pvt
        self._fake_target = p[0]
        self._fake_speed = self.speed
        self._fake_last_time = time()

    def _do_fake_move(self):
        """ This is only used to simulate the movement of the motor. """
        if not self._fake_moving:
            return

        if self._fake_position == self._fake_target:
            if self.fake_pvt_on:
                if self._fake_pvt is None:
                    self._fake_moving = False
                    self.fake_pvt_on = False
                    self._fake_speed = self.speed
                    return
                p, v, _ = self._fake_pvt[0, :]
                self._fake_target = p
                self._fake_speed = v
                if self._fake_pvt.shape[0] == 1:
                    self._fake_pvt = None
                else:
                    self._fake_pvt = self._fake_pvt[1:, :]
            else:
                self._fake_moving = False
                return

        t = time()
        dt = t - self._fake_last_time
        self._fake_last_time = t
        direction = copysign(1, self._fake_target - self._fake_position)
        dp = self._fake_speed * dt * direction

        self._fake_position += dp

        if direction > 0:
            if self._fake_position > self._fake_target:
                self._fake_position = self._fake_target
        else:
            if self._fake_position < self._fake_target:
                self._fake_position = self._fake_target


class LinearMotorControls(magscope.ControlPanelBase):
    def __init__(self, manager):
        super().__init__(title='Fake Linear Motor', manager=manager)

        self.force_calibration = force_calibration

        # Buffer
        self._buffer = magscope.MatrixBuffer(
            create=False,
            locks=self.manager.locks,
            name='FakeLinearMotor'
        )

        # Timer
        self._timer = QTimer()
        self._timer.timeout.connect(self.update_values)
        self._timer.setInterval(100)
        self._timer.start()


        # =========== Direct Control ===========
        # Label - Force ramp
        label = QLabel('Direct Control')
        label.setStyleSheet('font-weight: bold;')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout().addWidget(label)

        # Current position
        self.position_label = QLabel('Current Position:')
        self.layout().addWidget(self.position_label)

        # Target
        row = QHBoxLayout()
        self.layout().addLayout(row)
        row.addWidget(QLabel('Target (mm):'))
        self.target_textedit = QLineEdit()
        row.addWidget(self.target_textedit)
        self.target_label = QLabel('')
        row.addWidget(self.target_label)

        # Speed
        row = QHBoxLayout()
        self.layout().addLayout(row)
        row.addWidget(QLabel('Speed (mm/s):'))
        self.speed_textedit = QLineEdit()
        row.addWidget(self.speed_textedit)
        self.speed_label = QLabel('')
        row.addWidget(self.speed_label)

        # Move button
        move_button = QPushButton('Move')
        move_button.clicked.connect(self.callback_move)
        self.layout().addWidget(move_button)


        # =========== Force ===========
        # Gap
        label = QLabel()
        label.setFixedHeight(5)
        self.layout().addWidget(label)

        # Underline
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setLineWidth(1)
        self.layout().addWidget(line)

        # Label - Force
        label = QLabel('Force')
        label.setStyleSheet('font-weight: bold;')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout().addWidget(label)

        # Current force
        self.force_label = QLabel('Current Force:')
        self.layout().addWidget(self.force_label)

        # Target Force
        row = QHBoxLayout()
        self.layout().addLayout(row)
        row.addWidget(QLabel('Target Force (pN):'))
        self.target_force_textedit = QLineEdit()
        row.addWidget(self.target_force_textedit)
        self.target_force_label = QLabel('')
        row.addWidget(self.target_force_label)

        # Speed
        row = QHBoxLayout()
        self.layout().addLayout(row)
        row.addWidget(QLabel('Speed (mm/s):'))
        self.speed_force_textedit = QLineEdit()
        row.addWidget(self.speed_force_textedit)
        self.speed_force_label = QLabel('')
        row.addWidget(self.speed_force_label)

        # Move button
        move_force_button = QPushButton('Move')
        move_force_button.clicked.connect(self.callback_move_force)
        self.layout().addWidget(move_force_button)


        # =========== Force Ramp ===========
        # Gap
        label = QLabel()
        label.setFixedHeight(5)
        self.layout().addWidget(label)

        # Underline
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setLineWidth(1)
        self.layout().addWidget(line)

        #
        row = QHBoxLayout()
        self.layout().addLayout(row)

        # A
        column = QVBoxLayout()
        row.addLayout(column)
        label = QLabel('A (pN)')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(label)
        self.force_ramp_a_textedit = QLineEdit()
        column.addWidget(self.force_ramp_a_textedit)

        # Label(Force ramp) and Arrows < >
        column = QVBoxLayout()
        row.addLayout(column)
        label = QLabel('Force Ramp')
        label.setStyleSheet('font-weight: bold;')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(label)
        # Arrows < >
        row1 = QHBoxLayout()
        column.addLayout(row1)
        # <
        button = QPushButton('⮜')
        button.setFixedWidth(25)
        button.clicked.connect(lambda: self.callback_force_ramp('⮜'))
        row1.addWidget(button)
        # >
        button = QPushButton('⮞')
        button.setFixedWidth(25)
        button.clicked.connect(lambda: self.callback_force_ramp('⮞'))
        row1.addWidget(button)

        # B
        column = QVBoxLayout()
        row.addLayout(column)
        label = QLabel('B (pN)')
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(label)
        self.force_ramp_b_textedit = QLineEdit()
        column.addWidget(self.force_ramp_b_textedit)

        # Rate
        row = QHBoxLayout()
        self.layout().addLayout(row)
        row.addWidget(QLabel('Rate (pN/s):'))
        self.force_ramp_rate_textedit = QLineEdit('1')
        self.force_ramp_rate_textedit.setMinimumWidth(100)
        row.addWidget(self.force_ramp_rate_textedit)

    def update_values(self):
        _, position, target, speed = self._buffer.peak_sorted()[-1, :]
        current_force = self.force_calibration.motor2force(position)
        target_force = self.force_calibration.motor2force(target)

        # Direct
        self.position_label.setText(f'Current Position: {position:.2f} mm')
        self.target_label.setText(f'{target:.2f}')
        self.speed_label.setText(f'{speed:.2f}')

        # Force
        self.force_label.setText(f'Current Force: {current_force:.2f} pN')
        self.target_force_label.setText(f'{target_force:.2f}')
        self.speed_force_label.setText(f'{speed:.2f}')

    def callback_move(self):
        # Try to get target
        try:
            target = float(self.target_textedit.text())
        except ValueError:
            target = None
        if target is not None:
            if target < FakeLinearMotor.position_min_max[0] or target > FakeLinearMotor.position_min_max[1]:
                warn(f'Target position {target} outside of range {FakeLinearMotor.position_min_max}')
                return

        # Try to get speed
        try:
            speed = float(self.speed_textedit.text())
        except ValueError:
            speed = None
        if speed is not None:
            if speed < FakeLinearMotor.speed_min_max[0] or speed > FakeLinearMotor.speed_min_max[1]:
                warn(f'Speed {speed} outside of range {FakeLinearMotor.speed_min_max}')
                return

        # Send inter-process command to motor
        command = MoveLinearMotorCommand(target=target, speed=speed)
        self.manager.send_ipc(command)

    def callback_move_force(self):
        # Try to get target
        try:
            target_force = float(self.target_force_textedit.text())
        except ValueError:
            target_force = None

        # Try to get speed
        try:
            speed = float(self.speed_force_textedit.text())
        except ValueError:
            speed = None
        if speed is not None:
            if speed < FakeLinearMotor.speed_min_max[0] or speed > FakeLinearMotor.speed_min_max[1]:
                warn(f'Speed {speed} outside of range {FakeLinearMotor.speed_min_max}')
                return

        # Send inter-process command to motor
        command = MoveLinearMotorForceCommand(target_force=target_force, speed=speed)
        self.manager.send_ipc(command)

    def callback_force_ramp(self, direction: str):
        # Direction
        direction = int(2*(float(direction == '⮞') - 0.5))

        # A
        a_force = self.force_ramp_a_textedit.text()
        try: a_force = float(a_force)
        except ValueError: return

        # B
        b_force = self.force_ramp_b_textedit.text()
        try: b_force = float(b_force)
        except ValueError: return

        # Rate
        rate = self.force_ramp_rate_textedit.text()
        try: rate = float(rate)
        except ValueError: return

        # Send inter-process command to motor
        command = ForceRampCommand(
            a_force=a_force,
            b_force=b_force,
            rate=rate,
            direction=direction,
        )
        self.manager.send_ipc(command)


class LinearMotorPlot(magscope.TimeSeriesPlotBase):
    def __init__(self, buffer_name: str):
        super().__init__(buffer_name, 'Liner Motor (mm)')
        self.line_position: matplotlib.lines.Line2D
        self.line_target: matplotlib.lines.Line2D

    def setup(self):
        super().setup()
        self.line_position, self.line_target = self.axes.plot([], [], 'r', [], [], 'g')

    def update(self):
        # Get data from buffer
        data = self.buffer.peak_unsorted()
        t = data[:, 0]
        position = data[:, 1]
        target = data[:, 2]

        # Remove nan/inf
        selection = np.isfinite(t)
        t = t[selection]
        position = position[selection]
        target = target[selection]

        # Sort by time
        sort_index = np.argsort(t)
        t = t[sort_index]
        position = position[sort_index]
        target = target[sort_index]

        # Remove value outside of axis limits
        xmin = self.parent.limits.get('Time', (None, None))[0]
        xmax = self.parent.limits.get('Time', (None, None))[1]
        ymin = self.parent.limits.get(self.ylabel, (None, None))[0]
        ymax = self.parent.limits.get(self.ylabel, (None, None))[1]
        selection = ((xmin or -np.inf) <= t) & (t <= (xmax or np.inf))
        t = t[selection]
        position = position[selection]
        target = target[selection]

        # Prevent unintended axis inversion
        if xmin is None or xmax is None:
            self.axes.xaxis.set_inverted(False)
        if ymin is None or ymax is None:
            self.axes.yaxis.set_inverted(False)

        # Convert time to timepoints
        t = [datetime.fromtimestamp(t_) for t_ in t]

        # Update the plot
        self.line_target.set_xdata(t)
        self.line_target.set_ydata(target)
        self.line_position.set_xdata(t)
        self.line_position.set_ydata(position)

        # Prevent equal limits error
        if xmin is not None and xmin==xmax:
            xmax += 1
        if ymin is not None and ymin==ymax:
            ymax += 1

        # Convert the x-limits to timestamps
        xmin, xmax = [datetime.fromtimestamp(t_) if t_ else None for t_ in (xmin, xmax)]

        # Update the axis limits
        self.axes.autoscale()
        self.axes.autoscale_view()
        self.axes.set_xlim(xmin=xmin, xmax=xmax)
        self.axes.set_ylim(ymin=ymin, ymax=ymax)
        self.axes.relim()


class ForcePlot(magscope.TimeSeriesPlotBase):
    def __init__(self, buffer_name: str):
        super().__init__(buffer_name, 'Force (pN)')
        self.line_position: matplotlib.lines.Line2D
        self.line_target: matplotlib.lines.Line2D

        self.force_calibration = force_calibration

    def setup(self):
        super().setup()
        self.line_position, self.line_target = self.axes.plot([], [], 'r', [], [], 'g')

    def update(self):
        # Get data from buffer
        data = self.buffer.peak_unsorted()
        t = data[:, 0]
        position = data[:, 1]
        target = data[:, 2]

        # Convert to force
        position = self.force_calibration.motor2force(position)
        target = self.force_calibration.motor2force(target)

        # Remove nan/inf
        selection = np.isfinite(t)
        t = t[selection]
        position = position[selection]
        target = target[selection]

        # Sort by time
        sort_index = np.argsort(t)
        t = t[sort_index]
        position = position[sort_index]
        target = target[sort_index]

        # Remove value outside of axis limits
        xmin = self.parent.limits.get('Time', (None, None))[0]
        xmax = self.parent.limits.get('Time', (None, None))[1]
        ymin = self.parent.limits.get(self.ylabel, (None, None))[0]
        ymax = self.parent.limits.get(self.ylabel, (None, None))[1]
        selection = ((xmin or -np.inf) <= t) & (t <= (xmax or np.inf))
        t = t[selection]
        position = position[selection]
        target = target[selection]

        # Prevent unintended axis inversion
        if xmin is None or xmax is None:
            self.axes.xaxis.set_inverted(False)
        if ymin is None or ymax is None:
            self.axes.yaxis.set_inverted(False)

        # Convert time to timepoints
        t = [datetime.fromtimestamp(t_) for t_ in t]

        # Update the plot
        self.line_target.set_xdata(t)
        self.line_target.set_ydata(target)
        self.line_position.set_xdata(t)
        self.line_position.set_ydata(position)

        # Prevent equal limits error
        if xmin is not None and xmin==xmax:
            xmax += 1
        if ymin is not None and ymin==ymax:
            ymax += 1

        # Convert the x-limits to timestamps
        xmin, xmax = [datetime.fromtimestamp(t_) if t_ else None for t_ in (xmin, xmax)]

        # Update the axis limits
        self.axes.autoscale()
        self.axes.autoscale_view()
        self.axes.set_xlim(xmin=xmin, xmax=xmax)
        self.axes.set_ylim(ymin=ymin, ymax=ymax)
        self.axes.relim()


if __name__ == "__main__":
    scope = magscope.MagScope()

    # Add the motor
    scope.add_hardware(FakeLinearMotor())

    # Add a GUI to control the Motor
    scope.add_control(LinearMotorControls, column=0)

    # Add a plot of the motor's position/force
    scope.add_timeplot(LinearMotorPlot('FakeLinearMotor'))
    scope.add_timeplot(ForcePlot('FakeLinearMotor'))

    # Launch the scope
    scope.start()
