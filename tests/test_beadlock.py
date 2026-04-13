import numpy as np
import pytest

import magscope.beadlock as beadlock_module
from magscope.beadlock import BeadLockManager
from magscope.hardware import FocusMotorBase
from magscope.ipc_commands import (
    MoveFocusMotorAbsoluteCommand,
    UpdateZLockBeadCommand,
    UpdateZLockEnabledCommand,
    UpdateZLockTargetCommand,
    UpdateZLockWindowCommand,
)


class FakeTracksBuffer:
    def __init__(self, tracks: np.ndarray):
        self._tracks = np.asarray(tracks, dtype=np.float64)

    def peak_unsorted(self) -> np.ndarray:
        return self._tracks


class FakeFocusBuffer:
    def __init__(self, rows: np.ndarray):
        self._rows = np.asarray(rows, dtype=np.float64)

    def peak_sorted(self) -> np.ndarray:
        return self._rows


class DummyFocusMotor(FocusMotorBase):
    def connect(self):
        self._is_connected = True

    def disconnect(self):
        self._is_connected = False

    def move_absolute(self, z: float) -> None:
        self._target_z = z

    def get_current_z(self) -> float:
        return 0.0

    def get_is_moving(self) -> bool:
        return False

    def get_position_limits(self) -> tuple[float, float]:
        return (-1_000.0, 1_000.0)


def make_manager(*, tracks: np.ndarray | None = None) -> BeadLockManager:
    type(BeadLockManager)._instances.pop(BeadLockManager, None)
    manager = BeadLockManager()
    manager.settings = {
        'xy-lock default interval': 10,
        'xy-lock default max': 10,
        'xy-lock default window': 10,
        'z-lock default interval': 10,
        'z-lock default max': 1_000,
        'z-lock default window': 10,
    }
    manager.setup()
    manager._sent_commands = []
    manager.send_ipc = manager._sent_commands.append
    manager.tracks_buffer = FakeTracksBuffer(
        np.zeros((0, 7), dtype=np.float64) if tracks is None else tracks
    )
    manager.hardware_types = {}
    return manager


def set_tracks(manager: BeadLockManager, rows: list[list[float]] | np.ndarray) -> None:
    manager.tracks_buffer = FakeTracksBuffer(np.asarray(rows, dtype=np.float64))


def test_set_z_lock_on_waits_for_fresh_post_enable_samples_before_latching(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)

    manager = make_manager()
    manager.z_lock_bead = 2
    set_tracks(
        manager,
        [
            [995.0, 0.0, 0.0, 150.0, 2.0, 0.0, 0.0],
            [999.0, 0.0, 0.0, 175.0, 2.0, 0.0, 0.0],
        ],
    )

    manager.set_z_lock_on(True)
    manager.do_z_lock(now=1_001.0)

    assert manager.z_lock_on is True
    assert manager.z_lock_target is None
    assert manager._sent_commands == [UpdateZLockEnabledCommand(value=True)]

    set_tracks(
        manager,
        [
            [1_001.0, 0.0, 0.0, 150.0, 2.0, 0.0, 0.0],
            [1_002.0, 0.0, 0.0, 175.0, 2.0, 0.0, 0.0],
        ],
    )
    now = 1_002.5
    manager.do_z_lock(now=1_003.0)

    assert manager.z_lock_target == 162.5
    assert any(isinstance(command, UpdateZLockEnabledCommand) for command in manager._sent_commands)
    assert any(
        isinstance(command, UpdateZLockTargetCommand) and command.value == 162.5
        for command in manager._sent_commands
    )


def test_do_z_lock_latches_target_from_fresh_valid_track_before_moving(monkeypatch):
    now = 500.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()
    manager._z_lock_global_cutoff = 100.0
    set_tracks(
        manager,
        [
            [110.0, 0.0, 0.0, 90.0, 0.0, 0.0, 0.0],
            [120.0, 0.0, 0.0, 95.0, 0.0, 0.0, 0.0],
        ],
    )

    manager.do_z_lock(now=5.0)

    assert manager.z_lock_target == 92.5
    assert manager._sent_commands == [UpdateZLockTargetCommand(value=92.5)]


def test_do_z_lock_averages_recent_z_samples_using_window():
    manager = make_manager(
        tracks=np.asarray(
            [
                [100.0, 0.0, 0.0, 10.0, 0.0, 0.0, 0.0],
                [110.0, 0.0, 0.0, 20.0, 0.0, 0.0, 0.0],
                [120.0, 0.0, 0.0, 40.0, 0.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
    )
    manager.hardware_types = {'focus': DummyFocusMotor}
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[1.0, 500.0, 500.0, 1.0]], dtype=np.float64))
    manager.z_lock_target = 10.0
    manager.z_lock_window = 2

    manager.do_z_lock(now=5.0)

    assert manager._sent_commands == [MoveFocusMotorAbsoluteCommand(z=480.0)]


def test_do_z_lock_skips_when_no_focus_motor_is_registered():
    manager = make_manager(
        tracks=np.asarray([[120.0, 0.0, 0.0, 95.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    )
    manager.z_lock_target = 100.0

    manager.do_z_lock(now=5.0)

    assert manager._sent_commands == []


def test_do_z_lock_skips_when_latest_bead_z_is_not_available():
    manager = make_manager(
        tracks=np.asarray(
            [
                [0.0, 0.0, 0.0, 75.0, 0.0, 0.0, 0.0],
                [10.0, 0.0, 0.0, np.nan, 0.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
    )
    manager.z_lock_target = 100.0
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[1.0, 10.0, 10.0, 1.0]], dtype=np.float64))

    manager.do_z_lock(now=5.0)

    assert manager._sent_commands == []


def test_do_z_lock_sends_capped_focus_move_toward_target_and_resets_cutoff():
    manager = make_manager(
        tracks=np.asarray([[120.0, 0.0, 0.0, 140.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    )
    manager.hardware_types = {'focus': DummyFocusMotor}
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[1.0, 500.0, 500.0, 1.0]], dtype=np.float64))
    manager.z_lock_target = 100.0
    manager.z_lock_max = 25.0

    manager.do_z_lock(now=5.0)

    assert manager._sent_commands == [MoveFocusMotorAbsoluteCommand(z=475.0)]
    assert manager._z_lock_global_cutoff == 5.0


def test_do_z_lock_uses_only_post_move_samples(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()
    manager.hardware_types = {'focus': DummyFocusMotor}
    manager._focus_motor_name = 'focus'
    manager.z_lock_target = 100.0
    manager.z_lock_window = 10

    manager._focus_buffer = FakeFocusBuffer(np.asarray([[1.0, 500.0, 500.0, 1.0]], dtype=np.float64))
    set_tracks(manager, [[999.0, 0.0, 0.0, 140.0, 0.0, 0.0, 0.0]])
    manager.do_z_lock(now=1_000.0)

    assert manager._sent_commands == [MoveFocusMotorAbsoluteCommand(z=460.0)]

    manager._sent_commands.clear()
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[2.0, 460.0, 460.0, 1.0]], dtype=np.float64))
    set_tracks(
        manager,
        [
            [999.5, 0.0, 0.0, 140.0, 0.0, 0.0, 0.0],
            [1_000.5, 0.0, 0.0, 120.0, 0.0, 0.0, 0.0],
        ],
    )
    manager.do_z_lock(now=1_001.0)

    assert manager._sent_commands == [MoveFocusMotorAbsoluteCommand(z=440.0)]


def test_do_z_lock_skips_while_focus_motor_is_still_settling():
    manager = make_manager(
        tracks=np.asarray([[120.0, 0.0, 0.0, 80.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    )
    manager.hardware_types = {'focus': DummyFocusMotor}
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[1.0, 500.0, 520.0, 0.0]], dtype=np.float64))
    manager.z_lock_target = 100.0

    manager.do_z_lock(now=5.0)

    assert manager._sent_commands == []


def test_do_z_lock_skips_when_external_focus_target_changes_until_fresh_samples_arrive():
    manager = make_manager(
        tracks=np.asarray([[120.0, 0.0, 0.0, 80.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    )
    manager.hardware_types = {'focus': DummyFocusMotor}
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[1.0, 500.0, 510.0, 1.0]], dtype=np.float64))
    manager.z_lock_target = 100.0
    manager._z_lock_last_focus_target = 500.0

    manager.do_z_lock(now=200.0)

    assert manager._sent_commands == []
    assert manager._z_lock_global_cutoff == 200.0


def test_refresh_bead_rois_resets_z_lock_cutoff_when_selected_roi_changes(monkeypatch):
    now = 300.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()
    manager.z_lock_bead = 2
    manager._bead_roi_ids = np.asarray([2], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 10, 0, 10]], dtype=np.uint32)

    def refresh_cache() -> None:
        manager._bead_roi_ids = np.asarray([2], dtype=np.uint32)
        manager._bead_roi_values = np.asarray([[1, 11, 0, 10]], dtype=np.uint32)

    monkeypatch.setattr(manager, '_refresh_bead_roi_cache', refresh_cache)

    manager.refresh_bead_rois()

    assert manager._z_lock_global_cutoff == 300.0


def test_set_z_lock_bead_resets_cutoff_and_broadcasts_update(monkeypatch):
    now = 700.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()

    manager.set_z_lock_bead(4)

    assert manager.z_lock_bead == 4
    assert manager._z_lock_global_cutoff == 700.0
    assert manager._sent_commands == [UpdateZLockBeadCommand(value=4)]


def test_set_z_lock_window_clamps_to_at_least_one_and_broadcasts_update():
    manager = make_manager()

    manager.set_z_lock_window(0)

    assert manager.z_lock_window == 1
    assert manager._sent_commands == [UpdateZLockWindowCommand(value=1)]
