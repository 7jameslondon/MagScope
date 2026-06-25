import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from magscope.ipc_commands import ShowMessageCommand, UpdateZLUTMetadataCommand


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

magscope_pkg = types.ModuleType("magscope")
magscope_pkg.__path__ = [str(ROOT / "magscope")]
sys.modules.setdefault("magscope", magscope_pkg)

qt_module = types.ModuleType("PyQt6")
qt_core_module = types.ModuleType("PyQt6.QtCore")


class _DummyQSettings:
    def __init__(self, *args, **kwargs):
        self._values = {}

    def beginGroup(self, _):  # noqa: N802 - Qt naming
        return None

    def contains(self, key: str) -> bool:  # noqa: N802 - Qt naming
        return key in self._values

    def endGroup(self):  # noqa: N802 - Qt naming
        return None

    def remove(self, key: str):  # noqa: N802 - Qt naming
        if key in ("", None):
            self._values.clear()
        else:
            self._values.pop(key, None)

    def setValue(self, key: str, value: object):  # noqa: N802 - Qt naming
        self._values[key] = value

    def sync(self):  # noqa: N802 - Qt naming
        return None

    def value(self, key: str):  # noqa: N802 - Qt naming
        return self._values.get(key)


qt_core_module.QSettings = _DummyQSettings
qt_module.QtCore = qt_core_module
sys.modules.setdefault("PyQt6", qt_module)
sys.modules.setdefault("PyQt6.QtCore", qt_core_module)

magtrack_module = types.ModuleType("magtrack")
magtrack_cupy_module = types.ModuleType("magtrack._cupy")
magtrack_simulation_module = types.ModuleType("magtrack.simulation")
magtrack_cupy_module.cp = None
magtrack_cupy_module.is_cupy_available = lambda: False
magtrack_simulation_module.simulate_beads = lambda *args, **kwargs: None
sys.modules.setdefault("magtrack", magtrack_module)
sys.modules.setdefault("magtrack._cupy", magtrack_cupy_module)
sys.modules.setdefault("magtrack.simulation", magtrack_simulation_module)

videoprocessing_spec = importlib.util.spec_from_file_location(
    "magscope.videoprocessing", ROOT / "magscope" / "videoprocessing.py"
)
videoprocessing = importlib.util.module_from_spec(videoprocessing_spec)
sys.modules["magscope.videoprocessing"] = videoprocessing
videoprocessing_spec.loader.exec_module(videoprocessing)

camera_spec = importlib.util.spec_from_file_location(
    "magscope.camera", ROOT / "magscope" / "camera.py"
)
camera = importlib.util.module_from_spec(camera_spec)
sys.modules["magscope.camera"] = camera
camera_spec.loader.exec_module(camera)

VideoProcessorManager = videoprocessing.VideoProcessorManager
VideoWorker = videoprocessing.VideoWorker
BufferUnderflow = videoprocessing.BufferUnderflow
CameraManager = camera.CameraManager


class DummyQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


class DummyLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyValue:
    def __init__(self, value=0):
        self.value = value

    def get_lock(self):
        return DummyLock()


class DummyTaskQueue:
    def __init__(self, items):
        self._items = list(items)

    def get(self):
        return self._items.pop(0)


class DummyVideoBuffer:
    def __init__(self):
        self.read_calls = 0

    def read_stack_no_return(self):
        self.read_calls += 1


class DummyStackVideoBuffer(DummyVideoBuffer):
    def __init__(self):
        super().__init__()
        self.stack = np.arange(18, dtype=np.uint16).reshape((3, 3, 2))
        self.timestamps = np.asarray([1.0, 1.1], dtype=np.float64)
        self.stack_shape = self.stack.shape

    def peak_stack(self):
        return self.stack, self.timestamps


class DummyTracksBuffer:
    def __init__(self):
        self.rows = []

    def write(self, rows):
        self.rows.append(np.asarray(rows).copy())


class DummyReadableVideoBuffer:
    def __init__(self, *, unread_stacks: int, n_images: int = 5, level: float = 0.0):
        self.unread_stacks = unread_stacks
        self.n_images = n_images
        self.stack_shape = (1, 1, n_images)
        self.n_total_images = unread_stacks * n_images if unread_stacks > 0 else n_images
        self.read_calls = 0
        self._level = level

    def check_read_stack(self):
        return self.unread_stacks > 0

    def get_unread_stack_count(self):
        return self.unread_stacks

    def read_stack_no_return(self):
        if self.unread_stacks <= 0:
            raise BufferUnderflow('BufferUnderflow')
        self.unread_stacks -= 1
        self.read_calls += 1

    def get_level(self):
        return self._level


@pytest.fixture
def manager():
    type(VideoProcessorManager)._instances.pop(VideoProcessorManager, None)
    manager = VideoProcessorManager()
    manager._tasks = DummyQueue()
    manager.settings = {'magnification': 60}
    manager.camera_type = type('CameraType', (), {'nm_per_px': 123.0})
    manager._tracking_options = {'center_of_mass': {'background': 'median'}}
    manager._pending_profile_length_request = False
    manager._pending_zlut_profile_length_request = False
    manager._save_profiles = False
    manager._zlut = None
    manager._bead_roi_ids = []
    manager._bead_roi_values = []
    return manager


@pytest.fixture
def camera_manager():
    type(CameraManager)._instances.pop(CameraManager, None)
    manager = CameraManager()
    manager.shared_values = SimpleNamespace(
        video_process_reserved_stacks=DummyValue(0),
        video_process_completed_stacks=DummyValue(0),
    )
    return manager


def test_add_task_returns_true_for_normal_processing_task(manager):
    result = manager._add_task()

    assert result is True
    assert len(manager._tasks.items) == 1
    assert 'zlut_capture' not in manager._tasks.items[0]
    assert manager._tasks.items[0]['tracking_recording_id'] == manager._tracking_recording_id
    assert manager._tasks.items[0]['tracking_batch_sequence'] == 0
    assert manager._tracking_task_sequence == 1
    assert manager._tasks.items[0]['save_tracking_roi_positions'] is False
    assert manager._tasks.items[0]['tracking_file_max_duration_ns'] == 3_600_000_000_000


def test_add_task_uses_tracking_file_rotation_settings(manager):
    from magscope.settings import (
        MagScopeSettings,
        TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING,
        TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING,
    )

    manager.settings = MagScopeSettings(
        {
            'magnification': 60,
            TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING: False,
            TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING: 30,
        }
    )

    manager._add_task()

    assert manager._tasks.items[0]['tracking_file_max_duration_ns'] is None

    manager._tasks.items.clear()
    manager.settings = MagScopeSettings(
        {
            'magnification': 60,
            TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING: True,
            TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING: 30,
        }
    )

    manager._add_task()

    assert manager._tasks.items[0]['tracking_file_max_duration_ns'] == 1_800_000_000_000


def test_add_task_assigns_monotonic_tracking_batch_sequence(manager):
    manager._add_task()
    manager._add_task()

    assert manager._tasks.items[0]['tracking_batch_sequence'] == 0
    assert manager._tasks.items[1]['tracking_batch_sequence'] == 1
    assert manager._tracking_task_sequence == 2


def test_add_task_clears_zlut_capture_state_after_successful_enqueue(manager):
    manager._zlut_capture_step_index = 4
    manager._zlut_capture_earliest_timestamp = 12.5
    manager._zlut_capture_motor_z_value = 33.0
    manager._zlut_capture_remaining_profiles_per_bead = 7
    manager._zlut_frozen_bead_ids = np.asarray([9, 10], dtype=np.uint32)
    manager._zlut_frozen_bead_rois = np.asarray([[1, 2, 3, 4], [5, 6, 7, 8]], dtype=np.uint32)

    result = manager._add_task()

    assert result is True
    assert manager._tasks.items[0]['zlut_capture'] == {
        'step_index': 4,
        'earliest_timestamp': 12.5,
        'motor_z_value': 33.0,
        'remaining_profiles_per_bead': 7,
    }
    np.testing.assert_array_equal(manager._tasks.items[0]['bead_ids'], np.asarray([9, 10], dtype=np.uint32))
    np.testing.assert_array_equal(
        manager._tasks.items[0]['bead_rois'],
        np.asarray([[1, 2, 3, 4], [5, 6, 7, 8]], dtype=np.uint32),
    )
    assert manager._zlut_capture_step_index is None
    assert manager._zlut_capture_earliest_timestamp is None
    assert manager._zlut_capture_motor_z_value is None
    assert manager._zlut_capture_remaining_profiles_per_bead is None


def test_set_acquisition_dir_on_rotates_tracking_recording(manager):
    initial_recording_id = manager._tracking_recording_id

    manager.set_acquisition_dir_on(True)
    manager.set_acquisition_dir_on(True)
    manager.set_acquisition_dir_on(False)

    assert manager._tracking_recording_id == initial_recording_id + 1


def test_set_acquisition_dir_rotates_tracking_recording_when_saving(manager, tmp_path):
    manager._acquisition_dir_on = True
    manager._acquisition_dir = str(tmp_path / 'first')
    initial_recording_id = manager._tracking_recording_id

    manager.set_acquisition_dir(str(tmp_path / 'second'))
    manager.set_acquisition_dir(str(tmp_path / 'second'))

    assert manager._tracking_recording_id == initial_recording_id + 1


def test_set_settings_rotates_tracking_recording_when_roi_save_setting_changes(manager):
    from magscope.settings import MagScopeSettings, SAVE_TRACKING_ROI_POSITIONS_SETTING

    manager._acquisition_dir_on = True
    manager.settings = MagScopeSettings({SAVE_TRACKING_ROI_POSITIONS_SETTING: False})
    initial_recording_id = manager._tracking_recording_id

    manager.set_settings(MagScopeSettings({SAVE_TRACKING_ROI_POSITIONS_SETTING: True}))
    manager.set_settings(MagScopeSettings({SAVE_TRACKING_ROI_POSITIONS_SETTING: True}))

    assert manager._tracking_recording_id == initial_recording_id + 1


def test_set_settings_rotates_tracking_recording_when_file_rotation_setting_changes(manager):
    from magscope.settings import (
        MagScopeSettings,
        TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING,
        TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING,
    )

    manager._acquisition_dir_on = True
    manager.settings = MagScopeSettings(
        {
            TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING: True,
            TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING: 60,
        }
    )
    initial_recording_id = manager._tracking_recording_id

    manager.set_settings(
        MagScopeSettings(
            {
                TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING: True,
                TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING: 30,
            }
        )
    )
    manager.set_settings(
        MagScopeSettings(
            {
                TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING: True,
                TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING: 30,
            }
        )
    )
    manager.set_settings(
        MagScopeSettings(
            {
                TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING: False,
                TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING: 30,
            }
        )
    )

    assert manager._tracking_recording_id == initial_recording_id + 2


def test_manual_tracking_file_rotation_only_rotates_when_saving(manager):
    initial_recording_id = manager._tracking_recording_id

    manager._acquisition_dir_on = False
    manager.start_new_tracking_data_file()
    assert manager._tracking_recording_id == initial_recording_id

    manager._acquisition_dir_on = True
    manager.start_new_tracking_data_file()
    assert manager._tracking_recording_id == initial_recording_id + 1


def test_add_task_uses_frozen_rois_for_pending_zlut_profile_length(manager):
    manager._bead_roi_ids = np.asarray([1], dtype=np.uint32)
    manager._bead_roi_values = np.asarray([[10, 20, 30, 40]], dtype=np.uint32)
    manager._pending_zlut_profile_length_request = True
    manager._zlut_frozen_bead_ids = np.asarray([7, 8], dtype=np.uint32)
    manager._zlut_frozen_bead_rois = np.asarray([[1, 2, 3, 4], [11, 12, 13, 14]], dtype=np.uint32)

    result = manager._add_task()

    assert result is True
    np.testing.assert_array_equal(manager._tasks.items[0]['bead_ids'], np.asarray([7, 8], dtype=np.uint32))
    np.testing.assert_array_equal(
        manager._tasks.items[0]['bead_rois'],
        np.asarray([[1, 2, 3, 4], [11, 12, 13, 14]], dtype=np.uint32),
    )


def test_clear_pending_zlut_profile_length_request_resets_frozen_rois(manager):
    manager._pending_zlut_profile_length_request = True
    manager._zlut_frozen_bead_ids = np.asarray([7, 8], dtype=np.uint32)
    manager._zlut_frozen_bead_rois = np.asarray([[1, 2, 3, 4], [11, 12, 13, 14]], dtype=np.uint32)

    manager.clear_pending_zlut_profile_length_request()

    assert manager._pending_zlut_profile_length_request is False
    assert manager._zlut_frozen_bead_ids.size == 0
    assert manager._zlut_frozen_bead_rois.shape == (0, 4)
    assert manager._should_use_frozen_zlut_rois() is False


def test_do_main_loop_reserves_stack_before_enqueue(manager):
    manager._acquisition_on = True
    manager.video_buffer = DummyReadableVideoBuffer(unread_stacks=1)
    manager.shared_values = SimpleNamespace(video_process_reserved_stacks=DummyValue(0))
    reserved_during_enqueue = []

    def fake_add_task():
        reserved_during_enqueue.append(manager.shared_values.video_process_reserved_stacks.value)
        return True

    manager._add_task = fake_add_task

    manager.do_main_loop()

    assert reserved_during_enqueue == [1]
    assert manager.shared_values.video_process_reserved_stacks.value == 1


def test_do_main_loop_releases_reservation_when_enqueue_fails(manager):
    manager._acquisition_on = True
    manager.video_buffer = DummyReadableVideoBuffer(unread_stacks=1)
    manager.shared_values = SimpleNamespace(video_process_reserved_stacks=DummyValue(0))
    manager._add_task = lambda: False

    manager.do_main_loop()

    assert manager.shared_values.video_process_reserved_stacks.value == 0


def test_do_main_loop_skips_enqueue_when_stack_already_reserved(manager):
    manager._acquisition_on = True
    manager.video_buffer = DummyReadableVideoBuffer(unread_stacks=1)
    manager.shared_values = SimpleNamespace(video_process_reserved_stacks=DummyValue(1))
    calls = []
    manager._add_task = lambda: calls.append(True)

    manager.do_main_loop()

    assert calls == []


def test_worker_reports_zlut_capture_failure_to_manager():
    queue = DummyQueue()
    worker = VideoWorker(
        tasks=queue,
        locks={},
        reserved_stacks=DummyValue(0),
        completed_stacks=DummyValue(0),
        busy_count=None,
        gpu_lock=None,
        profile_length_queue=None,
        warning_queue=None,
        zlut_capture_complete_queue=queue,
        zlut_profile_length_queue=None,
        live_profile_enabled=None,
        live_profile_bead=None,
    )

    worker._report_zlut_capture_task_failure(
        {
            'zlut_capture': {
                'step_index': 4,
            }
        },
        RuntimeError('capture failed'),
    )

    assert queue.items == [(4, 0, 0, 'capture failed')]


def test_worker_reports_zlut_capture_retry_when_dataset_is_not_ready():
    queue = DummyQueue()
    worker = VideoWorker(
        tasks=queue,
        locks={},
        reserved_stacks=DummyValue(0),
        completed_stacks=DummyValue(0),
        busy_count=None,
        gpu_lock=None,
        profile_length_queue=None,
        warning_queue=None,
        zlut_capture_complete_queue=queue,
        zlut_profile_length_queue=None,
        live_profile_enabled=None,
        live_profile_bead=None,
    )

    worker._report_zlut_capture_task_retry({'step_index': 4})

    assert queue.items == [(4, 0, 0, None)]


def test_worker_recovers_from_buffer_underflow_and_retries_zlut_capture(monkeypatch):
    task = {'zlut_capture': {'step_index': 4}}
    completion_queue = DummyQueue()
    reserved_stacks = DummyValue(1)
    completed_stacks = DummyValue(0)
    busy_count = DummyValue(0)
    worker = VideoWorker(
        tasks=DummyTaskQueue([task, None]),
        locks={},
        reserved_stacks=reserved_stacks,
        completed_stacks=completed_stacks,
        busy_count=busy_count,
        gpu_lock=None,
        profile_length_queue=None,
        warning_queue=None,
        zlut_capture_complete_queue=completion_queue,
        zlut_profile_length_queue=None,
        live_profile_enabled=None,
        live_profile_bead=None,
    )

    monkeypatch.setattr(videoprocessing, 'LiveProfileBuffer', lambda create, locks: object())
    monkeypatch.setattr(videoprocessing, 'MatrixBuffer', lambda create, name, locks: object())
    monkeypatch.setattr(videoprocessing, 'VideoBuffer', lambda create, locks: object())
    monkeypatch.setattr(worker, 'process', lambda current_task: (_ for _ in ()).throw(BufferUnderflow('BufferUnderflow')))

    worker.run()

    assert completion_queue.items == [(4, 0, 0, None)]
    assert reserved_stacks.value == 0
    assert completed_stacks.value == 0
    assert busy_count.value == 0


def test_release_stack_marks_completion_and_clears_reservation():
    worker = VideoWorker(
        tasks=DummyQueue(),
        locks={},
        reserved_stacks=DummyValue(1),
        completed_stacks=DummyValue(0),
        busy_count=DummyValue(0),
        gpu_lock=None,
        profile_length_queue=None,
        warning_queue=None,
        zlut_capture_complete_queue=None,
        zlut_profile_length_queue=None,
        live_profile_enabled=None,
        live_profile_bead=None,
    )
    worker._video_buffer = DummyVideoBuffer()
    worker._task_owes_reserved_stack = True

    worker._release_stack()

    assert worker._video_buffer.read_calls == 1
    assert worker._completed_stacks.value == 1
    assert worker._reserved_stacks.value == 0
    assert worker._task_owes_reserved_stack is False


def test_worker_enqueues_tracking_data_batch_when_saving_enabled(monkeypatch, tmp_path):
    tracking_queue = DummyQueue()
    worker = VideoWorker(
        tasks=DummyQueue(),
        locks={},
        reserved_stacks=DummyValue(1),
        completed_stacks=DummyValue(0),
        busy_count=DummyValue(0),
        gpu_lock=DummyLock(),
        profile_length_queue=None,
        warning_queue=None,
        zlut_capture_complete_queue=None,
        zlut_profile_length_queue=None,
        live_profile_enabled=DummyValue(0),
        live_profile_bead=DummyValue(-1),
        tracking_data_queue=tracking_queue,
    )
    worker._video_buffer = DummyStackVideoBuffer()
    worker._tracks_buffer = DummyTracksBuffer()
    worker._live_profile_buffer = None
    worker._task_owes_reserved_stack = True

    def fake_tracker(stack, zlut, **kwargs):
        return (
            np.asarray([0.5, 1.5, 2.5, 3.5], dtype=np.float64),
            np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float64),
            np.asarray([10.0, np.nan, 30.0, 40.0], dtype=np.float64),
            np.zeros((2, 4), dtype=np.float64),
        )

    monkeypatch.setattr(
        videoprocessing.magtrack,
        'stack_to_xyzp_advanced',
        fake_tracker,
        raising=False,
    )

    worker.process(
        {
            'acquisition_dir': str(tmp_path),
            'acquisition_dir_on': True,
            'acquisition_mode': videoprocessing.AcquisitionMode.TRACK,
            'bead_ids': np.asarray([3, 4], dtype=np.uint32),
            'bead_rois': np.asarray([[0, 2, 0, 2], [1, 3, 1, 3]], dtype=np.uint32),
            'save_profiles': False,
            'save_tracking_roi_positions': True,
            'tracking_file_max_duration_ns': 123_000_000,
            'tracking_recording_id': 42,
            'tracking_batch_sequence': 17,
            'zlut': None,
            'nm_per_px': 2.0,
            'magnification': 1.0,
            'tracking_options': {},
        }
    )

    assert len(worker._tracks_buffer.rows) == 1
    saved_tracks = worker._tracks_buffer.rows[0]
    assert saved_tracks.shape == (4, 7)
    np.testing.assert_array_equal(saved_tracks[:, 4], np.asarray([3.0, 4.0, 3.0, 4.0]))
    assert len(tracking_queue.items) == 1
    batch = tracking_queue.items[0]
    assert batch.recording_id == 42
    assert batch.batch_sequence == 17
    assert batch.include_roi_positions is True
    assert batch.max_file_duration_ns == 123_000_000
    np.testing.assert_array_equal(batch.frame_offsets, np.asarray([0, 2, 4], dtype=np.uint64))
    np.testing.assert_array_equal(batch.bead_ids, np.asarray([3, 4, 3, 4], dtype=np.uint16))
    np.testing.assert_array_equal(
        batch.roi_positions_px,
        np.asarray([[0, 0], [1, 1], [0, 0], [1, 1]], dtype=np.uint16),
    )
    assert list(tmp_path.glob('Bead Positions*.txt')) == []


def test_worker_does_not_enqueue_tracking_data_when_saving_disabled(monkeypatch, tmp_path):
    tracking_queue = DummyQueue()
    worker = VideoWorker(
        tasks=DummyQueue(),
        locks={},
        reserved_stacks=DummyValue(1),
        completed_stacks=DummyValue(0),
        busy_count=DummyValue(0),
        gpu_lock=DummyLock(),
        profile_length_queue=None,
        warning_queue=None,
        zlut_capture_complete_queue=None,
        zlut_profile_length_queue=None,
        live_profile_enabled=DummyValue(0),
        live_profile_bead=DummyValue(-1),
        tracking_data_queue=tracking_queue,
    )
    worker._video_buffer = DummyStackVideoBuffer()
    worker._tracks_buffer = DummyTracksBuffer()
    worker._live_profile_buffer = None
    worker._task_owes_reserved_stack = True

    def fake_tracker(stack, zlut, **kwargs):
        return (
            np.zeros((4,), dtype=np.float64),
            np.zeros((4,), dtype=np.float64),
            np.zeros((4,), dtype=np.float64),
            np.zeros((2, 4), dtype=np.float64),
        )

    monkeypatch.setattr(
        videoprocessing.magtrack,
        'stack_to_xyzp_advanced',
        fake_tracker,
        raising=False,
    )

    worker.process(
        {
            'acquisition_dir': str(tmp_path),
            'acquisition_dir_on': False,
            'acquisition_mode': videoprocessing.AcquisitionMode.TRACK,
            'bead_ids': np.asarray([3, 4], dtype=np.uint32),
            'bead_rois': np.asarray([[0, 2, 0, 2], [1, 3, 1, 3]], dtype=np.uint32),
            'save_profiles': False,
            'save_tracking_roi_positions': False,
            'tracking_recording_id': 42,
            'zlut': None,
            'nm_per_px': 2.0,
            'magnification': 1.0,
            'tracking_options': {},
        }
    )

    assert len(worker._tracks_buffer.rows) == 1
    assert tracking_queue.items == []


def test_camera_manager_releases_only_newly_completed_stacks(camera_manager):
    releases = []
    camera_manager.video_buffer = DummyReadableVideoBuffer(unread_stacks=0, n_images=5)
    camera_manager.camera = SimpleNamespace(release=lambda: releases.append('release'))
    camera_manager.shared_values.video_process_completed_stacks.value = 2

    camera_manager._release_completed_pool_buffers()
    camera_manager.shared_values.video_process_completed_stacks.value = 3
    camera_manager._release_completed_pool_buffers()

    assert len(releases) == 15


def test_camera_manager_does_not_release_reserved_unattached_stack(camera_manager):
    releases = []
    camera_manager.video_buffer = DummyReadableVideoBuffer(unread_stacks=1, n_images=5)
    camera_manager.camera = SimpleNamespace(release=lambda: releases.append('release'))
    camera_manager.shared_values.video_process_reserved_stacks.value = 1

    camera_manager._release_unattached_buffers()

    assert camera_manager.video_buffer.read_calls == 0
    assert releases == []


def test_camera_manager_purge_preserves_reserved_stack(camera_manager):
    releases = []
    camera_manager.video_buffer = DummyReadableVideoBuffer(unread_stacks=2, n_images=5, level=0.8)
    camera_manager.camera = SimpleNamespace(release=lambda: releases.append('release'))
    camera_manager.shared_values.video_process_reserved_stacks.value = 1

    purged = camera_manager._purge_buffers()

    assert purged == 1
    assert camera_manager.video_buffer.unread_stacks == 1
    assert len(releases) == 5


def test_extract_zlut_metadata_allows_nan_profile_values():
    metadata = VideoProcessorManager._extract_zlut_metadata(
        np.asarray(
            [
                [10.0, 20.0, 30.0],
                [1.0, np.nan, 3.0],
                [4.0, 5.0, np.nan],
            ],
            dtype=np.float64,
        )
    )

    assert metadata == {
        'z_min': 10.0,
        'z_max': 30.0,
        'step_size': 10.0,
        'profile_length': 2,
    }


@pytest.mark.parametrize('bad_value', [np.nan, np.inf])
def test_extract_zlut_metadata_rejects_non_finite_z_reference_values(bad_value):
    with pytest.raises(ValueError, match='z-reference row'):
        VideoProcessorManager._extract_zlut_metadata(
            np.asarray(
                [
                    [10.0, bad_value, 30.0],
                    [1.0, 2.0, 3.0],
                ],
                dtype=np.float64,
            )
        )


def test_load_zlut_file_failure_clears_state_and_broadcasts_empty_metadata(manager, monkeypatch, tmp_path):
    manager._zlut_path = Path('existing.txt')
    manager._zlut_metadata = {
        'z_min': 10.0,
        'z_max': 30.0,
        'step_size': 10.0,
        'profile_length': 2,
    }
    manager._zlut = np.asarray([[10.0, 20.0], [1.0, 2.0]], dtype=np.float64)
    manager._lookup_z_warning_reported = True

    monkeypatch.setattr(
        videoprocessing.np,
        'loadtxt',
        lambda _: np.asarray([[10.0, np.nan], [1.0, 2.0]], dtype=np.float64),
    )

    sent_commands = []
    manager.send_ipc = sent_commands.append

    manager.load_zlut_file(str(tmp_path / 'bad_zlut.txt'))

    assert manager._zlut is None
    assert manager._zlut_path is None
    assert manager._zlut_metadata is None
    assert manager._lookup_z_warning_reported is False
    assert isinstance(sent_commands[0], ShowMessageCommand)
    assert isinstance(sent_commands[1], UpdateZLUTMetadataCommand)
    assert sent_commands[1] == UpdateZLUTMetadataCommand(
        filepath=None,
        z_min=None,
        z_max=None,
        step_size=None,
        profile_length=None,
    )


# ---------------------------------------------------------------------------
# _extract_zlut_metadata error paths
# ---------------------------------------------------------------------------

def test_extract_zlut_metadata_rejects_1d_array():
    from magscope.videoprocessing import VideoProcessorManager
    with pytest.raises(ValueError, match="2D array"):
        VideoProcessorManager._extract_zlut_metadata(np.asarray([1.0, 2.0]))


def test_extract_zlut_metadata_rejects_insufficient_rows():
    from magscope.videoprocessing import VideoProcessorManager
    with pytest.raises(ValueError, match="at least one profile row"):
        VideoProcessorManager._extract_zlut_metadata(np.asarray([[1.0, 2.0]]))


def test_extract_zlut_metadata_rejects_insufficient_columns():
    from magscope.videoprocessing import VideoProcessorManager
    with pytest.raises(ValueError, match="at least two z-reference values"):
        VideoProcessorManager._extract_zlut_metadata(np.asarray([[1.0], [2.0]]))


# ---------------------------------------------------------------------------
# update_tracking_options / set_settings
# ---------------------------------------------------------------------------

def test_update_tracking_options_deep_copies():
    from magscope.videoprocessing import VideoProcessorManager
    type(VideoProcessorManager)._instances.pop(VideoProcessorManager, None)
    manager = VideoProcessorManager()
    original = {"key": "value"}
    manager.update_tracking_options(original)
    assert manager._tracking_options == original
    assert manager._tracking_options is not original


def test_set_settings_resets_lookup_z_warning():
    from magscope.videoprocessing import VideoProcessorManager
    from magscope.settings import MagScopeSettings
    type(VideoProcessorManager)._instances.pop(VideoProcessorManager, None)
    manager = VideoProcessorManager()
    manager._lookup_z_warning_reported = True
    manager.settings = MagScopeSettings({"video processors n": 2})
    manager.set_settings(MagScopeSettings({"video processors n": 2}))
    assert manager._lookup_z_warning_reported is False


# ---------------------------------------------------------------------------
# ZLUT sweep arm / disarm
# ---------------------------------------------------------------------------

def test_arm_zlut_sweep_capture_sets_flags():
    from magscope.videoprocessing import VideoProcessorManager
    from magscope.settings import MagScopeSettings
    type(VideoProcessorManager)._instances.pop(VideoProcessorManager, None)
    manager = VideoProcessorManager()
    manager.settings = MagScopeSettings({"video processors n": 2})
    manager.arm_zlut_sweep_capture(
        step_index=0,
        motor_z_value=50.0,
        remaining_profiles_per_bead=4,
        earliest_timestamp=100.0,
    )
    assert manager._zlut_capture_step_index == 0
    assert manager._zlut_capture_motor_z_value == 50.0


def test_disarm_zlut_sweep_capture_resets_state():
    from magscope.videoprocessing import VideoProcessorManager
    from magscope.settings import MagScopeSettings
    type(VideoProcessorManager)._instances.pop(VideoProcessorManager, None)
    manager = VideoProcessorManager()
    manager.settings = MagScopeSettings({"video processors n": 2})
    manager._zlut_capture_step_index = 0
    manager._zlut_capture_motor_z_value = 50.0

    manager.disarm_zlut_sweep_capture()

    assert manager._zlut_capture_step_index is None
    assert manager._zlut_capture_motor_z_value is None


# ---------------------------------------------------------------------------
# script_wait_until_acquisition_on / _finish_waiting_when_ready
# ---------------------------------------------------------------------------

def test_script_wait_until_acquisition_on_sets_flag():
    from magscope.videoprocessing import VideoProcessorManager
    type(VideoProcessorManager)._instances.pop(VideoProcessorManager, None)
    manager = VideoProcessorManager()
    manager.script_wait_until_acquisition_on(True)
    assert manager._waiting_for_acquisition is True


def test_finish_waiting_when_ready_sends_ipc():
    from magscope.videoprocessing import VideoProcessorManager
    type(VideoProcessorManager)._instances.pop(VideoProcessorManager, None)
    manager = VideoProcessorManager()
    sent = []
    manager.send_ipc = sent.append
    manager._waiting_for_acquisition = True
    manager._acquisition_on = True

    manager._finish_waiting_when_ready()

    assert manager._waiting_for_acquisition is None
    assert len(sent) == 1
