from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import time
from typing import Final

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

import magscope
from magscope.datatypes import MatrixBuffer
from magscope.hardware import FocusMotorBase
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import Command, MoveFocusMotorAbsoluteCommand
from pipython import GCSDevice


NM_PER_CONTROLLER_UNIT: Final[float] = 1000.0
CONTROLLER_NAME: Final[str] = "E-709"
FOCUS_MOTOR_BUFFER_NAME: Final[str] = "PiE709FocusMotor"


@dataclass(frozen=True)
class ConnectPiE709Command(Command):
    pass


@dataclass(frozen=True)
class DisconnectPiE709Command(Command):
    pass


@dataclass(frozen=True)
class JogPiE709RelativeCommand(Command):
    delta_nm: float | None = None


@dataclass(frozen=True)
class ZeroPiE709PositionCommand(Command):
    pass


def controller_to_nm(value: float) -> float:
    return value * NM_PER_CONTROLLER_UNIT


def nm_to_controller(value: float) -> float:
    return value / NM_PER_CONTROLLER_UNIT


def make_numeric_lineedit(text: str, validator: QDoubleValidator) -> QLineEdit:
    lineedit = QLineEdit(text)
    lineedit.setValidator(validator)
    return lineedit


def clamp_button_to_text(button: QPushButton, *, extra_width: int = 16) -> None:
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    text_width = button.fontMetrics().horizontalAdvance(button.text())
    button.setFixedWidth(text_width + extra_width)


class PiE709FocusMotor(FocusMotorBase):
    """PI E-709 focus motor controlled over USB using PIPython.

    This implementation assumes the controller reports position and limits in micrometres,
    which are converted to nanometres for MagScope.
    """

    position_min_max: Final[tuple[float, float]] = (0.0, 100000.0)

    def __init__(self):
        super().__init__()
        self.fetch_interval = 0.05
        self._pidevice: GCSDevice | None = None
        self._usb_description: str | None = None
        self._axis: str = "Z"
        self._last_timestamp = time()
        self._controller_idn = "-"
        self._servo_enabled = False

    def connect(self):
        if self._is_connected:
            return

        pidevice = GCSDevice(CONTROLLER_NAME)
        usb_devices = [description for description in pidevice.EnumerateUSB() if CONTROLLER_NAME in description]
        if not usb_devices:
            return

        self._usb_description = usb_devices[0]
        pidevice.ConnectUSB(self._usb_description)
        axes = pidevice.qSAI()
        self._axis = axes[0] if isinstance(axes, list) else list(axes.values())[0]
        self._controller_idn = str(pidevice.qIDN())
        if hasattr(pidevice, "qSVO"):
            self._servo_enabled = bool(pidevice.qSVO(self._axis)[self._axis])
        if hasattr(pidevice, "SVO") and not self._servo_enabled:
            pidevice.SVO(self._axis, True)
            self._servo_enabled = True

        self.position_min_max = (
            controller_to_nm(float(pidevice.qTMN(self._axis)[self._axis])),
            controller_to_nm(float(pidevice.qTMX(self._axis)[self._axis])),
        )

        self._pidevice = pidevice
        self._is_connected = True
        self._last_timestamp = time()

    def disconnect(self):
        if self._pidevice is not None:
            try:
                self._pidevice.CloseConnection()
            except Exception:
                pass

        self._pidevice = None
        self._usb_description = None
        self._is_connected = False

    def move_absolute(self, z: float) -> None:
        if self._pidevice is None:
            return
        self._pidevice.MOV(self._axis, nm_to_controller(z))

    def get_current_z(self) -> float:
        if self._pidevice is None:
            return 0.0
        return controller_to_nm(float(self._pidevice.qPOS(self._axis)[self._axis]))

    def get_is_moving(self) -> bool:
        if self._pidevice is None:
            return False
        return bool(1. - self._pidevice.qONT(self._axis)[self._axis])

    def get_position_limits(self) -> tuple[float, float]:
        return self.position_min_max

    @register_ipc_command(ConnectPiE709Command)
    def handle_connect(self):
        self.connect()
        if self._is_connected:
            self._target_z = float(self.get_current_z())
            self._write_state(time(), float(self.get_current_z()), force=True)

    @register_ipc_command(DisconnectPiE709Command)
    def handle_disconnect(self):
        self.disconnect()
        self._target_z = np.nan
        if self._buffer is not None:
            row = np.array([[time(), np.nan, np.nan, 0.0]], dtype=float)
            self._buffer.write(row)

    @register_ipc_command(JogPiE709RelativeCommand)
    def handle_jog(self, delta_nm: float | None = None):
        if delta_nm is None:
            return
        self.handle_move_absolute(self.get_current_z() + float(delta_nm))

    @register_ipc_command(ZeroPiE709PositionCommand)
    def handle_zero_position(self):
        if self._pidevice is None:
            return
        if hasattr(self._pidevice, "ATZ") and self._pidevice.HasATZ():
            self._pidevice.ATZ({self._axis: 0.0})
            self._target_z = 0.0
            self._write_state(time(), float(self.get_current_z()), force=True)


class DeviceInfoDialog(QDialog):
    def __init__(self, *, controller_name: str, axis: str, connection: str, limits_nm: tuple[float, float], parent: QWidget | None = None):
        super().__init__(parent)

        self.setWindowTitle("Device Info")
        self.setModal(True)

        layout = QVBoxLayout()
        grid = QGridLayout()
        grid.addWidget(QLabel("Controller"), 0, 0)
        grid.addWidget(QLabel(controller_name), 0, 1)
        grid.addWidget(QLabel("Axis"), 1, 0)
        grid.addWidget(QLabel(axis), 1, 1)
        grid.addWidget(QLabel("Connection"), 2, 0)
        grid.addWidget(QLabel(connection), 2, 1)
        grid.addWidget(QLabel("Min Position"), 3, 0)
        grid.addWidget(QLabel(f"{limits_nm[0]:.0f} nm"), 3, 1)
        grid.addWidget(QLabel("Max Position"), 4, 0)
        grid.addWidget(QLabel(f"{limits_nm[1]:.0f} nm"), 4, 1)
        layout.addLayout(grid)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        self.setLayout(layout)


class AdvancedControlsDialog(QDialog):
    def __init__(self, manager: magscope.UIManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.manager = manager

        self.setWindowTitle("Advanced Controls")
        self.setModal(True)

        zero_button = QPushButton("Auto Zero Position")
        zero_button.clicked.connect(lambda: self.manager.send_ipc(ZeroPiE709PositionCommand()))
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout()
        layout.addWidget(zero_button)
        layout.addWidget(close_button)
        self.setLayout(layout)


class PiE709Controls(magscope.ControlPanelBase):
    def __init__(self, manager: magscope.UIManager):
        super().__init__(title="PI E-709 (Focus Motor)", manager=manager)

        self._buffer = MatrixBuffer(create=False, locks=self.manager.locks, name=FOCUS_MOTOR_BUFFER_NAME)
        self.latest_row = np.full((4,), np.nan, dtype=float)
        self._position_limits_nm = PiE709FocusMotor.position_min_max

        self.position_name_label = QLabel("Position")
        self.last_update_name_label = QLabel("Last Update")
        self.moving_name_label = QLabel("Moving")
        self.position_value = QLabel("-")
        self.last_update_value = QLabel("-")
        self.moving_value = QLabel("-")
        self.position_name_label.setStyleSheet("color: #5aa6ff;")
        self.position_value.setStyleSheet("color: #5aa6ff;")
        self.position_name_label.setFixedWidth(70)
        self.position_value.setFixedWidth(135)
        self.last_update_name_label.setFixedWidth(70)
        self.last_update_value.setFixedWidth(135)
        self.moving_name_label.setFixedWidth(50)
        self.moving_value.setFixedWidth(35)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(lambda: self.manager.send_ipc(ConnectPiE709Command()))
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(lambda: self.manager.send_ipc(DisconnectPiE709Command()))
        self.device_info_button = QPushButton("Info")
        self.device_info_button.clicked.connect(self.show_device_info)
        self.advanced_controls_button = QPushButton("Advanced")
        self.advanced_controls_button.clicked.connect(self.show_advanced_controls)

        self.target_validator = QDoubleValidator(*PiE709FocusMotor.position_min_max, 0, self)
        self.jog_validator = QDoubleValidator(1.0, PiE709FocusMotor.position_min_max[1], 0, self)
        self.target_input = make_numeric_lineedit("0", self.target_validator)
        self.jog_input = make_numeric_lineedit("1000", self.jog_validator)
        self.max_position_value = QLabel(f"Max Position: {self._position_limits_nm[1]:.0f} nm")

        self.move_button = QPushButton("Move")
        self.move_button.clicked.connect(self.send_move_to)
        self.move_min_button = QPushButton("Min")
        self.move_min_button.clicked.connect(self.send_move_min)
        self.move_mid_button = QPushButton("Mid")
        self.move_mid_button.clicked.connect(self.send_move_mid)
        self.move_max_button = QPushButton("Max")
        self.move_max_button.clicked.connect(self.send_move_max)
        self.jog_minus_button = QPushButton("-")
        self.jog_minus_button.clicked.connect(lambda: self.send_jog(-1.0))
        self.jog_plus_button = QPushButton("+")
        self.jog_plus_button.clicked.connect(lambda: self.send_jog(1.0))

        clamp_button_to_text(self.device_info_button)
        clamp_button_to_text(self.advanced_controls_button)
        clamp_button_to_text(self.move_button)
        clamp_button_to_text(self.move_min_button)
        clamp_button_to_text(self.move_mid_button)
        clamp_button_to_text(self.move_max_button)

        outer = self.layout()

        status_label = QLabel("Status")
        status_label.setStyleSheet("font-weight: bold;")
        outer.addWidget(status_label)

        connection_buttons = QHBoxLayout()
        self.connect_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.disconnect_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        connection_buttons.addWidget(self.connect_button, 1)
        connection_buttons.addWidget(self.disconnect_button, 1)
        outer.addLayout(connection_buttons)

        status = QVBoxLayout()
        top_status_row = QHBoxLayout()
        top_status_row.addWidget(self.position_name_label)
        top_status_row.addWidget(self.position_value)
        top_status_row.addSpacing(5)
        top_status_row.addWidget(self.moving_name_label)
        top_status_row.addWidget(self.moving_value)
        top_status_row.addStretch(1)
        status.addLayout(top_status_row)

        middle_status_row = QHBoxLayout()
        middle_status_row.addWidget(self.last_update_name_label)
        middle_status_row.addWidget(self.last_update_value)
        middle_status_row.addStretch(1)
        status.addLayout(middle_status_row)
        outer.addLayout(status)

        status_divider = QFrame()
        status_divider.setFrameShape(QFrame.Shape.HLine)
        status_divider.setLineWidth(1)
        outer.addWidget(status_divider)

        motion_label = QLabel("Motion")
        motion_label.setStyleSheet("font-weight: bold;")
        outer.addWidget(motion_label)

        motion = QVBoxLayout()
        move_row = QHBoxLayout()
        move_row.addWidget(QLabel("Move"))
        move_row.addWidget(self.target_input)
        move_row.addWidget(self.move_button)
        move_row.addWidget(self.move_min_button)
        move_row.addWidget(self.move_mid_button)
        move_row.addWidget(self.move_max_button)
        motion.addLayout(move_row)

        jog_row = QHBoxLayout()
        jog_row.addWidget(QLabel("Jog"))
        jog_row.addWidget(self.jog_input)
        jog_row.addWidget(self.jog_minus_button)
        jog_row.addWidget(self.jog_plus_button)
        motion.addLayout(jog_row)
        motion.addWidget(self.max_position_value)
        outer.addLayout(motion)

        motion_divider = QFrame()
        motion_divider.setFrameShape(QFrame.Shape.HLine)
        motion_divider.setLineWidth(1)
        outer.addWidget(motion_divider)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.device_info_button)
        footer.addWidget(self.advanced_controls_button)
        outer.addLayout(footer)

        self._timer = QTimer()
        self._timer.timeout.connect(self.update_values)
        self._timer.setInterval(100)
        self._timer.start()

    def update_values(self) -> None:
        data = self._buffer.peak_sorted()
        if data.size == 0:
            return

        finite_rows = np.isfinite(data[:, 0])
        if not np.any(finite_rows):
            return

        latest = data[finite_rows][-1, :]
        self.latest_row = latest
        _, position_nm, target_nm, is_at_target_value = latest
        connected = bool(np.isfinite(position_nm))

        self.disconnect_button.setEnabled(connected)
        self.device_info_button.setEnabled(connected)
        self.advanced_controls_button.setEnabled(connected)
        self.move_button.setEnabled(connected)
        self.move_min_button.setEnabled(connected)
        self.move_mid_button.setEnabled(connected)
        self.move_max_button.setEnabled(connected)
        self.jog_minus_button.setEnabled(connected)
        self.jog_plus_button.setEnabled(connected)

        if not connected:
            self.position_value.setText("-")
            self.last_update_value.setText("-")
            self.moving_value.setText("-")
            return

        is_at_target = bool(round(float(is_at_target_value)))
        is_moving = not is_at_target

        self.position_value.setText(f"{position_nm:.0f} nm")
        self.last_update_value.setText(datetime.fromtimestamp(latest[0]).strftime("%H:%M:%S.%f")[:-5])
        self.moving_value.setText("Yes" if is_moving else "No")
        self.max_position_value.setText(f"Max Position: {self._position_limits_nm[1]:.0f} nm")

    def send_move_to(self) -> None:
        target = self._to_float(self.target_input.text())
        if target is None:
            return
        self.manager.send_ipc(MoveFocusMotorAbsoluteCommand(z=target))

    def send_move_min(self) -> None:
        self.manager.send_ipc(MoveFocusMotorAbsoluteCommand(z=self._position_limits_nm[0]))

    def send_move_mid(self) -> None:
        midpoint = 0.5 * (self._position_limits_nm[0] + self._position_limits_nm[1])
        self.manager.send_ipc(MoveFocusMotorAbsoluteCommand(z=midpoint))

    def send_move_max(self) -> None:
        self.manager.send_ipc(MoveFocusMotorAbsoluteCommand(z=self._position_limits_nm[1]))

    def send_jog(self, direction: float) -> None:
        jog_nm = self._to_float(self.jog_input.text())
        if jog_nm is None:
            return
        self.manager.send_ipc(JogPiE709RelativeCommand(delta_nm=direction * jog_nm))

    def show_device_info(self) -> None:
        dialog = DeviceInfoDialog(
            controller_name="PI E-709",
            axis="Z",
            connection="USB auto-detect",
            limits_nm=self._position_limits_nm,
            parent=self,
        )
        dialog.exec()

    def show_advanced_controls(self) -> None:
        dialog = AdvancedControlsDialog(self.manager, self)
        dialog.exec()

    @staticmethod
    def _to_float(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None


class PiE709FocusPlot(magscope.TimeSeriesPlotBase):
    def __init__(self, buffer_name: str = FOCUS_MOTOR_BUFFER_NAME):
        super().__init__(buffer_name, "Focus (nm)")
        self.line_position = None
        self.line_target = None

    def setup(self):
        super().setup()
        self.line_position, self.line_target = self.axes.plot([], [], "c", [], [], "y")

    def update(self):
        data = self.buffer.peak_unsorted()
        if data.size == 0:
            return

        t = data[:, 0]
        position = data[:, 1]
        target = data[:, 2]

        selection = np.isfinite(t)
        t = t[selection]
        position = position[selection]
        target = target[selection]

        sort_index = np.argsort(t)
        t = t[sort_index]
        position = position[sort_index]
        target = target[sort_index]

        xmin, xmax = self.parent.limits.get("Time", (None, None))
        ymin, ymax = self.parent.limits.get(self.ylabel, (None, None))

        selection = ((xmin or -np.inf) <= t) & (t <= (xmax or np.inf))
        t = t[selection]
        position = position[selection]
        target = target[selection]

        timepoints = [datetime.fromtimestamp(t_) for t_ in t]
        self.line_target.set_xdata(timepoints)
        self.line_target.set_ydata(target)
        self.line_position.set_xdata(timepoints)
        self.line_position.set_ydata(position)

        xmin_dt, xmax_dt = [datetime.fromtimestamp(t_) if t_ else None for t_ in (xmin, xmax)]
        self.axes.autoscale()
        self.axes.autoscale_view()
        self.axes.set_xlim(xmin=xmin_dt, xmax=xmax_dt)
        self.axes.set_ylim(ymin=ymin, ymax=ymax)
        self.axes.relim()


if __name__ == "__main__":
    scope = magscope.MagScope(verbose=True)
    scope.ui_manager.n_windows = 1

    scope.add_hardware(PiE709FocusMotor())
    scope.add_control(PiE709Controls, column=0)
    scope.add_timeplot(PiE709FocusPlot())

    scope.start()
