import numpy as np
import pytest

from magscope.ipc_commands import (
    ArmZLUTSweepCaptureCommand,
    ClearPendingZLUTLoadRequestCommand,
    ClearPendingZLUTProfileLengthCommand,
    LoadZLUTCommand,
    RequestFocusMotorLimitsCommand,
    RequestZLUTProfileLengthCommand,
    SetAcquisitionOnCommand,
    ShowErrorCommand,
    ShowMessageCommand,
    UpdateZLUTGenerationEvaluationCommand,
    UpdateZLUTGenerationStateCommand,
)
from magscope.utils import AcquisitionMode
from magscope.zlut_generation import ZLUTGenerationManager


class FakeDataset:
    def __init__(self, snapshot, n_steps, profile_length):
        self._snapshot = snapshot
        self.n_steps = n_steps
        self.profile_length = profile_length

    def peak(self):
        return self._snapshot


def make_manager() -> ZLUTGenerationManager:
    type(ZLUTGenerationManager)._instances.pop(ZLUTGenerationManager, None)
    manager = ZLUTGenerationManager()
    manager.send_ipc = lambda command: manager._sent_commands.append(command)
    manager._sent_commands = []
    return manager


def test_build_generated_zluts_averages_profiles_per_bead_and_step():
    manager = make_manager()
    manager._dataset = FakeDataset(
        snapshot={
            'bead_ids': np.asarray([2, 2, 2, 2, 5, 5, 5, 5], dtype=np.uint32),
            'step_indices': np.asarray([0, 0, 1, 1, 0, 0, 1, 1], dtype=np.uint32),
            'timestamps': np.arange(8, dtype=np.float64),
            'motor_z_values': np.asarray([10.0, 12.0, 20.0, 22.0, 10.0, 12.0, 20.0, 22.0]),
            'valid_flags': np.ones((8,), dtype=np.uint8),
            'profiles': np.asarray(
                [
                    [1.0, 3.0],
                    [3.0, 5.0],
                    [5.0, 7.0],
                    [7.0, 9.0],
                    [2.0, 4.0],
                    [4.0, 6.0],
                    [6.0, 8.0],
                    [8.0, 10.0],
                ],
                dtype=np.float64,
            ),
        },
        n_steps=2,
        profile_length=2,
    )

    manager._build_generated_zluts()

    assert sorted(manager._generated_zluts) == [2, 5]
    assert manager._selected_bead_id == 2
    np.testing.assert_allclose(
        manager._generated_zluts[2].zlut_array,
        np.asarray(
            [
                [11.0, 21.0],
                [2.0, 6.0],
                [4.0, 8.0],
            ],
            dtype=np.float64,
        ),
    )


def test_build_generated_zluts_preserves_descending_step_order():
    manager = make_manager()
    manager._dataset = FakeDataset(
        snapshot={
            'bead_ids': np.asarray([4, 4], dtype=np.uint32),
            'step_indices': np.asarray([0, 1], dtype=np.uint32),
            'timestamps': np.asarray([1.0, 2.0], dtype=np.float64),
            'motor_z_values': np.asarray([100.0, 50.0], dtype=np.float64),
            'valid_flags': np.ones((2,), dtype=np.uint8),
            'profiles': np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
        },
        n_steps=2,
        profile_length=2,
    )

    manager._build_generated_zluts()

    np.testing.assert_allclose(
        manager._generated_zluts[4].zlut_array[0],
        np.asarray([100.0, 50.0], dtype=np.float64),
    )


def test_build_generated_zluts_converts_non_finite_averages_to_nan():
    manager = make_manager()
    manager._dataset = FakeDataset(
        snapshot={
            'bead_ids': np.asarray([4, 4], dtype=np.uint32),
            'step_indices': np.asarray([0, 1], dtype=np.uint32),
            'timestamps': np.asarray([1.0, 2.0], dtype=np.float64),
            'motor_z_values': np.asarray([100.0, 50.0], dtype=np.float64),
            'valid_flags': np.ones((2,), dtype=np.uint8),
            'profiles': np.asarray([[np.inf, 2.0], [3.0, -np.inf]], dtype=np.float64),
        },
        n_steps=2,
        profile_length=2,
    )

    manager._build_generated_zluts()

    result = manager._generated_zluts[4].zlut_array
    assert np.isnan(result[1, 0])
    assert np.isnan(result[2, 1])
    assert result[1, 1] == 3.0
    assert result[2, 0] == 2.0


def test_save_generated_zlut_writes_and_loads(monkeypatch, tmp_path):
    manager = make_manager()
    manager._phase = 'evaluating'
    manager._generated_zluts = {
        3: type('Result', (), {'zlut_array': np.asarray([[1.0, 2.0], [3.0, 4.0]])})()
    }

    saved = []

    def fake_savetxt(path, array):
        saved.append((path, array.copy()))

    monkeypatch.setattr('magscope.zlut_generation.np.savetxt', fake_savetxt)

    filepath = tmp_path / 'generated.txt'
    manager.save_generated_zlut(str(filepath), 3, load_request_id=42)

    assert saved[0][0] == filepath
    np.testing.assert_allclose(saved[0][1], np.asarray([[1.0, 2.0], [3.0, 4.0]]))
    assert manager._sent_commands[0] == LoadZLUTCommand(
        filepath=str(filepath),
        load_request_id=42,
    )
    assert isinstance(manager._sent_commands[1], ShowMessageCommand)
    assert isinstance(manager._sent_commands[2], UpdateZLUTGenerationStateCommand)
    assert any(isinstance(command, UpdateZLUTGenerationEvaluationCommand) for command in manager._sent_commands)


def test_save_generated_zlut_without_loading_keeps_evaluation_active(monkeypatch, tmp_path):
    manager = make_manager()
    manager._phase = 'evaluating'
    manager._generated_zluts = {
        3: type('Result', (), {'zlut_array': np.asarray([[1.0, 2.0], [3.0, 4.0]])})()
    }

    saved = []

    def fake_savetxt(path, array):
        saved.append((path, array.copy()))

    monkeypatch.setattr('magscope.zlut_generation.np.savetxt', fake_savetxt)

    filepath = tmp_path / 'generated.txt'
    manager.save_generated_zlut(str(filepath), 3, load_after_save=False)

    assert saved[0][0] == filepath
    np.testing.assert_allclose(saved[0][1], np.asarray([[1.0, 2.0], [3.0, 4.0]]))
    assert not any(isinstance(command, LoadZLUTCommand) for command in manager._sent_commands)
    assert not any(isinstance(command, ShowMessageCommand) for command in manager._sent_commands)
    state_command = next(
        command for command in manager._sent_commands if isinstance(command, UpdateZLUTGenerationStateCommand)
    )
    assert state_command.status == 'Generated Z-LUT saved.'
    assert state_command.phase == 'evaluating'
    assert any(isinstance(command, UpdateZLUTGenerationEvaluationCommand) for command in manager._sent_commands)


def test_save_generated_zlut_missing_directory_clears_pending_load_request(tmp_path):
    manager = make_manager()
    manager._phase = 'evaluating'
    manager._generated_zluts = {
        3: type('Result', (), {'zlut_array': np.asarray([[1.0, 2.0], [3.0, 4.0]])})()
    }

    manager.save_generated_zlut(
        str(tmp_path / 'missing' / 'generated.txt'),
        3,
        load_after_save=True,
        load_request_id=42,
    )

    assert not any(isinstance(command, LoadZLUTCommand) for command in manager._sent_commands)
    assert ClearPendingZLUTLoadRequestCommand(load_request_id=42) in manager._sent_commands
    error_command = next(
        command for command in manager._sent_commands if isinstance(command, ShowErrorCommand)
    )
    assert error_command.details == f"Directory does not exist: {tmp_path / 'missing'}"


def test_save_generated_zlut_without_loading_failure_does_not_clear_pending_load_request(tmp_path):
    manager = make_manager()
    manager._phase = 'evaluating'
    manager._generated_zluts = {
        3: type('Result', (), {'zlut_array': np.asarray([[1.0, 2.0], [3.0, 4.0]])})()
    }

    manager.save_generated_zlut(
        str(tmp_path / 'missing' / 'generated.txt'),
        3,
        load_after_save=False,
        load_request_id=42,
    )

    assert not any(isinstance(command, LoadZLUTCommand) for command in manager._sent_commands)
    assert not any(
        isinstance(command, ClearPendingZLUTLoadRequestCommand)
        for command in manager._sent_commands
    )


def test_prepare_session_uses_requested_profiles_per_bead():
    manager = make_manager()
    manager._cleanup_runtime_state = lambda destroy_dataset: None
    manager._discover_focus_motor_name = lambda: 'focus'
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = object()
    manager.video_buffer = type('VideoBuffer', (), {'n_images': 40})()
    manager._bead_roi_ids = np.asarray([1, 2], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 10, 0, 10], [10, 20, 10, 20]], dtype=np.float64)
    manager._acquisition_on = False

    manager._prepare_session(0.0, 5.0, 10.0, 7)

    assert manager._profiles_per_bead == 7
    np.testing.assert_array_equal(manager._session_bead_roi_ids, np.asarray([1, 2], dtype=np.uint32))
    np.testing.assert_array_equal(
        manager._session_bead_roi_values,
        np.asarray([[0, 10, 0, 10], [10, 20, 10, 20]], dtype=np.uint32),
    )


def test_prepare_session_freezes_bead_roi_snapshot():
    manager = make_manager()
    manager._cleanup_runtime_state = lambda destroy_dataset: None
    manager._discover_focus_motor_name = lambda: 'focus'
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = object()
    manager.video_buffer = type('VideoBuffer', (), {'n_images': 40})()
    manager._bead_roi_ids = np.asarray([1, 2], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 10, 0, 10], [10, 20, 10, 20]], dtype=np.uint32)
    manager._acquisition_on = False

    manager._prepare_session(0.0, 5.0, 10.0, 7)
    manager._bead_roi_ids[0] = 99
    manager._bead_roi_values[0, 0] = 99

    np.testing.assert_array_equal(manager._session_bead_roi_ids, np.asarray([1, 2], dtype=np.uint32))
    np.testing.assert_array_equal(
        manager._session_bead_roi_values,
        np.asarray([[0, 10, 0, 10], [10, 20, 10, 20]], dtype=np.uint32),
    )


def test_prepare_session_requires_tracking_acquisition_mode():
    manager = make_manager()
    manager._cleanup_runtime_state = lambda destroy_dataset: None
    manager._acquisition_mode = AcquisitionMode.VIDEO_FULL

    try:
        manager._prepare_session(0.0, 5.0, 10.0, 7)
    except RuntimeError as exc:
        assert str(exc) == (
            'Z-LUT generation requires a tracking acquisition mode. '
            'Switch to Track, Track and Video (ROIs), or Track and Video (Full).'
        )
    else:
        raise AssertionError('Expected RuntimeError for non-tracking acquisition mode')


def test_start_generation_fails_fast_for_non_tracking_acquisition_mode():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._acquisition_mode = AcquisitionMode.VIDEO_ROIS

    manager.start_generation(0.0, 5.0, 10.0, 7)

    assert any(isinstance(command, ShowErrorCommand) for command in manager._sent_commands)
    error_command = next(command for command in manager._sent_commands if isinstance(command, ShowErrorCommand))
    assert error_command.text == 'Could not start Z-LUT generation'
    assert error_command.details == (
        'Z-LUT generation requires a tracking acquisition mode. '
        'Switch to Track, Track and Video (ROIs), or Track and Video (Full).'
    )
    assert state_updates == [
        (
            ('Generation failed to start.',),
            {
                'detail': (
                    'Z-LUT generation requires a tracking acquisition mode. '
                    'Switch to Track, Track and Video (ROIs), or Track and Video (Full).'
                ),
                'phase': 'idle',
            },
        )
    ]


def test_start_generation_rejects_single_position_sweep():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._refresh_bead_roi_cache = lambda: None
    manager._cleanup_runtime_state = lambda destroy_dataset: None
    manager._acquisition_mode = AcquisitionMode.TRACK
    manager._discover_focus_motor_name = lambda: 'focus'
    manager._focus_motor_name = 'focus'
    manager._focus_buffer = object()
    manager.video_buffer = object()
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[0, 10, 0, 10]], dtype=np.float64)
    manager._acquisition_on = False

    manager.start_generation(5.0, 1.0, 5.0, 3)

    assert any(isinstance(command, ShowErrorCommand) for command in manager._sent_commands)
    error_command = next(command for command in manager._sent_commands if isinstance(command, ShowErrorCommand))
    assert error_command.text == 'Could not start Z-LUT generation'
    assert error_command.details == 'Z-LUT generation requires at least two z positions.'
    assert state_updates == [
        (
            ('Generation failed to start.',),
            {
                'detail': 'Z-LUT generation requires at least two z positions.',
                'phase': 'idle',
            },
        )
    ]


def test_start_generation_requests_focus_motor_limits_before_preparing_session():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._refresh_bead_roi_cache = lambda: None
    manager._discover_focus_motor_name = lambda: 'focus'

    manager.start_generation(0.0, 5.0, 10.0, 7)

    assert manager._phase == 'waiting_focus_limits'
    assert manager._pending_start_request == (0.0, 5.0, 10.0, 7)
    assert isinstance(manager._sent_commands[0], RequestFocusMotorLimitsCommand)
    assert state_updates == [
        (
            ('Waiting for focus motor limits.',),
            {
                'detail': 'Checking that the requested Z-LUT sweep stays within the focus motor range.',
                'running': True,
                'can_cancel': True,
                'phase': 'waiting_focus_limits',
            },
        )
    ]


def test_cancel_generation_while_waiting_for_focus_limits_resets_state():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._refresh_bead_roi_cache = lambda: None
    manager._discover_focus_motor_name = lambda: 'focus'
    manager._cleanup_runtime_state = lambda destroy_dataset: state_updates.append(
        (('cleanup',), {'destroy_dataset': destroy_dataset})
    )

    manager.start_generation(0.0, 5.0, 10.0, 7)
    manager.cancel_generation()

    assert isinstance(manager._sent_commands[0], RequestFocusMotorLimitsCommand)
    assert state_updates == [
        (
            ('Waiting for focus motor limits.',),
            {
                'detail': 'Checking that the requested Z-LUT sweep stays within the focus motor range.',
                'running': True,
                'can_cancel': True,
                'phase': 'waiting_focus_limits',
            },
        ),
        (
            ('Z-LUT generation canceled.',),
            {
                'running': False,
                'can_cancel': False,
                'phase': 'idle',
            },
        ),
        (
            ('cleanup',),
            {'destroy_dataset': True},
        ),
    ]


def test_start_generation_while_waiting_for_focus_limits_stays_cancelable():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._refresh_bead_roi_cache = lambda: None
    manager._discover_focus_motor_name = lambda: 'focus'

    manager.start_generation(0.0, 5.0, 10.0, 7)
    manager.start_generation(0.0, 5.0, 10.0, 7)

    assert isinstance(manager._sent_commands[0], RequestFocusMotorLimitsCommand)
    assert len(manager._sent_commands) == 1
    assert state_updates == [
        (
            ('Waiting for focus motor limits.',),
            {
                'detail': 'Checking that the requested Z-LUT sweep stays within the focus motor range.',
                'running': True,
                'can_cancel': True,
                'phase': 'waiting_focus_limits',
            },
        ),
        (
            ('Generation already running.',),
            {
                'detail': 'Cancel the current sweep before starting another one.',
                'running': True,
                'can_cancel': True,
                'phase': 'waiting_focus_limits',
            },
        ),
    ]


def test_report_focus_motor_limits_rejects_out_of_range_sweep():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._refresh_bead_roi_cache = lambda: None
    manager._discover_focus_motor_name = lambda: 'focus'
    manager._acquisition_mode = AcquisitionMode.TRACK

    manager.start_generation(-5.0, 5.0, 10.0, 7)
    manager.report_focus_motor_limits(0.0, 10.0)

    assert any(isinstance(command, ShowErrorCommand) for command in manager._sent_commands)
    error_command = next(command for command in manager._sent_commands if isinstance(command, ShowErrorCommand))
    assert error_command.text == 'Could not start Z-LUT generation'
    assert error_command.details == (
        'Requested sweep range [-5.000, 10.000] nm exceeds focus motor limits '
        '[0.000, 10.000] nm.'
    )
    assert manager._phase == 'idle'
    assert manager._pending_start_request is None
    assert state_updates[-1] == (
        ('Generation failed to start.',),
        {
            'detail': (
                'Requested sweep range [-5.000, 10.000] nm exceeds focus motor limits '
                '[0.000, 10.000] nm.'
            ),
            'phase': 'idle',
        },
    )


def test_report_focus_motor_limits_continues_startup_when_in_range():
    manager = make_manager()
    state_updates = []
    prepared = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._send_progress = lambda **kwargs: None
    manager._refresh_bead_roi_cache = lambda: None
    manager._discover_focus_motor_name = lambda: 'focus'
    manager._prepare_session = lambda *args: (
        prepared.append(args),
        setattr(manager, '_session_bead_roi_ids', np.asarray([4], dtype=np.uint32)),
        setattr(manager, '_session_bead_roi_values', np.asarray([[1, 2, 3, 4]], dtype=np.uint32)),
    )

    manager.start_generation(0.0, 5.0, 10.0, 7)
    manager.report_focus_motor_limits(0.0, 10.0)

    assert prepared == [(0.0, 5.0, 10.0, 7)]
    assert manager._pending_start_request is None
    assert isinstance(manager._sent_commands[-2], SetAcquisitionOnCommand)
    assert manager._sent_commands[-2].value is True
    assert isinstance(manager._sent_commands[-1], RequestZLUTProfileLengthCommand)
    assert manager._sent_commands[-1].bead_ids == (4,)
    assert manager._sent_commands[-1].bead_rois == ((1, 2, 3, 4),)
    assert state_updates[-1] == (
        ('Waiting for a processed frame to measure profile length.',),
        {
            'detail': 'Z-LUT generation is preparing shared memory and capture settings.',
            'running': True,
            'can_cancel': True,
            'phase': 'waiting_profile_length',
        },
    )


def test_advance_when_in_position_waits_for_at_target_flag():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._phase = 'moving'
    manager._current_step_index = 0
    manager._profiles_per_bead = 3
    manager._steps = np.asarray([12.5], dtype=np.float64)
    manager._session_bead_roi_ids = np.asarray([4], dtype=np.uint32)
    manager._session_bead_roi_values = np.asarray([[1, 2, 3, 4]], dtype=np.uint32)
    manager._latest_focus_state = lambda: (7.5, 12.5, False)

    manager._advance_when_in_position()

    assert manager._phase == 'moving'
    assert manager._sent_commands == []
    assert state_updates == []


def test_advance_when_in_position_arms_capture_once_at_target():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._phase = 'moving'
    manager._current_step_index = 0
    manager._profiles_per_bead = 3
    manager._steps = np.asarray([12.5], dtype=np.float64)
    manager._session_bead_roi_ids = np.asarray([4], dtype=np.uint32)
    manager._session_bead_roi_values = np.asarray([[1, 2, 3, 4]], dtype=np.uint32)
    manager._latest_focus_state = lambda: (7.5, 12.5, True)

    manager._advance_when_in_position()

    assert manager._phase == 'capturing'
    assert isinstance(manager._sent_commands[0], SetAcquisitionOnCommand)
    assert manager._sent_commands[0].value is True
    assert isinstance(manager._sent_commands[1], ArmZLUTSweepCaptureCommand)
    assert manager._sent_commands[1].motor_z_value == 7.5
    assert state_updates[-1][1]['phase'] == 'capturing'


def test_cancel_session_clears_pending_zlut_profile_length_request():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._previous_acquisition_on = True
    manager._cleanup_runtime_state = lambda destroy_dataset: state_updates.append(
        (('cleanup',), {'destroy_dataset': destroy_dataset})
    )

    manager._cancel_session()

    assert isinstance(manager._sent_commands[0], ClearPendingZLUTProfileLengthCommand)
    assert state_updates[-1] == (("cleanup",), {'destroy_dataset': True})


def test_fail_session_clears_pending_zlut_profile_length_request():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._cleanup_runtime_state = lambda destroy_dataset: state_updates.append(
        (('cleanup',), {'destroy_dataset': destroy_dataset})
    )

    manager._fail_session('boom')

    assert isinstance(manager._sent_commands[1], ClearPendingZLUTProfileLengthCommand)


def test_report_focus_motor_limits_ignores_out_of_phase_reports():
    manager = make_manager()
    manager._phase = 'idle'
    manager._pending_start_request = (0.0, 5.0, 10.0, 7)

    manager.report_focus_motor_limits(0.0, 10.0)

    assert manager._pending_start_request == (0.0, 5.0, 10.0, 7)
    assert manager._sent_commands == []


def test_handle_capture_complete_waits_until_requested_profiles_per_bead():
    manager = make_manager()
    sent_commands = []
    state_updates = []
    manager.send_ipc = sent_commands.append
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._active = True
    manager._phase = 'capturing'
    manager._current_step_index = 1
    manager._profiles_per_bead = 5
    manager._current_step_profiles_written = 3
    manager._steps = np.asarray([0.0, 10.0, 20.0], dtype=np.float64)

    manager.handle_capture_complete(step_index=1, written_count=4, written_profiles_per_bead=2)

    assert manager._step_capture_complete is True
    assert manager._current_step_profiles_written == 5
    assert sent_commands == []


def test_handle_capture_complete_rearms_until_requested_profiles_per_bead_reached():
    manager = make_manager()
    sent_commands = []
    state_updates = []
    manager.send_ipc = sent_commands.append
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._active = True
    manager._phase = 'capturing'
    manager._current_step_index = 0
    manager._profiles_per_bead = 5
    manager._current_step_capture_earliest_timestamp = 123.0
    manager._current_step_profiles_written = 2
    manager._session_bead_roi_ids = np.asarray([4], dtype=np.uint32)
    manager._session_bead_roi_values = np.asarray([[1, 2, 3, 4]], dtype=np.uint32)
    manager._steps = np.asarray([12.5], dtype=np.float64)

    manager.handle_capture_complete(step_index=0, written_count=2, written_profiles_per_bead=1)

    assert manager._step_capture_complete is False
    assert manager._current_step_profiles_written == 3
    assert sent_commands[0].remaining_profiles_per_bead == 2
    assert sent_commands[0].earliest_timestamp == 123.0
    assert sent_commands[0].bead_ids == (4,)
    assert sent_commands[0].bead_rois == ((1, 2, 3, 4),)


def test_handle_capture_complete_rearms_when_stale_stack_is_skipped():
    manager = make_manager()
    sent_commands = []
    state_updates = []
    manager.send_ipc = sent_commands.append
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._active = True
    manager._phase = 'capturing'
    manager._current_step_index = 0
    manager._profiles_per_bead = 4
    manager._current_step_capture_earliest_timestamp = 321.0
    manager._current_step_profiles_written = 1
    manager._session_bead_roi_ids = np.asarray([8], dtype=np.uint32)
    manager._session_bead_roi_values = np.asarray([[5, 6, 7, 8]], dtype=np.uint32)
    manager._steps = np.asarray([7.5], dtype=np.float64)

    manager.handle_capture_complete(step_index=0, written_count=0, written_profiles_per_bead=0)

    assert manager._step_capture_complete is False
    assert manager._current_step_profiles_written == 1
    assert sent_commands[0].remaining_profiles_per_bead == 3
    assert sent_commands[0].earliest_timestamp == 321.0
    assert sent_commands[0].bead_ids == (8,)
    assert sent_commands[0].bead_rois == ((5, 6, 7, 8),)
    assert state_updates[-1][1]['phase'] == 'capturing'


# ---------------------------------------------------------------------------
# New ZLUT pipeline tests
# ---------------------------------------------------------------------------

def test_complete_session_dataset_none_guard():
    manager = make_manager()
    manager._dataset = None
    manager._complete_session()
    assert manager._phase != 'evaluating'


def test_maybe_send_progress_delegates():
    manager = make_manager()
    manager._send_progress_called = False

    def fake_send_progress(force=False):
        manager._send_progress_called = True

    manager._send_progress = fake_send_progress
    manager._maybe_send_progress()
    assert manager._send_progress_called is True


def test_latest_focus_state_buffer_none():
    manager = make_manager()
    manager._focus_buffer = None
    assert manager._latest_focus_state() is None


def test_latest_focus_state_no_data():
    manager = make_manager()

    class EmptyBuffer:
        def peak_sorted(self):
            return np.empty((0, 4), dtype=np.float64)

    manager._focus_buffer = EmptyBuffer()
    assert manager._latest_focus_state() is None


def test_latest_focus_state_all_nan():
    manager = make_manager()

    class NaNBuffer:
        def peak_sorted(self):
            return np.asarray([[np.nan, np.nan, np.nan, np.nan]], dtype=np.float64)

    manager._focus_buffer = NaNBuffer()
    assert manager._latest_focus_state() is None


def test_latest_focus_state_valid():
    manager = make_manager()

    class ValidBuffer:
        def peak_sorted(self):
            return np.asarray([[1.0, 50.0, 50.0, 1.0]], dtype=np.float64)

    manager._focus_buffer = ValidBuffer()
    current_z, target_z, is_at_target = manager._latest_focus_state()
    assert current_z == 50.0
    assert target_z == 50.0
    assert is_at_target is True


def test_do_main_loop_not_active():
    manager = make_manager()
    manager._active = False
    manager.do_main_loop()


def test_do_main_loop_cancel_path(monkeypatch):
    manager = make_manager()
    manager._active = True
    manager._cancel_requested = True

    cancel_called = False
    monkeypatch.setattr(manager, "_cancel_session", lambda: setattr(manager, "_cancel_called", True) or True)
    manager._cancel_called = False

    manager.do_main_loop()
    assert manager._cancel_called is True


def test_advance_after_capture_incomplete_step():
    manager = make_manager()
    manager._step_capture_complete = False

    manager._advance_after_capture()

    # Should not advance - step not complete


def test_advance_after_capture_step_complete_not_final():
    manager = make_manager()
    manager._step_capture_complete = True
    manager._current_step_index = 0
    manager._session_bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._session_bead_roi_values = np.asarray([[0, 10, 0, 10]], dtype=np.uint32)
    manager._steps = np.asarray([10.0, 20.0])
    manager._phase = 'capturing'

    manager._advance_after_capture()

    assert manager._current_step_index == 1


def test_report_profile_length_guard_not_active():
    manager = make_manager()
    manager._active = False
    manager.report_profile_length(profile_length=100)
    assert manager._dataset is None


def test_report_profile_length_invalid():
    manager = make_manager()
    manager._active = True
    manager._phase = 'capturing'
    manager.report_profile_length(profile_length=None)
    assert manager._dataset is None


def test_reset_dataset_none_guard():
    manager = make_manager()
    manager._dataset = None
    manager._reset_dataset(destroy=True)
    # Should not crash


def test_reset_dataset_destroy_path():
    manager = make_manager()
    destroy_called = False

    class CloseableDataset:
        def destroy(self):
            nonlocal destroy_called
            destroy_called = True

        def close(self):
            pass

    manager._dataset = CloseableDataset()
    manager._reset_dataset(destroy=True)
    assert destroy_called is True


def test_reset_dataset_close_path():
    manager = make_manager()
    close_called = False

    class CloseableDataset:
        def destroy(self):
            pass

        def close(self):
            nonlocal close_called
            close_called = True

    manager._dataset = CloseableDataset()
    manager._reset_dataset(destroy=False)
    assert close_called is True


# ---------------------------------------------------------------------------
# _build_steps
# ---------------------------------------------------------------------------

def test_build_steps_valid_ascending():
    from magscope.zlut_generation import ZLUTGenerationManager
    result = ZLUTGenerationManager._build_steps(0.0, 10.0, 50.0)
    np.testing.assert_allclose(result, np.asarray([0.0, 10.0, 20.0, 30.0, 40.0, 50.0]))


def test_build_steps_valid_descending():
    from magscope.zlut_generation import ZLUTGenerationManager
    result = ZLUTGenerationManager._build_steps(50.0, -10.0, 0.0)
    np.testing.assert_allclose(result, np.asarray([50.0, 40.0, 30.0, 20.0, 10.0, 0.0]))


def test_build_steps_zero_step_raises():
    from magscope.zlut_generation import ZLUTGenerationManager
    with pytest.raises(ValueError, match="non-zero"):
        ZLUTGenerationManager._build_steps(0.0, 0.0, 50.0)


def test_build_steps_same_start_stop_raises():
    from magscope.zlut_generation import ZLUTGenerationManager
    with pytest.raises(ValueError, match="at least two"):
        ZLUTGenerationManager._build_steps(10.0, 5.0, 10.0)


def test_build_steps_wrong_direction_raises():
    from magscope.zlut_generation import ZLUTGenerationManager
    with pytest.raises(ValueError, match="direction"):
        ZLUTGenerationManager._build_steps(0.0, -10.0, 50.0)


def test_build_steps_not_on_grid_raises():
    from magscope.zlut_generation import ZLUTGenerationManager
    with pytest.raises(ValueError, match="step grid"):
        ZLUTGenerationManager._build_steps(0.0, 7.0, 50.0)


# ---------------------------------------------------------------------------
# _validate_sweep_limits
# ---------------------------------------------------------------------------

def test_validate_sweep_limits_in_range():
    from magscope.zlut_generation import ZLUTGenerationManager
    ZLUTGenerationManager._validate_sweep_limits(10.0, 100.0, 0.0, 200.0)


def test_validate_sweep_limits_below_min():
    from magscope.zlut_generation import ZLUTGenerationManager
    with pytest.raises(ValueError, match="exceeds focus motor limits"):
        ZLUTGenerationManager._validate_sweep_limits(-10.0, 50.0, 0.0, 100.0)


def test_validate_sweep_limits_above_max():
    from magscope.zlut_generation import ZLUTGenerationManager
    with pytest.raises(ValueError, match="exceeds focus motor limits"):
        ZLUTGenerationManager._validate_sweep_limits(50.0, 150.0, 0.0, 100.0)


# ---------------------------------------------------------------------------
# _discover_focus_motor_name TypeError
# ---------------------------------------------------------------------------

def test_discover_focus_motor_name_typeerror_continue():
    from magscope.hardware import FocusMotorBase
    manager = make_manager()
    manager.hardware_types = {'focus': FocusMotorBase, 'bad': object()}
    name = manager._discover_focus_motor_name()
    assert name == 'focus'


# ---------------------------------------------------------------------------
# _cleanup_runtime_state
# ---------------------------------------------------------------------------

def test_cleanup_runtime_state_resets_all_attributes():
    manager = make_manager()
    manager._active = True
    manager._cancel_requested = True
    manager._current_step_index = 5
    manager._profile_length = 100
    manager._profiles_per_bead = 4
    manager._phase = 'capturing'

    manager._cleanup_runtime_state(destroy_dataset=True)

    assert manager._active is False
    assert manager._cancel_requested is False
    assert manager._current_step_index == 0
    assert manager._profile_length is None
    assert manager._profiles_per_bead == 0
    assert manager._phase == 'idle'


# ---------------------------------------------------------------------------
# _bead_id_payload / _bead_roi_payload
# ---------------------------------------------------------------------------

def test_bead_id_payload():
    from magscope.zlut_generation import ZLUTGenerationManager
    manager = make_manager()
    manager._session_bead_roi_ids = np.asarray([1, 2, 3], dtype=np.uint32)
    result = manager._bead_id_payload()
    assert result == (1, 2, 3)


def test_bead_roi_payload():
    from magscope.zlut_generation import ZLUTGenerationManager
    manager = make_manager()
    manager._session_bead_roi_values = np.asarray([[0, 10, 0, 10], [10, 20, 10, 20]], dtype=np.uint32)
    result = manager._bead_roi_payload()
    assert result == ((0, 10, 0, 10), (10, 20, 10, 20))
