from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton
import matplotlib
import numpy as np
from time import time
from math import copysign
from warnings import warn
from datetime import datetime

import magscope


class FakeLinearMotor(magscope.HardwareManagerBase):
    position_min_max = (0, 20)
    speed_min_max = (0.1, 10.0)
    def __init__(self):
        super().__init__()

        self.buffer_shape = (100000, 4)
        self.last_fetch = 0
        self.fetch_interval = 0.01

        self._fake_position = 0.0
        self._fake_speed = 1.0
        self._fake_moving = False
        self._fake_target = 0.0
        self._fake_last_time = time()

    def connect(self):
        self._is_connected = True

    def disconnect(self):
        self._is_connected = False

    def fetch(self):
        self._do_fake_move() # Only needed to simulate motor movement

        if (now := time()) - self.last_fetch > self.fetch_interval:
            self.last_fetch = now
            row = np.array([[now, self._fake_position, self._fake_target, self._fake_speed]])
            self._buffer.write(row)

    def move(self, target=None, speed=None):
        if target is not None:
            self._fake_target = target
            self._fake_moving = True
            self._fake_last_time = time()

        if speed is not None:
            self._fake_speed = speed

    def _do_fake_move(self):
        """ This is only used to simulate the movement of the motor. """
        if not self._fake_moving:
            return
        if self._fake_position == self._fake_target:
            self._fake_moving = False

        t = time()
        dt = t - self._fake_last_time
        self._fake_last_time = t
        direction = copysign(1, self._fake_target - self._fake_position)
        dp = self._fake_speed * dt * direction

        self._fake_position += dp

        if direction > 0:
            if self._fake_position > self._fake_target:
                self._fake_position = self._fake_target
                self._fake_moving = False
        else:
            if self._fake_position < self._fake_target:
                self._fake_position = self._fake_target
                self._fake_moving = False


class LinearMotorControls(magscope.ControlPanelBase):
    def __init__(self, manager):
        super().__init__(title='Fake Linear Motor', manager=manager)

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

        # First row
        row_1 = QHBoxLayout()
        self.layout().addLayout(row_1)

        # Current position
        self.position_label = QLabel('Position:')
        row_1.addWidget(self.position_label)

        # Current target
        self.target_label = QLabel('Target:')
        row_1.addWidget(self.target_label)

        # Current speed
        self.speed_label = QLabel('Speed:')
        row_1.addWidget(self.speed_label)

        # Second row
        row_2 = QHBoxLayout()
        self.layout().addLayout(row_2)

        # Target label
        row_2.addWidget(QLabel('Target:'))

        # Target textedit
        self.target_textedit = QLineEdit()
        row_2.addWidget(self.target_textedit)

        # Speed label
        row_2.addWidget(QLabel('Speed:'))

        # Speed textedit
        self.speed_textedit = QLineEdit()
        row_2.addWidget(self.speed_textedit)

        # Move button
        move_button = QPushButton('Move')
        move_button.clicked.connect(self.callback_move)
        row_2.addWidget(move_button)

    def update_values(self):
        _, position, target, speed = self._buffer.peak_sorted()[-1, :]
        self.position_label.setText(f'Position: {position:.2f}')
        self.target_label.setText(f'Target: {target:.2f}')
        self.speed_label.setText(f'Speed: {speed:.2f}')

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

        # Send inter-process message to motor
        message = magscope.Message(
            to=FakeLinearMotor,
            meth=FakeLinearMotor.move,
            target=target,
            speed=speed,
        )
        self.manager.send(message)


class LinearMotorPlot(magscope.TimeSeriesPlotBase):
    def __init__(self, buffer_name: str, ylabel: str):
        super().__init__(buffer_name, ylabel)
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


if __name__ == "__main__":
    scope = magscope.MagScope()

    # Add the motor
    scope.add_hardware(FakeLinearMotor())

    # Add a GUI to control the Motor
    scope.add_control(LinearMotorControls, column=0)

    # Add a plot of the motor's position
    scope.add_timeplot(LinearMotorPlot('FakeLinearMotor', ylabel='Linear Motor'))

    # Launch the scope
    scope.start()