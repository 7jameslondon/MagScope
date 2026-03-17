from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

try:
    import numpy as np
except Exception:  # pragma: no cover - optional dependency path for CI environments
    np = None

try:
    from magscope_motors import force_calibration
    from magscope_motors.force_calibration import ForceCalibrantError, ForceCalibrantModel
except Exception:  # pragma: no cover - optional dependency path for CI environments
    force_calibration = None
    ForceCalibrantError = RuntimeError
    ForceCalibrantModel = None


def _write_table(rows: np.ndarray) -> str:
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    Path(path).write_text("\n".join(f"{r[0]} {r[1]}" for r in rows), encoding="utf-8")
    return path


SCIPY_AVAILABLE = force_calibration is not None and force_calibration.PchipInterpolator is not None


@unittest.skipIf(ForceCalibrantModel is None, "magscope dependencies not available")
@unittest.skipIf(np is None, "numpy is required for force calibration model tests")
@unittest.skipUnless(SCIPY_AVAILABLE, "scipy is required for force calibration model tests")
class TestForceCalibrantModel(unittest.TestCase):
    def test_valid_load_sorts_rows_and_builds_inverse(self):
        mm = np.linspace(0.0, 20.0, 20)
        force = 0.5 * mm + 1.0
        rows = np.column_stack((mm, force))[::-1]
        path = _write_table(rows)
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))

        model = ForceCalibrantModel()
        model.load(path)

        self.assertTrue(model.is_loaded())
        self.assertIsNotNone(model.data)
        self.assertTrue(np.all(np.diff(model.data[:, 0]) > 0))
        converted = model.force_to_motor(6.0)
        self.assertIsNotNone(converted)
        self.assertAlmostEqual(converted, 10.0, places=2)

    def test_out_of_range_returns_none(self):
        mm = np.linspace(0.0, 9.0, 12)
        force = 2.0 * mm + 3.0
        rows = np.column_stack((mm, force))
        path = _write_table(rows)
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))

        model = ForceCalibrantModel()
        model.load(path)
        self.assertIsNone(model.force_to_motor(-100.0))
        self.assertIsNone(model.motor_to_force(1000.0))

    def test_invalid_shape_is_rejected(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        Path(path).write_text("1 2 3\n2 3 4\n", encoding="utf-8")
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))

        model = ForceCalibrantModel()
        with self.assertRaises(ForceCalibrantError):
            model.load(path)

    def test_duplicate_force_values_rejected(self):
        mm = np.linspace(0.0, 9.0, 10)
        force = np.ones_like(mm)
        rows = np.column_stack((mm, force))
        path = _write_table(rows)
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))

        model = ForceCalibrantModel()
        with self.assertRaises(ForceCalibrantError):
            model.load(path)

    def test_non_monotonic_linear_positions_rejected(self):
        rows = np.array(
            [
                [0.0, 0.0],
                [1.0, 1.0],
                [1.0, 2.0],
                [2.0, 3.0],
                [3.0, 4.0],
                [4.0, 5.0],
                [5.0, 6.0],
                [6.0, 7.0],
                [7.0, 8.0],
                [8.0, 9.0],
            ],
            dtype=np.float64,
        )
        path = _write_table(rows)
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))

        model = ForceCalibrantModel()
        with self.assertRaises(ForceCalibrantError):
            model.load(path)


if __name__ == "__main__":
    unittest.main()
