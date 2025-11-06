import os
from types import SimpleNamespace

import numpy as np
import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget

from magscope.gui.windows import LoadingWindow, WindowManager
from magscope.utils import AcquisitionMode
from magscope.processes import SingletonMeta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeStatusPanel:
    def __init__(self):
        self.video_buffer_status = None
        self.video_processors_status = None
        self.display_rate_texts = []

    def update_video_buffer_status(self, text: str) -> None:
        self.video_buffer_status = text

    def update_video_processors_status(self, text: str) -> None:
        self.video_processors_status = text

    def update_display_rate(self, text: str) -> None:
        self.display_rate_texts.append(text)


class FakeVideoViewer:
    def __init__(self):
        self.cleared = False
        self.plot_args = None

    def clear_crosshairs(self) -> None:
        self.cleared = True

    def plot(self, x, y, marker_size):
        self.plot_args = (np.asarray(x), np.asarray(y), marker_size)


class FakeCheckable:
    def __init__(self):
        self.block_calls = []
        self.checked = None

    def blockSignals(self, state: bool) -> None:
        self.block_calls.append(state)

    def setChecked(self, value: bool) -> None:
        self.checked = value


class FakeTextEdit:
    def __init__(self):
        self.block_calls = []
        self.text = None

    def blockSignals(self, state: bool) -> None:
        self.block_calls.append(state)

    def setText(self, text: str) -> None:
        self.text = text


class FakeComboBox:
    def __init__(self):
        self.block_calls = []
        self.current_text = None

    def blockSignals(self, state: bool) -> None:
        self.block_calls.append(state)

    def setCurrentText(self, value: str) -> None:
        self.current_text = value


class FakeAcquisitionPanel:
    def __init__(self):
        self.acquisition_on_checkbox = SimpleNamespace(checkbox=FakeCheckable())
        self.acquisition_dir_textedit = FakeTextEdit()
        self.acquisition_dir_on_checkbox = SimpleNamespace(checkbox=FakeCheckable())
        self.acquisition_mode_combobox = FakeComboBox()


class FakeControls:
    def __init__(self):
        self.status_panel = FakeStatusPanel()
        self.acquisition_panel = FakeAcquisitionPanel()


class FakeSharedValues:
    def __init__(self, busy_count: int):
        self.video_process_busy_count = SimpleNamespace(value=busy_count)


class FakeTracksBuffer:
    def __init__(self, data: np.ndarray):
        self._data = data

    def peak_unsorted(self):
        return self._data


@pytest.fixture
def window_manager():
    SingletonMeta._instances.pop(WindowManager, None)
    manager = WindowManager()
    manager.controls = FakeControls()
    manager.settings = {
        'video processors n': 4,
        'magnification': 2,
    }
    manager.camera_type = SimpleNamespace(bits=12, nm_per_px=100)
    yield manager
    SingletonMeta._instances.pop(WindowManager, None)


def contains_widget(root: QWidget, target: QWidget) -> bool:
    return target is root or target in root.findChildren(QWidget)


def test_loading_window_defaults(qtbot):
    window = LoadingWindow()
    qtbot.addWidget(window)

    assert window.label.text() == 'MagScope\n\nloading ...'
    expected_flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
    assert window.windowFlags() & expected_flags == expected_flags


@pytest.mark.parametrize('n_windows', [1, 2, 3])
def test_create_central_widgets_attaches_expected_children(qtbot, n_windows):
    SingletonMeta._instances.pop(WindowManager, None)
    manager = WindowManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.n_windows = n_windows
    manager.create_central_widgets()

    assert len(manager.central_widgets) == n_windows

    if n_windows == 1:
        assert contains_widget(manager.central_widgets[0], manager.controls)
        assert contains_widget(manager.central_widgets[0], manager.plots_widget)
        assert contains_widget(manager.central_widgets[0], manager.video_viewer)
    elif n_windows == 2:
        assert contains_widget(manager.central_widgets[0], manager.controls)
        assert contains_widget(manager.central_widgets[0], manager.video_viewer)
        assert contains_widget(manager.central_widgets[1], manager.plots_widget)
    else:
        assert contains_widget(manager.central_widgets[0], manager.controls)
        assert contains_widget(manager.central_widgets[1], manager.video_viewer)
        assert contains_widget(manager.central_widgets[2], manager.plots_widget)

    SingletonMeta._instances.pop(WindowManager, None)


def test_status_updates_format_strings(window_manager):
    window_manager.video_buffer = SimpleNamespace(
        get_level=lambda: 0.25,
        n_total_images=20,
    )
    window_manager.shared_values = FakeSharedValues(busy_count=3)

    window_manager.update_video_buffer_status()
    window_manager.update_video_processors_status()

    assert window_manager.controls.status_panel.video_buffer_status == '25% full, 20 max images'
    assert window_manager.controls.status_panel.video_processors_status == '3/4 busy'

    window_manager._display_rate_counter = 5
    window_manager._display_rate_last_time -= 2
    window_manager._update_display_rate()

    assert window_manager.controls.status_panel.display_rate_texts[-1] == '2 updates/sec'


def test_update_beads_in_view_handles_disabled_and_recent_points(window_manager):
    fake_viewer = FakeVideoViewer()
    window_manager.video_viewer = fake_viewer

    window_manager.beads_in_view_on = False
    window_manager.beads_in_view_count = 2
    window_manager._update_beads_in_view()
    assert fake_viewer.cleared is True
    assert fake_viewer.plot_args is None

    tracks = np.array([
        [0, 50.0, 100.0],
        [1, 100.0, 150.0],
        [2, 300.0, 450.0],
        [2, 350.0, 500.0],
        [np.nan, 999.0, 999.0],
    ])
    window_manager.tracks_buffer = FakeTracksBuffer(tracks)
    window_manager.beads_in_view_on = True
    window_manager.beads_in_view_count = 2

    window_manager._update_beads_in_view()

    expected_scale = window_manager.camera_type.nm_per_px / window_manager.settings['magnification']
    expected_x = np.array([100.0, 300.0, 350.0]) / expected_scale
    expected_y = np.array([150.0, 450.0, 500.0]) / expected_scale

    plotted_x, plotted_y, marker_size = fake_viewer.plot_args
    np.testing.assert_allclose(plotted_x, expected_x)
    np.testing.assert_allclose(plotted_y, expected_y)
    assert marker_size == window_manager.beads_in_view_marker_size


def test_acquisition_setters_update_controls_and_state(window_manager):
    panel = window_manager.controls.acquisition_panel

    window_manager.set_acquisition_on(True)
    assert window_manager._acquisition_on is True
    assert panel.acquisition_on_checkbox.checkbox.block_calls == [True, False]
    assert panel.acquisition_on_checkbox.checkbox.checked is True

    window_manager.set_acquisition_dir('path/to/data')
    assert window_manager._acquisition_dir == 'path/to/data'
    assert panel.acquisition_dir_textedit.block_calls == [True, False]
    assert panel.acquisition_dir_textedit.text == 'path/to/data'

    window_manager.set_acquisition_dir_on(True)
    assert window_manager._acquisition_dir_on is True
    assert panel.acquisition_dir_on_checkbox.checkbox.block_calls == [True, False]
    assert panel.acquisition_dir_on_checkbox.checkbox.checked is True

    window_manager.set_acquisition_mode(AcquisitionMode.FULL_VIDEO)
    assert window_manager._acquisition_mode == AcquisitionMode.FULL_VIDEO
    assert panel.acquisition_mode_combobox.block_calls == [True, False]
    assert panel.acquisition_mode_combobox.current_text == AcquisitionMode.FULL_VIDEO
