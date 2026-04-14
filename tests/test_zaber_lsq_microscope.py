from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "examples" / "motors" / "zaber_lsq_via_python_microscope.py"
SPEC = importlib.util.spec_from_file_location("test_zaber_lsq_via_python_microscope", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load module from {MODULE_PATH}")
zaber_lsq = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = zaber_lsq
SPEC.loader.exec_module(zaber_lsq)


class FakeBuffer:
    def __init__(self):
        self.rows = []

    def write(self, row):
        self.rows.append(np.array(row, copy=True))


class FakeAxisLimits:
    def __init__(self, lower: float, upper: float):
        self.lower = lower
        self.upper = upper


class FakeAxis:
    def __init__(self):
        self.position = 2.5
        self.limits = FakeAxisLimits(0.0, 10.0)
        self.moving = False
        self.homed = True
        self.move_to_calls = []
        self.move_by_calls = []

    def move_to(self, position: float) -> None:
        self.move_to_calls.append(position)
        self.position = position

    def move_by(self, delta: float) -> None:
        self.move_by_calls.append(delta)
        self.position += delta


class FakeStage:
    def __init__(self):
        self.enabled = False
        self.shutdown_called = False
        self.axes = {"z": FakeAxis()}

    def enable(self) -> None:
        self.enabled = True

    def shutdown(self) -> None:
        self.shutdown_called = True


def reset_singleton_instance(cls) -> None:
    type(cls)._instances.pop(cls, None)


def make_motor(stage: FakeStage | None = None):
    reset_singleton_instance(zaber_lsq.ZaberLsqMicroscopeMotor)
    motor = zaber_lsq.ZaberLsqMicroscopeMotor(device=stage or FakeStage())
    motor._buffer = FakeBuffer()
    return motor


def latest_row(motor) -> np.ndarray:
    return motor._buffer.rows[-1][0]


def test_zaber_lsq_microscope_motor_reuses_zaber_buffer_name():
    motor = make_motor()

    assert motor.name == zaber_lsq.BUFFER_NAME
    assert motor.buffer_shape == (100000, 21)


def test_zaber_lsq_microscope_motor_connects_and_writes_expected_row():
    stage = FakeStage()
    motor = make_motor(stage)

    motor.connect()
    motor.fetch()

    row = latest_row(motor)
    assert stage.enabled is True
    assert row[zaber_lsq.COL_POSITION] == pytest.approx(2.5)
    assert row[zaber_lsq.COL_TARGET] == pytest.approx(2.5)
    assert row[zaber_lsq.COL_CONNECTED] == pytest.approx(1.0)
    assert row[zaber_lsq.COL_HOMED] == pytest.approx(1.0)
    assert row[zaber_lsq.COL_LIMIT_MAX] == pytest.approx(10.0)
    assert np.isnan(row[zaber_lsq.COL_SERIAL])
    assert np.isnan(row[zaber_lsq.COL_PORT_NUMBER])


def test_zaber_lsq_microscope_motor_moves_and_jogs_with_existing_commands():
    stage = FakeStage()
    motor = make_motor(stage)
    motor.connect()

    motor.handle_move_absolute(target_mm=7.5, speed_mm_s=3.0)
    motor.handle_jog_relative(delta_mm=-2.0, speed_mm_s=2.0)

    row = latest_row(motor)
    assert stage.axes["z"].move_to_calls == [7.5]
    assert stage.axes["z"].move_by_calls == [-2.0]
    assert row[zaber_lsq.COL_TARGET] == pytest.approx(5.5)
    assert row[zaber_lsq.COL_SPEED] == pytest.approx(2.0)
    assert row[zaber_lsq.COL_COMMAND_ERROR] == pytest.approx(zaber_lsq.COMMAND_ERROR_NONE)


def test_zaber_lsq_microscope_motor_uses_software_max_limit_override():
    stage = FakeStage()
    motor = make_motor(stage)
    motor.connect()

    motor.handle_set_max_limit(limit_mm=4.0)
    motor.handle_move_max(speed_mm_s=1.0)

    row = latest_row(motor)
    assert row[zaber_lsq.COL_LIMIT_MAX] == pytest.approx(4.0)
    assert row[zaber_lsq.COL_TARGET] == pytest.approx(4.0)
    assert stage.axes["z"].move_to_calls[-1] == pytest.approx(4.0)

    motor.handle_use_default_max_limit()
    row = latest_row(motor)
    assert row[zaber_lsq.COL_LIMIT_MAX] == pytest.approx(10.0)


def test_zaber_lsq_microscope_motor_reports_unsupported_home_and_stop():
    stage = FakeStage()
    motor = make_motor(stage)
    motor.connect()

    motor.handle_home()
    assert latest_row(motor)[zaber_lsq.COL_COMMAND_ERROR] == pytest.approx(zaber_lsq.COMMAND_ERROR_HOME)

    motor.handle_stop()
    assert latest_row(motor)[zaber_lsq.COL_COMMAND_ERROR] == pytest.approx(zaber_lsq.COMMAND_ERROR_STOP)


def test_zaber_lsq_microscope_motor_disconnects_cleanly():
    stage = FakeStage()
    motor = make_motor(stage)
    motor.connect()

    motor.disconnect()
    motor._write_state(force=True)

    row = latest_row(motor)
    assert stage.shutdown_called is True
    assert row[zaber_lsq.COL_CONNECTED] == pytest.approx(0.0)
    assert np.isnan(row[zaber_lsq.COL_POSITION])
