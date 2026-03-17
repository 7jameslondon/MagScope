from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton
    from magscope_motors.commands import (
        MoveLinearToForceCommand,
        RunLinearForceRampCommand,
        UnloadForceCalibrantCommand,
    )
    from magscope_motors.control_panel import ForceCalibrationControlPanel
except Exception:  # pragma: no cover - optional dependency path for CI environments
    QApplication = None
    QMessageBox = None
    QPushButton = None
    MoveLinearToForceCommand = None
    RunLinearForceRampCommand = None
    UnloadForceCalibrantCommand = None
    ForceCalibrationControlPanel = None


class _FakeManager:
    def __init__(self):
        self.commands = []
        self.locks = {}

    def send_ipc(self, command):
        self.commands.append(command)


class _FakeCalibrant:
    def __init__(self):
        self._loaded = True

    def is_loaded(self):
        return self._loaded

    def motor_to_force(self, linear_mm):
        return float(linear_mm)


@unittest.skipIf(ForceCalibrationControlPanel is None or QApplication is None, "Qt/magscope dependencies unavailable")
class TestForceCalibrationPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.manager = _FakeManager()
        self.panel = ForceCalibrationControlPanel(self.manager)

    def test_required_controls_exist(self):
        buttons = {button.text() for button in self.panel.findChildren(QPushButton)}
        self.assertIn("Load Force Calibrant", buttons)
        self.assertIn("Plot", buttons)
        self.assertIn("Ramp A->B", buttons)
        self.assertIn("Ramp A<-B", buttons)
        self.assertEqual(self.panel.filepath_textedit.toPlainText(), "No force calibrant selected")

    def test_target_input_sends_force_move_command(self):
        self.panel.target_input.setText("12.5")
        self.panel._move_to_force_target()

        self.assertIsInstance(self.manager.commands[-1], MoveLinearToForceCommand)
        self.assertEqual(self.manager.commands[-1].force_pn, 12.5)

    def test_ramp_direction_mapping(self):
        self.panel.a_input.lineedit.setText("2")
        self.panel.b_input.lineedit.setText("8")
        self.panel.rate_input.setText("0.5")

        self.panel._run_force_ramp(forward=True)
        forward_command = self.manager.commands[-1]
        self.assertIsInstance(forward_command, RunLinearForceRampCommand)
        self.assertEqual(forward_command.start_pn, 2.0)
        self.assertEqual(forward_command.stop_pn, 8.0)

        self.panel._run_force_ramp(forward=False)
        backward_command = self.manager.commands[-1]
        self.assertEqual(backward_command.start_pn, 8.0)
        self.assertEqual(backward_command.stop_pn, 2.0)

    def test_ramp_asks_before_forward_when_already_at_b(self):
        self.panel.a_input.lineedit.setText("2")
        self.panel.b_input.lineedit.setText("8")
        self.panel.rate_input.setText("0.5")

        with patch.object(self.panel, "_current_force_pn", return_value=8.0):
            with patch(
                "magscope_motors.control_panel.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Cancel,
            ):
                self.panel._run_force_ramp(forward=True)

        self.assertEqual(len(self.manager.commands), 0)

    def test_ramp_asks_before_reverse_when_already_at_a(self):
        self.panel.a_input.lineedit.setText("2")
        self.panel.b_input.lineedit.setText("8")
        self.panel.rate_input.setText("0.5")

        with patch.object(self.panel, "_current_force_pn", return_value=2.0):
            with patch(
                "magscope_motors.control_panel.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Cancel,
            ):
                self.panel._run_force_ramp(forward=False)

        self.assertEqual(len(self.manager.commands), 0)

    def test_ramp_moves_to_start_before_running(self):
        self.panel.a_input.lineedit.setText("2")
        self.panel.b_input.lineedit.setText("8")
        self.panel.rate_input.setText("0.5")

        with patch.object(self.panel, "_current_force_pn", return_value=6.0):
            self.panel._run_force_ramp(forward=True)

        self.assertEqual(len(self.manager.commands), 1)
        self.assertIsInstance(self.manager.commands[-1], MoveLinearToForceCommand)
        self.assertEqual(self.manager.commands[-1].force_pn, 2.0)

    def test_pending_ramp_dispatches_after_start_reached(self):
        self.panel._force_calibrant = _FakeCalibrant()
        self.panel.a_input.lineedit.setText("2")
        self.panel.b_input.lineedit.setText("8")
        self.panel.rate_input.setText("0.5")

        with patch.object(self.panel, "_current_force_pn", return_value=6.0):
            self.panel._run_force_ramp(forward=True)

        with patch.object(
            self.panel,
            "_read_latest_rows",
            return_value={"linear": [0.0, 1.0, 2.0, 2.0, 0.0, 1.0, 1.0, 0.0]},
        ):
            self.panel._refresh_from_buffer()

        self.assertEqual(len(self.manager.commands), 2)
        self.assertIsInstance(self.manager.commands[0], MoveLinearToForceCommand)
        self.assertIsInstance(self.manager.commands[1], RunLinearForceRampCommand)
        self.assertEqual(self.manager.commands[1].start_pn, 2.0)
        self.assertEqual(self.manager.commands[1].stop_pn, 8.0)

    def test_cancel_load_unloads_calibrant(self):
        with patch("magscope_motors.control_panel.QFileDialog.getOpenFileName", return_value=("", "")):
            self.panel._load_force_calibrant()

        self.assertIsInstance(self.manager.commands[-1], UnloadForceCalibrantCommand)
        self.assertEqual(self.panel.filepath_textedit.toPlainText(), "No force calibrant selected")
        self.assertIn("unloaded", self.panel.calibrant_status_label.text().lower())


if __name__ == "__main__":
    unittest.main()
