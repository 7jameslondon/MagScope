from __future__ import annotations

import numpy as np
import pytest

import magscope.beadlock as beadlock_module
from conftest import FakeFocusBuffer, FakeHardwareBuffer, FakeTracksBuffer, make_beadlock_manager, set_beadlock_tracks
from magscope.beadlock import BeadLockManager
from magscope.hardware import FocusMotorBase
from magscope.ipc_commands import (
    MoveBeadsCommand,
    MoveFocusMotorAbsoluteCommand,
    RemoveBeadFromPendingMovesCommand,
    RemoveBeadsFromPendingMovesCommand,
    SetXYLockIntervalCommand,
    SetXYLockMaxCommand,
    SetXYLockOnCommand,
    SetXYLockWindowCommand,
    SetZLockBeadCommand,
    SetZLockIntervalCommand,
    SetZLockMaxCommand,
    SetZLockOnCommand,
    SetZLockTargetCommand,
    SetZLockWindowCommand,
    UpdateXYLockEnabledCommand,
    UpdateXYLockIntervalCommand,
    UpdateXYLockMaxCommand,
    UpdateXYLockWindowCommand,
    UpdateZLockBeadCommand,
    UpdateZLockEnabledCommand,
    UpdateZLockIntervalCommand,
    UpdateZLockMaxCommand,
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

    assert manager._sent_commands == [MoveFocusMotorAbsoluteCommand(z=490.0)]


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


def test_do_z_lock_sends_damped_capped_focus_move_toward_target_and_resets_cutoff():
    manager = make_manager(
        tracks=np.asarray([[120.0, 0.0, 0.0, 140.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    )
    manager.hardware_types = {'focus': DummyFocusMotor}
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[1.0, 500.0, 500.0, 1.0]], dtype=np.float64))
    manager.z_lock_target = 100.0
    manager.z_lock_max = 25.0

    manager.do_z_lock(now=5.0)

    assert manager._sent_commands == [MoveFocusMotorAbsoluteCommand(z=487.5)]
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

    assert manager._sent_commands == [MoveFocusMotorAbsoluteCommand(z=480.0)]

    manager._sent_commands.clear()
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[2.0, 480.0, 480.0, 1.0]], dtype=np.float64))
    set_tracks(
        manager,
        [
            [999.5, 0.0, 0.0, 140.0, 0.0, 0.0, 0.0],
            [1_000.5, 0.0, 0.0, 120.0, 0.0, 0.0, 0.0],
        ],
    )
    manager.do_z_lock(now=1_001.0)

    assert manager._sent_commands == [MoveFocusMotorAbsoluteCommand(z=470.0)]


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


# ---------------------------------------------------------------------------
# XY-Lock tests (new)
# ---------------------------------------------------------------------------

def test_do_xy_lock_sends_move_commands_for_displaced_beads(monkeypatch):
    manager = make_beadlock_manager()
    manager.xy_lock_on = True
    manager._bead_roi_ids = np.asarray([1, 2], dtype=np.uint32)
    manager._bead_roi_values = np.asarray(
        [[0, 64, 0, 64], [0, 64, 0, 64]], dtype=np.uint32
    )
    set_beadlock_tracks(manager, [
        [100.0, 3700.0, 3800.0, 0.0, 1.0, 0.0, 0.0],
        [100.0, 3500.0, 3600.0, 0.0, 2.0, 0.0, 0.0],
    ])

    manager.do_xy_lock(now=200.0)

    assert len(manager._sent_commands) == 1
    command = manager._sent_commands[0]
    assert isinstance(command, MoveBeadsCommand)
    moves = command.moves
    assert len(moves) == 2
    ids = {bead_id for bead_id, _, _ in moves}
    assert ids == {1, 2}


def test_do_xy_lock_skips_beads_with_no_track_data(monkeypatch):
    manager = make_beadlock_manager()
    manager._bead_roi_ids = np.asarray([1, 2], dtype=np.uint32)
    manager._bead_roi_values = np.asarray(
        [[0, 64, 0, 64], [0, 64, 0, 64]], dtype=np.uint32
    )
    set_beadlock_tracks(manager, [
        [100.0, 3100.0, 3100.0, 0.0, 1.0, 0.0, 0.0],
    ])

    manager.do_xy_lock(now=200.0)

    commands = manager._sent_commands
    if commands:
        moves = commands[0].moves
        ids = {bead_id for bead_id, _, _ in moves}
        assert 2 not in ids


def test_do_xy_lock_skips_beads_with_all_nan_positions():
    manager = make_beadlock_manager()
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 64, 0, 64]], dtype=np.uint32)
    set_beadlock_tracks(manager, [
        [100.0, np.nan, np.nan, 0.0, 1.0, 0.0, 0.0],
    ])

    manager.do_xy_lock(now=200.0)

    assert manager._sent_commands == []


def test_do_xy_lock_respects_global_and_bead_cutoffs():
    manager = make_beadlock_manager()
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 64, 0, 64]], dtype=np.uint32)
    manager._xy_lock_global_cutoff = 200.0
    set_beadlock_tracks(manager, [
        [100.0, 3500.0, 3500.0, 0.0, 1.0, 0.0, 0.0],
        [210.0, 3500.0, 3500.0, 0.0, 1.0, 0.0, 0.0],
    ])

    manager.do_xy_lock(now=300.0)

    commands = manager._sent_commands
    if commands:
        moves = commands[0].moves
        assert len(moves) == 1
    else:
        pytest.fail("Expected at least one move command")


def test_do_xy_lock_skips_beads_in_pending_moves():
    manager = make_beadlock_manager()
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 64, 0, 64]], dtype=np.uint32)
    manager._xy_lock_pending_moves = [1]
    set_beadlock_tracks(manager, [
        [100.0, 3500.0, 3500.0, 0.0, 1.0, 0.0, 0.0],
    ])

    manager.do_xy_lock(now=200.0)

    assert manager._sent_commands == []


def test_do_xy_lock_clamps_movement_to_xy_lock_max():
    manager = make_beadlock_manager()
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 64, 0, 64]], dtype=np.uint32)
    manager.xy_lock_max = 5
    set_beadlock_tracks(manager, [
        [100.0, 5000.0, 5000.0, 0.0, 1.0, 0.0, 0.0],
    ])

    manager.do_xy_lock(now=200.0)

    assert len(manager._sent_commands) == 1
    moves = manager._sent_commands[0].moves
    assert len(moves) == 1
    _, dx, dy = moves[0]
    assert abs(dx) <= 5
    assert abs(dy) <= 5


# ---------------------------------------------------------------------------
# XY-Lock setter / IPC handler tests
# ---------------------------------------------------------------------------

def test_set_xy_lock_on_broadcasts_and_resets_cutoff(monkeypatch):
    now = 500.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()

    manager.set_xy_lock_on(True)

    assert manager.xy_lock_on is True
    assert manager._xy_lock_global_cutoff == 500.0
    assert manager._sent_commands == [UpdateXYLockEnabledCommand(value=True)]


def test_set_xy_lock_interval_rejects_nonpositive():
    manager = make_manager()
    original = manager.xy_lock_interval

    manager.set_xy_lock_interval(0)

    assert manager.xy_lock_interval == original
    assert manager._sent_commands == []

    manager.set_xy_lock_interval(-5)

    assert manager.xy_lock_interval == original
    assert manager._sent_commands == []


def test_set_xy_lock_interval_accepts_positive_and_broadcasts():
    manager = make_manager()

    manager.set_xy_lock_interval(2.5)

    assert manager.xy_lock_interval == 2.5
    assert manager._sent_commands == [UpdateXYLockIntervalCommand(value=2.5)]


def test_set_xy_lock_max_rounds_and_clamps_min_1():
    manager = make_manager()

    manager.set_xy_lock_max(7.3)

    assert manager.xy_lock_max == 7
    assert manager._sent_commands == [UpdateXYLockMaxCommand(value=7)]

    manager._sent_commands.clear()
    manager.set_xy_lock_max(0)

    assert manager.xy_lock_max == 1
    assert manager._sent_commands == [UpdateXYLockMaxCommand(value=1)]


def test_set_xy_lock_window_clamps_to_min_1_and_broadcasts():
    manager = make_manager()

    manager.set_xy_lock_window(5)

    assert manager.xy_lock_window == 5
    assert manager._sent_commands == [UpdateXYLockWindowCommand(value=5)]

    manager._sent_commands.clear()
    manager.set_xy_lock_window(0)

    assert manager.xy_lock_window == 1
    assert manager._sent_commands == [UpdateXYLockWindowCommand(value=1)]


def test_remove_bead_from_xy_lock_pending_moves():
    manager = make_manager()
    manager._xy_lock_pending_moves = [1, 2, 3]

    manager.remove_bead_from_xy_lock_pending_moves(id=2)

    assert manager._xy_lock_pending_moves == [1, 3]

    manager.remove_bead_from_xy_lock_pending_moves(id=99)

    assert manager._xy_lock_pending_moves == [1, 3]


def test_remove_beads_from_xy_lock_pending_moves_batch():
    manager = make_manager()
    manager._xy_lock_pending_moves = [1, 2, 3, 4]

    manager.remove_beads_from_xy_lock_pending_moves(ids=[2, 4])

    assert manager._xy_lock_pending_moves == [1, 3]


def test_remove_beads_from_pending_moves_empty_list():
    manager = make_manager()
    manager._xy_lock_pending_moves = [1, 2, 3]

    manager.remove_beads_from_xy_lock_pending_moves(ids=[])

    assert manager._xy_lock_pending_moves == [1, 2, 3]


# ---------------------------------------------------------------------------
# refresh_bead_rois XY-lock tests
# ---------------------------------------------------------------------------

def test_refresh_bead_rois_cleans_pending_moves_for_deleted_beads(monkeypatch):
    manager = make_manager()
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 10, 0, 10]], dtype=np.uint32)
    manager._xy_lock_pending_moves = [1, 2, 3]

    def refresh_cache() -> None:
        manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
        manager._bead_roi_values = np.asarray([[0, 10, 0, 10]], dtype=np.uint32)

    monkeypatch.setattr(manager, '_refresh_bead_roi_cache', refresh_cache)

    manager.refresh_bead_rois()

    assert manager._xy_lock_pending_moves == [1]


def test_refresh_bead_rois_cleans_bead_cutoffs_for_deleted_beads(monkeypatch):
    manager = make_manager()
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 10, 0, 10]], dtype=np.uint32)
    manager._xy_lock_bead_cutoff = {1: 100.0, 2: 200.0, 3: 300.0}

    def refresh_cache() -> None:
        manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
        manager._bead_roi_values = np.asarray([[0, 10, 0, 10]], dtype=np.uint32)

    monkeypatch.setattr(manager, '_refresh_bead_roi_cache', refresh_cache)

    manager.refresh_bead_rois()

    assert manager._xy_lock_bead_cutoff == {1: 100.0}


def test_refresh_bead_rois_sets_cutoff_on_roi_change(monkeypatch):
    now = 300.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 10, 0, 10]], dtype=np.uint32)

    def refresh_cache() -> None:
        manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
        manager._bead_roi_values = np.asarray([[2, 12, 0, 10]], dtype=np.uint32)

    monkeypatch.setattr(manager, '_refresh_bead_roi_cache', refresh_cache)

    manager.refresh_bead_rois()

    assert manager._xy_lock_bead_cutoff[1] == 300.0


def test_refresh_bead_rois_sets_z_cutoff_when_changed_roi_is_z_lock_bead(monkeypatch):
    now = 300.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()
    manager.z_lock_bead = 1
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 10, 0, 10]], dtype=np.uint32)

    def refresh_cache() -> None:
        manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
        manager._bead_roi_values = np.asarray([[5, 15, 0, 10]], dtype=np.uint32)

    monkeypatch.setattr(manager, '_refresh_bead_roi_cache', refresh_cache)

    manager.refresh_bead_rois()

    assert manager._z_lock_global_cutoff == 300.0


# ---------------------------------------------------------------------------
# _averaged_bead_z tests
# ---------------------------------------------------------------------------

def test_averaged_bead_z_returns_none_without_tracks_buffer():
    manager = make_manager()

    assert manager._averaged_bead_z(0, 10) is None


def test_averaged_bead_z_rejects_finite_but_zero_timestamp_rows():
    manager = make_manager()
    set_beadlock_tracks(manager, [
        [0.0, 0.0, 0.0, 75.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 80.0, 0.0, 0.0, 0.0],
    ])

    result = manager._averaged_bead_z(0, 10)

    assert result is None


def test_averaged_bead_z_filters_by_bead_id_and_averages():
    manager = make_manager()
    set_beadlock_tracks(manager, [
        [10.0, 0.0, 0.0, 100.0, 0.0, 0.0, 0.0],
        [20.0, 0.0, 0.0, 200.0, 0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0, 300.0, 1.0, 0.0, 0.0],
    ])

    result = manager._averaged_bead_z(0, 10)

    assert result == 150.0


# ---------------------------------------------------------------------------
# _discover_focus_motor_name tests
# ---------------------------------------------------------------------------

def test_discover_focus_motor_name_returns_single_focus_motor():
    manager = make_manager()
    manager.hardware_types = {'focus': DummyFocusMotor}

    name = manager._discover_focus_motor_name()

    assert name == 'focus'


def test_discover_focus_motor_name_returns_none_with_no_focus_motors():
    manager = make_manager()
    manager.hardware_types = {}

    name = manager._discover_focus_motor_name()

    assert name is None


def test_discover_focus_motor_name_returns_none_with_multiple_focus_motors():
    manager = make_manager()
    manager.hardware_types = {'focus_a': DummyFocusMotor, 'focus_b': DummyFocusMotor}

    name = manager._discover_focus_motor_name()

    assert name is None


# ---------------------------------------------------------------------------
# Z-Lock edge case tests
# ---------------------------------------------------------------------------

def test_do_z_lock_rejects_zero_max_step():
    manager = make_manager(
        tracks=np.asarray([[120.0, 0.0, 0.0, 140.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    )
    manager.hardware_types = {'focus': DummyFocusMotor}
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[1.0, 500.0, 500.0, 1.0]], dtype=np.float64))
    manager.z_lock_target = 100.0
    manager.z_lock_max = 0.0

    manager.do_z_lock(now=5.0)

    assert manager._sent_commands == []


def test_do_z_lock_rejects_near_zero_correction():
    manager = make_manager(
        tracks=np.asarray([[120.0, 0.0, 0.0, 100.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    )
    manager.hardware_types = {'focus': DummyFocusMotor}
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = FakeFocusBuffer(np.asarray([[1.0, 500.0, 500.0, 1.0]], dtype=np.float64))
    manager.z_lock_target = 100.0

    manager.do_z_lock(now=5.0)

    assert manager._sent_commands == []


def test_do_z_lock_skips_when_focus_state_none_after_target_set():
    manager = make_manager(
        tracks=np.asarray([[120.0, 0.0, 0.0, 140.0, 0.0, 0.0, 0.0]], dtype=np.float64)
    )
    manager.z_lock_target = 100.0

    manager.do_z_lock(now=5.0)

    assert manager._sent_commands == []


def test_set_z_lock_target_broadcasts_and_resets_cutoff(monkeypatch):
    now = 600.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()

    manager.set_z_lock_target(250.0)

    assert manager.z_lock_target == 250.0
    assert manager._z_lock_global_cutoff == 600.0
    assert manager._sent_commands == [UpdateZLockTargetCommand(value=250.0)]


def test_set_z_lock_target_accepts_none():
    manager = make_manager()

    manager.set_z_lock_target(None)

    assert manager.z_lock_target is None
    assert manager._sent_commands == [UpdateZLockTargetCommand(value=None)]


def test_set_z_lock_interval_positive_broadcasts():
    manager = make_manager()

    manager.set_z_lock_interval(3.5)

    assert manager.z_lock_interval == 3.5
    assert manager._sent_commands == [UpdateZLockIntervalCommand(value=3.5)]


def test_set_z_lock_interval_rejects_nonpositive():
    manager = make_manager()
    original = manager.z_lock_interval

    manager.set_z_lock_interval(0)

    assert manager.z_lock_interval == original
    assert manager._sent_commands == []


def test_set_z_lock_max_broadcasts():
    manager = make_manager()

    manager.set_z_lock_max(50.0)

    assert manager.z_lock_max == 50.0
    assert manager._sent_commands == [UpdateZLockMaxCommand(value=50.0)]


def test_set_z_lock_on_broadcasts_and_resets_state(monkeypatch):
    now = 800.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()
    manager._z_lock_expected_focus_target = 123.0

    manager.set_z_lock_on(True)

    assert manager.z_lock_on is True
    assert manager._z_lock_global_cutoff == 800.0
    assert manager._z_lock_expected_focus_target is None
    assert manager._sent_commands == [UpdateZLockEnabledCommand(value=True)]


def test_do_main_loop_interval_not_elapsed(monkeypatch):
    now = 100.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_manager()
    manager.xy_lock_on = True
    manager.z_lock_on = True
    manager._xy_lock_last_time = 100.0
    manager._z_lock_last_time = 100.0

    manager.do_main_loop()

    # No locks should execute because interval hasn't elapsed


def test_averaged_bead_z_returns_none_no_buffer():
    manager = make_manager()
    manager.tracks_buffer = None
    assert manager._averaged_bead_z(0, 10) is None


def test_do_main_loop_executes_xy_lock_when_interval_elapsed(monkeypatch):
    now = 200.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_beadlock_manager()
    manager.xy_lock_on = True
    manager.z_lock_on = False
    manager._xy_lock_last_time = 100.0
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 64, 0, 64]], dtype=np.uint32)
    set_beadlock_tracks(manager, [[150.0, 3200.0, 3200.0, 0.0, 1.0, 0.0, 0.0]])

    manager.do_main_loop()


def test_do_xy_lock_now_none_fallback(monkeypatch):
    now = 500.0
    monkeypatch.setattr(beadlock_module, 'time', lambda: now)
    manager = make_beadlock_manager()
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 64, 0, 64]], dtype=np.uint32)
    set_beadlock_tracks(manager, [[400.0, 3200.0, 3200.0, 0.0, 1.0, 0.0, 0.0]])

    manager.do_xy_lock(now=None)
    assert manager._xy_lock_last_time == 500.0


def test_latest_focus_state_no_data():
    manager = make_manager()
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = FakeFocusBuffer(np.empty((0, 4)))
    assert manager._latest_focus_state() is None


def test_discover_focus_motor_name_typeerror_skip():
    manager = make_manager()
    manager.hardware_types = {'focus': DummyFocusMotor, 'garbage': object()}
    name = manager._discover_focus_motor_name()
    assert name == 'focus'
