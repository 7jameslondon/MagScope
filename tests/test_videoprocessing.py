import importlib.util
import sys
import types
from pathlib import Path

import pytest
import numpy as np

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
magtrack_cupy_module.cp = None
magtrack_cupy_module.is_cupy_available = lambda: False
sys.modules.setdefault("magtrack", magtrack_module)
sys.modules.setdefault("magtrack._cupy", magtrack_cupy_module)

videoprocessing_spec = importlib.util.spec_from_file_location(
    "magscope.videoprocessing", ROOT / "magscope" / "videoprocessing.py"
)
videoprocessing = importlib.util.module_from_spec(videoprocessing_spec)
sys.modules["magscope.videoprocessing"] = videoprocessing
videoprocessing_spec.loader.exec_module(videoprocessing)

VideoProcessorManager = videoprocessing.VideoProcessorManager
VideoWorker = videoprocessing.VideoWorker


class DummyQueue:
    def __init__(self):
        self.items = []

    def put_nowait(self, item):
        self.items.append(item)


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


def test_add_task_returns_true_for_normal_processing_task(manager):
    result = manager._add_task()

    assert result is True
    assert len(manager._tasks.items) == 1
    assert 'zlut_capture' not in manager._tasks.items[0]


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


def test_worker_reports_zlut_capture_failure_to_manager():
    queue = DummyQueue()
    worker = VideoWorker(
        tasks=queue,
        locks={},
        video_flag=None,
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
