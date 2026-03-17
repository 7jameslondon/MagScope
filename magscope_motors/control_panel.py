from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from magscope import ControlPanelBase, MatrixBuffer, TimeSeriesPlotBase
from magscope.ui.widgets import LabeledLineEdit

from .commands import (
    ConnectMotorsCommand,
    DisconnectMotorsCommand,
    LoadForceCalibrantCommand,
    MoveLinearToForceCommand,
    MoveLinearAbsoluteCommand,
    MoveLinearRelativeCommand,
    MoveObjectiveAbsoluteCommand,
    MoveObjectiveRelativeCommand,
    MoveRotaryAbsoluteCommand,
    MoveRotaryRelativeCommand,
    RunLinearForceRampCommand,
    SetMotorArmedCommand,
    SetLinearUiSpeedCommand,
    StopAllMotorsCommand,
    UnloadForceCalibrantCommand,
)
from .force_calibration import ForceCalibrantError, ForceCalibrantModel


AXIS_INDEX = {"objective": 0.0, "linear": 1.0, "rotary": 2.0}
LINEAR_USER_MIN_MM = 0.0
LINEAR_USER_MAX_MM = 34.5
OBJECTIVE_MIN_NM = 0.0
OBJECTIVE_MID_NM = 50000.0
OBJECTIVE_MAX_NM = 100000.0
FORCE_POSITION_TOL_PN = 1e-3
FAULT_CODE_MESSAGES = {
    0: "",
    1: "Not connected",
    2: "Not armed",
    3: "Legacy arm-timeout code (unused)",
    4: "Hard limit",
    5: "Session window",
    6: "Legacy step-cap code (unused)",
    7: "Speed cap",
    8: "Adapter error",
    9: "Disabled in settings",
    10: "Unknown axis",
    11: "Permission required",
}

_SHARED_FORCE_CALIBRANT_MODEL = ForceCalibrantModel()


def _shared_force_calibrant_model() -> ForceCalibrantModel:
    return _SHARED_FORCE_CALIBRANT_MODEL


def _latest_rows_by_axis(data: np.ndarray) -> dict[str, np.ndarray]:
    if data.size == 0 or data.ndim != 2 or data.shape[1] < 8:
        return {}
    latest: dict[str, np.ndarray] = {}
    for axis, axis_id in AXIS_INDEX.items():
        mask = np.isfinite(data[:, 0]) & (data[:, 1] == axis_id)
        if not np.any(mask):
            continue
        axis_rows = data[mask]
        latest[axis] = axis_rows[np.argmax(axis_rows[:, 0])]
    return latest


class _MotorBufferMixin:
    def __init__(self):
        self._buffer: MatrixBuffer | None = None

    def _read_latest_rows(self) -> dict[str, np.ndarray]:
        if self._buffer is None:
            try:
                self._buffer = MatrixBuffer(
                    create=False,
                    locks=self.manager.locks,
                    name="MotorManager",
                )
            except Exception:
                return {}

        try:
            data = self._buffer.peak_unsorted().copy()
        except Exception:
            return {}
        return _latest_rows_by_axis(data)


class MotorControlPanel(ControlPanelBase, _MotorBufferMixin):
    """Shared motor controls and aggregate status."""

    def __init__(self, manager):
        super().__init__(manager=manager, title="Motors", collapsed_by_default=False)
        _MotorBufferMixin.__init__(self)

        note = QLabel("Shared controls for objective, linear, and rotary motors.")
        note.setWordWrap(True)
        self.layout().addWidget(note)

        controls_row = QHBoxLayout()
        self.layout().addLayout(controls_row)

        connect_button = QPushButton("Connect")
        connect_button.clicked.connect(self._connect_motors)  # type: ignore
        controls_row.addWidget(connect_button)

        disconnect_button = QPushButton("Disconnect")
        disconnect_button.clicked.connect(self._disconnect_motors)  # type: ignore
        controls_row.addWidget(disconnect_button)

        arm_button = QPushButton("Arm")
        arm_button.clicked.connect(lambda: self._set_armed(True))  # type: ignore
        controls_row.addWidget(arm_button)

        disarm_button = QPushButton("Disarm")
        disarm_button.clicked.connect(lambda: self._set_armed(False))  # type: ignore
        controls_row.addWidget(disarm_button)

        stop_button = QPushButton("STOP ALL")
        stop_button.clicked.connect(self._stop_all)  # type: ignore
        controls_row.addWidget(stop_button)

        self.global_status_label = QLabel("Connected axes: 0/3 | Armed: no")
        self.layout().addWidget(self.global_status_label)

        self.shared_fault_label = QLabel("")
        self.shared_fault_label.setWordWrap(True)
        self.layout().addWidget(self.shared_fault_label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_from_buffer)  # type: ignore
        self._timer.setInterval(150)
        self._timer.start()

    def _connect_motors(self):
        self.manager.send_ipc(ConnectMotorsCommand())

    def _disconnect_motors(self):
        self.manager.send_ipc(DisconnectMotorsCommand())

    def _set_armed(self, value: bool):
        self.manager.send_ipc(SetMotorArmedCommand(value=value))

    def _stop_all(self):
        self.manager.send_ipc(StopAllMotorsCommand())

    def _refresh_from_buffer(self):
        latest_rows = self._read_latest_rows()
        if not latest_rows:
            return

        connected_count = 0
        armed = False
        faults: list[str] = []
        for axis, row in latest_rows.items():
            connected_count += int(round(float(row[5])))
            armed = armed or bool(round(float(row[6])))
            fault_code = int(round(float(row[7])))
            fault_text = FAULT_CODE_MESSAGES.get(fault_code, f"Fault {fault_code}")
            if fault_text:
                faults.append(f"{axis.title()}: {fault_text}")

        self.global_status_label.setText(
            f"Connected axes: {connected_count}/3 | Armed: {'yes' if armed else 'no'}"
        )
        self.shared_fault_label.setText(" | ".join(faults))


class _AxisMotorControlPanel(ControlPanelBase, _MotorBufferMixin):
    axis: str = ""
    units: str = ""

    def __init__(self, manager, *, title: str, axis: str, units: str):
        super().__init__(manager=manager, title=title, collapsed_by_default=True)
        _MotorBufferMixin.__init__(self)
        self.axis = axis
        self.units = units

        self.state_text = QLabel("Disconnected")
        state_row = QHBoxLayout()
        state_row.addWidget(QLabel("State:"))
        state_row.addWidget(self.state_text)
        state_row.addStretch(1)
        self.layout().addLayout(state_row)

        self.relative_input = LabeledLineEdit(label_text=f"Relative ({self.units})", widths=(105, 120), default="")
        self.layout().addWidget(self.relative_input)

        self.absolute_input = LabeledLineEdit(label_text=f"Absolute ({self.units})", widths=(105, 120), default="")
        self.layout().addWidget(self.absolute_input)

        self.speed_input = LabeledLineEdit(label_text=f"Speed ({self.units}/s)", widths=(105, 120), default="")
        self.layout().addWidget(self.speed_input)

        move_row = QHBoxLayout()
        self.layout().addLayout(move_row)
        relative_button = QPushButton("Move Relative")
        relative_button.clicked.connect(self._move_relative)  # type: ignore
        move_row.addWidget(relative_button)
        absolute_button = QPushButton("Move Absolute")
        absolute_button.clicked.connect(self._move_absolute)  # type: ignore
        move_row.addWidget(absolute_button)

        self.fault_text = QLabel("")
        self.fault_text.setWordWrap(True)
        self.layout().addWidget(self.fault_text)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_from_buffer)  # type: ignore
        self._timer.setInterval(150)
        self._timer.start()

    def _parse_float(self, input_widget: LabeledLineEdit, field: str) -> float | None:
        text = input_widget.lineedit.text().strip()
        if not text:
            self.fault_text.setText(f"Missing {field} value")
            return None
        try:
            value = float(text)
        except ValueError:
            self.fault_text.setText(f"Invalid {field} value")
            return None
        finally:
            input_widget.lineedit.setText("")
        return value

    def _parse_speed(self) -> tuple[bool, float | None]:
        text = self.speed_input.lineedit.text().strip()
        if not text:
            return True, None
        try:
            value = float(text)
        except ValueError:
            self.fault_text.setText("Invalid speed value")
            self.speed_input.lineedit.setText("")
            return False, None
        self.speed_input.lineedit.setText("")
        return True, value

    def _move_relative(self):
        delta = self._parse_float(self.relative_input, "relative move")
        if delta is None:
            return
        speed_ok, speed = self._parse_speed()
        if not speed_ok:
            return
        self.manager.send_ipc(self._build_relative_command(delta, speed))

    def _move_absolute(self):
        target = self._parse_float(self.absolute_input, "absolute move")
        if target is None:
            return
        speed_ok, speed = self._parse_speed()
        if not speed_ok:
            return
        self.manager.send_ipc(self._build_absolute_command(target, speed))

    def _build_relative_command(self, value: float, speed: float | None) -> Any:
        raise NotImplementedError

    def _build_absolute_command(self, value: float, speed: float | None) -> Any:
        raise NotImplementedError

    def _refresh_from_buffer(self):
        latest_rows = self._read_latest_rows()
        row = latest_rows.get(self.axis)
        if row is None:
            return
        connected = bool(round(float(row[5])))
        fault_code = int(round(float(row[7])))
        fault_message = FAULT_CODE_MESSAGES.get(fault_code, f"Fault {fault_code}")

        self.state_text.setText("Connected" if connected else "Disconnected")
        self.fault_text.setText(fault_message)


class ObjectiveMotorControlPanel(ControlPanelBase, _MotorBufferMixin):
    def __init__(self, manager):
        super().__init__(manager=manager, title="Objective Motor", collapsed_by_default=True)
        _MotorBufferMixin.__init__(self)

        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(4)
        self.layout().addLayout(target_row)
        target_row.addWidget(QLabel("Target (nm):"))

        self.target_input = QLineEdit("")
        self.target_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.target_input.returnPressed.connect(self._move_target_absolute)  # type: ignore
        target_row.addWidget(self.target_input, 1)

        step_row = QHBoxLayout()
        step_row.setContentsMargins(0, 0, 0, 0)
        step_row.setSpacing(4)
        self.layout().addLayout(step_row)
        step_row.addWidget(QLabel("Step (nm):"))

        self.step_down_button = QPushButton("-")
        self.step_down_button.setFixedWidth(50)
        self.step_down_button.clicked.connect(lambda: self._step_move(direction=-1.0))  # type: ignore
        step_row.addWidget(self.step_down_button)

        self.step_input = QLineEdit("1000")
        self.step_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        step_row.addWidget(self.step_input, 1)

        self.step_up_button = QPushButton("+")
        self.step_up_button.setFixedWidth(50)
        self.step_up_button.clicked.connect(lambda: self._step_move(direction=1.0))  # type: ignore
        step_row.addWidget(self.step_up_button)

        quick_row = QHBoxLayout()
        quick_row.setContentsMargins(0, 0, 0, 0)
        quick_row.setSpacing(4)
        self.layout().addLayout(quick_row)

        self.min_button = QPushButton("Min (0)")
        self.min_button.clicked.connect(lambda: self._move_quick_target(OBJECTIVE_MIN_NM))  # type: ignore
        quick_row.addWidget(self.min_button, 1)

        self.mid_button = QPushButton("Mid (50000)")
        self.mid_button.clicked.connect(lambda: self._move_quick_target(OBJECTIVE_MID_NM))  # type: ignore
        quick_row.addWidget(self.mid_button, 1)

        self.max_button = QPushButton("Max (100000)")
        self.max_button.clicked.connect(lambda: self._move_quick_target(OBJECTIVE_MAX_NM))  # type: ignore
        quick_row.addWidget(self.max_button, 1)

        self.fault_text = QLabel("")
        self.fault_text.setWordWrap(True)
        self.fault_text.setVisible(False)
        self.layout().addWidget(self.fault_text)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_from_buffer)  # type: ignore
        self._timer.setInterval(150)
        self._timer.start()

    def _set_fault_text(self, text: str) -> None:
        message = str(text).strip()
        self.fault_text.setText(message)
        self.fault_text.setVisible(bool(message))

    def _parse_float(self, lineedit: QLineEdit, label: str, *, clear: bool) -> float | None:
        text = lineedit.text().strip()
        if clear:
            lineedit.setText("")
        if not text:
            self._set_fault_text(f"Missing {label}")
            return None
        try:
            return float(text)
        except ValueError:
            self._set_fault_text(f"Invalid {label}")
            return None

    def _move_target_absolute(self) -> None:
        target_nm = self._parse_float(self.target_input, "target", clear=True)
        if target_nm is None:
            return
        self._submit_absolute_move(target_nm)

    def _step_move(self, *, direction: float) -> None:
        step_nm = self._parse_float(self.step_input, "step", clear=False)
        if step_nm is None:
            return
        if step_nm <= 0:
            self._set_fault_text("Step must be > 0")
            return
        delta_nm = direction * step_nm
        self.manager.send_ipc(MoveObjectiveRelativeCommand(delta_nm=delta_nm, speed_nm_s=None, source="ui"))

    def _move_quick_target(self, target_nm: float) -> None:
        self._submit_absolute_move(target_nm)

    def _submit_absolute_move(self, target_nm: float) -> None:
        self.manager.send_ipc(MoveObjectiveAbsoluteCommand(position_nm=target_nm, speed_nm_s=None, source="ui"))

    def _refresh_from_buffer(self) -> None:
        latest_rows = self._read_latest_rows()
        row = latest_rows.get("objective")
        if row is None:
            return
        fault_code = int(round(float(row[7])))
        fault_message = FAULT_CODE_MESSAGES.get(fault_code, f"Fault {fault_code}")
        self._set_fault_text(fault_message)


class LinearMotorControlPanel(ControlPanelBase, _MotorBufferMixin):
    def __init__(self, manager):
        super().__init__(manager=manager, title="Linear Motor", collapsed_by_default=True)
        _MotorBufferMixin.__init__(self)
        self._actual_mm = 0.0

        speed_row = QHBoxLayout()
        speed_row.setContentsMargins(0, 0, 0, 0)
        speed_row.setSpacing(4)
        self.layout().addLayout(speed_row)
        speed_row.addWidget(QLabel("Speed (mm/s):"))
        self.speed_input = QLineEdit("0.2")
        self.speed_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.speed_input.editingFinished.connect(self._publish_linear_speed_from_ui)  # type: ignore
        speed_row.addWidget(self.speed_input, 1)

        target_zero_row = QHBoxLayout()
        target_zero_row.setContentsMargins(0, 0, 0, 0)
        target_zero_row.setSpacing(4)
        self.layout().addLayout(target_zero_row)
        target_zero_row.addWidget(QLabel("Target from Zero (mm):"))
        self.target_from_zero_input = QLineEdit("")
        self.target_from_zero_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        target_zero_row.addWidget(self.target_from_zero_input, 1)
        self.target_from_zero_input.editingFinished.connect(self._move_target_from_zero)  # type: ignore

        target_max_row = QHBoxLayout()
        target_max_row.setContentsMargins(0, 0, 0, 0)
        target_max_row.setSpacing(4)
        self.layout().addLayout(target_max_row)
        target_max_row.addWidget(QLabel("Target from Max (mm):"))
        self.target_from_max_input = QLineEdit("")
        self.target_from_max_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        target_max_row.addWidget(self.target_from_max_input, 1)
        self.target_from_max_input.editingFinished.connect(self._move_target_from_max)  # type: ignore

        step_row = QHBoxLayout()
        self.layout().addLayout(step_row)
        step_row.addWidget(QLabel("Step (mm)"))
        self.step_down_button = QPushButton("\u2193")
        self.step_down_button.setFixedSize(50, 24)
        self.step_down_button.clicked.connect(lambda: self._step_move(direction=-1.0))  # type: ignore
        step_row.addWidget(self.step_down_button)
        self.step_input = QLineEdit("0.1")
        self.step_input.setFixedWidth(100)
        step_row.addWidget(self.step_input)
        self.step_up_button = QPushButton("\u2191")
        self.step_up_button.setFixedSize(50, 24)
        self.step_up_button.clicked.connect(lambda: self._step_move(direction=1.0))  # type: ignore
        step_row.addWidget(self.step_up_button)
        step_row.addStretch(1)

        self.fault_text = QLabel("")
        self.fault_text.setWordWrap(True)
        self.fault_text.setVisible(False)
        self.layout().addWidget(self.fault_text)

        min_max_stop_group = QVBoxLayout()
        min_max_stop_group.setContentsMargins(0, 0, 0, 0)
        min_max_stop_group.setSpacing(0)
        self.layout().addLayout(min_max_stop_group)

        min_max_row = QHBoxLayout()
        min_max_row.setContentsMargins(0, 0, 0, 0)
        min_max_row.setSpacing(4)
        min_max_stop_group.addLayout(min_max_row)
        self.min_button = QPushButton(f"Min ({LINEAR_USER_MIN_MM:g})")
        self.min_button.clicked.connect(self._move_min)  # type: ignore
        min_max_row.addWidget(self.min_button)
        self.max_button = QPushButton(f"Max ({LINEAR_USER_MAX_MM:g})")
        self.max_button.clicked.connect(self._move_max)  # type: ignore
        min_max_row.addWidget(self.max_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self._stop_all)  # type: ignore
        min_max_stop_group.addWidget(self.stop_button)

        QTimer.singleShot(0, self._publish_linear_speed_from_ui)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_from_buffer)  # type: ignore
        self._timer.setInterval(150)
        self._timer.start()

    def _set_fault_text(self, text: str) -> None:
        message = str(text).strip()
        self.fault_text.setText(message)
        self.fault_text.setVisible(bool(message))

    def _parse_optional_speed(self) -> tuple[bool, float | None]:
        text = self.speed_input.text().strip()
        if not text:
            self.manager.send_ipc(SetLinearUiSpeedCommand(speed_mm_s=None))
            return True, None
        try:
            speed = float(text)
        except ValueError:
            self._set_fault_text("Invalid speed value")
            return False, None
        if speed <= 0:
            self._set_fault_text("Speed must be > 0")
            return False, None
        self.manager.send_ipc(SetLinearUiSpeedCommand(speed_mm_s=speed))
        return True, speed

    def _publish_linear_speed_from_ui(self) -> None:
        text = self.speed_input.text().strip()
        if not text:
            self.manager.send_ipc(SetLinearUiSpeedCommand(speed_mm_s=None))
            return
        try:
            speed = float(text)
        except ValueError:
            self._set_fault_text("Invalid speed value")
            return
        if speed <= 0:
            self._set_fault_text("Speed must be > 0")
            return
        self.manager.send_ipc(SetLinearUiSpeedCommand(speed_mm_s=speed))

    def _parse_float(self, lineedit: QLineEdit, label: str, *, clear: bool) -> float | None:
        text = lineedit.text().strip()
        if clear:
            lineedit.setText("")
        if not text:
            self._set_fault_text(f"Missing {label}")
            return None
        try:
            return float(text)
        except ValueError:
            self._set_fault_text(f"Invalid {label}")
            return None

    def _validate_linear_target(self, target_mm: float) -> float | None:
        if target_mm < LINEAR_USER_MIN_MM or target_mm > LINEAR_USER_MAX_MM:
            self._set_fault_text(
                f"Target must be within {LINEAR_USER_MIN_MM:g} to {LINEAR_USER_MAX_MM:g} mm"
            )
            return None
        return target_mm

    def _submit_linear_absolute_move(self, target_mm: float) -> None:
        bounded = self._validate_linear_target(target_mm)
        if bounded is None:
            return
        speed_ok, speed = self._parse_optional_speed()
        if not speed_ok:
            return
        self.manager.send_ipc(MoveLinearAbsoluteCommand(position_mm=bounded, speed_mm_s=speed, source="ui"))

    def _move_target_from_zero(self) -> None:
        value = self._parse_float(self.target_from_zero_input, "target from zero", clear=True)
        if value is None:
            return
        self._submit_linear_absolute_move(value)

    def _move_target_from_max(self) -> None:
        value = self._parse_float(self.target_from_max_input, "target from max", clear=True)
        if value is None:
            return
        target = LINEAR_USER_MAX_MM - value
        self._submit_linear_absolute_move(target)

    def _step_move(self, *, direction: float) -> None:
        step_value = self._parse_float(self.step_input, "step", clear=False)
        if step_value is None:
            return
        if step_value <= 0:
            self._set_fault_text("Step must be > 0")
            return
        target = self._actual_mm + (direction * step_value)
        self._submit_linear_absolute_move(target)

    def _move_min(self) -> None:
        self._submit_linear_absolute_move(LINEAR_USER_MIN_MM)

    def _move_max(self) -> None:
        self._submit_linear_absolute_move(LINEAR_USER_MAX_MM)

    def _stop_all(self) -> None:
        self.manager.send_ipc(StopAllMotorsCommand())

    def _refresh_from_buffer(self) -> None:
        latest_rows = self._read_latest_rows()
        row = latest_rows.get("linear")
        if row is None:
            return
        self._actual_mm = float(row[2])
        fault_code = int(round(float(row[7])))
        self._set_fault_text(FAULT_CODE_MESSAGES.get(fault_code, f"Fault {fault_code}"))


class ForceCalibrationControlPanel(ControlPanelBase, _MotorBufferMixin):
    no_file_str = "No force calibrant selected"

    def __init__(self, manager):
        super().__init__(manager=manager, title="Force Calibration", collapsed_by_default=True)
        _MotorBufferMixin.__init__(self)
        self._force_calibrant = _shared_force_calibrant_model()
        self._selected_calibrant_path: str | None = None
        self._pending_force_ramp_command: RunLinearForceRampCommand | None = None
        self._pending_force_ramp_start_pn: float | None = None

        load_button = QPushButton("Load Force Calibrant")
        load_button.clicked.connect(self._load_force_calibrant)  # type: ignore
        self.layout().addWidget(load_button)

        self.filepath_textedit = QTextEdit(self.no_file_str)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filepath_textedit.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filepath_textedit.setFixedHeight(40)
        self.filepath_textedit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.filepath_textedit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout().addWidget(self.filepath_textedit)

        plot_button = QPushButton("Plot")
        plot_button.clicked.connect(self._plot_calibrant)  # type: ignore
        self.layout().addWidget(plot_button)

        self.layout().addWidget(self._divider())
        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(4)
        target_row.addStretch(1)
        target_row.addWidget(QLabel("Target(pN)"))
        self.target_input = QLineEdit("")
        self.target_input.setFixedWidth(140)
        self.target_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.target_input.returnPressed.connect(self._move_to_force_target)  # type: ignore
        target_row.addWidget(self.target_input)
        target_row.addStretch(1)
        self.layout().addLayout(target_row)

        self.layout().addWidget(self._divider())
        ramp_buttons = QHBoxLayout()
        self.layout().addLayout(ramp_buttons)

        ramp_ab = QPushButton("Ramp A->B")
        ramp_ab.clicked.connect(lambda: self._run_force_ramp(forward=True))  # type: ignore
        ramp_buttons.addWidget(ramp_ab)

        ramp_ba = QPushButton("Ramp A<-B")
        ramp_ba.clicked.connect(lambda: self._run_force_ramp(forward=False))  # type: ignore
        ramp_buttons.addWidget(ramp_ba)

        ab_row = QHBoxLayout()
        self.layout().addLayout(ab_row)
        self.a_input = LabeledLineEdit(label_text="A(pN)", widths=(46, 88), default="")
        self.b_input = LabeledLineEdit(label_text="B(pN)", widths=(46, 88), default="")
        ab_row.addWidget(self.a_input)
        ab_row.addWidget(self.b_input)

        rate_row = QHBoxLayout()
        rate_row.setContentsMargins(0, 0, 0, 0)
        rate_row.setSpacing(4)
        rate_row.addStretch(1)
        rate_row.addWidget(QLabel("Rate(pN/s)"))
        self.rate_input = QLineEdit("")
        self.rate_input.setFixedWidth(140)
        self.rate_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        rate_row.addWidget(self.rate_input)
        rate_row.addStretch(1)
        self.layout().addLayout(rate_row)

        self.calibrant_status_label = QLabel("")
        self.calibrant_status_label.setWordWrap(True)
        self.layout().addWidget(self.calibrant_status_label)

        self.fault_label = QLabel("")
        self.fault_label.setWordWrap(True)
        self.layout().addWidget(self.fault_label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_from_buffer)  # type: ignore
        self._timer.setInterval(150)
        self._timer.start()

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _set_calibrant_status(self, text: str) -> None:
        self.calibrant_status_label.setText(str(text).strip())

    def _parse_float(self, widget: LabeledLineEdit, label: str, *, clear: bool = False) -> float | None:
        text = widget.lineedit.text().strip()
        if clear:
            widget.lineedit.setText("")
        if not text:
            self._set_calibrant_status(f"Missing {label}")
            return None
        try:
            return float(text)
        except ValueError:
            self._set_calibrant_status(f"Invalid {label}")
            return None

    def _parse_lineedit_float(self, lineedit: QLineEdit, label: str, *, clear: bool = False) -> float | None:
        text = lineedit.text().strip()
        if clear:
            lineedit.setText("")
        if not text:
            self._set_calibrant_status(f"Missing {label}")
            return None
        try:
            return float(text)
        except ValueError:
            self._set_calibrant_status(f"Invalid {label}")
            return None

    def _load_force_calibrant(self) -> None:
        self._clear_pending_force_ramp()
        start_dir = Path(self._selected_calibrant_path).parent if self._selected_calibrant_path else Path.home()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Force Calibrant",
            str(start_dir),
            "Force Calibrant (*.txt);;Text files (*.txt);;All files (*)",
        )

        if not path:
            self._force_calibrant.unload()
            self._selected_calibrant_path = None
            self.filepath_textedit.setText(self.no_file_str)
            self._set_calibrant_status("Force calibrant unloaded")
            self.manager.send_ipc(UnloadForceCalibrantCommand(source="ui"))
            return

        try:
            self._force_calibrant.load(path)
        except ForceCalibrantError as exc:
            self._force_calibrant.unload()
            self._selected_calibrant_path = None
            self.filepath_textedit.setText(self.no_file_str)
            self._set_calibrant_status(str(exc))
            self.manager.send_ipc(UnloadForceCalibrantCommand(source="ui"))
            return

        self._selected_calibrant_path = path
        self.filepath_textedit.setText(path)
        data = self._force_calibrant.data
        if data is None:
            self._set_calibrant_status("Failed to read calibrant data")
            self.manager.send_ipc(UnloadForceCalibrantCommand(source="ui"))
            return

        force_min = float(np.min(data[:, 1]))
        force_max = float(np.max(data[:, 1]))
        self._set_calibrant_status(
            f"Loaded {data.shape[0]} rows ({force_min:.3f} to {force_max:.3f} pN)"
        )
        self.manager.send_ipc(LoadForceCalibrantCommand(path=path, source="ui"))

    def _plot_calibrant(self) -> None:
        if not self._force_calibrant.is_loaded():
            self._set_calibrant_status("No force calibrant selected")
            return
        try:
            self._force_calibrant.plot()
        except ForceCalibrantError as exc:
            self._set_calibrant_status(str(exc))

    def _move_to_force_target(self) -> None:
        self._clear_pending_force_ramp()
        target_pn = self._parse_lineedit_float(self.target_input, "target", clear=True)
        if target_pn is None:
            return
        self.manager.send_ipc(MoveLinearToForceCommand(force_pn=target_pn, speed_mm_s=None, source="ui"))

    def _clear_pending_force_ramp(self) -> None:
        self._pending_force_ramp_command = None
        self._pending_force_ramp_start_pn = None

    @staticmethod
    def _force_matches(value_pn: float, target_pn: float) -> bool:
        return bool(np.isclose(float(value_pn), float(target_pn), rtol=0.0, atol=FORCE_POSITION_TOL_PN))

    def _current_force_pn(self) -> float | None:
        if not self._force_calibrant.is_loaded():
            return None
        latest_rows = self._read_latest_rows()
        row = latest_rows.get("linear")
        if row is None:
            return None
        return self._force_calibrant.motor_to_force(float(row[2]))

    def _confirm_ramp_proceed(self, *, destination_label: str, destination_pn: float) -> bool:
        message = (
            f"The force value is already at the {destination_label} position ({destination_pn:.3f} pN).\n\n"
            "Do you want to cancel the move command or proceed anyway?"
        )
        choice = QMessageBox.warning(
            self,
            "Force Ramp Confirmation",
            message,
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        return choice == QMessageBox.StandardButton.Yes

    def _dispatch_pending_ramp_if_ready(self, current_force_pn: float | None) -> None:
        command = self._pending_force_ramp_command
        start_pn = self._pending_force_ramp_start_pn
        if command is None or start_pn is None or current_force_pn is None:
            return
        if not self._force_matches(current_force_pn, start_pn):
            return
        self._clear_pending_force_ramp()
        self._set_calibrant_status(f"Reached start force {start_pn:.3f} pN; running ramp")
        self.manager.send_ipc(command)

    def _run_force_ramp(self, *, forward: bool) -> None:
        self._clear_pending_force_ramp()
        a_pn = self._parse_float(self.a_input, "A", clear=False)
        if a_pn is None:
            return
        b_pn = self._parse_float(self.b_input, "B", clear=False)
        if b_pn is None:
            return
        rate = self._parse_lineedit_float(self.rate_input, "rate", clear=False)
        if rate is None:
            return
        if rate <= 0:
            self._set_calibrant_status("Rate must be > 0")
            return

        start_pn = a_pn if forward else b_pn
        stop_pn = b_pn if forward else a_pn
        destination_label = "B" if forward else "A"
        current_force = self._current_force_pn()
        if current_force is not None and self._force_matches(current_force, stop_pn):
            if not self._confirm_ramp_proceed(destination_label=destination_label, destination_pn=stop_pn):
                self._set_calibrant_status("Ramp command cancelled")
                return

        command = RunLinearForceRampCommand(
            start_pn=start_pn,
            stop_pn=stop_pn,
            rate_pn_s=rate,
            speed_mm_s=None,
            source="ui",
        )
        if current_force is not None and not self._force_matches(current_force, start_pn):
            self._pending_force_ramp_command = command
            self._pending_force_ramp_start_pn = start_pn
            self._set_calibrant_status(f"Moving to start force {start_pn:.3f} pN before ramp")
            self.manager.send_ipc(MoveLinearToForceCommand(force_pn=start_pn, speed_mm_s=None, source="ui"))
            return

        self.manager.send_ipc(command)

    def _refresh_from_buffer(self) -> None:
        latest_rows = self._read_latest_rows()
        row = latest_rows.get("linear")
        if row is None:
            return

        current_force = None
        if self._force_calibrant.is_loaded():
            current_force = self._force_calibrant.motor_to_force(float(row[2]))
        self._dispatch_pending_ramp_if_ready(current_force)

        fault_code = int(round(float(row[7])))
        message = FAULT_CODE_MESSAGES.get(fault_code, f"Fault {fault_code}")
        if message:
            self.fault_label.setText(f"Linear motor: {message}")
        else:
            self.fault_label.setText("")


class RotaryMotorControlPanel(ControlPanelBase, _MotorBufferMixin):
    def __init__(self, manager):
        super().__init__(manager=manager, title="Rotary Motor", collapsed_by_default=True)
        _MotorBufferMixin.__init__(self)

        speed_row = QHBoxLayout()
        speed_row.setContentsMargins(0, 0, 0, 0)
        speed_row.setSpacing(4)
        self.layout().addLayout(speed_row)
        speed_row.addWidget(QLabel("Speed (turns/s):"))
        self.speed_input = QLineEdit("0.1")
        self.speed_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        speed_row.addWidget(self.speed_input, 1)

        turns_row = QHBoxLayout()
        turns_row.setContentsMargins(0, 0, 0, 0)
        turns_row.setSpacing(4)
        self.layout().addLayout(turns_row)
        turns_row.addWidget(QLabel("Turns:"))

        self.turns_down_button = QPushButton("-")
        self.turns_down_button.setFixedSize(50, 24)
        self.turns_down_button.clicked.connect(lambda: self._move_relative(direction=-1.0))  # type: ignore
        turns_row.addWidget(self.turns_down_button)

        self.turns_input = QLineEdit("")
        self.turns_input.setFixedWidth(142)
        self.turns_input.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        turns_row.addWidget(self.turns_input)

        self.turns_up_button = QPushButton("+")
        self.turns_up_button.setFixedSize(50, 24)
        self.turns_up_button.clicked.connect(lambda: self._move_relative(direction=1.0))  # type: ignore
        turns_row.addWidget(self.turns_up_button)
        turns_row.addStretch(1)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self._stop_all)  # type: ignore
        self.layout().addWidget(self.stop_button)

        self.fault_text = QLabel("")
        self.fault_text.setWordWrap(True)
        self.fault_text.setVisible(False)
        self.layout().addWidget(self.fault_text)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_from_buffer)  # type: ignore
        self._timer.setInterval(150)
        self._timer.start()

    def _set_fault_text(self, text: str) -> None:
        message = str(text).strip()
        self.fault_text.setText(message)
        self.fault_text.setVisible(bool(message))

    def _parse_speed(self) -> tuple[bool, float | None]:
        text = self.speed_input.text().strip()
        if not text:
            return True, None
        try:
            return True, float(text)
        except ValueError:
            self._set_fault_text("Invalid speed value")
            return False, None

    def _parse_turns(self) -> float | None:
        text = self.turns_input.text().strip()
        if not text:
            self._set_fault_text("Missing turns value")
            return None
        try:
            value = float(text)
        except ValueError:
            self._set_fault_text("Invalid turns value")
            return None
        if value <= 0:
            self._set_fault_text("Turns must be > 0")
            return None
        return value

    def _move_relative(self, *, direction: float) -> None:
        turns = self._parse_turns()
        if turns is None:
            return
        speed_ok, speed = self._parse_speed()
        if not speed_ok:
            return
        delta = direction * turns
        self.manager.send_ipc(MoveRotaryRelativeCommand(delta_turns=delta, speed_turns_s=speed, source="ui"))

    def _stop_all(self) -> None:
        self.manager.send_ipc(StopAllMotorsCommand())

    def _refresh_from_buffer(self) -> None:
        latest_rows = self._read_latest_rows()
        row = latest_rows.get("rotary")
        if row is None:
            return
        fault_code = int(round(float(row[7])))
        fault_message = FAULT_CODE_MESSAGES.get(fault_code, f"Fault {fault_code}")
        self._set_fault_text(fault_message)


class MotorAxisTimeSeriesPlot(TimeSeriesPlotBase):
    def __init__(self, axis: str, ylabel: str):
        super().__init__(buffer_name="MotorManager", ylabel=ylabel)
        self.axis = axis
        self.axis_id = AXIS_INDEX[axis]
        self.actual_line = None
        self.target_line = None

    def setup(self):
        super().setup()
        self.actual_line, = self.axes.plot([], [], "r")
        self.target_line, = self.axes.plot([], [], "g")

    def _transform_positions(self, actual: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return actual, target

    def update(self):
        data = self.buffer.peak_unsorted()
        mask = (
            np.isfinite(data[:, 0])
            & (data[:, 1] == self.axis_id)
            & np.isfinite(data[:, 2])
            & np.isfinite(data[:, 3])
        )
        if not np.any(mask):
            self.actual_line.set_xdata([])
            self.actual_line.set_ydata([])
            self.target_line.set_xdata([])
            self.target_line.set_ydata([])
            return

        rows = data[mask]
        order = np.argsort(rows[:, 0])
        rows = rows[order]

        ts = [datetime.fromtimestamp(t) for t in rows[:, 0]]
        actual = rows[:, 2]
        target = rows[:, 3]
        actual, target = self._transform_positions(actual, target)

        self.actual_line.set_xdata(ts)
        self.actual_line.set_ydata(actual)
        self.target_line.set_xdata(ts)
        self.target_line.set_ydata(target)

        self.axes.autoscale()
        self.axes.autoscale_view()
        self.axes.relim()


class ObjectiveMotorPlot(MotorAxisTimeSeriesPlot):
    def __init__(self):
        super().__init__(axis="objective", ylabel="Objective (nm)")


class LinearMotorPlot(MotorAxisTimeSeriesPlot):
    def __init__(self):
        super().__init__(axis="linear", ylabel="Linear (mm)")


class ForceMotorPlot(MotorAxisTimeSeriesPlot):
    def __init__(self):
        super().__init__(axis="linear", ylabel="Force (pN)")

    def _transform_positions(self, actual: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        model = _shared_force_calibrant_model()
        if not model.is_loaded():
            nan_values = np.full(actual.shape, np.nan, dtype=np.float64)
            return nan_values, nan_values.copy()
        return model.motor_array_to_force(actual), model.motor_array_to_force(target)


class RotaryMotorPlot(MotorAxisTimeSeriesPlot):
    def __init__(self):
        super().__init__(axis="rotary", ylabel="Rotary (turns)")
        self._plot_zero_offset: float | None = None

    def setup(self):
        super().setup()
        self._plot_zero_offset = None

    def _transform_positions(self, actual: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if actual.size == 0:
            return actual, target
        if self._plot_zero_offset is None:
            # Per app-launch baseline: current rotary position is plotted as 0.
            self._plot_zero_offset = float(actual[0])
        offset = float(self._plot_zero_offset)
        return actual - offset, target - offset
