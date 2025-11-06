import types
from time import time

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget

from magscope.gui.windows import LoadingWindow, WindowManager
from magscope.utils import AcquisitionMode


@pytest.fixture(autouse=True)
def reset_window_manager_singleton():
    """Ensure each test gets a fresh WindowManager instance."""
    WindowManager.__class__._instances.pop(WindowManager, None)
    yield
    WindowManager.__class__._instances.pop(WindowManager, None)


def test_loading_window_defaults(qtbot):
    window = LoadingWindow()
    qtbot.addWidget(window)

    assert window.label.text() == "MagScope\n\nloading ..."

    expected_flags = (
        Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
    )
    assert window.windowFlags() & expected_flags == expected_flags


def _basic_window_manager(qtbot) -> WindowManager:
    manager = WindowManager()
    manager.central_widgets = []
    manager.central_layouts = []
    manager.controls = QWidget()
    manager.plots_widget = QLabel()
    manager.video_viewer = QWidget()

    qtbot.addWidget(manager.controls)
    qtbot.addWidget(manager.plots_widget)
    qtbot.addWidget(manager.video_viewer)
    return manager


def test_create_one_window_widgets(qtbot):
    manager = _basic_window_manager(qtbot)
    manager.n_windows = 1

    manager.create_central_widgets()

    assert len(manager.central_widgets) == 1
    splitter = manager.central_layouts[0].itemAt(0).widget()
    left_widget = splitter.widget(0)
    right_widget = splitter.widget(1)

    assert left_widget.layout().itemAt(0).widget() is manager.controls

    ud_splitter = right_widget.layout().itemAt(0).widget()
    right_top_widget = ud_splitter.widget(0)
    right_bottom_widget = ud_splitter.widget(1)

    assert right_top_widget.layout().itemAt(0).widget() is manager.plots_widget
    assert right_bottom_widget.layout().itemAt(0).widget() is manager.video_viewer


def test_create_two_window_widgets(qtbot):
    manager = _basic_window_manager(qtbot)
    manager.n_windows = 2

    manager.create_central_widgets()

    assert len(manager.central_widgets) == 2
    splitter = manager.central_layouts[0].itemAt(0).widget()
    left_widget = splitter.widget(0)
    right_widget = splitter.widget(1)

    assert left_widget.layout().itemAt(0).widget() is manager.controls
    assert right_widget.layout().itemAt(0).widget() is manager.video_viewer
    assert manager.central_layouts[1].itemAt(0).widget() is manager.plots_widget


def test_create_three_window_widgets(qtbot):
    manager = _basic_window_manager(qtbot)
    manager.n_windows = 3

    manager.create_central_widgets()

    assert len(manager.central_widgets) == 3
    assert manager.central_layouts[0].itemAt(0).widget() is manager.controls
    assert manager.central_layouts[1].itemAt(0).widget() is manager.video_viewer
    assert manager.central_layouts[2].itemAt(0).widget() is manager.plots_widget


class FakeStatusPanel:
    def __init__(self):
        self.video_buffer_status = None
        self.video_processors_status = None
        self.display_rate_updates: list[str] = []

    def update_video_buffer_status(self, text: str):
        self.video_buffer_status = text

    def update_video_processors_status(self, text: str):
        self.video_processors_status = text

    def update_display_rate(self, text: str):
        self.display_rate_updates.append(text)

    def update_video_buffer_purge(self, value: float):
        self.video_buffer_purge = value


class FakeVideoBuffer:
    def __init__(self, level: float, n_total_images: int):
        self._level = level
        self.n_total_images = n_total_images

    def get_level(self):
        return self._level


def test_status_updates_formatting():
    manager = WindowManager()
    status_panel = FakeStatusPanel()
    manager.controls = types.SimpleNamespace(status_panel=status_panel)
    manager.video_buffer = FakeVideoBuffer(level=0.5, n_total_images=100)
    manager.shared_values = types.SimpleNamespace(
        video_process_busy_count=types.SimpleNamespace(value=3)
    )
    manager.settings = {'video processors n': 8}

    manager.update_video_buffer_status()
    assert status_panel.video_buffer_status == "50% full, 100 max images"

    manager.update_video_processors_status()
    assert status_panel.video_processors_status == "3/8 busy"

    manager._display_rate_counter = 5
    manager._display_rate_last_time = time() - 2
    manager._update_display_rate()
    assert status_panel.display_rate_updates[-1] == "2 updates/sec"

    manager._update_display_rate()
    assert status_panel.display_rate_updates[-1] == "2 updates/sec"


class FakeVideoViewer:
    def __init__(self):
        self.cleared = False
        self.plotted_args = None

    def clear_crosshairs(self):
        self.cleared = True

    def plot(self, x, y, size):
        self.plotted_args = (np.array(x), np.array(y), size)


class FakeTracksBuffer:
    def __init__(self, data: np.ndarray):
        self._data = data

    def peak_unsorted(self):
        return self._data


def test_update_beads_in_view_disabled():
    manager = WindowManager()
    video_viewer = FakeVideoViewer()
    manager.video_viewer = video_viewer
    manager.beads_in_view_on = False
    manager.beads_in_view_count = 2

    manager._update_beads_in_view()

    assert video_viewer.cleared is True
    assert video_viewer.plotted_args is None


def test_update_beads_in_view_enabled():
    manager = WindowManager()
    data = np.array([
        [0.0, 100.0, 150.0],
        [1.0, 200.0, 300.0],
        [2.0, 400.0, 500.0],
    ])
    manager.tracks_buffer = FakeTracksBuffer(data)
    manager.video_viewer = FakeVideoViewer()
    manager.camera_type = types.SimpleNamespace(nm_per_px=200.0)
    manager.settings = {'magnification': 2}
    manager.beads_in_view_on = True
    manager.beads_in_view_count = 2
    manager.beads_in_view_marker_size = 7

    manager._update_beads_in_view()

    plotted = manager.video_viewer.plotted_args
    assert plotted is not None
    x, y, size = plotted
    np.testing.assert_array_equal(x, np.array([1.0, 2.0]))
    np.testing.assert_array_equal(y, np.array([1.5, 2.5]))
    assert size == 7


class FakeBlockWidget:
    def __init__(self):
        self.block_calls = []

    def blockSignals(self, state: bool):
        self.block_calls.append(state)


class FakeCheckbox(FakeBlockWidget):
    def __init__(self):
        super().__init__()
        self.checked = None

    def setChecked(self, value: bool):
        self.checked = value


class FakeCheckboxWrapper:
    def __init__(self):
        self.checkbox = FakeCheckbox()


class FakeTextEdit(FakeBlockWidget):
    def __init__(self):
        super().__init__()
        self.text = None

    def setText(self, value: str):
        self.text = value


class FakeComboBox(FakeBlockWidget):
    def __init__(self):
        super().__init__()
        self.current_text = None

    def setCurrentText(self, value: str):
        self.current_text = value


class FakeAcquisitionPanel:
    def __init__(self):
        self.acquisition_on_checkbox = FakeCheckboxWrapper()
        self.acquisition_dir_textedit = FakeTextEdit()
        self.acquisition_dir_on_checkbox = FakeCheckboxWrapper()
        self.acquisition_mode_combobox = FakeComboBox()


class FakeControls:
    def __init__(self):
        self.acquisition_panel = FakeAcquisitionPanel()


def test_acquisition_setters_update_controls():
    manager = WindowManager()
    manager.controls = FakeControls()

    manager.set_acquisition_on(True)
    checkbox = manager.controls.acquisition_panel.acquisition_on_checkbox.checkbox
    assert checkbox.checked is True
    assert checkbox.block_calls == [True, False]
    assert manager._acquisition_on is True

    manager.set_acquisition_dir("/tmp/data")
    textedit = manager.controls.acquisition_panel.acquisition_dir_textedit
    assert textedit.text == "/tmp/data"
    assert textedit.block_calls == [True, False]
    assert manager._acquisition_dir == "/tmp/data"

    manager.set_acquisition_dir_on(True)
    dir_checkbox = manager.controls.acquisition_panel.acquisition_dir_on_checkbox.checkbox
    assert dir_checkbox.checked is True
    assert dir_checkbox.block_calls == [True, False]
    assert manager._acquisition_dir_on is True

    manager.set_acquisition_mode(AcquisitionMode.FULL_VIDEO)
    combobox = manager.controls.acquisition_panel.acquisition_mode_combobox
    assert combobox.current_text == AcquisitionMode.FULL_VIDEO
    assert combobox.block_calls == [True, False]
    assert manager._acquisition_mode == AcquisitionMode.FULL_VIDEO
