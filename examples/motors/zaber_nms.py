from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import time
import winreg

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import magscope
from magscope.datatypes import MatrixBuffer
from magscope.hardware import HardwareManagerBase
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import Command
from zaber_motion import MotionLibException, Units
from zaber_motion.ascii import Axis, Connection
from zaber_motion.ascii.setting_constants import SettingConstants
from zaber_motion.ascii.warning_flags import WarningFlags
from zaber_motion.dto.ascii.device_identity import DeviceIdentity


EXPECTED_MODEL = "X-NMS17-E01"
POSITION_UNIT = Units.ANGLE_DEGREES
VELOCITY_UNIT = Units.ANGULAR_VELOCITY_DEGREES_PER_SECOND
FETCH_INTERVAL_S = 0.05
DEFAULT_POSITION_UI_MAX = 1_000_000.0
DEFAULT_SPEED_UI_MAX = 1_000_000.0
DEGREES_PER_TURN = 360.0

WARNING_FLAG_NAMES = sorted(name for name in dir(WarningFlags) if name.isupper())
WARNING_FLAG_INDEX = {name: index for index, name in enumerate(WARNING_FLAG_NAMES)}

BUFFER_NAME = "ZaberNmsMotor"

COL_TIMESTAMP = 0
COL_POSITION = 1
COL_TARGET = 2
COL_SPEED = 3
COL_BUSY = 4
COL_HOMED = 5
COL_LIMIT_MAX = 6
COL_SPEED_MAX = 7
COL_WARNING_MASK = 8
COL_CONNECTED = 9
COL_SERIAL = 10
COL_DEVICE_ID = 11
COL_AXIS_COUNT = 12
COL_AXIS_NUMBER = 13
COL_DEVICE_ADDRESS = 14
COL_THEORETICAL_RESOLUTION_TURNS = 15
COL_PORT_NUMBER = 16
COL_FIRMWARE_MAJOR = 17
COL_FIRMWARE_MINOR = 18
COL_FIRMWARE_BUILD = 19
COL_COMMAND_ERROR = 20

COMMAND_ERROR_NONE = 0
COMMAND_ERROR_CONNECT = 1
COMMAND_ERROR_HOME = 2
COMMAND_ERROR_STOP = 3
COMMAND_ERROR_MOVE = 4
COMMAND_ERROR_JOG = 5
COMMAND_ERROR_MOVE_MIN = 6
COMMAND_ERROR_MOVE_MAX = 7
COMMAND_ERROR_SET_LIMIT = 8
COMMAND_ERROR_POLL = 9

COMMAND_ERROR_LABELS = {
    COMMAND_ERROR_NONE: None,
    COMMAND_ERROR_CONNECT: "Connection failed",
    COMMAND_ERROR_HOME: "Home failed",
    COMMAND_ERROR_STOP: "Stop failed",
    COMMAND_ERROR_MOVE: "Move-to failed",
    COMMAND_ERROR_JOG: "Jog move failed",
    COMMAND_ERROR_MOVE_MIN: "Move-to-min failed",
    COMMAND_ERROR_MOVE_MAX: "Move-to-max failed",
    COMMAND_ERROR_SET_LIMIT: "Set max limit failed",
    COMMAND_ERROR_POLL: "Hardware poll failed",
}

STOP_BUTTON_STYLE = (
    "QPushButton { background: #4a2b2f; }"
    "QPushButton:hover { background: #563238; }"
    "QPushButton:pressed { background: #61393f; }"
)


@dataclass(frozen=True)
class ConnectZaberNmsCommand(Command):
    pass


@dataclass(frozen=True)
class DisconnectZaberNmsCommand(Command):
    pass


@dataclass(frozen=True)
class HomeZaberNmsCommand(Command):
    pass


@dataclass(frozen=True)
class StopZaberNmsCommand(Command):
    pass


@dataclass(frozen=True)
class MoveZaberNmsAbsoluteCommand(Command):
    target_turns: float | None = None
    speed_turns_s: float | None = None


@dataclass(frozen=True)
class JogZaberNmsRelativeCommand(Command):
    delta_turns: float | None = None
    speed_turns_s: float | None = None


@dataclass(frozen=True)
class MoveZaberNmsMinCommand(Command):
    speed_turns_s: float | None = None


@dataclass(frozen=True)
class MoveZaberNmsMaxCommand(Command):
    speed_turns_s: float | None = None


@dataclass(frozen=True)
class SetZaberNmsMaxLimitCommand(Command):
    limit_turns: float | None = None


@dataclass(frozen=True)
class UseDefaultZaberNmsMaxLimitCommand(Command):
    pass


@dataclass(frozen=True)
class ZeroZaberNmsPositionCommand(Command):
    pass


def get_serial_ports() -> list[str]:
    ports: set[str] = set()

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            value_count = winreg.QueryInfoKey(key)[1]
            for index in range(value_count):
                _, port_name, _ = winreg.EnumValue(key, index)
                if isinstance(port_name, str) and port_name.upper().startswith("COM"):
                    ports.add(port_name.upper())
    except OSError:
        return []

    return sorted(ports, key=lambda port_name: int(port_name[3:]))


def encode_warning_mask(flags: set[str]) -> int:
    mask = 0
    for flag in flags:
        index = WARNING_FLAG_INDEX.get(flag)
        if index is not None:
            mask |= 1 << index
    return mask


def decode_warning_mask(mask: int) -> list[str]:
    return [name for index, name in enumerate(WARNING_FLAG_NAMES) if mask & (1 << index)]


def parse_firmware_version(version: object) -> tuple[int, int, int]:
    if all(hasattr(version, attr) for attr in ("major", "minor", "build")):
        try:
            return int(version.major), int(version.minor), int(version.build)
        except (TypeError, ValueError):
            pass

    text = str(version)
    parts = text.split(".")
    numbers = []
    for part in parts[:3]:
        try:
            numbers.append(int(part))
        except ValueError:
            numbers.append(0)
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


def make_numeric_lineedit(text: str, validator: QDoubleValidator) -> QLineEdit:
    lineedit = QLineEdit(text)
    lineedit.setValidator(validator)
    return lineedit


def clamp_button_to_text(button: QPushButton, *, extra_width: int = 16) -> None:
    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    text_width = button.fontMetrics().horizontalAdvance(button.text())
    button.setFixedWidth(text_width + extra_width)


def turns_to_degrees(value: float) -> float:
    return value * DEGREES_PER_TURN


def degrees_to_turns(value: float) -> float:
    return value / DEGREES_PER_TURN


class ZaberNmsMotor(HardwareManagerBase):
    position_min_max = (0.0, DEFAULT_POSITION_UI_MAX)
    speed_min_max = (0.001, DEFAULT_SPEED_UI_MAX)

    def __init__(self):
        super().__init__()
        self.buffer_shape = (100000, 21)
        self.fetch_interval = FETCH_INTERVAL_S

        self._connection: Connection | None = None
        self._axis: Axis | None = None
        self._identity: DeviceIdentity | None = None
        self._port_name: str | None = None
        self._target_turns = np.nan
        self._speed_turns_s = 1.0
        self._last_fetch = 0.0
        self._command_error = COMMAND_ERROR_NONE
        self._last_state: tuple[float, ...] | None = None

        self._limit_max_turns = np.nan
        self._speed_max_turns_s = np.nan
        self._warning_mask = 0
        self._serial = np.nan
        self._device_id = np.nan
        self._axis_count = np.nan
        self._axis_number = np.nan
        self._device_address = np.nan
        self._theoretical_resolution_turns = np.nan
        self._port_number = np.nan
        self._firmware_major = np.nan
        self._firmware_minor = np.nan
        self._firmware_build = np.nan

    def setup(self):
        super().setup()
        self.connect()
        self._write_state(force=True)

    def connect(self):
        if self._is_connected:
            return

        for port_name in get_serial_ports():
            connection: Connection | None = None
            try:
                connection = Connection.open_serial_port(port_name)
                devices = connection.detect_devices(identify_devices=True)
                for device in devices:
                    if device.identity.name == EXPECTED_MODEL:
                        self._connection = connection
                        self._axis = device.get_axis(1)
                        self._identity = device.identity
                        self._port_name = port_name
                        self._is_connected = True
                        self._command_error = COMMAND_ERROR_NONE
                        self._refresh_metadata()
                        current_degrees = float(self._axis.get_position(POSITION_UNIT))
                        self._target_turns = degrees_to_turns(current_degrees)
                        return
                connection.close()
            except MotionLibException:
                if connection is not None:
                    connection.close()

        self._command_error = COMMAND_ERROR_CONNECT

    def disconnect(self):
        if self._axis is not None:
            try:
                self._axis.stop(wait_until_idle=False)
            except MotionLibException:
                pass

        if self._connection is not None:
            self._connection.close()

        self._connection = None
        self._axis = None
        self._identity = None
        self._port_name = None
        self._is_connected = False
        self._target_turns = np.nan
        self._warning_mask = 0
        self._reset_metadata()

    def fetch(self):
        now = time()
        if not self._is_connected:
            if (now - self._last_fetch) >= self.fetch_interval:
                self._last_fetch = now
                self._write_state()
            return

        if self._axis is None:
            self.disconnect()
            self._write_state(force=True)
            return

        if (now - self._last_fetch) < self.fetch_interval:
            return

        self._last_fetch = now

        try:
            if np.isnan(self._target_turns):
                self._target_turns = degrees_to_turns(float(self._axis.get_position(POSITION_UNIT)))
            self._refresh_metadata()
            if self._command_error == COMMAND_ERROR_POLL:
                self._command_error = COMMAND_ERROR_NONE
        except MotionLibException:
            self._command_error = COMMAND_ERROR_POLL
        finally:
            self._write_state()

    def _refresh_metadata(self) -> None:
        if self._axis is None or self._identity is None or self._port_name is None:
            return

        limit_max_degrees = float(self._axis.settings.get(SettingConstants.LIMIT_MAX, POSITION_UNIT))
        speed_max_degrees_s = float(self._axis.settings.get(SettingConstants.MAXSPEED_MAX, VELOCITY_UNIT))
        warning_flags = self._axis.warnings.get_flags() | self._axis.device.warnings.get_flags()

        self._limit_max_turns = degrees_to_turns(limit_max_degrees)
        self._speed_max_turns_s = degrees_to_turns(speed_max_degrees_s)
        self._warning_mask = encode_warning_mask(warning_flags)
        self._serial = float(self._identity.serial_number)
        self._device_id = float(self._identity.device_id)
        self._axis_count = float(self._identity.axis_count)
        self._axis_number = float(self._axis.axis_number)
        self._device_address = float(self._axis.device.device_address)
        one_native_step_degrees = float(self._axis.settings.convert_from_native_units("pos", 1.0, POSITION_UNIT))
        self._theoretical_resolution_turns = degrees_to_turns(one_native_step_degrees)
        self._port_number = float(int(self._port_name[3:]))
        firmware_major, firmware_minor, firmware_build = parse_firmware_version(self._identity.firmware_version)
        self._firmware_major = float(firmware_major)
        self._firmware_minor = float(firmware_minor)
        self._firmware_build = float(firmware_build)

    def _reset_metadata(self) -> None:
        self._limit_max_turns = np.nan
        self._speed_max_turns_s = np.nan
        self._serial = np.nan
        self._device_id = np.nan
        self._axis_count = np.nan
        self._axis_number = np.nan
        self._device_address = np.nan
        self._theoretical_resolution_turns = np.nan
        self._port_number = np.nan
        self._firmware_major = np.nan
        self._firmware_minor = np.nan
        self._firmware_build = np.nan

    def _write_state(self, *, force: bool = False) -> None:
        if self._buffer is None:
            return

        timestamp = time()
        if self._is_connected and self._axis is not None:
            try:
                position_turns = degrees_to_turns(float(self._axis.get_position(POSITION_UNIT)))
                busy = float(self._axis.is_busy())
                homed = float(self._axis.is_homed())
            except MotionLibException:
                position_turns = np.nan
                busy = 0.0
                homed = 0.0
        else:
            position_turns = np.nan
            busy = 0.0
            homed = 0.0

        row_values = (
            timestamp,
            position_turns,
            self._target_turns,
            self._speed_turns_s,
            busy,
            homed,
            self._limit_max_turns,
            self._speed_max_turns_s,
            float(self._warning_mask),
            float(self._is_connected),
            self._serial,
            self._device_id,
            self._axis_count,
            self._axis_number,
            self._device_address,
            self._theoretical_resolution_turns,
            self._port_number,
            self._firmware_major,
            self._firmware_minor,
            self._firmware_build,
            float(self._command_error),
        )

        if not force and self._last_state == row_values:
            return

        row = np.array([row_values], dtype=float)
        self._buffer.write(row)
        self._last_state = row_values

    @register_ipc_command(ConnectZaberNmsCommand)
    def handle_connect(self) -> None:
        self.connect()
        self._write_state(force=True)

    @register_ipc_command(DisconnectZaberNmsCommand)
    def handle_disconnect(self) -> None:
        self.disconnect()
        self._write_state(force=True)

    @register_ipc_command(StopZaberNmsCommand)
    def handle_stop(self) -> None:
        if self._axis is None:
            return
        try:
            self._axis.stop(wait_until_idle=False)
            self._command_error = COMMAND_ERROR_NONE
        except MotionLibException:
            self._command_error = COMMAND_ERROR_STOP
        self._write_state(force=True)

    @register_ipc_command(MoveZaberNmsAbsoluteCommand)
    def handle_move_absolute(self, target_turns: float | None = None, speed_turns_s: float | None = None) -> None:
        if self._axis is None or target_turns is None:
            return
        try:
            clipped_speed_turns = None if speed_turns_s is None else float(np.clip(speed_turns_s, 0.001, self._speed_max_turns_s))
            self._target_turns = float(np.clip(target_turns, 0.0, self._limit_max_turns))
            self._speed_turns_s = clipped_speed_turns if clipped_speed_turns is not None else self._speed_turns_s
            self._axis.move_absolute(
                turns_to_degrees(self._target_turns),
                POSITION_UNIT,
                wait_until_idle=False,
                velocity=turns_to_degrees(self._speed_turns_s),
                velocity_unit=VELOCITY_UNIT,
            )
            self._command_error = COMMAND_ERROR_NONE
        except MotionLibException:
            self._command_error = COMMAND_ERROR_MOVE
        self._write_state(force=True)

    @register_ipc_command(JogZaberNmsRelativeCommand)
    def handle_jog_relative(self, delta_turns: float | None = None, speed_turns_s: float | None = None) -> None:
        if self._axis is None or delta_turns is None:
            return
        try:
            clipped_speed_turns = None if speed_turns_s is None else float(np.clip(speed_turns_s, 0.001, self._speed_max_turns_s))
            current_turns = degrees_to_turns(float(self._axis.get_position(POSITION_UNIT)))
            self._target_turns = float(np.clip(current_turns + delta_turns, 0.0, self._limit_max_turns))
            self._speed_turns_s = clipped_speed_turns if clipped_speed_turns is not None else self._speed_turns_s
            self._axis.move_relative(
                turns_to_degrees(delta_turns),
                POSITION_UNIT,
                wait_until_idle=False,
                velocity=turns_to_degrees(self._speed_turns_s),
                velocity_unit=VELOCITY_UNIT,
            )
            self._command_error = COMMAND_ERROR_NONE
        except MotionLibException:
            self._command_error = COMMAND_ERROR_JOG
        self._write_state(force=True)

    @register_ipc_command(SetZaberNmsMaxLimitCommand)
    def handle_set_max_limit(self, limit_turns: float | None = None) -> None:
        if self._axis is None or limit_turns is None:
            return
        try:
            self._axis.settings.set(SettingConstants.LIMIT_MAX, turns_to_degrees(limit_turns), POSITION_UNIT)
            self._limit_max_turns = degrees_to_turns(float(self._axis.settings.get(SettingConstants.LIMIT_MAX, POSITION_UNIT)))
            self._command_error = COMMAND_ERROR_NONE
        except MotionLibException:
            self._command_error = COMMAND_ERROR_SET_LIMIT
        self._write_state(force=True)

    @register_ipc_command(UseDefaultZaberNmsMaxLimitCommand)
    def handle_use_default_max_limit(self) -> None:
        if self._axis is None:
            return
        try:
            default_limit_degrees = float(self._axis.settings.get_default(SettingConstants.LIMIT_MAX, POSITION_UNIT))
            self._axis.settings.set(SettingConstants.LIMIT_MAX, default_limit_degrees, POSITION_UNIT)
            self._limit_max_turns = degrees_to_turns(float(self._axis.settings.get(SettingConstants.LIMIT_MAX, POSITION_UNIT)))
            self._command_error = COMMAND_ERROR_NONE
        except MotionLibException:
            self._command_error = COMMAND_ERROR_SET_LIMIT
        self._write_state(force=True)

    @register_ipc_command(ZeroZaberNmsPositionCommand)
    def handle_zero_position(self) -> None:
        if self._axis is None:
            return
        try:
            self._axis.settings.set("pos", 0.0, POSITION_UNIT)
            self._target_turns = 0.0
            self._command_error = COMMAND_ERROR_NONE
            self._refresh_metadata()
        except MotionLibException:
            self._command_error = COMMAND_ERROR_SET_LIMIT
        self._write_state(force=True)


class DeviceInfoDialog(QDialog):
    def __init__(self, latest_row: np.ndarray, parent: QWidget | None = None):
        super().__init__(parent)

        self.setWindowTitle("Device Info")
        self.setModal(True)

        port_value = "-" if np.isnan(latest_row[COL_PORT_NUMBER]) else f"COM{int(latest_row[COL_PORT_NUMBER])}"
        serial_value = "-" if np.isnan(latest_row[COL_SERIAL]) else f"{int(latest_row[COL_SERIAL])}"
        firmware_value = "-"
        if not np.isnan(latest_row[COL_FIRMWARE_MAJOR]):
            firmware_value = (
                f"{int(latest_row[COL_FIRMWARE_MAJOR])}."
                f"{int(latest_row[COL_FIRMWARE_MINOR])}."
                f"{int(latest_row[COL_FIRMWARE_BUILD])}"
            )

        layout = QVBoxLayout()
        identity_title = QLabel("Identity")
        layout.addWidget(identity_title)
        identity = QGridLayout()
        identity.addWidget(QLabel("Port"), 0, 0)
        identity.addWidget(QLabel(port_value), 0, 1)
        identity.addWidget(QLabel("Model"), 1, 0)
        identity.addWidget(QLabel(EXPECTED_MODEL), 1, 1)
        identity.addWidget(QLabel("Serial"), 2, 0)
        identity.addWidget(QLabel(serial_value), 2, 1)
        identity.addWidget(QLabel("Firmware"), 3, 0)
        identity.addWidget(QLabel(firmware_value), 3, 1)
        identity.addWidget(QLabel("Device ID"), 4, 0)
        identity.addWidget(QLabel(f"{int(latest_row[COL_DEVICE_ID])}" if not np.isnan(latest_row[COL_DEVICE_ID]) else "-"), 4, 1)
        layout.addLayout(identity)

        axis_title = QLabel("Axis")
        layout.addWidget(axis_title)
        axis = QGridLayout()
        axis.addWidget(QLabel("Device Address"), 0, 0)
        axis.addWidget(QLabel(f"{int(latest_row[COL_DEVICE_ADDRESS])}" if not np.isnan(latest_row[COL_DEVICE_ADDRESS]) else "-"), 0, 1)
        axis.addWidget(QLabel("Axis Count"), 1, 0)
        axis.addWidget(QLabel(f"{int(latest_row[COL_AXIS_COUNT])}" if not np.isnan(latest_row[COL_AXIS_COUNT]) else "-"), 1, 1)
        axis.addWidget(QLabel("Axis Number"), 2, 0)
        axis.addWidget(QLabel(f"{int(latest_row[COL_AXIS_NUMBER])}" if not np.isnan(latest_row[COL_AXIS_NUMBER]) else "-"), 2, 1)
        axis.addWidget(QLabel("Theoretical Resolution"), 3, 0)
        axis.addWidget(
            QLabel(f"{latest_row[COL_THEORETICAL_RESOLUTION_TURNS]:.9f} turns" if not np.isnan(latest_row[COL_THEORETICAL_RESOLUTION_TURNS]) else "-"),
            3,
            1,
        )
        layout.addLayout(axis)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        self.setLayout(layout)


class AdvancedControlsDialog(QDialog):
    def __init__(self, manager: magscope.UIManager, latest_row: np.ndarray, parent: QWidget | None = None):
        super().__init__(parent)

        self.manager = manager
        self.setWindowTitle("Advanced Controls")
        self.setModal(True)

        zero_button = QPushButton("Zero Position")
        zero_button.clicked.connect(self._send_zero_position)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)

        layout = QGridLayout()
        layout.addWidget(zero_button, 0, 0, 1, 2)
        layout.addWidget(close_button, 1, 1)
        self.setLayout(layout)

    def _send_zero_position(self) -> None:
        self.manager.send_ipc(ZeroZaberNmsPositionCommand())


class WarningDetailsDialog(QDialog):
    def __init__(self, warning_lines: list[str], parent: QWidget | None = None):
        super().__init__(parent)

        self.setWindowTitle("Warnings")
        self.setModal(True)

        layout = QVBoxLayout()
        for line in warning_lines:
            label = QLabel(line)
            label.setWordWrap(True)
            layout.addWidget(label)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
        self.setLayout(layout)


class ZaberNmsControls(magscope.ControlPanelBase):
    def __init__(self, manager: magscope.UIManager):
        super().__init__(title="Zaber NMS (Rotary Magnet Motor)", manager=manager)

        self._buffer = MatrixBuffer(create=False, locks=self.manager.locks, name=BUFFER_NAME)
        self.active_warning_lines: list[str] = []
        self.latest_row = np.full((21,), np.nan, dtype=float)

        self.warning_status_value = QLabel("OK")
        self.warning_summary_value = QLabel("No active warnings")
        self.warning_details_button = QPushButton("Warning Details")
        self.warning_details_button.clicked.connect(self.show_warning_details)
        self.warning_details_button.setEnabled(False)
        self.position_name_label = QLabel("Position")
        self.last_update_name_label = QLabel("Last Update")
        self.busy_name_label = QLabel("Busy")
        self.homed_name_label = QLabel("Homed")
        self.warnings_name_label = QLabel("Warnings")
        self.busy_value = QLabel("-")
        self.homed_value = QLabel("-")
        self.position_value = QLabel("-")
        self.last_update_value = QLabel("-")
        self.position_name_label.setStyleSheet("color: #5aa6ff;")
        self.position_value.setStyleSheet("color: #5aa6ff;")
        self.position_name_label.setFixedWidth(70)
        self.position_value.setFixedWidth(135)
        self.last_update_name_label.setFixedWidth(70)
        self.last_update_value.setFixedWidth(135)
        self.busy_name_label.setFixedWidth(50)
        self.busy_value.setFixedWidth(35)
        self.homed_name_label.setFixedWidth(50)
        self.homed_value.setFixedWidth(35)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(lambda: self.manager.send_ipc(ConnectZaberNmsCommand()))
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(lambda: self.manager.send_ipc(DisconnectZaberNmsCommand()))
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(lambda: self.manager.send_ipc(StopZaberNmsCommand()))
        self.stop_button.setStyleSheet(STOP_BUTTON_STYLE)
        self.device_info_button = QPushButton("Info")
        self.device_info_button.clicked.connect(self.show_device_info)
        self.advanced_controls_button = QPushButton("Advanced")
        self.advanced_controls_button.clicked.connect(self.show_advanced_controls)

        self.position_limit_max = DEFAULT_POSITION_UI_MAX
        self.speed_limit_max = DEFAULT_SPEED_UI_MAX
        self.jog_step_validator = QDoubleValidator(0.001, DEFAULT_POSITION_UI_MAX, 3, self)
        self.move_to_validator = QDoubleValidator(0.0, DEFAULT_POSITION_UI_MAX, 3, self)
        self.speed_validator = QDoubleValidator(0.001, DEFAULT_SPEED_UI_MAX, 3, self)
        self.jog_step_input = make_numeric_lineedit("1.000", self.jog_step_validator)
        self.speed_input = make_numeric_lineedit("1.000", self.speed_validator)
        self.move_to_input = make_numeric_lineedit("0.000", self.move_to_validator)
        self.max_speed_value = QLabel()
        self.update_limit_labels()

        self.jog_minus_button = QPushButton("-")
        self.jog_minus_button.clicked.connect(lambda: self.send_jog(-1.0))
        self.jog_plus_button = QPushButton("+")
        self.jog_plus_button.clicked.connect(lambda: self.send_jog(1.0))
        self.move_to_button = QPushButton("Move")
        self.move_to_button.clicked.connect(self.send_move_to)

        clamp_button_to_text(self.stop_button)
        clamp_button_to_text(self.device_info_button)
        clamp_button_to_text(self.advanced_controls_button)

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
        top_status_row.addWidget(self.busy_name_label)
        top_status_row.addWidget(self.busy_value)
        top_status_row.addStretch(1)
        status.addLayout(top_status_row)

        middle_status_row = QHBoxLayout()
        middle_status_row.addWidget(self.last_update_name_label)
        middle_status_row.addWidget(self.last_update_value)
        middle_status_row.addSpacing(5)
        middle_status_row.addWidget(self.homed_name_label)
        middle_status_row.addWidget(self.homed_value)
        middle_status_row.addStretch(1)
        status.addLayout(middle_status_row)

        warning_status_row = QHBoxLayout()
        warning_status_row.addWidget(self.warnings_name_label)
        warning_status_row.addWidget(self.warning_status_value)
        warning_status_row.addWidget(self.warning_summary_value, 1)
        warning_status_row.addWidget(self.warning_details_button)
        status.addLayout(warning_status_row)
        outer.addLayout(status)

        status_divider = QFrame()
        status_divider.setFrameShape(QFrame.Shape.HLine)
        status_divider.setLineWidth(1)
        outer.addWidget(status_divider)

        motion_label = QLabel("Motion")
        motion_label.setStyleSheet("font-weight: bold;")
        outer.addWidget(motion_label)

        motion = QGridLayout()
        motion.setColumnStretch(1, 1)
        motion.addWidget(QLabel("Move"), 0, 0)
        motion.addWidget(self.move_to_input, 0, 1)
        motion.addWidget(self.move_to_button, 0, 2)
        motion.addWidget(QLabel("Jog"), 1, 0)
        motion.addWidget(self.jog_step_input, 1, 1)
        motion.addWidget(self.jog_minus_button, 1, 2)
        motion.addWidget(self.jog_plus_button, 1, 3)
        motion.addWidget(QLabel("Speed"), 2, 0)
        motion.addWidget(self.speed_input, 2, 1)
        motion.addWidget(self.max_speed_value, 3, 0, 1, 4)
        outer.addLayout(motion)

        motion_divider = QFrame()
        motion_divider.setFrameShape(QFrame.Shape.HLine)
        motion_divider.setLineWidth(1)
        outer.addWidget(motion_divider)

        footer = QHBoxLayout()
        footer.addWidget(self.stop_button)
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

        finite_rows = np.isfinite(data[:, COL_TIMESTAMP])
        if not np.any(finite_rows):
            return

        latest = data[finite_rows][-1, :]
        self.latest_row = latest

        connected = bool(round(latest[COL_CONNECTED])) if np.isfinite(latest[COL_CONNECTED]) else False
        busy = bool(round(latest[COL_BUSY])) if np.isfinite(latest[COL_BUSY]) else False
        homed = bool(round(latest[COL_HOMED])) if np.isfinite(latest[COL_HOMED]) else False
        position_text = "-" if np.isnan(latest[COL_POSITION]) else f"{latest[COL_POSITION]:.3f} turns"

        self.position_value.setText(position_text)
        self.busy_value.setText("Yes" if busy else "No")
        self.homed_value.setText("Yes" if homed else "No")
        self.last_update_value.setText(datetime.fromtimestamp(latest[COL_TIMESTAMP]).strftime("%H:%M:%S.%f")[:-5])

        self.update_limits_from_row(latest)
        self.update_warning_state(latest, homed)

        self.disconnect_button.setEnabled(connected)
        self.stop_button.setEnabled(connected)
        self.device_info_button.setEnabled(connected)
        self.advanced_controls_button.setEnabled(connected)
        self.jog_minus_button.setEnabled(connected)
        self.jog_plus_button.setEnabled(connected)
        self.move_to_button.setEnabled(connected)

    def update_limits_from_row(self, row: np.ndarray) -> None:
        if np.isfinite(row[COL_LIMIT_MAX]):
            self.position_limit_max = float(row[COL_LIMIT_MAX])
            self.jog_step_validator.setTop(self.position_limit_max)
            self.move_to_validator.setTop(self.position_limit_max)
            jog_value = self._parse_lineedit_float(self.jog_step_input)
            if jog_value is not None and jog_value > self.position_limit_max:
                self.jog_step_input.setText(f"{self.position_limit_max:.3f}")
            move_value = self._parse_lineedit_float(self.move_to_input)
            if move_value is not None and move_value > self.position_limit_max:
                self.move_to_input.setText(f"{self.position_limit_max:.3f}")
        if np.isfinite(row[COL_SPEED_MAX]):
            self.speed_limit_max = float(row[COL_SPEED_MAX])
            self.speed_validator.setTop(self.speed_limit_max)
            speed_value = self._parse_lineedit_float(self.speed_input)
            if speed_value is not None and speed_value > self.speed_limit_max:
                self.speed_input.setText(f"{self.speed_limit_max:.3f}")
        self.update_limit_labels()

    def update_limit_labels(self) -> None:
        self.max_speed_value.setText(f"Max Speed: {self.speed_limit_max:.3f} turns/s")

    def _parse_lineedit_float(self, lineedit: QLineEdit) -> float | None:
        text = lineedit.text().strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def update_warning_state(self, row: np.ndarray, homed: bool) -> None:
        warning_lines = [flag.replace("_", " ").title() for flag in decode_warning_mask(int(row[COL_WARNING_MASK]))] if np.isfinite(row[COL_WARNING_MASK]) else []
        command_error_code = int(row[COL_COMMAND_ERROR]) if np.isfinite(row[COL_COMMAND_ERROR]) else COMMAND_ERROR_NONE
        command_error = COMMAND_ERROR_LABELS.get(command_error_code)
        if not homed and bool(round(row[COL_CONNECTED])):
            warning_lines.insert(0, "Device is not homed")
        if command_error is not None:
            warning_lines.insert(0, command_error)

        self.active_warning_lines = warning_lines
        if not warning_lines:
            self.warning_status_value.setText("OK")
            self.warning_summary_value.setText("No active warnings")
            self.warning_details_button.setEnabled(False)
            return

        severity = "FAULT" if command_error is not None or len(warning_lines) > 1 else "WARNING"
        self.warning_status_value.setText(severity)
        self.warning_summary_value.setText(warning_lines[0])
        self.warning_details_button.setEnabled(True)

    def send_jog(self, direction: float) -> None:
        jog_step = self._parse_lineedit_float(self.jog_step_input)
        speed = self._parse_lineedit_float(self.speed_input)
        if jog_step is None or speed is None:
            return
        self.manager.send_ipc(
            JogZaberNmsRelativeCommand(
                delta_turns=direction * jog_step,
                speed_turns_s=speed,
            )
        )

    def send_move_to(self) -> None:
        target = self._parse_lineedit_float(self.move_to_input)
        speed = self._parse_lineedit_float(self.speed_input)
        if target is None or speed is None:
            return
        self.manager.send_ipc(
            MoveZaberNmsAbsoluteCommand(
                target_turns=target,
                speed_turns_s=speed,
            )
        )

    def show_device_info(self) -> None:
        if not np.isfinite(self.latest_row[COL_TIMESTAMP]):
            return
        dialog = DeviceInfoDialog(self.latest_row, self)
        dialog.exec()

    def show_warning_details(self) -> None:
        if not self.active_warning_lines:
            return
        dialog = WarningDetailsDialog(self.active_warning_lines, self)
        dialog.exec()

    def show_advanced_controls(self) -> None:
        if not np.isfinite(self.latest_row[COL_TIMESTAMP]):
            return
        dialog = AdvancedControlsDialog(self.manager, self.latest_row, self)
        dialog.exec()


class ZaberNmsPositionPlot(magscope.TimeSeriesPlotBase):
    def __init__(self, buffer_name: str = BUFFER_NAME):
        super().__init__(buffer_name, "Rotary Motor (turns)")
        self.position_line = None
        self.target_line = None

    def setup(self):
        super().setup()
        self.position_line, self.target_line = self.axes.plot([], [], "c", [], [], "y")

    def update(self):
        data = self.buffer.peak_unsorted()
        if data.size == 0:
            return

        t = data[:, COL_TIMESTAMP]
        position = data[:, COL_POSITION]
        target = data[:, COL_TARGET]
        selection = np.isfinite(t) & np.isfinite(position)
        if not np.any(selection):
            return

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

        self.position_line.set_xdata(timepoints)
        self.position_line.set_ydata(position)
        self.target_line.set_xdata(timepoints)
        self.target_line.set_ydata(target)

        xmin_dt, xmax_dt = [datetime.fromtimestamp(t_) if t_ else None for t_ in (xmin, xmax)]
        self.axes.autoscale()
        self.axes.autoscale_view()
        self.axes.set_xlim(xmin=xmin_dt, xmax=xmax_dt)
        self.axes.set_ylim(ymin=ymin, ymax=ymax)
        self.axes.relim()


if __name__ == "__main__":
    scope = magscope.MagScope(verbose=True)
    scope.ui_manager.n_windows = 1

    scope.add_hardware(ZaberNmsMotor())
    scope.add_control(ZaberNmsControls, column=0)
    scope.add_timeplot(ZaberNmsPositionPlot())

    scope.start()
