from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

try:
    import numpy as np
except Exception:  # pragma: no cover - optional dependency path for CI environments
    np = None

try:
    from magscope_motors import force_calibration
    from magscope_motors.manager import MotorManager
    from magscope_motors.safety import SafetyGuard
    from magscope.ipc_commands import ShowMessageCommand
except Exception:  # pragma: no cover - optional dependency path for CI environments
    force_calibration = None
    MotorManager = None
    SafetyGuard = None
    ShowMessageCommand = None


def _clear_singletons() -> None:
    for cls in (MotorManager, SafetyGuard):
        if cls is None:
            continue
        registry = getattr(type(cls), "_instances", None)
        if isinstance(registry, dict):
            registry.pop(cls, None)


class _FakeAdapter:
    def __init__(self, *, connected: bool = False):
        self.connected = connected
        self.stop_called = 0

    def get_status(self) -> dict[str, float | bool]:
        return {
            "connected": self.connected,
            "actual_position": 0.0,
            "target_position": 0.0,
            "velocity": 0.0,
        }

    def stop(self) -> None:
        self.stop_called += 1


class _FakeLinearAdapter(_FakeAdapter):
    def __init__(self, *, supports_stream: bool, stream_result: bool):
        super().__init__(connected=True)
        self.actual_position = 5.0
        self.target_position = 5.0
        self.supports_stream_flag = supports_stream
        self.stream_result = stream_result
        self.stream_called = False
        self.move_absolute_calls: list[tuple[float, float | None]] = []

    def get_status(self) -> dict[str, float | bool]:
        return {
            "connected": True,
            "actual_position": self.actual_position,
            "target_position": self.target_position,
            "velocity": 0.2,
        }

    def move_absolute(self, target_mm: float, *, speed: float | None = None) -> None:
        self.target_position = float(target_mm)
        self.actual_position = float(target_mm)
        self.move_absolute_calls.append((float(target_mm), None if speed is None else float(speed)))

    def supports_force_ramp_stream(self) -> bool:
        return self.supports_stream_flag

    def execute_force_ramp_stream(
        self,
        *,
        positions_mm: np.ndarray,
        velocities_mm_s: np.ndarray,
        dt_s: float,
        speed_cap_mm_s: float | None = None,
    ) -> bool:
        del velocities_mm_s, dt_s, speed_cap_mm_s
        self.stream_called = True
        if positions_mm.size > 0:
            self.target_position = float(positions_mm[-1])
        return self.stream_result


def _write_calibrant(rows: np.ndarray) -> str:
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    Path(path).write_text("\n".join(f"{a} {b}" for a, b in rows), encoding="utf-8")
    return path


SCIPY_AVAILABLE = force_calibration is not None and force_calibration.PchipInterpolator is not None


@unittest.skipIf(np is None, "numpy is required for manager force tests")
@unittest.skipIf(MotorManager is None or SafetyGuard is None, "magscope dependencies not available")
@unittest.skipUnless(SCIPY_AVAILABLE, "scipy is required for manager force tests")
class TestManagerForceCalibration(unittest.TestCase):
    def setUp(self):
        _clear_singletons()
        self.manager = MotorManager()
        self.manager._enabled = True  # noqa: SLF001 - focused unit test
        self.linear_adapter = _FakeLinearAdapter(supports_stream=True, stream_result=True)
        self.manager._adapters = {
            "objective": _FakeAdapter(connected=False),
            "linear": self.linear_adapter,
            "rotary": _FakeAdapter(connected=False),
        }  # noqa: SLF001 - focused unit test

        guard = SafetyGuard(
            {
                "require_arm": True,
                "objective": {"min_nm": -100000, "max_nm": 100000},
                "linear": {"min_mm": 0.0, "max_mm": 34.5},
                "rotary": {"min_turns": -100, "max_turns": 100},
                "session_window": {"enabled": False, "linear_mm": 1000, "objective_nm": 1000, "rotary_turns": 1000},
            }
        )
        guard.arm()
        guard.set_session_origin("linear", 5.0)
        self.manager._safety_guard = guard  # noqa: SLF001 - focused unit test

    def tearDown(self):
        _clear_singletons()

    def _load_default_calibrant(self) -> None:
        mm = np.linspace(0.0, 20.0, 20)
        force = 0.5 * mm + 1.0
        rows = np.column_stack((mm, force))
        path = _write_calibrant(rows)
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        self.manager.load_force_calibrant(path=path)

    def test_unloaded_calibrant_blocks_force_move(self):
        self.manager._force_calibrant.unload()  # noqa: SLF001 - focused unit test
        self.manager._dispatch_move = MagicMock()  # noqa: SLF001 - focused unit test
        self.manager._emit_fault = MagicMock()  # noqa: SLF001 - focused unit test
        self.manager.send_ipc = MagicMock()  # noqa: SLF001 - focused unit test

        self.manager.move_linear_to_force(force_pn=5.0)

        self.manager._dispatch_move.assert_not_called()  # noqa: SLF001 - focused unit test
        self.manager._emit_fault.assert_called_once()  # noqa: SLF001 - focused unit test
        _, kwargs = self.manager._emit_fault.call_args  # noqa: SLF001 - focused unit test
        self.assertFalse(kwargs.get("notify", True))
        if ShowMessageCommand is not None:
            self.manager.send_ipc.assert_called_once()  # noqa: SLF001 - focused unit test
            warning = self.manager.send_ipc.call_args.args[0]  # noqa: SLF001 - focused unit test
            self.assertIsInstance(warning, ShowMessageCommand)
            self.assertEqual(warning.text, "Warning")
            self.assertEqual(warning.details, "Force Calibrant not Loaded")

    def test_unloaded_calibrant_force_move_from_script_has_no_popup(self):
        self.manager._force_calibrant.unload()  # noqa: SLF001 - focused unit test
        self.manager._dispatch_move = MagicMock()  # noqa: SLF001 - focused unit test
        self.manager._emit_fault = MagicMock()  # noqa: SLF001 - focused unit test
        self.manager.send_ipc = MagicMock()  # noqa: SLF001 - focused unit test

        self.manager.move_linear_to_force(force_pn=5.0, source="script")

        self.manager._dispatch_move.assert_not_called()  # noqa: SLF001 - focused unit test
        self.manager._emit_fault.assert_called_once()  # noqa: SLF001 - focused unit test
        self.manager.send_ipc.assert_not_called()  # noqa: SLF001 - focused unit test

    def test_valid_force_move_dispatches_linear_absolute(self):
        self._load_default_calibrant()
        self.manager._dispatch_move = MagicMock()  # noqa: SLF001 - focused unit test

        self.manager.move_linear_to_force(force_pn=6.0)

        self.manager._dispatch_move.assert_called_once()  # noqa: SLF001 - focused unit test
        _, kwargs = self.manager._dispatch_move.call_args  # noqa: SLF001 - focused unit test
        self.assertEqual(kwargs["absolute_target"], 10.0)

    def test_force_ramp_uses_stream_when_supported(self):
        self._load_default_calibrant()
        self.linear_adapter.supports_stream_flag = True
        self.linear_adapter.stream_result = True

        self.manager.run_linear_force_ramp(start_pn=3.0, stop_pn=8.0, rate_pn_s=1.0)

        self.assertTrue(self.linear_adapter.stream_called)
        self.assertIsNone(self.manager._active_force_ramp)  # noqa: SLF001 - focused unit test

    def test_force_ramp_falls_back_to_segmented_when_stream_unavailable(self):
        self._load_default_calibrant()
        self.linear_adapter.supports_stream_flag = False
        self.linear_adapter.stream_result = False

        self.manager.run_linear_force_ramp(start_pn=3.0, stop_pn=8.0, rate_pn_s=1.0)

        self.assertFalse(self.linear_adapter.stream_called)
        self.assertIsNotNone(self.manager._active_force_ramp)  # noqa: SLF001 - focused unit test

    def test_stop_all_cancels_active_force_ramp(self):
        self._load_default_calibrant()
        self.linear_adapter.supports_stream_flag = False

        self.manager.run_linear_force_ramp(start_pn=3.0, stop_pn=8.0, rate_pn_s=1.0)
        self.assertIsNotNone(self.manager._active_force_ramp)  # noqa: SLF001 - focused unit test

        self.manager.stop_all_motors()

        self.assertIsNone(self.manager._active_force_ramp)  # noqa: SLF001 - focused unit test
        self.assertGreaterEqual(self.linear_adapter.stop_called, 1)


if __name__ == "__main__":
    unittest.main()
