import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QGraphicsScene, QMainWindow, QWidget

from magscope.ipc_commands import (
    RemoveBeadsFromPendingMovesCommand,
    UpdateBeadRoisCommand,
)
from magscope.settings import MagScopeSettings
from magscope.ui.ui import LoadingWindow, UIManager
from magscope.ui.widgets import BeadGraphic
from magscope.utils import AcquisitionMode


def clear_ui_manager_singleton() -> None:
    type(UIManager)._instances.pop(UIManager, None)


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
        self.scene = None
        self.viewport_updates = 0

    def viewport(self):
        return SimpleNamespace(update=self._update_viewport)

    def _update_viewport(self) -> None:
        self.viewport_updates += 1

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

    def isChecked(self) -> bool:
        return bool(self.checked)


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
        self.save_highlight_calls: list[bool] = []

    def update_save_highlight(self, should_save: bool) -> None:
        self.save_highlight_calls.append(should_save)


class FakeLabel:
    def __init__(self):
        self.text = None

    def setText(self, text: str) -> None:
        self.text = text


class FakeBeadSelectionPanel:
    def __init__(self):
        self.roi_size_label = FakeLabel()
        self.next_bead_id_label = None
        self.lock_button = FakeCheckable()

    def update_next_bead_id_label(self, next_bead_id: int) -> None:
        self.next_bead_id_label = next_bead_id


class FakeZLutGenerationPanel:
    def __init__(self):
        self.roi_size_label = FakeLabel()


class FakeControls:
    def __init__(self):
        self.status_panel = FakeStatusPanel()
        self.acquisition_panel = FakeAcquisitionPanel()
        self.bead_selection_panel = FakeBeadSelectionPanel()
        self.z_lut_generation_panel = FakeZLutGenerationPanel()


class FakeSharedValues:
    def __init__(self, busy_count: int):
        self.video_process_busy_count = SimpleNamespace(value=busy_count)


class FakeTracksBuffer:
    def __init__(self, data: np.ndarray):
        self._data = data

    def peak_unsorted(self):
        return self._data


class FakeBeadRoiBuffer:
    def __init__(self):
        self.add_calls = []
        self.update_calls = []
        self.remove_calls = []

    def add_beads(self, value):
        self.add_calls.append(value)

    def update_beads(self, value):
        self.update_calls.append(value)

    def remove_beads(self, value):
        self.remove_calls.append(value)


class FakeGraphic:
    def __init__(self, roi: tuple[int, int, int, int]):
        self.roi = roi
        self.moves = []

    def move(self, dx: int, dy: int) -> None:
        self.moves.append((dx, dy))
        x0, x1, y0, y1 = self.roi
        self.roi = (x0 + dx, x1 + dx, y0 + dy, y1 + dy)

    def get_roi_bounds(self) -> tuple[int, int, int, int]:
        return self.roi


@pytest.fixture
def ui_manager():
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = FakeControls()
    manager.settings = {
        'video processors n': 4,
        'magnification': 2,
    }
    manager.camera_type = SimpleNamespace(bits=12, nm_per_px=100)
    yield manager
    clear_ui_manager_singleton()


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
    clear_ui_manager_singleton()
    manager = UIManager()
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

    clear_ui_manager_singleton()


def test_status_updates_format_strings(ui_manager):
    ui_manager.video_buffer = SimpleNamespace(
        get_level=lambda: 0.25,
        n_total_images=20,
    )
    ui_manager.shared_values = FakeSharedValues(busy_count=3)

    ui_manager.update_video_buffer_status()
    ui_manager.update_video_processors_status()

    assert ui_manager.controls.status_panel.video_buffer_status == '25% full, 20 max images'
    assert ui_manager.controls.status_panel.video_processors_status == '3/4 busy'

    ui_manager._display_rate_counter = 5
    ui_manager._display_rate_last_time -= 2
    ui_manager._update_display_rate()

    assert ui_manager.controls.status_panel.display_rate_texts[-1] == '2 updates/sec'


def test_update_beads_in_view_handles_disabled_and_recent_points(ui_manager):
    fake_viewer = FakeVideoViewer()
    ui_manager.video_viewer = fake_viewer

    ui_manager.beads_in_view_on = False
    ui_manager.beads_in_view_count = 2
    ui_manager._update_beads_in_view()
    assert fake_viewer.cleared is True
    assert fake_viewer.plot_args is None

    tracks = np.array([
        [0, 50.0, 100.0],
        [1, 100.0, 150.0],
        [2, 300.0, 450.0],
        [2, 350.0, 500.0],
        [np.nan, 999.0, 999.0],
    ])
    ui_manager.tracks_buffer = FakeTracksBuffer(tracks)
    ui_manager.beads_in_view_on = True
    ui_manager.beads_in_view_count = 2

    ui_manager._update_beads_in_view()

    expected_scale = ui_manager.camera_type.nm_per_px / ui_manager.settings['magnification']
    expected_x = np.array([100.0, 300.0, 350.0]) / expected_scale
    expected_y = np.array([150.0, 450.0, 500.0]) / expected_scale

    plotted_x, plotted_y, marker_size = fake_viewer.plot_args
    np.testing.assert_allclose(plotted_x, expected_x)
    np.testing.assert_allclose(plotted_y, expected_y)
    assert marker_size == ui_manager.beads_in_view_marker_size


def test_bead_graphic_reports_label_scene_position(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager._suppress_bead_roi_updates = False
    ui_manager._update_bead_roi = lambda bead_id, roi: None
    ui_manager.remove_bead = lambda bead_id: None

    graphic = BeadGraphic(ui_manager, 12, 100, 120, 40, scene)

    label_pos = graphic.get_label_scene_position()
    assert graphic.LABEL_FONT.family() == 'Arial'
    assert label_pos.x() == 90
    assert label_pos.y() == 101
    graphic.remove()


def test_reset_bead_ids_updates_graphic_ids(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.video_viewer.scene = scene
    ui_manager.settings['ROI'] = 40
    ui_manager._bead_roi_capacity = 10000
    ui_manager._add_bead_roi = lambda bead_id, roi: None
    ui_manager._update_bead_highlight = lambda bead_id: None
    ui_manager._update_bead_highlights = lambda **kwargs: None
    ui_manager.selected_bead = None
    ui_manager.reference_bead = None

    ui_manager.add_bead(SimpleNamespace(x=lambda: 100, y=lambda: 100))
    ui_manager.add_bead(SimpleNamespace(x=lambda: 200, y=lambda: 200))
    second_graphic = ui_manager._bead_graphics.pop(1)
    ui_manager._bead_graphics[5] = second_graphic

    ui_manager.reset_bead_ids()

    assert list(sorted(ui_manager._bead_graphics)) == [0, 1]
    assert ui_manager._bead_graphics[0].id == 0
    assert ui_manager._bead_graphics[1].id == 1
    assert ui_manager.video_viewer.viewport_updates == 3


def test_acquisition_setters_update_controls_and_state(ui_manager):
    panel = ui_manager.controls.acquisition_panel

    ui_manager.set_acquisition_on(True)
    assert ui_manager._acquisition_on is True
    assert panel.acquisition_on_checkbox.checkbox.block_calls == [True, False]
    assert panel.acquisition_on_checkbox.checkbox.checked is True

    ui_manager.set_acquisition_dir('path/to/data')
    assert ui_manager._acquisition_dir == 'path/to/data'
    assert panel.acquisition_dir_textedit.block_calls == [True, False]
    assert panel.acquisition_dir_textedit.text == 'path/to/data'

    ui_manager.set_acquisition_dir_on(True)
    assert ui_manager._acquisition_dir_on is True
    assert panel.acquisition_dir_on_checkbox.checkbox.block_calls == [True, False]
    assert panel.acquisition_dir_on_checkbox.checkbox.checked is True

    ui_manager.set_acquisition_mode(AcquisitionMode.FULL_VIDEO)
    assert ui_manager._acquisition_mode == AcquisitionMode.FULL_VIDEO
    assert panel.acquisition_mode_combobox.block_calls == [True, False]
    assert panel.acquisition_mode_combobox.current_text == AcquisitionMode.FULL_VIDEO


def test_settings_persistence_warning_is_shown_once(qtbot, ui_manager, monkeypatch):
    window = QMainWindow()
    qtbot.addWidget(window)
    ui_manager.windows = [window]
    ui_manager.settings = MagScopeSettings(persistence_available=False)

    warning_calls: list[str] = []
    monkeypatch.setattr(
        ui_manager,
        "_show_settings_persistence_warning",
        lambda: warning_calls.append("shown"),
    )

    ui_manager._show_settings_persistence_warning_if_needed()
    ui_manager._show_settings_persistence_warning_if_needed()

    assert warning_calls == ["shown"]


def test_set_settings_warns_when_persistence_becomes_unavailable(qtbot, ui_manager, monkeypatch):
    window = QMainWindow()
    qtbot.addWidget(window)
    ui_manager.windows = [window]
    ui_manager.settings = MagScopeSettings()
    ui_manager._last_applied_roi = ui_manager.settings["ROI"]
    monkeypatch.setattr(ui_manager, "_update_roi_labels", lambda roi: None)

    warning_calls: list[str] = []
    monkeypatch.setattr(
        ui_manager,
        "_show_settings_persistence_warning",
        lambda: warning_calls.append("shown"),
    )

    ui_manager.set_settings(MagScopeSettings({"ROI": 50}, persistence_available=False))
    ui_manager.set_settings(MagScopeSettings({"ROI": 50}, persistence_available=False))

    assert warning_calls == ["shown"]


def test_incremental_bead_roi_helpers_update_buffer_and_broadcast(ui_manager):
    buffer = FakeBeadRoiBuffer()
    commands = []
    ui_manager.bead_roi_buffer = buffer
    ui_manager._broadcast_bead_roi_update = lambda: commands.append(UpdateBeadRoisCommand())

    ui_manager._add_bead_roi(2, (1, 2, 3, 4))
    ui_manager._update_bead_roi(2, (5, 6, 7, 8))
    ui_manager._remove_bead_roi(2)

    assert buffer.add_calls == [{2: (1, 2, 3, 4)}]
    assert buffer.update_calls == [{2: (5, 6, 7, 8)}]
    assert buffer.remove_calls == [[2]]
    assert [type(command) for command in commands] == [
        UpdateBeadRoisCommand,
        UpdateBeadRoisCommand,
        UpdateBeadRoisCommand,
    ]


def test_move_beads_updates_only_moved_rois_and_clears_pending(ui_manager):
    buffer = FakeBeadRoiBuffer()
    commands = []
    ui_manager.bead_roi_buffer = buffer
    ui_manager._broadcast_bead_roi_update = lambda: commands.append(UpdateBeadRoisCommand())
    ui_manager.send_ipc = commands.append
    ui_manager._bead_graphics = {
        1: FakeGraphic((10, 20, 30, 40)),
        2: FakeGraphic((50, 60, 70, 80)),
    }

    ui_manager.move_beads([(1, 2, 3), (99, 4, 5)])

    assert buffer.update_calls == [{1: (12, 22, 33, 43)}]
    assert len(commands) == 2
    assert isinstance(commands[0], UpdateBeadRoisCommand)
    assert isinstance(commands[1], RemoveBeadsFromPendingMovesCommand)
    assert commands[1].ids == [1]


def test_callback_view_clicked_ignores_new_add_while_bead_sync_pending(ui_manager, monkeypatch):
    calls = []
    pos = SimpleNamespace(x=lambda: 10, y=lambda: 20)
    monkeypatch.setattr(ui_manager, "add_bead", lambda value: calls.append(value))

    ui_manager._pending_bead_add_id = 3
    ui_manager._pending_bead_add_roi = (1, 2, 3, 4)
    ui_manager.callback_view_clicked(pos)

    assert calls == []


def test_refresh_bead_rois_clears_pending_only_after_matching_roi(ui_manager):
    ui_manager._refresh_bead_roi_cache = lambda: None
    ui_manager._pending_bead_add_id = 2
    ui_manager._pending_bead_add_roi = (1, 2, 3, 4)
    ui_manager._bead_roi_ids = np.asarray([1, 2], dtype=np.uint32)
    ui_manager._bead_roi_values = np.asarray([[9, 9, 9, 9], [1, 2, 3, 4]], dtype=np.uint32)

    ui_manager.refresh_bead_rois()

    assert ui_manager._pending_bead_add_id is None
    assert ui_manager._pending_bead_add_roi is None


def test_refresh_bead_rois_keeps_pending_for_unrelated_update(ui_manager):
    ui_manager._refresh_bead_roi_cache = lambda: None
    ui_manager._pending_bead_add_id = 2
    ui_manager._pending_bead_add_roi = (1, 2, 3, 4)
    ui_manager._bead_roi_ids = np.asarray([1, 3], dtype=np.uint32)
    ui_manager._bead_roi_values = np.asarray([[9, 9, 9, 9], [5, 6, 7, 8]], dtype=np.uint32)

    ui_manager.refresh_bead_rois()

    assert ui_manager._pending_bead_add_id == 2
    assert ui_manager._pending_bead_add_roi == (1, 2, 3, 4)


def test_add_bead_clears_pending_state_on_roi_update_failure(ui_manager, monkeypatch):
    ui_manager.settings["ROI"] = 20
    ui_manager.video_viewer = SimpleNamespace(
        scene=SimpleNamespace(width=lambda: 100, addItem=lambda item: None),
        viewport=lambda: SimpleNamespace(update=lambda: None),
    )
    ui_manager._update_bead_highlight = lambda bead_id: None
    ui_manager._update_next_bead_id_label = lambda: None

    class FakeNewGraphic:
        def __init__(self, manager, bead_id, x, y, width, scene):
            self.id = bead_id

        def get_roi_bounds(self):
            return (1, 2, 3, 4)

    monkeypatch.setattr("magscope.ui.ui.BeadGraphic", FakeNewGraphic)
    monkeypatch.setattr(ui_manager, "_add_bead_roi", lambda bead_id, roi: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        ui_manager.add_bead(SimpleNamespace(x=lambda: 10, y=lambda: 20))

    assert ui_manager._pending_bead_add_id is None
    assert ui_manager._pending_bead_add_roi is None
