from __future__ import annotations

from datetime import datetime
import os

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
    QSizePolicy,
    QVBoxLayout,
)

import magscope
from magscope.datatypes import MatrixBuffer
from magscope.force_calibration import ForceCalibrantModel
from magscope.force_calibration.commands import (
    LoadForceCalibrantCommand,
    MoveLinearToForceCommand,
    RunLinearForceRampCommand,
    UnloadForceCalibrantCommand,
)
from motors.zaber_lsq import (
    BUFFER_NAME,
    COL_POSITION,
    COL_TARGET,
    COL_TIMESTAMP,
    COL_COMMAND_ERROR,
    COMMAND_ERROR_LABELS,
    COMMAND_ERROR_NONE,
)

_force_calibrant_model = ForceCalibrantModel()


def _parse_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class ForceCalibrationControlPanel(magscope.ControlPanelBase):
    def __init__(self, manager: magscope.UIManager):
        super().__init__(title="Force Calibration", manager=manager)

        self._buffer = MatrixBuffer(create=False, locks=self.manager.locks, name=BUFFER_NAME)
        self._pending_ramp_profile = None
        self._pending_ramp_start_mm = None

        outer = self.layout()

        # 1. Load Calibrant section
        load_section = QVBoxLayout()

        self.load_button = QPushButton("Load Force Calibrant")
        self.load_button.clicked.connect(self._load_calibrant)
        load_section.addWidget(self.load_button)

        self.file_path_label = QLabel("No force calibrant selected")
        self.file_path_label.setWordWrap(True)
        load_section.addWidget(self.file_path_label)

        self.plot_button = QPushButton("Plot")
        self.plot_button.setEnabled(False)
        self.plot_button.clicked.connect(self._plot_calibrant)
        load_section.addWidget(self.plot_button)

        outer.addLayout(load_section)
        outer.addWidget(self._divider())

        # 2. Target Force section
        target_section = QVBoxLayout()

        target_row = QHBoxLayout()
        target_row.addStretch(1)
        target_row.addWidget(QLabel("Target (pN)"))
        self.target_input = QLineEdit("1")
        self.target_input.setMaximumWidth(160)
        target_row.addWidget(self.target_input)
        self.move_to_force_button = QPushButton("Move")
        self.move_to_force_button.clicked.connect(self._move_to_force)
        target_row.addWidget(self.move_to_force_button)
        target_row.addStretch(1)
        target_section.addLayout(target_row)

        outer.addLayout(target_section)
        outer.addWidget(self._divider())

        # 3. Force Ramp section
        ramp_section = QVBoxLayout()

        ramp_buttons = QHBoxLayout()
        ramp_buttons.addStretch(1)
        self.ramp_ab_button = QPushButton("Ramp A \u2192 B")
        self.ramp_ab_button.setFixedWidth(120)
        self.ramp_ab_button.clicked.connect(lambda: self._run_ramp(forward=True))
        ramp_buttons.addWidget(self.ramp_ab_button)
        self.ramp_ba_button = QPushButton("Ramp B \u2192 A")
        self.ramp_ba_button.setFixedWidth(120)
        self.ramp_ba_button.clicked.connect(lambda: self._run_ramp(forward=False))
        ramp_buttons.addWidget(self.ramp_ba_button)
        ramp_buttons.addStretch(1)
        ramp_section.addLayout(ramp_buttons)

        ramp_a_row = QHBoxLayout()
        ramp_a_row.addWidget(QLabel("A (pN)"))
        self.ramp_a_input = QLineEdit("1")
        ramp_a_row.addWidget(self.ramp_a_input)
        ramp_a_row.addWidget(QLabel("B (pN)"))
        self.ramp_b_input = QLineEdit("10")
        ramp_a_row.addWidget(self.ramp_b_input)
        ramp_section.addLayout(ramp_a_row)

        rate_row = QHBoxLayout()
        rate_row.addStretch(1)
        rate_row.addWidget(QLabel("Rate (pN/s)"))
        self.rate_input = QLineEdit("1")
        self.rate_input.setMaximumWidth(160)
        rate_row.addWidget(self.rate_input)
        rate_row.addStretch(1)
        ramp_section.addLayout(rate_row)

        outer.addLayout(ramp_section)
        outer.addWidget(self._divider())

        # 4. Force range readout
        self.force_range_label = QLabel("Force range: no calibrant loaded")
        self.force_range_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.force_range_label)

        self.fault_label = QLabel("")
        self.fault_label.setStyleSheet("color: #ff6666;")
        self.fault_label.setVisible(False)
        outer.addWidget(self.fault_label)

        self._timer = QTimer()
        self._timer.timeout.connect(self._update_values)
        self._timer.setInterval(150)
        self._timer.start()

    def _divider(self) -> QFrame:
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setLineWidth(1)
        return f

    def _load_calibrant(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Force Calibrant File", "", "Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
        try:
            _force_calibrant_model.load(path)
            fr = _force_calibrant_model.get_force_range()
            self.file_path_label.setText(path)
            self.plot_button.setEnabled(True)
            if fr is not None:
                self.force_range_label.setText(f"Force range: {fr[0]:.3f} \u2192 {fr[1]:.3f} pN")
            self.manager.send_ipc(LoadForceCalibrantCommand(path=path))
        except Exception as exc:
            self.force_range_label.setText(f"Force range: unavailable ({exc})")

    def _plot_calibrant(self) -> None:
        if not _force_calibrant_model.is_loaded():
            return
        import matplotlib.pyplot as plt
        _force_calibrant_model.plot()
        plt.show()

    def _move_to_force(self) -> None:
        if not _force_calibrant_model.is_loaded():
            QMessageBox.warning(
                self, "Move to Force",
                "Load a force calibrant first.",
            )
            return

        target = _parse_float(self.target_input.text())
        if target is None:
            return

        force_range = _force_calibrant_model.get_force_range()
        if force_range is not None:
            force_min_pn, force_max_pn = force_range
            if not (force_min_pn <= target <= force_max_pn):
                QMessageBox.warning(
                    self,
                    "Move to Force",
                    (
                        f"Target force {target:.3f} pN is outside the loaded calibrant range "
                        f"({force_min_pn:.3f} to {force_max_pn:.3f} pN)."
                    ),
                )
                return

        rate = _parse_float(self.rate_input.text())
        if rate is not None and rate <= 0:
            rate = None
        self.manager.send_ipc(MoveLinearToForceCommand(force_pn=target, rate_pn_s=rate))

    def _run_ramp(self, forward: bool = True) -> None:
        if not _force_calibrant_model.is_loaded():
            QMessageBox.warning(
                self, "Force Ramp",
                "Load a force calibrant first.",
            )
            return
        a = _parse_float(self.ramp_a_input.text())
        b = _parse_float(self.ramp_b_input.text())
        rate = _parse_float(self.rate_input.text())
        if a is None or b is None or rate is None or rate <= 0:
            return
        force_range = _force_calibrant_model.get_force_range()
        if force_range is not None:
            force_min_pn, force_max_pn = force_range
            if not (force_min_pn <= a <= force_max_pn) or not (force_min_pn <= b <= force_max_pn):
                QMessageBox.warning(
                    self,
                    "Force Ramp",
                    (
                        f"Force value A ({a:.3f} pN) or B ({b:.3f} pN) is outside the "
                        f"loaded calibrant range ({force_min_pn:.3f} to {force_max_pn:.3f} pN)."
                    ),
                )
                return
        if abs(a - b) < 1e-3:
            reply = QMessageBox.question(
                self, "Force Ramp",
                "Start and stop forces are nearly identical. Cancel?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                return
        start_pn, stop_pn = (a, b) if forward else (b, a)

        # Warn if already at the target (stop) position
        data = self._buffer.peak_sorted()
        if data.size > 0:
            finite_rows = np.isfinite(data[:, COL_TIMESTAMP])
            if np.any(finite_rows):
                current_mm = data[finite_rows][-1, COL_POSITION]
                if np.isfinite(current_mm):
                    current_pn = _force_calibrant_model.motor_to_force(current_mm)
                    if current_pn is not None and abs(current_pn - stop_pn) < 0.5:
                        label_a = "A" if forward else "B"
                        label_b = "B" if forward else "A"
                        reply = QMessageBox.question(
                            self, "Force Ramp",
                            f"You are already at position {label_b} ({stop_pn:.3f} pN).\n"
                            f"The ramp will first move to position {label_a} ({start_pn:.3f} pN) "
                            f"before ramping back.\n\nProceed anyway?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        )
                        if reply != QMessageBox.StandardButton.Yes:
                            return

        self.manager.send_ipc(RunLinearForceRampCommand(
            start_pn=start_pn, stop_pn=stop_pn, rate_pn_s=rate,
        ))

    def _update_values(self) -> None:
        data = self._buffer.peak_sorted()
        if data.size == 0:
            return
        finite_rows = np.isfinite(data[:, COL_TIMESTAMP])
        if not np.any(finite_rows):
            return
        latest = data[finite_rows][-1, :]

        error_code = int(latest[COL_COMMAND_ERROR]) if np.isfinite(latest[COL_COMMAND_ERROR]) else COMMAND_ERROR_NONE
        error_text = COMMAND_ERROR_LABELS.get(error_code)
        if error_text:
            self.fault_label.setText(f"Linear motor: {error_text}")
            self.fault_label.setVisible(True)
        else:
            self.fault_label.setText("")
            self.fault_label.setVisible(False)


class ForcePlot(magscope.TimeSeriesPlotBase):
    def __init__(self, buffer_name: str = BUFFER_NAME):
        super().__init__(buffer_name, "Force (pN)")
        self.force_line = None
        self.target_line = None

    def setup(self):
        super().setup()
        self.force_line, self.target_line = self.axes.plot([], [], "r", [], [], "g")

    def update(self):
        if not _force_calibrant_model.is_loaded():
            return
        data = self.buffer.peak_sorted()
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
        force = _force_calibrant_model.motor_array_to_force(position)
        target_force = _force_calibrant_model.motor_array_to_force(target)
        sort_idx = np.argsort(t)
        t = t[sort_idx]
        force = force[sort_idx]
        target_force = target_force[sort_idx]
        xmin, xmax = self.parent.limits.get("Time", (None, None))
        ymin, ymax = self.parent.limits.get(self.ylabel, (None, None))
        selection = ((xmin or -np.inf) <= t) & (t <= (xmax or np.inf))
        t = t[selection]
        force = force[selection]
        target_force = target_force[selection]
        if t.size == 0:
            return
        timepoints = [datetime.fromtimestamp(t_) for t_ in t]
        self.force_line.set_xdata(timepoints)
        self.force_line.set_ydata(force)
        self.target_line.set_xdata(timepoints)
        self.target_line.set_ydata(target_force)
        xmin_dt, xmax_dt = [datetime.fromtimestamp(t_) if t_ else None for t_ in (xmin, xmax)]
        self.axes.autoscale()
        self.axes.autoscale_view()
        self.axes.set_xlim(xmin=xmin_dt, xmax=xmax_dt)
        self.axes.set_ylim(ymin=ymin, ymax=ymax)
        self.axes.relim()
