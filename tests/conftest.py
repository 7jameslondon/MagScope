from __future__ import annotations

import numpy as np
import pytest


MANAGER_SINGLETON_CLASSES: tuple[type, ...] = ()


def _register_manager_classes() -> None:
    global MANAGER_SINGLETON_CLASSES
    if MANAGER_SINGLETON_CLASSES:
        return
    from magscope.beadlock import BeadLockManager
    from magscope.camera import CameraManager
    from magscope.scripting import ScriptManager
    from magscope.videoprocessing import VideoProcessorManager
    from magscope.zlut_generation import ZLUTGenerationManager
    MANAGER_SINGLETON_CLASSES = (
        BeadLockManager,
        CameraManager,
        ScriptManager,
        VideoProcessorManager,
        ZLUTGenerationManager,
    )


@pytest.fixture(autouse=True)
def clear_manager_singletons():
    _register_manager_classes()
    for cls in MANAGER_SINGLETON_CLASSES:
        type(cls)._instances.pop(cls, None)
    yield
    for cls in MANAGER_SINGLETON_CLASSES:
        type(cls)._instances.pop(cls, None)


# ---------------------------------------------------------------------------
# Shared buffer fakes used across multiple test modules
# ---------------------------------------------------------------------------

class FakeTracksBuffer:
    """A tracks buffer that returns pre-configured rows from ``peak_unsorted()``."""

    def __init__(self, tracks: np.ndarray | None = None):
        self._tracks = np.asarray(tracks, dtype=np.float64) if tracks is not None else np.zeros((0, 7), dtype=np.float64)

    def peak_unsorted(self) -> np.ndarray:
        return self._tracks


class FakeFocusBuffer:
    """A focus-hardware buffer that returns pre-configured rows from ``peak_sorted()``."""

    def __init__(self, rows: np.ndarray | None = None):
        if rows is None:
            self._rows = np.empty((0, 4), dtype=np.float64)
        elif rows.ndim == 1:
            self._rows = rows.reshape(1, -1)
        else:
            self._rows = np.asarray(rows, dtype=np.float64)

    def peak_sorted(self) -> np.ndarray:
        return self._rows


class FakeHardwareBuffer:
    """In-memory substitute for ``MatrixBuffer`` used by hardware managers."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.rows: list[np.ndarray] = []
        self._unread_rows: list[np.ndarray] = []

    def write(self, row: np.ndarray) -> None:
        copied = np.array(row, copy=True)
        self.rows.append(copied)
        self._unread_rows.append(copied)

    def read(self) -> np.ndarray:
        if not self._unread_rows:
            return np.empty((0, 0), dtype=float)
        unread = np.vstack(self._unread_rows)
        self._unread_rows.clear()
        return unread


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

class _StubCameraType:
    """Stand-in for ``manager.camera_type`` used during XY-lock testing."""
    nm_per_px = 100.0


def make_beadlock_manager() -> "BeadLockManager":
    """Return a BeadLockManager wired for isolated testing."""
    from magscope.beadlock import BeadLockManager
    manager = BeadLockManager()
    manager.settings = {
        'ROI': 64,
        'magnification': 1.0,
        'xy-lock default interval': 10,
        'xy-lock default max': 10,
        'xy-lock default window': 10,
        'z-lock default interval': 10,
        'z-lock default max': 1_000,
        'z-lock default window': 10,
    }
    manager.setup()
    manager._sent_commands: list = []
    manager.send_ipc = manager._sent_commands.append
    manager.tracks_buffer = FakeTracksBuffer()
    manager.hardware_types = {}
    manager.camera_type = _StubCameraType
    return manager


def set_beadlock_tracks(manager: "BeadLockManager", rows: list[list[float]] | np.ndarray) -> None:
    manager.tracks_buffer = FakeTracksBuffer(np.asarray(rows, dtype=np.float64))
