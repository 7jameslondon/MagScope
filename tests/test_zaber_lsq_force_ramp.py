from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Stub only the zaber_motion library (not available in the venv).
# Everything else (PyQt6, magscope, numpy, etc.) is imported for real.
# ---------------------------------------------------------------------------
def _install_zaber_motion_stubs() -> None:
    if "zaber_motion" in sys.modules:
        return

    zaber_motion_module = types.ModuleType("zaber_motion")

    class MotionLibException(Exception):
        pass

    class Units:
        LENGTH_MILLIMETRES = object()
        VELOCITY_MILLIMETRES_PER_SECOND = object()
        ACCELERATION_MILLIMETRES_PER_SECOND_SQUARED = object()

    zaber_motion_module.MotionLibException = MotionLibException
    zaber_motion_module.Units = Units
    sys.modules["zaber_motion"] = zaber_motion_module

    ascii_module = types.ModuleType("zaber_motion.ascii")
    ascii_module.Axis = type("Axis", (), {})
    ascii_module.Connection = type("Connection", (), {})
    sys.modules["zaber_motion.ascii"] = ascii_module

    setting_constants_module = types.ModuleType("zaber_motion.ascii.setting_constants")
    setting_constants_module.SettingConstants = type(
        "SettingConstants",
        (),
        {"LIMIT_MAX": object(), "MAXSPEED_MAX": object(), "ACCEL": object()},
    )
    sys.modules["zaber_motion.ascii.setting_constants"] = setting_constants_module

    warning_flags_module = types.ModuleType("zaber_motion.ascii.warning_flags")
    warning_flags_module.WarningFlags = type("WarningFlags", (), {})
    sys.modules["zaber_motion.ascii.warning_flags"] = warning_flags_module

    device_identity_module = types.ModuleType("zaber_motion.dto.ascii.device_identity")
    device_identity_module.DeviceIdentity = type("DeviceIdentity", (), {})
    sys.modules["zaber_motion.dto.ascii.device_identity"] = device_identity_module


_install_zaber_motion_stubs()

# Ensure magscope has the attributes that zaber_lsq module-level code needs.
# Some sibling test files may have installed a stub magscope that lacks __getattr__.
if "magscope" in sys.modules:
    _mag = sys.modules["magscope"]
    _Fake = type("_Fake", (), {})
    for _attr in ("ControlPanelBase", "TimeSeriesPlotBase", "UIManager"):
        if not hasattr(_mag, _attr):
            setattr(_mag, _attr, _Fake)

# Real imports now, with zaber_motion already stubbed in sys.modules
import magscope.processes  # noqa: E402
from examples.motors import zaber_lsq  # noqa: E402


class FakeAxis:
    def __init__(self, position_mm: float = 0.0):
        self.position_mm = position_mm
        self.move_absolute_calls: list[dict[str, object]] = []
        self.move_velocity_calls: list[dict[str, object]] = []

    def move_absolute(self, target_mm, position_unit, *, wait_until_idle, velocity, velocity_unit):
        self.move_absolute_calls.append(
            {
                "target_mm": target_mm,
                "position_unit": position_unit,
                "wait_until_idle": wait_until_idle,
                "velocity": velocity,
                "velocity_unit": velocity_unit,
            }
        )
        self.position_mm = target_mm

    def move_velocity(self, velocity, velocity_unit):
        self.move_velocity_calls.append(
            {
                "velocity": velocity,
                "velocity_unit": velocity_unit,
            }
        )

    def stop(self, wait_until_idle=False):
        pass

    def get_position(self, _unit) -> float:
        return self.position_mm

    def is_busy(self) -> bool:
        return False

    def is_homed(self) -> bool:
        return True


class FakeForceCalibrant:
    def is_loaded(self) -> bool:
        return True

    def force_to_motor(self, force_pn: float) -> float:
        return force_pn / 2.0

    def motor_to_force(self, motor_mm: float) -> float:
        return motor_mm * 2.0

    def velocity_for_force_rate(self, position_mm: float, rate_pn_s: float) -> float:
        return rate_pn_s / 2.0

    def build_force_ramp(self, *, start_pn, stop_pn, rate_pn_s):
        return SimpleNamespace(
            positions_mm=np.array([start_pn / 2.0, stop_pn / 2.0], dtype=float),
            velocities_mm_s=np.array([0.0, rate_pn_s / 2.0], dtype=float),
        )


@pytest.fixture(autouse=True)
def _clear_zaber_lsq_singleton():
    magscope.processes.SingletonMeta._instances.pop(zaber_lsq.ZaberLsqMotor, None)
    yield
    magscope.processes.SingletonMeta._instances.pop(zaber_lsq.ZaberLsqMotor, None)


def make_motor(position_mm: float = 0.0):
    motor = zaber_lsq.ZaberLsqMotor()
    motor._axis = FakeAxis(position_mm=position_mm)
    motor._limit_max_mm = 100.0
    motor._speed_max_mm_s = 10.0
    motor._speed_mm_s = 1.75
    motor._write_state = lambda force=False: None
    motor.send_ipc = lambda command: None
    return motor


def test_handle_linear_move_preserves_current_speed_when_speed_is_none():
    motor = make_motor(position_mm=1.0)

    motor.handle_linear_move(target_mm=4.0, speed_mm_s=None)

    assert motor._speed_mm_s == 1.75
    assert motor._target_mm == 4.0
    assert motor._axis.move_absolute_calls[-1]["velocity"] == 1.75


def test_run_linear_force_ramp_starts_velocity_mode(monkeypatch):
    motor = make_motor(position_mm=0.0)
    monkeypatch.setattr(zaber_lsq, "_force_calibrant", FakeForceCalibrant())

    motor.run_linear_force_ramp(start_pn=4.0, stop_pn=10.0, rate_pn_s=5.0)

    assert motor._velocity_ramp_active
    assert motor._velocity_ramp_phase == "pre"
    assert motor._velocity_ramp_start_mm == 2.0
    assert motor._velocity_ramp_stop_mm == 5.0
    assert motor._velocity_ramp_rate_pn_s == 5.0
    assert motor._velocity_ramp_direction == 1
    assert motor._speed_mm_s == 2.5
    assert len(motor._axis.move_velocity_calls) == 1
    assert motor._axis.move_velocity_calls[0]["velocity"] == 2.5


def test_run_linear_force_ramp_reverse_starts_velocity_mode(monkeypatch):
    """B->A direction: velocity mode with negative direction."""
    motor = make_motor(position_mm=10.0)
    monkeypatch.setattr(zaber_lsq, "_force_calibrant", FakeForceCalibrant())

    motor.run_linear_force_ramp(start_pn=10.0, stop_pn=4.0, rate_pn_s=5.0)

    assert motor._velocity_ramp_active
    assert motor._velocity_ramp_phase == "pre"
    assert motor._velocity_ramp_start_mm == 5.0
    assert motor._velocity_ramp_stop_mm == 2.0
    assert motor._velocity_ramp_rate_pn_s == 5.0
    assert motor._velocity_ramp_direction == -1
    assert motor._speed_mm_s == -2.5
    assert len(motor._axis.move_velocity_calls) == 1
    assert motor._axis.move_velocity_calls[0]["velocity"] == -2.5


def test_run_linear_force_ramp_already_at_start_direct_ramp(monkeypatch):
    """When already at start position, skip pre phase and go directly to ramp."""
    motor = make_motor(position_mm=2.0)
    monkeypatch.setattr(zaber_lsq, "_force_calibrant", FakeForceCalibrant())

    motor.run_linear_force_ramp(start_pn=4.0, stop_pn=10.0, rate_pn_s=5.0)

    assert motor._velocity_ramp_active
    assert motor._velocity_ramp_phase == "ramp"
    assert motor._velocity_ramp_start_mm == 2.0
    assert motor._velocity_ramp_stop_mm == 5.0
    assert motor._velocity_ramp_direction == 1
    assert motor._speed_mm_s == 2.5
    assert len(motor._axis.move_velocity_calls) == 1


def test_handle_linear_force_ramp_starts_velocity_mode(monkeypatch):
    """Script force ramp: should start velocity mode."""
    motor = make_motor(position_mm=0.0)
    monkeypatch.setattr(zaber_lsq, "_force_calibrant", FakeForceCalibrant())

    motor.handle_linear_force_ramp(start_pn=4.0, stop_pn=10.0, rate_pn_s=5.0)

    assert motor._velocity_ramp_active
    assert motor._velocity_ramp_phase == "pre"
    assert motor._velocity_ramp_start_mm == 2.0
    assert motor._velocity_ramp_stop_mm == 5.0
    assert motor._velocity_ramp_rate_pn_s == 5.0
    assert motor._velocity_ramp_direction == 1
    assert motor._speed_mm_s == 2.5
    assert len(motor._axis.move_velocity_calls) == 1
    assert motor._axis.move_velocity_calls[0]["velocity"] == 2.5


def test_force_move_without_explicit_speed_reuses_existing_speed(monkeypatch):
    motor = make_motor(position_mm=1.0)
    monkeypatch.setattr(zaber_lsq, "_force_calibrant", FakeForceCalibrant())

    motor.handle_linear_force_move(force_pn=6.0, rate_pn_s=None, wait_until_done=False)

    assert motor._target_mm == 3.0
    assert motor._speed_mm_s == 1.75
    assert motor._axis.move_absolute_calls[-1]["target_mm"] == 3.0
    assert motor._axis.move_absolute_calls[-1]["velocity"] == 1.75
