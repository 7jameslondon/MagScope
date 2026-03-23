import numpy as np

from magscope.ipc_commands import LoadZLUTCommand, UpdateZLUTGenerationEvaluationCommand
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
    manager._current_step_profiles_written = 2
    manager._steps = np.asarray([12.5], dtype=np.float64)

    manager.handle_capture_complete(step_index=0, written_count=2, written_profiles_per_bead=1)

    assert manager._step_capture_complete is False
    assert manager._current_step_profiles_written == 3
    assert sent_commands[0].remaining_profiles_per_bead == 2
