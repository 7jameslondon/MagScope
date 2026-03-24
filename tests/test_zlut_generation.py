import numpy as np

from magscope.ipc_commands import (
    LoadZLUTCommand,
    RequestFocusMotorLimitsCommand,
    RequestZLUTProfileLengthCommand,
    SetAcquisitionOnCommand,
    ShowErrorCommand,
    UpdateZLUTGenerationEvaluationCommand,
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
    manager.save_generated_zlut(str(filepath), 3)

    assert saved[0][0] == filepath
    np.testing.assert_allclose(saved[0][1], np.asarray([[1.0, 2.0], [3.0, 4.0]]))
    assert isinstance(manager._sent_commands[0], LoadZLUTCommand)
    assert any(isinstance(command, UpdateZLUTGenerationEvaluationCommand) for command in manager._sent_commands)


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


def test_prepare_session_requires_tracking_acquisition_mode():
    manager = make_manager()
    manager._cleanup_runtime_state = lambda destroy_dataset: None
    manager._acquisition_mode = AcquisitionMode.FULL_VIDEO

    try:
        manager._prepare_session(0.0, 5.0, 10.0, 7)
    except RuntimeError as exc:
        assert str(exc) == (
            'Z-LUT generation requires a tracking acquisition mode. '
            'Switch to track, track & video (cropped), or track & video (full).'
        )
    else:
        raise AssertionError('Expected RuntimeError for non-tracking acquisition mode')


def test_start_generation_fails_fast_for_non_tracking_acquisition_mode():
    manager = make_manager()
    state_updates = []
    manager._send_state = lambda *args, **kwargs: state_updates.append((args, kwargs))
    manager._acquisition_mode = AcquisitionMode.CROP_VIDEO

    manager.start_generation(0.0, 5.0, 10.0, 7)

    assert any(isinstance(command, ShowErrorCommand) for command in manager._sent_commands)
    error_command = next(command for command in manager._sent_commands if isinstance(command, ShowErrorCommand))
    assert error_command.text == 'Could not start Z-LUT generation'
    assert error_command.details == (
        'Z-LUT generation requires a tracking acquisition mode. '
        'Switch to track, track & video (cropped), or track & video (full).'
    )
    assert state_updates == [
        (
            ('Generation failed to start.',),
            {
                'detail': (
                    'Z-LUT generation requires a tracking acquisition mode. '
                    'Switch to track, track & video (cropped), or track & video (full).'
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
    manager._prepare_session = lambda *args: prepared.append(args)

    manager.start_generation(0.0, 5.0, 10.0, 7)
    manager.report_focus_motor_limits(0.0, 10.0)

    assert prepared == [(0.0, 5.0, 10.0, 7)]
    assert manager._pending_start_request is None
    assert isinstance(manager._sent_commands[-2], SetAcquisitionOnCommand)
    assert manager._sent_commands[-2].value is True
    assert isinstance(manager._sent_commands[-1], RequestZLUTProfileLengthCommand)
    assert state_updates[-1] == (
        ('Waiting for a processed frame to measure profile length.',),
        {
            'detail': 'Z-LUT generation is preparing shared memory and capture settings.',
            'running': True,
            'can_cancel': True,
            'phase': 'waiting_profile_length',
        },
    )


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
    manager._steps = np.asarray([12.5], dtype=np.float64)

    manager.handle_capture_complete(step_index=0, written_count=2, written_profiles_per_bead=1)

    assert manager._step_capture_complete is False
    assert manager._current_step_profiles_written == 3
    assert sent_commands[0].remaining_profiles_per_bead == 2
    assert sent_commands[0].earliest_timestamp == 123.0


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
    manager._steps = np.asarray([7.5], dtype=np.float64)

    manager.handle_capture_complete(step_index=0, written_count=0, written_profiles_per_bead=0)

    assert manager._step_capture_complete is False
    assert manager._current_step_profiles_written == 1
    assert sent_commands[0].remaining_profiles_per_bead == 3
    assert sent_commands[0].earliest_timestamp == 321.0
    assert state_updates[-1][1]['phase'] == 'capturing'
