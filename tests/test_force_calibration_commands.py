from __future__ import annotations

import unittest


try:
    from magscope_motors.commands import (
        LoadForceCalibrantCommand,
        MoveLinearToForceCommand,
        RunLinearForceRampCommand,
        SetLinearUiSpeedCommand,
        UnloadForceCalibrantCommand,
        UpdateForceCalibrantStatusCommand,
    )
except Exception:  # pragma: no cover - optional dependency path for CI environments
    LoadForceCalibrantCommand = None
    MoveLinearToForceCommand = None
    RunLinearForceRampCommand = None
    SetLinearUiSpeedCommand = None
    UnloadForceCalibrantCommand = None
    UpdateForceCalibrantStatusCommand = None


@unittest.skipIf(LoadForceCalibrantCommand is None, "magscope dependencies not available")
class TestForceCalibrationCommands(unittest.TestCase):
    def test_load_unload_commands(self):
        load = LoadForceCalibrantCommand(path="C:/tmp/calibrant.txt")
        self.assertEqual(load.path, "C:/tmp/calibrant.txt")
        self.assertEqual(load.source, "ui")

        unload = UnloadForceCalibrantCommand()
        self.assertEqual(unload.source, "ui")

    def test_force_move_command_defaults(self):
        command = MoveLinearToForceCommand(force_pn=12.5)
        self.assertEqual(command.force_pn, 12.5)
        self.assertIsNone(command.speed_mm_s)
        self.assertEqual(command.source, "ui")

    def test_force_ramp_command_fields(self):
        command = RunLinearForceRampCommand(
            start_pn=1.0,
            stop_pn=4.0,
            rate_pn_s=0.5,
            speed_mm_s=0.2,
            source="script",
        )
        self.assertEqual(command.start_pn, 1.0)
        self.assertEqual(command.stop_pn, 4.0)
        self.assertEqual(command.rate_pn_s, 0.5)
        self.assertEqual(command.speed_mm_s, 0.2)
        self.assertEqual(command.source, "script")

    def test_linear_ui_speed_command(self):
        command = SetLinearUiSpeedCommand(speed_mm_s=0.3)
        self.assertEqual(command.speed_mm_s, 0.3)

    def test_force_status_command(self):
        status = UpdateForceCalibrantStatusCommand(
            loaded=True,
            path="C:/tmp/calibrant.txt",
            message="ok",
            force_min_pn=1.0,
            force_max_pn=10.0,
        )
        self.assertTrue(status.loaded)
        self.assertEqual(status.path, "C:/tmp/calibrant.txt")
        self.assertEqual(status.message, "ok")
        self.assertEqual(status.force_min_pn, 1.0)
        self.assertEqual(status.force_max_pn, 10.0)


if __name__ == "__main__":
    unittest.main()
