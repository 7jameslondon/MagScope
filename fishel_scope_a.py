from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton

import magscope
import numpy as np
from time import time
from math import copysign, isclose
from warnings import warn

from magscope import Message
from magscope.datatypes import MatrixBuffer


class FakeLinearMotor(magscope.HardwareManagerBase):
    position_min_max = (0, 20)
    speed_min_max = (0.1, 10.0)
    def __init__(self):
        super().__init__()

        self.buffer_shape = (100000, 4)

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

        t = time()
        row = np.array([[t, self._fake_position, self._fake_target, self._fake_speed]])
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
        self._buffer = MatrixBuffer(
            create=False,
            locks=self.manager._locks,
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
        _, position, target, speed = self._buffer.peak_sorted()[0, :]
        self.position_label.setText(f'Position: {position:.2f}')
        self.target_label.setText(f'Target: {target:.2f}')
        self.speed_label.setText(f'Speed: {speed:.2f}')

    def callback_move(self):
        # Try to get target
        try:
            target = float(self.target_textedit.text())
        except Exception:
            target = None
        if target is not None:
            if target < FakeLinearMotor.position_min_max[0] or target > FakeLinearMotor.position_min_max[1]:
                warn(f'Target position {target} outside of range {FakeLinearMotor.position_min_max}')
                return

        # Try to get speed
        try:
            speed = float(self.speed_textedit.text())
        except Exception:
            speed = None
        if speed is not None:
            if speed < FakeLinearMotor.speed_min_max[0] or target > FakeLinearMotor.speed_min_max[1]:
                warn(f'Speed {speed} outside of range {FakeLinearMotor.speed_min_max}')
                return

        # Send inter-process message to motor
        message = Message(
            to=FakeLinearMotor,
            func=FakeLinearMotor.move,
            target=target,
            speed=speed,
        )
        self.manager._send(message)


if __name__ == "__main__":
    scope = magscope.MagScope()

    # Add the motor
    my_linear_motor = FakeLinearMotor()
    scope.add_hardware(my_linear_motor)

    # Add a GUI to control the Motor
    scope.add_control(LinearMotorControls, column=0)

    # Launch the scope
    scope.start()