import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt
from PyQt6.QtWidgets import QLabel, QGraphicsScene, QMainWindow, QWidget

from magscope.ipc_commands import (
    AddRandomBeadsCommand,
    CancelGeneratedZLUTEvaluationCommand,
    CancelZLUTGenerationCommand,
    RemoveBeadsFromPendingMovesCommand,
    SaveGeneratedZLUTCommand,
    SelectGeneratedZLUTBeadCommand,
    StartZLUTGenerationCommand,
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
        self.overlay_args = None
        self.scene = SimpleNamespace(sceneRect=lambda: QRectF(0, 0, 512, 512))
        self.viewport_updates = 0
        self._viewport_rect = QRect(0, 0, 512, 512)

    def viewport(self):
        return SimpleNamespace(update=self._update_viewport, rect=self._viewport_rect_fn)

    def _viewport_rect_fn(self):
        return self._viewport_rect

    def mapToScene(self, rect):
        return SimpleNamespace(boundingRect=lambda: QRectF(rect))

    def image_scene_rect(self):
        return self.scene.sceneRect()

    def _update_viewport(self) -> None:
        self.viewport_updates += 1

    def clear_crosshairs(self) -> None:
        self.cleared = True

    def plot(self, x, y, marker_size):
        self.plot_args = (np.asarray(x), np.asarray(y), marker_size)

    def set_bead_overlay(
        self,
        bead_rois: dict[int, tuple[int, int, int, int]],
        active_bead_id: int | None,
        selected_bead_id: int | None,
        reference_bead_id: int | None,
    ) -> None:
        self.overlay_args = (
            dict(bead_rois),
            active_bead_id,
            selected_bead_id,
            reference_bead_id,
        )


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

    def toPlainText(self) -> str:
        return self.text or ''


class FakeLineEdit:
    def __init__(self, text: str = ''):
        self.block_calls = []
        self._text = text

    def blockSignals(self, state: bool) -> None:
        self.block_calls.append(state)

    def setText(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        return self._text


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


class FakeButton:
    def __init__(self):
        self.enabled = True

    def setEnabled(self, value: bool) -> None:
        self.enabled = value


class FakeBeadSelectionPanel:
    def __init__(self):
        self.roi_size_label = FakeLabel()
        self.next_bead_id_label = None
        self.auto_select_button = FakeButton()

    def update_next_bead_id_label(self, next_bead_id: int) -> None:
        self.next_bead_id_label = next_bead_id


class FakeZLutGenerationPanel:
    def __init__(self):
        self.roi_size_label = FakeLabel()
        self.state_calls = []
        self.progress_calls = []

    def update_state(
        self,
        status,
        detail=None,
        *,
        running=False,
        can_cancel=False,
        phase='idle',
        z_axis_min_nm=None,
        z_axis_max_nm=None,
    ) -> None:
        self.state_calls.append((status, detail, running, can_cancel))

    def update_progress(
        self,
        current_step,
        total_steps,
        capture_count,
        capture_capacity,
        motor_z_value=None,
    ) -> None:
        self.progress_calls.append(
            (current_step, total_steps, capture_count, capture_capacity, motor_z_value)
        )


class FakeZLutPreviewWidget:
    def __init__(self):
        self.clear_calls = []
        self.preview_calls = []

    def clear(self, message='Waiting for Z-LUT sweep data...') -> None:
        self.clear_calls.append(message)

    def update_preview(self, **kwargs) -> None:
        self.preview_calls.append(kwargs)


class FakeZLutGenerationDialog:
    def __init__(self):
        self.state_calls = []
        self.progress_calls = []
        self.evaluation_calls = []
        self.preview_widget = FakeZLutPreviewWidget()
        self.visible = True
        self.show_calls = 0
        self.raise_calls = 0
        self.activate_calls = 0

    def show(self) -> None:
        self.show_calls += 1
        self.visible = True

    def raise_(self) -> None:
        self.raise_calls += 1

    def activateWindow(self) -> None:
        self.activate_calls += 1

    def isVisible(self) -> bool:
        return self.visible

    def update_state(
        self,
        status,
        detail=None,
        *,
        running=False,
        can_cancel=False,
        phase='idle',
        z_axis_min_nm=None,
        z_axis_max_nm=None,
    ) -> None:
        self.state_calls.append((status, detail, running, can_cancel))

    def update_progress(
        self,
        current_step,
        total_steps,
        capture_count,
        capture_capacity,
        motor_z_value=None,
    ) -> None:
        self.progress_calls.append(
            (current_step, total_steps, capture_count, capture_capacity, motor_z_value)
        )

    def update_evaluation(self, *, active, bead_ids, selected_bead_id=None) -> None:
        self.evaluation_calls.append((active, bead_ids, selected_bead_id))

    def close(self) -> None:
        self.visible = False

    def force_close(self) -> None:
        self.visible = False


class FakeControls:
    def __init__(self):
        self.status_panel = FakeStatusPanel()
        self.acquisition_panel = FakeAcquisitionPanel()
        self.bead_selection_panel = FakeBeadSelectionPanel()
        self.plot_settings_panel = SimpleNamespace(
            selected_bead=SimpleNamespace(lineedit=FakeLineEdit('0')),
            reference_bead=SimpleNamespace(lineedit=FakeLineEdit('')),
        )
        self.z_lut_generation_panel = FakeZLutGenerationPanel()


class FakeSignal:
    def __init__(self):
        self.calls = []

    def emit(self, value) -> None:
        self.calls.append(value)


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
    manager.plot_worker = SimpleNamespace(
        selected_bead_signal=FakeSignal(),
        reference_bead_signal=FakeSignal(),
    )
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


def test_refresh_bead_overlay_pushes_cached_overlay_state(ui_manager):
    fake_viewer = FakeVideoViewer()
    ui_manager.video_viewer = fake_viewer
    ui_manager._bead_rois = {1: (10, 20, 30, 40), 2: (50, 60, 70, 80)}
    ui_manager._active_bead_id = 2
    ui_manager.selected_bead = 1
    ui_manager.reference_bead = 2

    ui_manager._refresh_bead_overlay()

    assert fake_viewer.overlay_args == (
        {1: (10, 20, 30, 40), 2: (50, 60, 70, 80)},
        2,
        1,
        2,
    )
    assert fake_viewer.viewport_updates == 1


def test_clear_beads_refreshes_overlay_cache(ui_manager):
    fake_viewer = FakeVideoViewer()
    ui_manager.video_viewer = fake_viewer
    ui_manager._bead_rois = {1: (10, 20, 30, 40)}
    ui_manager._bead_next_id = 1
    ui_manager._broadcast_bead_roi_update = lambda: None

    ui_manager.clear_beads()

    assert fake_viewer.overlay_args == ({}, None, 0, None)


def test_bead_graphic_reports_label_scene_position(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager._suppress_bead_roi_updates = False
    ui_manager._update_bead_roi = lambda bead_id, roi: None
    ui_manager.remove_bead = lambda bead_id: None

    graphic = BeadGraphic(ui_manager, 12, BeadGraphic.roi_from_center(100, 120, 40), scene)

    label_pos = graphic.get_label_scene_position()
    assert graphic.LABEL_FONT.family() == 'Arial'
    assert label_pos.x() == 90
    assert label_pos.y() == 101
    graphic.remove()


def test_bead_graphic_label_moves_with_active_roi(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager._suppress_bead_roi_updates = False
    ui_manager._update_bead_roi = lambda bead_id, roi: None
    ui_manager.remove_bead = lambda bead_id: None

    graphic = BeadGraphic(ui_manager, 12, BeadGraphic.roi_from_center(100, 120, 40), scene)

    initial_label_pos = graphic.label.scenePos()
    initial_roi_label_pos = graphic.get_label_scene_position()
    assert initial_label_pos == initial_roi_label_pos

    graphic.set_roi_bounds(BeadGraphic.roi_from_center(140, 160, 40))

    moved_label_pos = graphic.label.scenePos()
    moved_roi_label_pos = graphic.get_label_scene_position()
    assert moved_label_pos == moved_roi_label_pos
    assert moved_label_pos != initial_label_pos
    graphic.remove()


def test_bead_graphic_selected_roi_shows_four_corner_grips(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager._suppress_bead_roi_updates = False
    ui_manager._update_bead_roi = lambda bead_id, roi: None
    ui_manager.remove_bead = lambda bead_id: None

    graphic = BeadGraphic(ui_manager, 12, BeadGraphic.roi_from_center(100, 120, 40), scene)
    graphic.set_selection_state('selected')

    grip_rects = graphic._corner_grip_rects()

    assert len(grip_rects) == 4
    assert all(grip_rect.width() > 0 for grip_rect in grip_rects)
    assert all(grip_rect.height() > 0 for grip_rect in grip_rects)
    graphic.remove()


def test_bead_graphic_non_selected_roi_hides_corner_grips(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager._suppress_bead_roi_updates = False
    ui_manager._update_bead_roi = lambda bead_id, roi: None
    ui_manager.remove_bead = lambda bead_id: None

    graphic = BeadGraphic(ui_manager, 12, BeadGraphic.roi_from_center(100, 120, 40), scene)

    assert graphic._corner_grip_rects() == []
    graphic.set_selection_state('reference')
    assert graphic._corner_grip_rects() == []
    graphic.remove()


def test_bead_graphic_updates_cursor_for_hover_and_drag(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager._suppress_bead_roi_updates = False
    ui_manager._update_bead_roi = lambda bead_id, roi: None
    ui_manager.remove_bead = lambda bead_id: None

    graphic = BeadGraphic(ui_manager, 12, BeadGraphic.roi_from_center(100, 120, 40), scene)

    assert graphic.cursor().shape() == Qt.CursorShape.ArrowCursor

    graphic._is_hovered = True
    graphic._update_cursor()
    assert graphic.cursor().shape() == Qt.CursorShape.OpenHandCursor

    graphic._is_moving = True
    graphic._update_cursor()
    assert graphic.cursor().shape() == Qt.CursorShape.ClosedHandCursor

    graphic._is_moving = False
    graphic._is_hovered = False
    graphic._update_cursor()
    assert graphic.cursor().shape() == Qt.CursorShape.ArrowCursor
    graphic.remove()


def test_bead_graphic_validate_move_clamps_to_scene_bounds(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager._suppress_bead_roi_updates = False
    ui_manager._update_bead_roi = lambda bead_id, roi: None
    ui_manager.remove_bead = lambda bead_id: None

    graphic = BeadGraphic(ui_manager, 12, (100, 140, 100, 140), scene)
    graphic.set_selection_state('selected')

    clamped_bottom_right = graphic.validate_move(graphic.pos() + QPointF(600, 600))
    assert clamped_bottom_right.x() == 472
    assert clamped_bottom_right.y() == 472

    clamped_top_left = graphic.validate_move(QPointF(-100, -100))
    assert clamped_top_left.x() == 0
    assert clamped_top_left.y() == 0
    graphic.remove()


def test_bead_graphic_move_keeps_roi_inside_scene(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager._suppress_bead_roi_updates = False
    ui_manager._update_bead_roi = lambda bead_id, roi: None
    ui_manager.remove_bead = lambda bead_id: None

    graphic = BeadGraphic(ui_manager, 12, (100, 140, 100, 140), scene)
    graphic.set_selection_state('selected')

    graphic.move(600, 600)
    assert graphic.get_roi_bounds() == (472, 512, 472, 512)

    graphic.move(-1000, -1000)
    assert graphic.get_roi_bounds() == (0, 40, 0, 40)
    graphic.remove()


def test_bead_graphic_selected_grips_stay_inside_roi(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)
    ui_manager._suppress_bead_roi_updates = False
    ui_manager._update_bead_roi = lambda bead_id, roi: None
    ui_manager.remove_bead = lambda bead_id: None

    graphic = BeadGraphic(ui_manager, 12, (472, 512, 472, 512), scene)
    graphic.set_selection_state('selected')

    paint_rect = graphic._paint_rect()
    for grip_rect in graphic._corner_grip_rects():
        assert paint_rect.contains(grip_rect)
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
    ui_manager.selected_bead = 5
    ui_manager.reference_bead = None

    ui_manager.add_bead(SimpleNamespace(x=lambda: 100, y=lambda: 100))
    ui_manager.add_bead(SimpleNamespace(x=lambda: 200, y=lambda: 200))
    second_roi = ui_manager._bead_rois.pop(1)
    ui_manager._bead_rois[5] = second_roi
    ui_manager._set_active_bead(5)

    ui_manager.reset_bead_ids()

    assert list(sorted(ui_manager._bead_rois)) == [0, 1]
    assert ui_manager._bead_next_id == 2
    assert ui_manager.controls.bead_selection_panel.next_bead_id_label == 2
    assert ui_manager.selected_bead == 1
    assert ui_manager._active_bead_id == 1
    assert ui_manager.video_viewer.viewport_updates >= 3


def test_bead_graphic_right_click_defers_deletion_to_scene_handler(qtbot, ui_manager):
    scene = QGraphicsScene(0, 0, 512, 512)
    qtbot.wait(1)

    removed = []
    ui_manager.remove_bead = lambda bead_id: removed.append(bead_id)

    graphic = BeadGraphic(ui_manager, 12, (100, 140, 100, 140), scene)

    class FakeMouseEvent:
        def __init__(self):
            self.ignored = False

        def button(self):
            return Qt.MouseButton.RightButton

        def ignore(self):
            self.ignored = True

    event = FakeMouseEvent()

    graphic.mousePressEvent(event)

    assert removed == []
    assert event.ignored is True
    graphic.remove()


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
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.video_viewer.scene = QGraphicsScene(0, 0, 512, 512)
    ui_manager._bead_rois = {
        1: (10, 20, 30, 40),
        2: (50, 60, 70, 80),
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


def test_callback_view_clicked_selects_and_activates_existing_bead(ui_manager, monkeypatch):
    pos = SimpleNamespace(x=lambda: 15, y=lambda: 35)
    ui_manager._bead_rois = {4: (10, 20, 30, 40)}
    selected = []
    activated = []
    monkeypatch.setattr(ui_manager, 'set_selected_bead', lambda bead_id: selected.append(bead_id))
    monkeypatch.setattr(ui_manager, '_set_active_bead', lambda bead_id: activated.append(bead_id))

    ui_manager.callback_view_clicked(pos)

    assert activated == [4]
    assert selected == [4]


def test_set_selected_bead_syncs_plot_worker_and_controls(ui_manager, monkeypatch):
    activated = []
    ui_manager._bead_rois = {7: (10, 20, 30, 40)}

    ui_manager.video_viewer = FakeVideoViewer()
    monkeypatch.setattr(
        ui_manager,
        '_set_active_bead',
        lambda bead_id: (activated.append(bead_id), setattr(ui_manager, '_active_bead_id', bead_id)),
    )

    ui_manager.set_selected_bead(7)

    assert ui_manager.selected_bead == 7
    assert ui_manager.plot_worker.selected_bead_signal.calls == [7]
    assert ui_manager.controls.plot_settings_panel.selected_bead.lineedit.block_calls == [True, False]
    assert ui_manager.controls.plot_settings_panel.selected_bead.lineedit.text() == '7'
    assert activated == [7]
    assert ui_manager._active_bead_id == 7


def test_set_selected_bead_activates_selected_bead(ui_manager, monkeypatch):
    activated = []
    ui_manager._bead_rois = {7: (10, 20, 30, 40)}

    monkeypatch.setattr(
        ui_manager,
        '_set_active_bead',
        lambda bead_id: activated.append(bead_id),
    )

    ui_manager.set_selected_bead(7)

    assert activated == [7]


def test_set_reference_bead_syncs_plot_worker_and_controls(ui_manager):
    ui_manager.set_reference_bead(9)
    ui_manager.set_reference_bead(None)

    assert ui_manager.reference_bead is None
    assert ui_manager.plot_worker.reference_bead_signal.calls == [9, -1]
    assert ui_manager.controls.plot_settings_panel.reference_bead.lineedit.block_calls == [True, False, True, False]
    assert ui_manager.controls.plot_settings_panel.reference_bead.lineedit.text() == ''


def test_callback_view_clicked_right_click_removes_existing_bead(ui_manager, monkeypatch):
    pos = SimpleNamespace(x=lambda: 15, y=lambda: 35)
    ui_manager._bead_rois = {4: (10, 20, 30, 40)}
    removed = []
    monkeypatch.setattr(ui_manager, 'remove_bead', lambda bead_id: removed.append(bead_id))

    ui_manager.callback_view_clicked(pos, Qt.MouseButton.RightButton)

    assert removed == [4]


def test_add_random_beads_adds_requested_count_inside_visible_view(ui_manager):
    buffer = FakeBeadRoiBuffer()
    commands = []
    ui_manager.bead_roi_buffer = buffer
    ui_manager._broadcast_bead_roi_update = lambda: commands.append(UpdateBeadRoisCommand())
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.settings['ROI'] = 20
    ui_manager._set_active_bead = lambda bead_id: None

    ui_manager.add_random_beads(5, seed=7)

    assert len(ui_manager._bead_rois) == 5
    assert list(sorted(ui_manager._bead_rois)) == [0, 1, 2, 3, 4]
    assert len(buffer.add_calls) == 1
    assert set(buffer.add_calls[0]) == {0, 1, 2, 3, 4}
    assert len(commands) == 1
    for roi in ui_manager._bead_rois.values():
        x0, x1, y0, y1 = roi
        assert 0 <= x0 < x1 <= 512
        assert 0 <= y0 < y1 <= 512


def test_add_random_beads_respects_capacity(ui_manager, monkeypatch):
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager._bead_next_id = ui_manager._bead_roi_capacity
    errors = []
    monkeypatch.setattr(ui_manager, 'show_error', lambda text, details: errors.append((text, details)))

    ui_manager.add_random_beads(3, seed=1)

    assert errors[0][0] == 'Maximum bead count reached'


def test_add_random_beads_rolls_back_next_id_on_buffer_failure(ui_manager):
    class FailingBeadRoiBuffer(FakeBeadRoiBuffer):
        def add_beads(self, value):
            super().add_beads(value)
            raise RuntimeError('boom')

    ui_manager.bead_roi_buffer = FailingBeadRoiBuffer()
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.settings['ROI'] = 20

    with pytest.raises(RuntimeError, match='boom'):
        ui_manager.add_random_beads(3, seed=7)

    assert ui_manager._bead_rois == {}
    assert ui_manager._bead_next_id == 0
    assert ui_manager.controls.bead_selection_panel.next_bead_id_label == 0


def test_add_random_beads_command_dataclass_defaults():
    command = AddRandomBeadsCommand(count=100)

    assert command.count == 100
    assert command.seed is None


def test_add_random_beads_activates_selected_bead(ui_manager):
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.settings['ROI'] = 20
    activated = []
    ui_manager._set_active_bead = lambda bead_id: activated.append(
        bead_id if bead_id in ui_manager._bead_rois else None
    )

    ui_manager.add_random_beads(3, seed=7)

    assert activated == [0]


def test_first_selected_bead_is_active_after_add(ui_manager):
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.settings['ROI'] = 20
    ui_manager._add_bead_roi = lambda bead_id, roi: None
    activated = []
    ui_manager._set_active_bead = lambda bead_id: activated.append(bead_id)

    ui_manager.add_bead(SimpleNamespace(x=lambda: 50, y=lambda: 60))

    assert activated == [0]


def test_clear_beads_resets_selection_so_next_add_activates_first_bead(ui_manager):
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.settings['ROI'] = 20
    ui_manager._add_bead_roi = lambda bead_id, roi: None
    ui_manager._bead_rois = {4: (10, 30, 20, 40)}
    ui_manager.selected_bead = 4
    ui_manager.reference_bead = 4

    activated = []
    ui_manager._set_active_bead = lambda bead_id: activated.append(
        bead_id if bead_id in ui_manager._bead_rois else None
    )

    ui_manager.clear_beads()
    ui_manager.add_bead(SimpleNamespace(x=lambda: 50, y=lambda: 60))

    assert ui_manager.selected_bead == 0


def test_auto_bead_selection_button_state_tracks_conflicts(ui_manager):
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.video_buffer = SimpleNamespace(dtype=np.uint16)

    ui_manager._update_auto_bead_selection_button_state()
    assert ui_manager.controls.bead_selection_panel.auto_select_button.enabled is True

    ui_manager._pending_bead_add_id = 4
    ui_manager._update_auto_bead_selection_button_state()
    assert ui_manager.controls.bead_selection_panel.auto_select_button.enabled is False

    ui_manager._pending_bead_add_id = None
    ui_manager._auto_bead_selection_dialog = object()
    ui_manager._update_auto_bead_selection_button_state()
    assert ui_manager.controls.bead_selection_panel.auto_select_button.enabled is False


def test_start_auto_bead_selection_opens_dialog_and_reenables_button(ui_manager, monkeypatch):
    class FakeEmitter:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def emit(self, *args):
            for callback in list(self.callbacks):
                callback(*args)

    class FakeDialog:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.finished = FakeEmitter()
            self.selectionAccepted = FakeEmitter()
            self.opened = False

        def setAttribute(self, *_args):
            return None

        def open(self):
            self.opened = True

    created = []

    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.video_buffer = SimpleNamespace(dtype=np.uint16)
    ui_manager.settings['ROI'] = 20
    monkeypatch.setattr(ui_manager, '_snapshot_recent_image', lambda: np.zeros((32, 32), dtype=np.uint16))
    monkeypatch.setattr('magscope.ui.ui.AutoBeadSelectionDialog', lambda **kwargs: created.append(FakeDialog(**kwargs)) or created[-1])

    ui_manager.start_auto_bead_selection()

    assert len(created) == 1
    assert created[0].opened is True
    assert created[0].kwargs['roi_size'] == 20
    assert ui_manager.controls.bead_selection_panel.auto_select_button.enabled is False

    created[0].finished.emit(0)

    assert ui_manager.controls.bead_selection_panel.auto_select_button.enabled is True


def test_apply_auto_bead_selection_respects_remaining_capacity(ui_manager, monkeypatch):
    added = []
    warnings = []
    ui_manager._bead_next_id = ui_manager._bead_roi_capacity - 1
    monkeypatch.setattr(ui_manager, '_add_new_bead_batch', lambda rois: added.extend(rois) or {})
    monkeypatch.setattr(ui_manager, 'show_warning', lambda text, details=None: warnings.append((text, details)))

    ui_manager._apply_auto_bead_selection([
        (0, 10, 0, 10),
        (10, 20, 10, 20),
    ])

    assert added == [(0, 10, 0, 10)]
    assert warnings == [
        (
            'Maximum bead count reached',
            '1 bead could not be added because they would exceed the maximum allowed bead count of 10000 beads.',
        )
    ]


def test_apply_auto_bead_selection_keeps_seed_first(ui_manager, monkeypatch):
    added = []
    warnings = []
    monkeypatch.setattr(ui_manager, '_add_new_bead_batch', lambda rois: added.extend(rois) or {})
    monkeypatch.setattr(ui_manager, 'show_warning', lambda text, details=None: warnings.append((text, details)))

    ui_manager._apply_auto_bead_selection([
        (1, 11, 2, 12),
        (20, 30, 22, 32),
    ])

    assert added == [
        (1, 11, 2, 12),
        (20, 30, 22, 32),
    ]
    assert warnings == []


def test_apply_auto_bead_selection_skips_overlapping_existing_and_batch_rois(ui_manager, monkeypatch):
    added = []
    warnings = []
    ui_manager._bead_rois = {3: (1, 11, 2, 12)}
    monkeypatch.setattr(ui_manager, '_add_new_bead_batch', lambda rois: added.extend(rois) or {})
    monkeypatch.setattr(ui_manager, 'show_warning', lambda text, details=None: warnings.append((text, details)))

    ui_manager._apply_auto_bead_selection([
        (2, 12, 2, 12),
        (20, 30, 22, 32),
        (21, 31, 22, 32),
        (40, 50, 42, 52),
    ])

    assert added == [
        (20, 30, 22, 32),
        (40, 50, 42, 52),
    ]
    assert warnings == []


def test_apply_auto_bead_selection_warns_when_capacity_is_full(ui_manager, monkeypatch):
    added = []
    warnings = []
    ui_manager._bead_next_id = ui_manager._bead_roi_capacity
    monkeypatch.setattr(ui_manager, '_add_new_bead_batch', lambda rois: added.extend(rois) or {})
    monkeypatch.setattr(ui_manager, 'show_warning', lambda text, details=None: warnings.append((text, details)))

    ui_manager._apply_auto_bead_selection([
        (0, 10, 0, 10),
        (10, 20, 10, 20),
    ])

    assert added == []
    assert warnings == [
        (
            'Maximum bead count reached',
            '2 beads could not be added because they would exceed the maximum allowed bead count of 10000 beads.',
        )
    ]


def test_invalid_selected_bead_clears_active_bead(ui_manager):
    cleared = []
    ui_manager._set_active_bead = lambda bead_id: cleared.append(bead_id)

    ui_manager.set_selected_bead(-1)

    assert cleared == [None]


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
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.video_viewer.scene = QGraphicsScene(0, 0, 512, 512)
    ui_manager._update_next_bead_id_label = lambda: None
    monkeypatch.setattr(ui_manager, "_add_bead_roi", lambda bead_id, roi: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        ui_manager.add_bead(SimpleNamespace(x=lambda: 10, y=lambda: 20))

    assert ui_manager._pending_bead_add_id is None
    assert ui_manager._pending_bead_add_roi is None
    assert ui_manager._bead_rois == {}
    assert ui_manager._bead_next_id == 0


def test_add_bead_rolls_back_next_id_label_on_roi_update_failure(ui_manager, monkeypatch):
    ui_manager.settings['ROI'] = 20
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.video_viewer.scene = QGraphicsScene(0, 0, 512, 512)
    monkeypatch.setattr(ui_manager, '_add_bead_roi', lambda bead_id, roi: (_ for _ in ()).throw(RuntimeError('boom')))

    with pytest.raises(RuntimeError, match='boom'):
        ui_manager.add_bead(SimpleNamespace(x=lambda: 10, y=lambda: 20))

    assert ui_manager.controls.bead_selection_panel.next_bead_id_label == 0


def test_start_zlut_generation_sends_command(ui_manager):
    commands = []
    ui_manager.send_ipc = commands.append

    ui_manager.start_zlut_generation(start_nm=1.0, step_nm=2.0, stop_nm=3.0)

    assert commands == [StartZLUTGenerationCommand(start_nm=1.0, step_nm=2.0, stop_nm=3.0)]


def test_cancel_zlut_generation_sends_command(ui_manager):
    commands = []
    ui_manager.send_ipc = commands.append

    ui_manager.cancel_zlut_generation()

    assert commands == [CancelZLUTGenerationCommand()]


def test_select_generated_zlut_bead_sends_command(ui_manager):
    commands = []
    ui_manager.send_ipc = commands.append

    ui_manager.select_generated_zlut_bead(7)

    assert commands == [SelectGeneratedZLUTBeadCommand(bead_id=7)]


def test_save_generated_zlut_sends_command(ui_manager, monkeypatch):
    commands = []
    ui_manager.send_ipc = commands.append
    ui_manager.windows = [QMainWindow()]
    monkeypatch.setattr('magscope.ui.ui.QFileDialog.getSaveFileName', lambda *args, **kwargs: ('C:/tmp/test.txt', ''))

    ui_manager.save_generated_zlut(5)

    assert commands == [SaveGeneratedZLUTCommand(filepath='C:/tmp/test.txt', bead_id=5)]


def test_update_zlut_generation_state_forwards_to_panel(ui_manager):
    ui_manager.update_zlut_generation_state(
        'Running',
        detail='Collecting step 1',
        running=True,
        can_cancel=True,
        phase='capturing',
        z_axis_min_nm=10.0,
        z_axis_max_nm=30.0,
    )

    assert ui_manager.controls.z_lut_generation_panel.state_calls == [
        ('Running', 'Collecting step 1', True, True)
    ]
    assert ui_manager._zlut_generation_z_axis_min_nm == 10.0
    assert ui_manager._zlut_generation_z_axis_max_nm == 30.0


def test_update_zlut_generation_state_forwards_to_dialog(ui_manager):
    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()

    ui_manager.update_zlut_generation_state(
        'Running',
        detail='Collecting step 1',
        running=True,
        can_cancel=True,
        phase='capturing',
        z_axis_min_nm=10.0,
        z_axis_max_nm=30.0,
    )

    assert ui_manager._zlut_generation_dialog.state_calls == [
        ('Running', 'Collecting step 1', True, True)
    ]


def test_update_zlut_generation_progress_forwards_to_dialog(ui_manager):
    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()

    ui_manager.update_zlut_generation_progress(1, 4, 8, 32, 12.5)

    assert ui_manager._zlut_generation_dialog.progress_calls == [
        (1, 4, 8, 32, 12.5)
    ]


def test_update_zlut_generation_evaluation_forwards_to_dialog(ui_manager):
    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()

    ui_manager.update_zlut_generation_evaluation(True, [3, 5], selected_bead_id=5)

    assert ui_manager._zlut_evaluation_bead_ids == [3, 5]
    assert ui_manager._zlut_evaluation_selected_bead_id == 5
    assert ui_manager._zlut_generation_dialog.evaluation_calls == [
        (True, [3, 5], 5)
    ]


def test_cancel_generation_still_sends_cancel_during_evaluation(ui_manager):
    commands = []
    ui_manager.send_ipc = commands.append

    ui_manager.cancel_zlut_generation()

    assert commands == [CancelZLUTGenerationCommand()]


def test_update_zlut_generation_dialog_clears_preview_when_dataset_missing(ui_manager, monkeypatch):
    from magscope.ui import ui as ui_module

    class MissingDataset:
        @staticmethod
        def attach(*, locks):
            raise FileNotFoundError('missing')

    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'capturing'
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', MissingDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    assert ui_manager._zlut_generation_dialog.preview_widget.clear_calls == [
        'Waiting for Z-LUT sweep data...'
    ]


def test_update_zlut_generation_dialog_pushes_dataset_preview(ui_manager, monkeypatch):
    from magscope.ui import ui as ui_module

    class FakeDataset:
        STATE_COMPLETE = 4

        def __init__(self):
            self.state = self.STATE_COMPLETE
            self.n_steps = 2
            self.n_beads = 2
            self.profiles_per_bead = 2
            self.profile_length = 3

        @staticmethod
        def attach(*, locks):
            return FakeDataset()

        def peak(self):
            return {
                'bead_ids': np.asarray([5, 5, 7], dtype=np.uint32),
                'step_indices': np.asarray([0, 1, 0], dtype=np.uint32),
                'timestamps': np.asarray([1.0, 2.0, 3.0], dtype=np.float64),
                'motor_z_values': np.asarray([10.0, 20.0, 30.0], dtype=np.float64),
                'valid_flags': np.asarray([1, 1, 1], dtype=np.uint8),
                'profiles': np.asarray(
                    [
                        [1.0, 2.0, 3.0],
                        [4.0, 5.0, 6.0],
                        [7.0, 8.0, 9.0],
                    ],
                    dtype=np.float64,
                ),
            }

        def get_capacity(self):
            return 8

        def close(self):
            pass

    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'evaluating'
    ui_manager._zlut_evaluation_bead_ids = [5, 7]
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', FakeDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    preview_call = ui_manager._zlut_generation_dialog.preview_widget.preview_calls[-1]
    assert preview_call['state'] == FakeDataset.STATE_COMPLETE
    assert preview_call['count'] == 3
    assert preview_call['capacity'] == 8
    assert preview_call['selected_bead_id'] == 5
    assert preview_call['mode'] == 'Averaged sweep'
    assert preview_call['x_axis_label'] == 'Z Position (nm)'
    assert preview_call['x_axis_min'] == 10.0
    assert preview_call['x_axis_max'] == 20.0
    assert preview_call['image_x_min'] == 10.0
    assert preview_call['image_x_max'] == 20.0
    np.testing.assert_allclose(
        preview_call['preview_image'],
        np.asarray([[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]], dtype=np.float64),
    )


def test_live_preview_updates_available_beads_before_evaluation(ui_manager, monkeypatch):
    from magscope.ui import ui as ui_module

    class FakeDataset:
        STATE_CAPTURING = 3
        STATE_COMPLETE = 4

        def __init__(self):
            self.state = self.STATE_CAPTURING
            self.n_steps = 2
            self.n_beads = 2
            self.profiles_per_bead = 2
            self.profile_length = 3

        @staticmethod
        def attach(*, locks):
            return FakeDataset()

        def peak(self):
            return {
                'bead_ids': np.asarray([7, 5, 7, 5], dtype=np.uint32),
                'step_indices': np.asarray([0, 0, 1, 1], dtype=np.uint32),
                'timestamps': np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float64),
                'motor_z_values': np.asarray([10.0, 10.0, 20.0, 20.0], dtype=np.float64),
                'valid_flags': np.asarray([1, 1, 1, 1], dtype=np.uint8),
                'profiles': np.asarray(
                    [
                        [1.0, 2.0, 3.0],
                        [4.0, 5.0, 6.0],
                        [7.0, 8.0, 9.0],
                        [10.0, 11.0, 12.0],
                    ],
                    dtype=np.float64,
                ),
            }

        def get_capacity(self):
            return 8

        def close(self):
            pass

    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'capturing'
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', FakeDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    assert ui_manager._zlut_evaluation_bead_ids == [5, 7]
    assert ui_manager._zlut_evaluation_selected_bead_id == 5
    assert ui_manager._zlut_generation_dialog.evaluation_calls[-1] == (False, [5, 7], 5)
    preview_call = ui_manager._zlut_generation_dialog.preview_widget.preview_calls[-1]
    assert preview_call['x_axis_label'] == 'Z Position (nm)'
    assert preview_call['image_x_min'] is None
    assert preview_call['image_x_max'] is None


def test_live_preview_uses_user_selected_bead(ui_manager, monkeypatch):
    from magscope.ui import ui as ui_module

    class FakeDataset:
        STATE_CAPTURING = 3
        STATE_COMPLETE = 4

        def __init__(self):
            self.state = self.STATE_CAPTURING
            self.n_steps = 2
            self.n_beads = 2
            self.profiles_per_bead = 2
            self.profile_length = 3

        @staticmethod
        def attach(*, locks):
            return FakeDataset()

        def peak(self):
            return {
                'bead_ids': np.asarray([5, 7], dtype=np.uint32),
                'step_indices': np.asarray([0, 0], dtype=np.uint32),
                'timestamps': np.asarray([1.0, 2.0], dtype=np.float64),
                'motor_z_values': np.asarray([10.0, 11.0], dtype=np.float64),
                'valid_flags': np.asarray([1, 1], dtype=np.uint8),
                'profiles': np.asarray(
                    [
                        [1.0, 2.0, 3.0],
                        [4.0, 5.0, 6.0],
                    ],
                    dtype=np.float64,
                ),
            }

        def get_capacity(self):
            return 8

        def close(self):
            pass

    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'capturing'
    ui_manager._zlut_evaluation_selected_bead_id = 7
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', FakeDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    preview_call = ui_manager._zlut_generation_dialog.preview_widget.preview_calls[-1]
    assert preview_call['selected_bead_id'] == 7
    assert preview_call['x_axis_label'] == 'Z Position (nm)'
    assert preview_call['image_x_min'] is None
    assert preview_call['image_x_max'] is None
    np.testing.assert_allclose(
        preview_call['preview_image'],
        np.asarray([[4.0], [5.0], [6.0]], dtype=np.float64),
    )


def test_live_preview_passes_expected_capture_count(ui_manager, monkeypatch):
    from magscope.ui import ui as ui_module

    class FakeDataset:
        STATE_CAPTURING = 3
        STATE_COMPLETE = 4

        def __init__(self):
            self.state = self.STATE_CAPTURING
            self.n_steps = 4
            self.n_beads = 2
            self.profiles_per_bead = 3
            self.profile_length = 2

        @staticmethod
        def attach(*, locks):
            return FakeDataset()

        def peak(self):
            return {
                'bead_ids': np.asarray([5, 5], dtype=np.uint32),
                'step_indices': np.asarray([0, 1], dtype=np.uint32),
                'timestamps': np.asarray([1.0, 2.0], dtype=np.float64),
                'motor_z_values': np.asarray([10.0, 20.0], dtype=np.float64),
                'valid_flags': np.asarray([1, 1], dtype=np.uint8),
                'profiles': np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
            }

        def get_capacity(self):
            return 24

        def close(self):
            pass

    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'capturing'
    ui_manager._zlut_generation_z_axis_min_nm = 10.0
    ui_manager._zlut_generation_z_axis_max_nm = 40.0
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', FakeDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    preview_call = ui_manager._zlut_generation_dialog.preview_widget.preview_calls[-1]
    assert preview_call['mode'] == 'Raw sweep'
    assert preview_call['expected_capture_count'] == 12
    assert preview_call['x_axis_min'] == 10.0
    assert preview_call['x_axis_max'] == 40.0
    assert preview_call['image_x_min'] == 10.0
    assert preview_call['image_x_max'] == 15.0


def test_evaluation_preview_sorts_descending_z_positions_low_to_high(ui_manager, monkeypatch):
    from magscope.ui import ui as ui_module

    class FakeDataset:
        STATE_COMPLETE = 4

        def __init__(self):
            self.state = self.STATE_COMPLETE
            self.n_steps = 2
            self.n_beads = 1
            self.profiles_per_bead = 1
            self.profile_length = 2

        @staticmethod
        def attach(*, locks):
            return FakeDataset()

        def peak(self):
            return {
                'bead_ids': np.asarray([5, 5], dtype=np.uint32),
                'step_indices': np.asarray([0, 1], dtype=np.uint32),
                'timestamps': np.asarray([1.0, 2.0], dtype=np.float64),
                'motor_z_values': np.asarray([20.0, 10.0], dtype=np.float64),
                'valid_flags': np.asarray([1, 1], dtype=np.uint8),
                'profiles': np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
            }

        def get_capacity(self):
            return 2

        def close(self):
            pass

    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'evaluating'
    ui_manager._zlut_evaluation_bead_ids = [5]
    ui_manager._zlut_evaluation_selected_bead_id = 5
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', FakeDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    preview_call = ui_manager._zlut_generation_dialog.preview_widget.preview_calls[-1]
    assert preview_call['x_axis_min'] == 10.0
    assert preview_call['x_axis_max'] == 20.0
    assert preview_call['image_x_min'] == 10.0
    assert preview_call['image_x_max'] == 20.0
    np.testing.assert_allclose(
        preview_call['preview_image'],
        np.asarray([[3.0, 1.0], [4.0, 2.0]], dtype=np.float64),
    )
