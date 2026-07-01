import gc
import os
import sys
import logging
from datetime import datetime
from importlib import resources
from types import MethodType, ModuleType, SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import (
    QByteArray,
    QCoreApplication,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSettings,
    Qt,
)
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QGraphicsScene,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

import magscope.app_icon as app_icon
import magscope.ui.ui as ui_module
from magscope.app_icon import TASKBAR_ICON_RESOURCE, WINDOW_ICON_RESOURCE, load_app_icon
from magscope.ipc_commands import (
    AddRandomBeadsCommand,
    CancelGeneratedZLUTEvaluationCommand,
    CancelZLUTGenerationCommand,
    ClearPendingZLUTLoadRequestCommand,
    LoadZLUTCommand,
    RemoveBeadsFromPendingMovesCommand,
    SaveGeneratedZLUTCommand,
    SelectGeneratedZLUTBeadCommand,
    StartZLUTGenerationCommand,
    StartupReadyCommand,
    UnloadZLUTCommand,
    UpdateBeadRoisCommand,
    UpdateSettingsCommand,
    UpdateTrackingOptionsCommand,
)
from magscope.hardware import FocusMotorBase
from magscope.settings import (
    GUI_ACCENT_COLOR_SETTING,
    GUI_LIVE_PLOT_PROGRESS_BAR_SETTING,
    MagScopeSettings,
    SAVE_TRACKING_ROI_POSITIONS_SETTING,
    TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING,
    TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING,
    default_tracking_options,
)
from magscope.ui.controls import (
    AcquisitionPanel,
    AllanDeviationPanel,
    ControlPanelBase,
    CurrentZLUTDialog,
    PlotSettingsPanel,
    PreferencesDialog,
    SavingSettingsPanel,
    TrackingOptionsPanel,
    XYLockPanel,
    ZLUTGenerationSetupDialog,
    ZLockPanel,
    ZLUTGenerationDialog,
    ZLUTSweepPreviewWidget,
)
from magscope.ui.plots import PlotWorker, TimeSeriesPlotBase, TracksTimeSeriesPlot
from magscope.ui.search import PanelControlTarget, SearchHighlighter, SearchRegistry
from magscope.ui.theme import ACCENT_COLOR, PANEL_BACKGROUND_COLOR, set_accent_color
from magscope.ui.ui import (
    Controls,
    LoadingWindow,
    LivePlotProgressIndicator,
    PLOT_PROGRESS_INDICATOR_SIZE,
    UIManager,
    VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY,
    _StartupReadyWindow,
    _UnifiedTopBar,
    _UnifiedTopMenuBar,
    _default_restored_window_geometry,
    _is_fullscreenish_geometry,
)
from magscope.ui.widgets import BeadGraphic, CollapsibleGroupBox, ResizableLabel
from magscope.utils import AcquisitionMode


QT_GRAPHICS_WINDOWS_PY313 = sys.platform == 'win32' and sys.version_info >= (3, 13)
QT_GRAPHICS_WINDOWS_PY313_REASON = 'QGraphicsScene access violations on Windows Python 3.13'


def clear_ui_manager_singleton() -> None:
    type(UIManager)._instances.pop(UIManager, None)


class StubFocusMotor(FocusMotorBase):
    def move_absolute(self, z: float) -> None:
        pass

    def get_current_z(self) -> float:
        return 0.0

    def get_is_moving(self) -> bool:
        return False

    def get_position_limits(self) -> tuple[float, float]:
        return -100.0, 100.0


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
    NO_DIRECTORY_SELECTED_TEXT = 'No save folder selected'

    def __init__(self):
        self.acquisition_on_checkbox = SimpleNamespace(checkbox=FakeCheckable())
        self.acquisition_dir_textedit = FakeTextEdit()
        self.acquisition_dir_on_checkbox = SimpleNamespace(checkbox=FakeCheckable())
        self.acquisition_mode_combobox = FakeComboBox()
        self.save_highlight_calls: list[bool] = []

    def set_acquisition_dir_text(self, path: str | None) -> None:
        self.acquisition_dir_textedit.setText(path or self.NO_DIRECTORY_SELECTED_TEXT)

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


class FakeResponsivePlotCanvas(QWidget):
    def __init__(
        self,
        figure,
        *,
        minimum_height: int = 210,
        maximum_height: int | None = 235,
        height_for_width: float = 0.72,
    ):
        super().__init__()
        self.figure = figure
        self.draw_count = 0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(minimum_height)
        if maximum_height is not None:
            self.setMaximumHeight(maximum_height)

    def draw(self) -> None:
        self.draw_count += 1


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
        z_axis_descending=False,
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


class StubZLutPreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
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
        z_axis_descending=False,
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


@pytest.fixture
def isolated_qsettings(tmp_path):
    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QSettings('MagScope', 'MagScope').clear()
    yield
    QSettings('MagScope', 'MagScope').clear()
    QSettings.setDefaultFormat(previous_format)


@pytest.fixture(autouse=True)
def cleanup_ui_state(isolated_qsettings):
    set_accent_color(ACCENT_COLOR)
    clear_ui_manager_singleton()
    yield
    app = QApplication.instance()
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    if app is not None:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        gc.collect()
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    clear_ui_manager_singleton()
    set_accent_color(ACCENT_COLOR)


@pytest.fixture
def zlut_dialog_factory(qtbot, monkeypatch):
    from magscope.ui import controls as controls_module

    monkeypatch.setattr(controls_module, 'ZLUTSweepPreviewWidget', StubZLutPreviewWidget)

    def factory() -> ZLUTGenerationDialog:
        dialog = ZLUTGenerationDialog()
        qtbot.addWidget(dialog)
        return dialog

    return factory


@pytest.fixture
def fake_allan_canvas(monkeypatch):
    from magscope.ui import controls as controls_module

    monkeypatch.setattr(controls_module, 'ResponsivePlotCanvas', FakeResponsivePlotCanvas)


class FakeControls:
    def __init__(self):
        self.status_panel = FakeStatusPanel()
        self.acquisition_panel = FakeAcquisitionPanel()
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


class FakeConnectSignal:
    def __init__(self):
        self.connections = []

    def connect(self, callback) -> None:
        self.connections.append(callback)


class FakeLine:
    def __init__(self):
        self.xdata = None
        self.ydata = None

    def set_xdata(self, xdata):
        self.xdata = xdata

    def set_ydata(self, ydata):
        self.ydata = ydata


class FakeAxisDirection:
    def __init__(self):
        self.inverted = False
        self.set_major_formatter_calls = []

    def set_inverted(self, value: bool) -> None:
        self.inverted = value

    def set_major_formatter(self, formatter) -> None:
        self.set_major_formatter_calls.append(formatter)


class FakeAxes:
    def __init__(self):
        self.xaxis = FakeAxisDirection()
        self.yaxis = FakeAxisDirection()
        self.xlim = None
        self.ylim = None
        self.set_xlabel_calls = []

    def autoscale(self) -> None:
        pass

    def autoscale_view(self) -> None:
        pass

    def relim(self) -> None:
        pass

    def set_xlim(self, xmin=None, xmax=None) -> None:
        self.xlim = (xmin, xmax)

    def set_ylim(self, ymin=None, ymax=None) -> None:
        self.ylim = (ymin, ymax)

    def set_xlabel(self, label: str) -> None:
        self.set_xlabel_calls.append(label)


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
    manager.bead_roi_size_label = FakeLabel()
    manager.bead_total_count_label = FakeLabel()
    manager.bead_next_id_label = FakeLabel()
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
    manager.quit()
    clear_ui_manager_singleton()


def contains_widget(root: QWidget, target: QWidget) -> bool:
    return target is root or target in root.findChildren(QWidget)


def test_loading_window_defaults(qtbot):
    window = LoadingWindow()
    qtbot.addWidget(window)

    assert window.label.text() == 'MagScope\n\nloading ...'
    expected_flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
    assert window.windowFlags() & expected_flags == expected_flags

@pytest.mark.skipif(
    sys.platform == 'win32' and sys.version_info >= (3, 13),
    reason='FigureCanvasQTAgg teardown segfaults on Windows Python 3.13 in CI',
)
def test_zlut_preview_widget_masks_non_finite_values_without_red_override(qtbot):
    widget = ZLUTSweepPreviewWidget()
    qtbot.addWidget(widget)

    widget.update_preview(
        state=3,
        count=2,
        capacity=6,
        n_steps=3,
        n_beads=1,
        profiles_per_bead=2,
        profile_length=2,
        preview_image=np.asarray([[1.0, np.nan], [2.0, 3.0]], dtype=np.float64),
        selected_bead_id=5,
        mode='Raw sweep',
        motor_z_min=10.0,
        motor_z_max=20.0,
        x_axis_label='Z Position (nm)',
        x_axis_min=10.0,
        x_axis_max=20.0,
        image_x_min=10.0,
        image_x_max=20.0,
    )

    rendered = widget._image.get_array()
    assert np.ma.isMaskedArray(rendered)
    assert bool(rendered.mask[0, 1])
    assert widget._image.cmap is widget._preview_cmap


def test_build_zlut_preview_payload_averages_complete_sweep():
    preview_payload = UIManager._build_zlut_preview_payload(
        {
            'state': 4,
            'count': 3,
            'capacity': 8,
            'n_steps': 2,
            'n_beads': 2,
            'profiles_per_bead': 2,
            'profile_length': 3,
            'selected_bead_id': 5,
            'motor_z_min': 10.0,
            'motor_z_max': 30.0,
            'step_indices': np.asarray([0, 1], dtype=np.uint32),
            'motor_z_values': np.asarray([10.0, 20.0], dtype=np.float64),
            'profiles': np.asarray(
                [
                    [1.0, 2.0, 3.0],
                    [4.0, 5.0, 6.0],
                ],
                dtype=np.float64,
            ),
        },
        z_axis_min_nm=10.0,
        z_axis_max_nm=20.0,
        z_axis_descending=False,
    )

    assert preview_payload['mode'] == 'Averaged sweep'
    assert preview_payload['selected_bead_id'] == 5
    assert preview_payload['x_axis_min'] == 5.0
    assert preview_payload['x_axis_max'] == 25.0
    assert preview_payload['image_x_min'] == 5.0
    assert preview_payload['image_x_max'] == 25.0
    np.testing.assert_allclose(
        preview_payload['preview_image'],
        np.asarray([[1.0, 4.0], [2.0, 5.0], [3.0, 6.0]], dtype=np.float64),
    )


def test_build_zlut_preview_payload_sorts_descending_raw_low_to_high():
    preview_payload = UIManager._build_zlut_preview_payload(
        {
            'state': 3,
            'count': 3,
            'capacity': 3,
            'n_steps': 3,
            'n_beads': 1,
            'profiles_per_bead': 1,
            'profile_length': 2,
            'selected_bead_id': 5,
            'motor_z_min': 10.0,
            'motor_z_max': 30.0,
            'step_indices': np.asarray([0, 1, 2], dtype=np.uint32),
            'motor_z_values': np.asarray([30.0, 20.0, 10.0], dtype=np.float64),
            'profiles': np.asarray([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], dtype=np.float64),
        },
        z_axis_min_nm=10.0,
        z_axis_max_nm=30.0,
        z_axis_descending=True,
    )

    assert preview_payload['mode'] == 'Raw sweep'
    assert preview_payload['x_axis_min'] == 5.0
    assert preview_payload['x_axis_max'] == 35.0
    assert preview_payload['image_x_min'] == 5.0
    assert preview_payload['image_x_max'] == 35.0
    np.testing.assert_allclose(
        preview_payload['preview_image'],
        np.asarray([[5.0, 3.0, 1.0], [6.0, 4.0, 2.0]], dtype=np.float64),
    )


def test_build_zlut_preview_payload_preserves_sparse_slot_alignment():
    preview_payload = UIManager._build_zlut_preview_payload(
        {
            'state': 3,
            'count': 2,
            'capacity': 24,
            'n_steps': 4,
            'n_beads': 2,
            'profiles_per_bead': 3,
            'profile_length': 2,
            'selected_bead_id': 5,
            'motor_z_min': 10.0,
            'motor_z_max': 20.0,
            'step_indices': np.asarray([0, 1], dtype=np.uint32),
            'motor_z_values': np.asarray([10.0, 20.0], dtype=np.float64),
            'profiles': np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
        },
        z_axis_min_nm=10.0,
        z_axis_max_nm=40.0,
        z_axis_descending=False,
    )

    assert preview_payload['expected_capture_count'] == 12
    assert preview_payload['image_x_min'] == 5.0
    assert preview_payload['image_x_max'] == pytest.approx(18.3333333333)
    np.testing.assert_allclose(
        preview_payload['preview_image'],
        np.asarray([[1.0, np.nan, np.nan, 3.0], [2.0, np.nan, np.nan, 4.0]], dtype=np.float64),
        equal_nan=True,
    )


def test_build_zlut_preview_payload_aligns_descending_partial_capture_right():
    preview_payload = UIManager._build_zlut_preview_payload(
        {
            'state': 3,
            'count': 2,
            'capacity': 3,
            'n_steps': 3,
            'n_beads': 1,
            'profiles_per_bead': 1,
            'profile_length': 2,
            'selected_bead_id': 5,
            'motor_z_min': 20.0,
            'motor_z_max': 30.0,
            'step_indices': np.asarray([0, 1], dtype=np.uint32),
            'motor_z_values': np.asarray([30.0, 20.0], dtype=np.float64),
            'profiles': np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
        },
        z_axis_min_nm=10.0,
        z_axis_max_nm=30.0,
        z_axis_descending=True,
    )

    assert preview_payload['image_x_min'] == 15.0
    assert preview_payload['image_x_max'] == 35.0
    np.testing.assert_allclose(
        preview_payload['preview_image'],
        np.asarray([[3.0, 1.0], [4.0, 2.0]], dtype=np.float64),
    )


def test_zlut_generation_dialog_close_discards_during_evaluation(zlut_dialog_factory):
    dialog = zlut_dialog_factory()

    discard_calls = []
    dialog.set_close_callback(lambda: discard_calls.append('discard'))
    dialog.update_state('Review', running=False, can_cancel=False, phase='evaluating')

    dialog.close()

    assert discard_calls == ['discard']


def test_zlut_generation_dialog_force_close_skips_discard_callback(zlut_dialog_factory):
    dialog = zlut_dialog_factory()

    discard_calls = []
    dialog.set_close_callback(lambda: discard_calls.append('discard'))
    dialog.show()
    dialog.update_state('Review', running=False, can_cancel=False, phase='evaluating')

    dialog.force_close()

    assert discard_calls == []
    assert not dialog.isVisible()


def test_zlut_generation_dialog_cancel_hidden_during_evaluation(zlut_dialog_factory):
    dialog = zlut_dialog_factory()

    dialog.update_state('Review', running=False, can_cancel=False, phase='evaluating')

    assert not dialog.cancel_button.isVisible()
    assert dialog.close_button.text() == 'Cancel'


def test_zlut_generation_dialog_cannot_close_while_starting(zlut_dialog_factory):
    dialog = zlut_dialog_factory()

    dialog.show()
    dialog.mark_starting()
    dialog.close()

    assert dialog.isVisible()
    assert not dialog.close_button.isEnabled()


def test_zlut_generation_dialog_enables_save_actions_for_selected_bead(zlut_dialog_factory):
    dialog = zlut_dialog_factory()

    dialog.update_evaluation(active=True, bead_ids=[3, 5], selected_bead_id=5)

    assert dialog.save_button.text() == 'Save'
    assert dialog.save_button.isEnabled()
    assert dialog.save_and_load_button.text() == 'Save and Load'
    assert dialog.save_and_load_button.isEnabled()


def test_zlut_generation_setup_dialog_accepts_valid_values(qtbot):
    dialog = ZLUTGenerationSetupDialog(roi_size=64, default_measurements=8)
    qtbot.addWidget(dialog)

    dialog.start_input.lineedit.setText('1')
    dialog.step_input.lineedit.setText('2')
    dialog.stop_input.lineedit.setText('3')
    dialog.measurements_input.lineedit.setText('4')
    dialog._accept_if_valid()

    assert dialog.result() == ZLUTGenerationSetupDialog.DialogCode.Accepted
    assert dialog.values == (1.0, 2.0, 3.0, 4)


def test_zlut_generation_dialog_cancel_closes_after_idle_state(zlut_dialog_factory):
    dialog = zlut_dialog_factory()

    cancel_calls = []
    dialog.set_cancel_callback(lambda: cancel_calls.append('cancel'))
    dialog.show()
    dialog.update_state('Capturing', running=True, can_cancel=True, phase='capturing')

    dialog.cancel_button.click()

    assert cancel_calls == ['cancel']
    assert dialog.isVisible()

    dialog.update_state('Canceled', running=False, can_cancel=False, phase='idle')

    assert not dialog.isVisible()


def test_zlut_generation_dialog_cancel_does_not_close_on_failure_state(zlut_dialog_factory):
    dialog = zlut_dialog_factory()

    dialog.set_cancel_callback(lambda: None)
    dialog.show()
    dialog.update_state('Capturing', running=True, can_cancel=True, phase='capturing')

    dialog.cancel_button.click()
    dialog.update_state('Failed', running=False, can_cancel=False, phase='failed')

    assert dialog.isVisible()


class FakeMessageBox:
    class Icon:
        Information = 'information'
        Critical = 'critical'
        Warning = 'warning'

    class StandardButton:
        Ok = 'ok'

    instances = []

    def __init__(self, parent=None):
        self.parent = parent
        self.icon = None
        self.window_title = None
        self.text = None
        self.informative_text = None
        self.detailed_text = None
        self.buttons = None
        self.shown = False
        type(self).instances.append(self)

    def setIcon(self, icon):
        self.icon = icon

    def setWindowTitle(self, title: str):
        self.window_title = title

    def setText(self, text: str):
        self.text = text

    def setInformativeText(self, text: str):
        self.informative_text = text

    def setDetailedText(self, text: str):
        self.detailed_text = text

    def setStandardButtons(self, buttons):
        self.buttons = buttons

    def show(self):
        self.shown = True


@pytest.mark.parametrize(
    ('method_name', 'icon'),
    [
        ('print', FakeMessageBox.Icon.Information),
        ('show_error', FakeMessageBox.Icon.Critical),
        ('show_warning', FakeMessageBox.Icon.Warning),
    ],
)
def test_dialog_helpers_show_details_inline(ui_manager, monkeypatch, method_name, icon):
    FakeMessageBox.instances.clear()
    ui_manager.windows = [object()]
    monkeypatch.setattr('magscope.ui.ui.QMessageBox', FakeMessageBox)

    getattr(ui_manager, method_name)('Summary', 'Full details')

    assert len(FakeMessageBox.instances) == 1
    message_box = FakeMessageBox.instances[0]
    assert message_box.icon == icon
    assert message_box.text == 'Summary'
    assert message_box.informative_text == 'Full details'
    assert message_box.detailed_text is None
    assert message_box.buttons == FakeMessageBox.StandardButton.Ok
    assert message_box.shown is True


def test_startup_ready_window_waits_for_shown_state_before_scheduling(qtbot, monkeypatch):
    ready_calls: list[str] = []
    timer_calls = []
    window = _StartupReadyWindow(lambda: ready_calls.append('ready'))
    qtbot.addWidget(window)

    monkeypatch.setattr(window, 'isVisible', lambda: True)
    monkeypatch.setattr(window, 'windowHandle', lambda: SimpleNamespace(isExposed=lambda: True))
    monkeypatch.setattr(
        'magscope.ui.ui.QTimer.singleShot',
        lambda delay_ms, callback: timer_calls.append(delay_ms) or callback(),
    )

    window._maybe_schedule_startup_ready(after_paint=False)

    assert timer_calls == []
    assert ready_calls == []

    window._startup_shown = True
    window._maybe_schedule_startup_ready(after_paint=False)

    assert timer_calls == [0]
    assert ready_calls == ['ready']


def test_startup_ready_window_schedules_callback_once_when_ready(qtbot, monkeypatch):
    ready_calls: list[str] = []
    timer_calls = []
    window = _StartupReadyWindow(lambda: ready_calls.append('ready'))
    qtbot.addWidget(window)
    window._startup_shown = True

    monkeypatch.setattr(window, 'isVisible', lambda: True)
    monkeypatch.setattr(window, 'windowHandle', lambda: SimpleNamespace(isExposed=lambda: True))
    monkeypatch.setattr(
        'magscope.ui.ui.QTimer.singleShot',
        lambda delay_ms, callback: timer_calls.append(delay_ms) or callback(),
    )

    window._maybe_schedule_startup_ready(after_paint=False)
    window._maybe_schedule_startup_ready(after_paint=True)

    assert timer_calls == [0]
    assert ready_calls == ['ready']


def test_startup_ready_window_schedules_fallback_and_ready(qtbot, monkeypatch):
    ready_calls = []
    timer_calls = []
    window = _StartupReadyWindow(lambda: ready_calls.append('ready'))
    qtbot.addWidget(window)
    window._startup_shown = True
    monkeypatch.setattr(window, 'isVisible', lambda: True)
    monkeypatch.setattr(window, 'windowHandle', lambda: None)
    monkeypatch.setattr(
        ui_module.QTimer,
        'singleShot',
        staticmethod(lambda delay_ms, callback: timer_calls.append(delay_ms) or callback()),
    )

    window._schedule_startup_ready_fallback()
    window._maybe_schedule_startup_ready(after_paint=True)

    assert ui_module.STARTUP_READY_FALLBACK_DELAY_MS in timer_calls
    assert 0 in timer_calls
    assert ready_calls == ['ready']


def test_startup_ready_window_fallback_ignores_deleted_window(qtbot, monkeypatch):
    window = _StartupReadyWindow(lambda: None)
    qtbot.addWidget(window)
    monkeypatch.setattr(
        window,
        '_maybe_schedule_startup_ready',
        lambda after_paint: (_ for _ in ()).throw(RuntimeError('deleted')),
    )

    window._run_startup_ready_fallback()


def test_startup_ready_window_waits_for_exposure_on_regular_platform(qtbot, monkeypatch):
    ready_calls = []
    timer_calls = []
    window = _StartupReadyWindow(lambda: ready_calls.append('ready'))
    qtbot.addWidget(window)
    window._startup_shown = True
    monkeypatch.setattr(window, 'isVisible', lambda: True)
    monkeypatch.setattr(window, 'windowHandle', lambda: SimpleNamespace(isExposed=lambda: False))
    monkeypatch.setattr(ui_module.QGuiApplication, 'platformName', staticmethod(lambda: 'windows'))
    monkeypatch.setattr(
        ui_module.QTimer,
        'singleShot',
        staticmethod(lambda delay_ms, callback: timer_calls.append(delay_ms) or callback()),
    )

    window._maybe_schedule_startup_ready(after_paint=False)
    assert timer_calls == []
    assert ready_calls == []

    window._maybe_schedule_startup_ready(after_paint=True)
    assert timer_calls == [0]
    assert ready_calls == ['ready']


def test_dock_separator_hover_filter_resets_on_leave(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    hover_filter = ui_module._DockSeparatorHoverDelayFilter(window)
    window.setProperty(VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY, True)

    hover_filter.eventFilter(window, QEvent(QEvent.Type.Leave))

    assert window.property(VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY) is False


def test_notify_startup_ready_sends_command_once(ui_manager, monkeypatch):
    commands = []
    ui_manager._command_registry = object()
    ui_manager._pipe = object()
    ui_manager._magscope_quitting = object()

    monkeypatch.setattr(ui_manager, 'send_ipc', lambda command: commands.append(command))

    ui_manager._notify_startup_ready()
    ui_manager._notify_startup_ready()

    assert commands == [StartupReadyCommand(process_name=ui_manager.name)]


def test_create_central_widgets_and_viewer_docks_attach_expected_children(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()

    assert len(manager.central_widgets) == 1
    assert contains_widget(manager.central_widgets[0], manager.controls)
    assert not contains_widget(manager.central_widgets[0], manager.plots_widget)
    assert not contains_widget(manager.central_widgets[0], manager.video_viewer)

    window = QMainWindow()
    qtbot.addWidget(window)
    window.setStyleSheet('QLabel { color: red; }')
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)
    manager._create_view_menu(window)
    manager._reset_viewer_layout()

    assert manager.camera_dock is not None
    assert manager.plots_dock is not None
    assert contains_widget(manager.camera_dock.widget(), manager.video_viewer)
    assert contains_widget(manager.plots_dock.widget(), manager.plots_widget)
    assert manager.camera_dock.widget().findChild(QWidget, 'LiveCameraDockViewerWrapper') is None
    plots_wrapper = manager.plots_dock.widget().findChild(QWidget, 'LivePlotsDockViewerWrapper')
    assert plots_wrapper is not None
    margins = plots_wrapper.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (8, 6, 8, 8)
    assert plots_wrapper.autoFillBackground()
    assert plots_wrapper.palette().color(plots_wrapper.backgroundRole()).name() == PANEL_BACKGROUND_COLOR
    plots_progress_indicator = manager.plots_dock.widget().findChild(
        LivePlotProgressIndicator,
        'LivePlotsProgressIndicator',
    )
    assert plots_progress_indicator is manager.plots_progress_indicator
    assert plots_progress_indicator.parent() is plots_wrapper
    assert plots_progress_indicator.pos().x() == 8
    assert plots_progress_indicator.pos().y() == 6
    assert plots_progress_indicator.minimum() == 0
    assert plots_progress_indicator.maximum() == 1000
    assert plots_progress_indicator.width() == PLOT_PROGRESS_INDICATOR_SIZE
    assert plots_progress_indicator.height() == PLOT_PROGRESS_INDICATOR_SIZE
    assert manager._plot_progress_timer is not None
    assert manager._plot_progress_timer.interval() == 33
    assert manager.camera_dock.toggleViewAction() in window.menuBar().actions()[0].menu().actions()
    assert manager.plots_dock.toggleViewAction() in window.menuBar().actions()[0].menu().actions()
    assert 'QLabel { color: red; }' in window.styleSheet()
    assert 'QMainWindow::separator' in window.styleSheet()
    assert 'QMainWindow::separator:vertical' in window.styleSheet()
    assert 'QMainWindow::separator:horizontal' in window.styleSheet()
    assert '#808080' in window.styleSheet()
    assert (
        f'QMainWindow[{VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY}="true"]::separator:hover'
        in window.styleSheet()
    )
    assert f'background: {ACCENT_COLOR};' in window.styleSheet()
    assert 'width: 5px;' in window.styleSheet()
    assert 'height: 5px;' in window.styleSheet()
    assert window.property(VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY) is False

    hover_filter = window._viewer_dock_separator_hover_delay_filter
    hover_filter.eventFilter(window, QEvent(QEvent.Type.MouseMove))
    assert window.property(VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY) is False
    qtbot.wait(550)
    assert window.property(VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY) is True
    hover_filter.eventFilter(window, QEvent(QEvent.Type.MouseMove))
    assert window.property(VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY) is False

    clear_ui_manager_singleton()


@pytest.mark.parametrize(
    ('dock_name', 'title_bar_name', 'title'),
    [
        ('camera_dock', 'camera_dock_title_bar', 'Live Camera'),
        ('plots_dock', 'plots_dock_title_bar', 'Live Plots'),
    ],
)
def test_docked_viewer_docks_use_material_title_buttons(
    qtbot,
    dock_name,
    title_bar_name,
    title,
):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)

    dock = getattr(manager, dock_name)
    title_bar = getattr(manager, title_bar_name)
    assert dock.titleBarWidget() is title_bar

    title_label = title_bar.findChild(QLabel, f'{dock.objectName()}TitleLabel')
    assert title_label is not None
    assert title_label.text() == title
    assert '#d0d0d0' in title_label.styleSheet()

    undock_button = title_bar.findChild(QToolButton, f'{dock.objectName()}UndockButton')
    assert undock_button is not None
    assert undock_button.text() == 'open_in_new'
    assert undock_button.toolTip() == 'Undock this viewer'
    assert '#d0d0d0' in undock_button.styleSheet()

    close_button = title_bar.findChild(QToolButton, f'{dock.objectName()}CloseButton')
    assert close_button is not None
    assert close_button.text() == 'close'
    assert close_button.toolTip() == 'Close this viewer'
    assert '#d0d0d0' in close_button.styleSheet()

    undock_button.click()
    qtbot.wait(0)
    assert dock.isFloating()
    assert dock.titleBarWidget() is None

    dock.setFloating(False)
    qtbot.wait(0)
    assert not dock.isFloating()
    assert dock.titleBarWidget() is title_bar

    dock.show()
    close_button.click()
    assert dock.isHidden()

    clear_ui_manager_singleton()


@pytest.mark.parametrize('dock_name', ['camera_dock', 'plots_dock'])
def test_floating_viewer_docks_can_be_maximized(qtbot, dock_name):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)

    dock = getattr(manager, dock_name)
    dock.setFloating(True)
    qtbot.wait(0)

    flags = dock.windowFlags()
    assert flags & Qt.WindowType.Window
    assert flags & Qt.WindowType.WindowTitleHint
    assert flags & Qt.WindowType.WindowSystemMenuHint
    assert flags & Qt.WindowType.WindowMaximizeButtonHint
    assert flags & Qt.WindowType.WindowType_Mask == Qt.WindowType.Window
    assert dock.maximumWidth() > 100000
    assert dock.maximumHeight() > 100000
    assert dock.widget().maximumWidth() > 100000
    assert dock.widget().maximumHeight() > 100000
    assert dock.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert dock.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding

    clear_ui_manager_singleton()


@pytest.mark.parametrize('dock_name', ['camera_dock', 'plots_dock'])
def test_floating_viewer_docks_show_dock_button(qtbot, dock_name):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)

    dock = getattr(manager, dock_name)
    header = manager.camera_dock_header if dock_name == 'camera_dock' else manager.plots_dock_header
    assert header is not None
    assert not header.isVisible()
    assert header.height() == 22
    assert header.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed

    dock.setFloating(True)
    qtbot.wait(0)
    assert header.isVisible()

    dock_button = header.findChild(QToolButton)
    assert dock_button is not None
    assert dock_button.text() == 'push_pin'
    assert dock_button.toolTip() == 'Dock this viewer'
    qtbot.mouseClick(dock_button, Qt.MouseButton.LeftButton)

    assert not dock.isFloating()
    assert not header.isVisible()

    clear_ui_manager_singleton()


def test_layout_menu_can_dock_all_windows(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)
    manager._create_view_menu(window)

    assert manager.camera_dock is not None
    assert manager.plots_dock is not None
    manager.camera_dock.setFloating(True)
    manager.plots_dock.setFloating(True)
    qtbot.wait(0)

    layout_menu = window.menuBar().actions()[0].menu()
    dock_all_action = next(
        action for action in layout_menu.actions() if action.text() == 'Dock All Windows'
    )
    dock_all_action.trigger()

    assert not manager.camera_dock.isFloating()
    assert not manager.plots_dock.isFloating()
    assert not manager.camera_dock.isHidden()
    assert not manager.plots_dock.isHidden()
    assert not manager.camera_dock_header.isVisible()
    assert not manager.plots_dock_header.isVisible()

    clear_ui_manager_singleton()


def test_tools_menu_runs_auto_bead_selection(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    starts = []
    monkeypatch.setattr(manager, '_can_start_auto_bead_selection', lambda: True)
    monkeypatch.setattr(manager, 'start_auto_bead_selection', lambda: starts.append(True))

    manager._create_tools_menu(window)

    tools_menu = window.menuBar().actions()[0].menu()
    auto_bead_selection_action = tools_menu.actions()[0]
    auto_bead_selection_action.trigger()

    assert tools_menu.title() == 'Tools'
    assert auto_bead_selection_action.text() == 'Auto Bead Selection'
    assert auto_bead_selection_action.isEnabled()
    assert starts == [True]

    clear_ui_manager_singleton()


def test_zlut_menu_order_and_loaded_state(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    commands = []
    manager.send_ipc = commands.append

    manager._create_zlut_menu(window)

    zlut_menu = window.menuBar().actions()[0].menu()
    actions = zlut_menu.actions()
    assert zlut_menu.title() == 'Z-LUT'
    assert [action.text() for action in actions] == ['New', 'Load', 'Unload', 'Show Current']
    assert actions[0].isEnabled()
    assert actions[1].isEnabled()
    assert not actions[2].isEnabled()
    assert not actions[3].isEnabled()

    manager.update_zlut_metadata(
        filepath='C:/tmp/current_zlut.txt',
        z_min=1.0,
        z_max=5.0,
        step_size=2.0,
        profile_length=64,
    )

    assert actions[2].isEnabled()
    assert actions[3].isEnabled()

    actions[2].trigger()

    assert commands == [UnloadZLUTCommand()]
    assert manager._current_zlut_filepath is None
    assert not actions[2].isEnabled()
    assert not actions[3].isEnabled()

    clear_ui_manager_singleton()


def test_zlut_load_action_opens_file_picker_and_loads(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager.windows = [window]
    commands = []
    manager.send_ipc = commands.append
    monkeypatch.setattr(
        'magscope.ui.ui.QFileDialog.getOpenFileName',
        lambda *args, **kwargs: ('C:/tmp/loaded_zlut.txt', ''),
    )

    manager._create_zlut_menu(window)
    window.menuBar().actions()[0].menu().actions()[1].trigger()

    assert commands == [LoadZLUTCommand(filepath='C:/tmp/loaded_zlut.txt', load_request_id=1)]
    assert manager._current_zlut_filepath == 'C:/tmp/loaded_zlut.txt'
    assert manager._unload_zlut_action.isEnabled()
    assert manager._show_current_zlut_action.isEnabled()

    clear_ui_manager_singleton()


def test_zlut_load_dialog_ignores_stale_first_response_for_visible_state(
    qtbot,
    monkeypatch,
    tmp_path,
):
    clear_ui_manager_singleton()
    first_path = tmp_path / 'first_zlut.txt'
    second_path = tmp_path / 'second_zlut.txt'
    np.savetxt(first_path, np.array([[0.0, 10.0], [1.0, 2.0], [3.0, 4.0]]))
    np.savetxt(second_path, np.array([[20.0, 30.0], [5.0, 6.0], [7.0, 8.0]]))
    selections = iter([str(first_path), str(second_path)])
    monkeypatch.setattr(
        'magscope.ui.ui.QFileDialog.getOpenFileName',
        lambda *args, **kwargs: (next(selections), ''),
    )
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager.windows = [window]
    commands = []
    manager.send_ipc = commands.append
    manager._create_zlut_menu(window)

    load_action = window.menuBar().actions()[0].menu().actions()[1]
    load_action.trigger()
    load_action.trigger()
    manager.show_current_zlut_dialog()
    dialog = manager._current_zlut_dialog

    assert commands == [
        LoadZLUTCommand(filepath=str(first_path), load_request_id=1),
        LoadZLUTCommand(filepath=str(second_path), load_request_id=2),
    ]
    assert dialog is not None
    assert manager._current_zlut_filepath == str(second_path)
    assert manager._current_zlut_metadata == {
        'z_min': None,
        'z_max': None,
        'step_size': None,
        'profile_length': None,
    }
    assert manager._unload_zlut_action.isEnabled()
    assert manager._show_current_zlut_action.isEnabled()
    assert dialog.filepath_label.text() == f'File: {second_path}'
    assert dialog.min_value.text() == ''
    assert dialog.profile_length_value.text() == ''

    manager.update_zlut_metadata(
        filepath=str(first_path),
        z_min=-5.0,
        z_max=5.0,
        step_size=10.0,
        profile_length=2,
        load_request_id=1,
    )

    settings = QSettings('MagScope', 'MagScope')
    assert manager._current_zlut_filepath == str(second_path)
    assert manager._current_zlut_metadata == {
        'z_min': None,
        'z_max': None,
        'step_size': None,
        'profile_length': None,
    }
    assert manager._unload_zlut_action.isEnabled()
    assert manager._show_current_zlut_action.isEnabled()
    assert dialog.filepath_label.text() == f'File: {second_path}'
    assert dialog.min_value.text() == ''
    assert dialog.max_value.text() == ''
    assert dialog.step_value.text() == ''
    assert dialog.profile_length_value.text() == ''
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)

    manager.update_zlut_metadata(
        filepath=str(second_path),
        z_min=20.0,
        z_max=30.0,
        step_size=10.0,
        profile_length=2,
        load_request_id=2,
    )

    assert manager._current_zlut_filepath == str(second_path)
    assert manager._current_zlut_metadata == {
        'z_min': 20.0,
        'z_max': 30.0,
        'step_size': 10.0,
        'profile_length': 2,
    }
    assert dialog.filepath_label.text() == f'File: {second_path}'
    assert dialog.min_value.text() == '20 nm'
    assert dialog.max_value.text() == '30 nm'
    assert dialog.step_value.text() == '10 nm'
    assert dialog.profile_length_value.text() == '2'
    assert (
        settings.value(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, type=str)
        == str(second_path)
    )

    clear_ui_manager_singleton()


def test_update_zlut_metadata_without_pending_request_does_not_remember_filepath():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, '/tmp/dialogs')
    manager = UIManager()

    manager.update_zlut_metadata(
        filepath='/tmp/defaults/simulation_zlut.txt',
        z_min=0.0,
        z_max=10.0,
        step_size=10.0,
        profile_length=2,
    )

    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert settings.value(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, type=str) == '/tmp/dialogs'
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is False

    clear_ui_manager_singleton()


def test_disabled_zlut_ignores_unrequested_default_metadata():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, True)
    manager = UIManager()

    manager.update_zlut_metadata(
        filepath=manager._default_zlut_filepath(),
        z_min=0.0,
        z_max=10.0,
        step_size=10.0,
        profile_length=2,
    )

    assert manager._current_zlut_filepath is None
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is True

    clear_ui_manager_singleton()


def test_disabled_zlut_applies_untagged_load_after_startup_default_metadata():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, '/tmp/zluts/stale.txt')
    settings.setValue(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, True)
    manager = UIManager()
    commands = []
    manager.send_ipc = commands.append
    manager._command_registry = object()
    manager._pipe = object()
    manager._magscope_quitting = object()

    manager._load_remembered_zlut()
    manager.update_zlut_metadata(
        filepath=manager._default_zlut_filepath(),
        z_min=0.0,
        z_max=10.0,
        step_size=10.0,
        profile_length=2,
    )

    assert commands == [UnloadZLUTCommand()]
    assert manager._current_zlut_filepath is None
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is True

    manager.update_zlut_metadata(
        filepath='/tmp/zluts/script_loaded.txt',
        z_min=25.0,
        z_max=125.0,
        step_size=50.0,
        profile_length=64,
        load_request_id=None,
    )

    assert manager._current_zlut_filepath == '/tmp/zluts/script_loaded.txt'
    assert manager._current_zlut_metadata == {
        'z_min': 25.0,
        'z_max': 125.0,
        'step_size': 50.0,
        'profile_length': 64,
    }
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is True

    clear_ui_manager_singleton()


def test_update_zlut_metadata_clear_without_pending_request_clears_remembered_filepath():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, '/tmp/zluts/loaded_zlut.txt')
    settings.setValue(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, '/tmp/zluts')
    manager = UIManager()

    manager.update_zlut_metadata(filepath=None, load_request_id=None)

    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert settings.value(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, type=str) == '/tmp/zluts'
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is True

    clear_ui_manager_singleton()


def test_request_zlut_file_waits_for_matching_metadata_before_remembering():
    clear_ui_manager_singleton()
    manager = UIManager()
    commands = []
    manager.send_ipc = commands.append

    manager.request_zlut_file('/tmp/zluts/loaded_zlut.txt')

    settings = QSettings('MagScope', 'MagScope')
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert commands == [LoadZLUTCommand(filepath='/tmp/zluts/loaded_zlut.txt', load_request_id=1)]
    assert manager._current_zlut_filepath == '/tmp/zluts/loaded_zlut.txt'

    manager.update_zlut_metadata(
        filepath='/tmp/defaults/simulation_zlut.txt',
        z_min=0.0,
        z_max=10.0,
        step_size=10.0,
        profile_length=2,
        load_request_id=1,
    )
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)

    manager.update_zlut_metadata(
        filepath='/tmp/zluts/loaded_zlut.txt',
        z_min=0.0,
        z_max=10.0,
        step_size=10.0,
        profile_length=2,
        load_request_id=1,
    )

    assert (
        settings.value(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, type=str)
        == '/tmp/zluts/loaded_zlut.txt'
    )
    assert settings.value(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, type=str) == '/tmp/zluts'

    clear_ui_manager_singleton()


def test_confirmed_zlut_load_clears_disabled_preference():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, True)
    manager = UIManager()
    commands = []
    manager.send_ipc = commands.append

    manager.request_zlut_file('/tmp/zluts/loaded_zlut.txt')
    manager.update_zlut_metadata(
        filepath='/tmp/zluts/loaded_zlut.txt',
        z_min=0.0,
        z_max=10.0,
        step_size=10.0,
        profile_length=2,
        load_request_id=1,
    )

    assert commands == [LoadZLUTCommand(filepath='/tmp/zluts/loaded_zlut.txt', load_request_id=1)]
    assert settings.value(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, type=str) == '/tmp/zluts/loaded_zlut.txt'
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, True, type=bool) is False

    clear_ui_manager_singleton()


def test_load_remembered_zlut_requests_saved_filepath():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, '/tmp/zluts/remembered.txt')
    manager = UIManager()
    commands = []
    manager.send_ipc = commands.append
    manager._command_registry = object()
    manager._pipe = object()
    manager._magscope_quitting = object()

    manager._load_remembered_zlut()

    assert commands == [LoadZLUTCommand(filepath='/tmp/zluts/remembered.txt', load_request_id=1)]
    assert manager._current_zlut_filepath == '/tmp/zluts/remembered.txt'

    clear_ui_manager_singleton()


def test_load_remembered_zlut_disabled_preference_unloads_backend_default():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, '/tmp/zluts/stale.txt')
    settings.setValue(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, True)
    manager = UIManager()
    commands = []
    manager.send_ipc = commands.append
    manager._command_registry = object()
    manager._pipe = object()
    manager._magscope_quitting = object()
    manager._set_current_zlut(filepath='/tmp/defaults/simulation_zlut.txt')

    manager._load_remembered_zlut()

    assert commands == [UnloadZLUTCommand()]
    assert manager._current_zlut_filepath is None
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is True

    clear_ui_manager_singleton()


def test_load_remembered_zlut_without_ipc_configuration_is_noop():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, '/tmp/zluts/remembered.txt')
    manager = UIManager()
    commands = []
    manager.send_ipc = commands.append

    manager._load_remembered_zlut()

    assert commands == []
    assert manager._current_zlut_filepath is None

    clear_ui_manager_singleton()


def test_update_zlut_metadata_clears_remembered_filepath_after_failed_request():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, '/tmp/zluts/stale.txt')
    settings.setValue(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, '/tmp/zluts')
    manager = UIManager()
    commands = []
    manager.send_ipc = commands.append

    manager.request_zlut_file('/tmp/zluts/stale.txt')
    manager.update_zlut_metadata(filepath=None, load_request_id=1)

    assert commands == [LoadZLUTCommand(filepath='/tmp/zluts/stale.txt', load_request_id=1)]
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert settings.value(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, type=str) == '/tmp/zluts'
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is True

    manager.update_zlut_metadata(
        filepath='/tmp/zluts/stale.txt',
        z_min=0.0,
        z_max=10.0,
        step_size=10.0,
        profile_length=2,
        load_request_id=1,
    )
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is True

    clear_ui_manager_singleton()


def test_stale_failed_zlut_metadata_does_not_clear_newer_pending_request(qtbot):
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, '/tmp/zluts/previous.txt')
    settings.setValue(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, '/tmp/zluts')
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager.windows = [window]
    commands = []
    manager.send_ipc = commands.append
    manager._create_zlut_menu(window)

    manager.request_zlut_file('/tmp/zluts/old.txt')
    manager.request_zlut_file('/tmp/zluts/new.txt')
    manager.show_current_zlut_dialog()
    dialog = manager._current_zlut_dialog

    assert dialog is not None
    assert manager._current_zlut_filepath == '/tmp/zluts/new.txt'
    assert manager._current_zlut_metadata == {
        'z_min': None,
        'z_max': None,
        'step_size': None,
        'profile_length': None,
    }
    assert manager._unload_zlut_action.isEnabled()
    assert manager._show_current_zlut_action.isEnabled()
    assert dialog.filepath_label.text() == 'File: /tmp/zluts/new.txt'

    manager.update_zlut_metadata(filepath=None, load_request_id=1)

    assert commands == [
        LoadZLUTCommand(filepath='/tmp/zluts/old.txt', load_request_id=1),
        LoadZLUTCommand(filepath='/tmp/zluts/new.txt', load_request_id=2),
    ]
    assert manager._current_zlut_filepath == '/tmp/zluts/new.txt'
    assert manager._current_zlut_metadata == {
        'z_min': None,
        'z_max': None,
        'step_size': None,
        'profile_length': None,
    }
    assert manager._unload_zlut_action.isEnabled()
    assert manager._show_current_zlut_action.isEnabled()
    assert dialog.filepath_label.text() == 'File: /tmp/zluts/new.txt'
    assert (
        settings.value(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, type=str)
        == '/tmp/zluts/previous.txt'
    )
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is False

    manager.update_zlut_metadata(
        filepath='/tmp/zluts/new.txt',
        z_min=0.0,
        z_max=10.0,
        step_size=10.0,
        profile_length=2,
        load_request_id=2,
    )

    assert manager._current_zlut_filepath == '/tmp/zluts/new.txt'
    assert manager._current_zlut_metadata == {
        'z_min': 0.0,
        'z_max': 10.0,
        'step_size': 10.0,
        'profile_length': 2,
    }
    assert dialog.filepath_label.text() == 'File: /tmp/zluts/new.txt'
    assert dialog.min_value.text() == '0 nm'
    assert dialog.max_value.text() == '10 nm'
    assert dialog.step_value.text() == '10 nm'
    assert dialog.profile_length_value.text() == '2'
    assert (
        settings.value(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, type=str)
        == '/tmp/zluts/new.txt'
    )
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, True, type=bool) is False

    clear_ui_manager_singleton()


def test_unload_zlut_clears_remembered_filepath():
    clear_ui_manager_singleton()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, '/tmp/zluts/loaded_zlut.txt')
    settings.setValue(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, '/tmp/zluts')
    manager = UIManager()
    commands = []
    manager.send_ipc = commands.append

    manager.unload_zlut()

    assert commands == [UnloadZLUTCommand()]
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)
    assert settings.value(ui_module.LAST_ZLUT_DIRECTORY_SETTINGS_KEY, type=str) == '/tmp/zluts'
    assert settings.value(ui_module.LAST_ZLUT_DISABLED_SETTINGS_KEY, False, type=bool) is True

    clear_ui_manager_singleton()


def test_current_zlut_dialog_renders_loaded_zlut_preview(qtbot, tmp_path):
    zlut_path = tmp_path / 'zlut.txt'
    zlut_array = np.array([
        [0.0, 10.0, 20.0],
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
    ])
    np.savetxt(zlut_path, zlut_array)
    dialog = CurrentZLUTDialog()
    qtbot.addWidget(dialog)

    dialog.update_zlut(str(zlut_path), z_min=0.0, z_max=20.0, step_size=10.0, profile_length=2)

    assert not hasattr(dialog, 'unload_button')
    assert dialog.filepath_label.text() == f'File: {zlut_path}'
    assert dialog.layout().indexOf(dialog.filepath_label) > dialog.layout().indexOf(dialog.canvas)
    assert dialog.preview_status_label.text() == ''
    assert np.asarray(dialog._image.get_array()).shape == (2, 3)
    assert dialog.axes.get_title() == 'Current Z-LUT'


def test_show_current_zlut_reopens_after_close(qtbot, tmp_path):
    clear_ui_manager_singleton()
    zlut_path = tmp_path / 'zlut.txt'
    np.savetxt(zlut_path, np.array([[0.0, 10.0], [1.0, 2.0], [3.0, 4.0]]))
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager.windows = [window]
    manager.update_zlut_metadata(filepath=str(zlut_path), z_min=0.0, z_max=10.0, step_size=10.0, profile_length=2)

    manager.show_current_zlut_dialog()
    first_dialog = manager._current_zlut_dialog
    assert first_dialog is not None
    first_dialog.close()
    qtbot.waitUntil(lambda: manager._current_zlut_dialog is None, timeout=1000)

    manager.show_current_zlut_dialog()
    second_dialog = manager._current_zlut_dialog

    assert second_dialog is not None
    assert second_dialog is not first_dialog
    assert second_dialog.axes.get_title() == 'Current Z-LUT'

    clear_ui_manager_singleton()


def test_current_zlut_dialog_handles_malformed_preview_file(qtbot, tmp_path):
    zlut_path = tmp_path / 'bad_zlut.txt'
    zlut_path.write_text('1 2 3\n')
    dialog = CurrentZLUTDialog()
    qtbot.addWidget(dialog)

    dialog.update_zlut(str(zlut_path))

    assert dialog.preview_status_label.text().startswith('Could not load Z-LUT preview:')


def test_zlut_new_blocks_before_setup_when_no_beads(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = {'ROI': 32, 'video buffer n images': 8}
    manager.video_buffer = object()
    manager.hardware_types = {'focus': StubFocusMotor}
    warnings = []
    monkeypatch.setattr(manager, 'show_warning', lambda text, details=None: warnings.append((text, details)))
    monkeypatch.setattr(
        'magscope.ui.ui.ZLUTGenerationSetupDialog',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('setup dialog opened')),
    )

    manager.show_new_zlut_dialog()

    assert warnings == [
        ('Cannot generate Z-LUT', 'At least one bead ROI must be selected before generating a Z-LUT.')
    ]
    clear_ui_manager_singleton()


def test_zlut_new_blocks_before_setup_when_no_focus_motor(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = {'ROI': 32, 'video buffer n images': 8}
    manager.video_buffer = object()
    manager._bead_rois = {1: (0, 10, 0, 10)}
    warnings = []
    monkeypatch.setattr(manager, 'show_warning', lambda text, details=None: warnings.append((text, details)))

    manager.show_new_zlut_dialog()

    assert warnings == [('Cannot generate Z-LUT', 'No FocusMotorBase hardware is registered.')]
    clear_ui_manager_singleton()


def test_zlut_new_blocks_before_setup_for_non_tracking_mode(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = {'ROI': 32, 'video buffer n images': 8}
    manager.video_buffer = object()
    manager._bead_rois = {1: (0, 10, 0, 10)}
    manager.hardware_types = {'focus': StubFocusMotor}
    manager._acquisition_mode = AcquisitionMode.VIDEO_FULL
    warnings = []
    monkeypatch.setattr(manager, 'show_warning', lambda text, details=None: warnings.append((text, details)))

    manager.show_new_zlut_dialog()

    assert warnings == [
        (
            'Cannot generate Z-LUT',
            'Z-LUT generation requires a tracking acquisition mode. '
            'Switch to Track, Track and Video (ROIs), or Track and Video (Full).',
        )
    ]
    clear_ui_manager_singleton()


def test_zlut_new_action_uses_setup_dialog_values(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = {'ROI': 32, 'video buffer n images': 8}
    manager.video_buffer = object()
    manager._bead_rois = {1: (0, 10, 0, 10)}
    manager.hardware_types = {'focus': StubFocusMotor}
    window = QMainWindow()
    qtbot.addWidget(window)
    starts = []
    manager.start_zlut_generation = lambda **kwargs: starts.append(kwargs)

    class FakeSetupDialog:
        values = (1.0, 2.0, 3.0, 4)

        def __init__(self, parent=None, *, roi_size, default_measurements):
            self.parent = parent
            self.roi_size = roi_size
            self.default_measurements = default_measurements

        def exec(self):
            return 1

    monkeypatch.setattr('magscope.ui.ui.ZLUTGenerationSetupDialog', FakeSetupDialog)

    manager._create_zlut_menu(window)
    window.menuBar().actions()[0].menu().actions()[0].trigger()

    assert starts == [
        {
            'start_nm': 1.0,
            'step_nm': 2.0,
            'stop_nm': 3.0,
            'profiles_per_bead': 4,
        }
    ]

    clear_ui_manager_singleton()


def test_search_suggests_zlut_menu_actions(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._create_zlut_menu(window)
    manager._create_search_menu_widget(window)

    assert manager._search_completion_labels('generate zlut') == ['New Z-LUT - Z-LUT Menu']
    assert manager._search_completion_labels('current zlut') == [
        'Show Current Z-LUT - Z-LUT Menu'
    ]

    clear_ui_manager_singleton()


def test_menu_bar_search_box_follows_help_menu_item(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)

    manager._create_help_menu_action(window)
    manager._create_search_menu_widget(window)

    window.show()
    qtbot.wait(0)

    menu_container = window.menuWidget()
    assert menu_container.objectName() == 'MainMenuContainer'
    assert menu_container.layout().itemAt(0).widget() is manager._menu_row
    assert isinstance(manager._menu_row, _UnifiedTopBar)
    icon_label = manager._menu_row.findChild(QLabel, 'MainWindowIcon')
    title_label = manager._menu_row.findChild(QLabel, 'MainWindowTitleLabel')
    assert manager._menu_row.layout().itemAt(0).widget() is icon_label
    assert manager._menu_row.layout().itemAt(1).widget() is title_label
    assert manager._menu_row.layout().itemAt(2).widget() is manager._top_bar_menu_controls
    search_container = manager._menu_row.layout().itemAt(3).widget()
    search_box = search_container.findChild(QLineEdit, 'MenuSearchBox')
    menu_divider = menu_container.findChild(QFrame, 'MainMenuDivider')
    title_bar_safe_area_spacer = manager._menu_row.findChild(
        QWidget,
        'MainTitleBarSafeAreaSpacer',
    )
    assert manager._menu_row.objectName() == 'MainMenuRow'
    assert icon_label is manager._window_icon_label
    assert title_label is manager._window_title_label
    assert title_label.text() == 'MagScope'
    assert title_label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert search_container.objectName() == 'MenuSearchContainer'
    assert title_bar_safe_area_spacer is manager._title_bar_safe_area_spacer
    assert isinstance(manager._menu_bar, _UnifiedTopMenuBar)
    assert manager._menu_bar.isHidden()
    assert manager._top_bar_menu_controls.objectName() == 'MainTopBarMenuControls'
    assert manager._top_bar_menu_controls.height() == manager._menu_row.height()
    assert search_container.height() == manager._menu_row.height()
    assert title_bar_safe_area_spacer.height() == manager._menu_row.height()
    assert manager._help_menu_action in manager._menu_bar.actions()
    help_button = manager._top_bar_action_buttons['Help']
    assert help_button.action() is manager._help_menu_action
    assert help_button.text() == 'Help'
    assert help_button.height() == manager._menu_row.height()
    assert menu_divider is not None
    assert menu_divider.height() == 1
    assert '#808080' in menu_divider.styleSheet()
    assert isinstance(search_box, QLineEdit)
    assert search_box.isVisible()
    assert search_box.placeholderText() == 'Search for controls ...'
    assert search_box.toolTip() == 'Search shows where controls are; it does not run actions.'
    assert search_box.width() == 300
    assert search_box.height() == manager._menu_row.height() - 8
    assert manager._find_search_target('Auto Bead').label == 'Auto Bead Selection'
    assert manager._menu_row.findChild(QLabel, 'MenuSearchStatusLabel') is manager._search_status_label

    clear_ui_manager_singleton()


def test_unified_top_bar_uses_native_titlebar_extension_and_app_icon(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setWindowTitle('MagScope')
    window.setWindowIcon(load_app_icon())

    manager._configure_unified_top_bar_window(window)
    manager._create_search_menu_widget(window)
    window.show()
    qtbot.wait(0)

    flags = window.windowFlags()
    assert not (flags & Qt.WindowType.FramelessWindowHint)
    assert flags & Qt.WindowType.ExpandedClientAreaHint
    assert flags & Qt.WindowType.NoTitleBarBackgroundHint
    assert flags & Qt.WindowType.CustomizeWindowHint
    assert flags & Qt.WindowType.WindowSystemMenuHint
    assert not (flags & Qt.WindowType.WindowMinMaxButtonsHint)
    assert not (flags & Qt.WindowType.WindowCloseButtonHint)
    assert window.testAttribute(Qt.WidgetAttribute.WA_LayoutOnEntireRect)
    assert isinstance(manager._top_bar, _UnifiedTopBar)
    assert window.menuWidget().geometry().top() == 0

    icon_label = manager._top_bar.findChild(QLabel, 'MainWindowIcon')
    title_label = manager._top_bar.findChild(QLabel, 'MainWindowTitleLabel')
    assert icon_label is manager._window_icon_label
    assert icon_label.toolTip() == 'MagScope'
    assert icon_label.pixmap() is not None
    assert not icon_label.pixmap().isNull()
    assert title_label is manager._window_title_label
    assert title_label.text() == 'MagScope'
    assert title_label.toolTip() == 'MagScope'
    assert manager._menu_row.findChild(QWidget, 'MainWindowControls') is manager._window_controls
    assert manager._minimize_button.text() == ui_module.MAIN_CAPTION_MINIMIZE_ICON
    assert manager._maximize_restore_button.text() == ui_module.MAIN_CAPTION_MAXIMIZE_ICON
    assert manager._close_button.text() == ui_module.MAIN_CAPTION_CLOSE_ICON

    clear_ui_manager_singleton()


def test_unified_top_bar_reserves_native_caption_button_space(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    monkeypatch.setattr(ui_module.sys, 'platform', 'win32')
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setWindowTitle('MagScope')

    manager._configure_unified_top_bar_window(window)
    manager._create_help_menu_action(window)
    manager._create_search_menu_widget(window)
    window.show()
    qtbot.wait(0)

    assert manager._title_bar_safe_area_spacer.width() == (
        UIManager._title_bar_right_safe_area_width(window)
    )
    assert manager._title_bar_safe_area_spacer.height() == manager._menu_row.height()

    clear_ui_manager_singleton()


def test_custom_caption_maximize_restore_button_updates_icon(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._configure_unified_top_bar_window(window)
    manager._create_search_menu_widget(window)
    window.show()
    qtbot.wait(0)

    maximize_restore_button = manager._maximize_restore_button
    assert maximize_restore_button.text() == ui_module.MAIN_CAPTION_MAXIMIZE_ICON
    assert maximize_restore_button.toolTip() == 'Maximize'

    qtbot.mouseClick(maximize_restore_button, Qt.MouseButton.LeftButton)

    assert window.isMaximized()
    assert maximize_restore_button.text() == ui_module.MAIN_CAPTION_RESTORE_ICON
    assert maximize_restore_button.toolTip() == 'Restore'

    qtbot.mouseClick(maximize_restore_button, Qt.MouseButton.LeftButton)

    assert not window.isMaximized()
    assert maximize_restore_button.text() == ui_module.MAIN_CAPTION_MAXIMIZE_ICON
    assert maximize_restore_button.toolTip() == 'Maximize'
    clear_ui_manager_singleton()


def test_caption_state_filter_updates_restore_icon_on_window_state_change(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    maximize_restore_button = QToolButton()
    qtbot.addWidget(maximize_restore_button)
    state_filter = ui_module._CaptionButtonStateFilter(window, maximize_restore_button)
    window.installEventFilter(state_filter)

    window.showMaximized()
    state_filter.eventFilter(window, QEvent(QEvent.Type.WindowStateChange))
    qtbot.waitUntil(lambda: maximize_restore_button.text() != '', timeout=1000)

    assert maximize_restore_button.text() == ui_module.MAIN_CAPTION_RESTORE_ICON
    assert maximize_restore_button.toolTip() == 'Restore'


def test_caption_state_filter_ignores_deleted_button(monkeypatch, qtbot):
    window = QMainWindow()
    button = QToolButton()
    qtbot.addWidget(window)
    qtbot.addWidget(button)
    state_filter = ui_module._CaptionButtonStateFilter(window, button)
    monkeypatch.setattr(
        ui_module,
        '_update_maximize_restore_button',
        lambda *_args: (_ for _ in ()).throw(RuntimeError('deleted')),
    )

    state_filter._update()


def test_unified_top_bar_toggle_and_drag_with_fake_events(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    top_bar = _UnifiedTopBar(window)
    qtbot.addWidget(top_bar)

    top_bar.toggle_maximized()
    assert window.isMaximized()
    top_bar.toggle_maximized()
    assert not window.isMaximized()

    class FakeGlobalPosition:
        def __init__(self, point):
            self._point = point

        def toPoint(self):
            return self._point

    class FakeMouseEvent:
        def __init__(self, *, point=QPoint(20, 30), buttons=Qt.MouseButton.LeftButton):
            self.accepted = False
            self._point = point
            self._buttons = buttons

        def button(self):
            return Qt.MouseButton.LeftButton

        def buttons(self):
            return self._buttons

        def globalPosition(self):
            return FakeGlobalPosition(self._point)

        def accept(self):
            self.accepted = True

    double_click = FakeMouseEvent()
    top_bar.mouseDoubleClickEvent(double_click)
    assert double_click.accepted is True

    class FakeMoveWindow:
        def __init__(self):
            self.moves = []

        def isMaximized(self):
            return False

        def move(self, point):
            self.moves.append(point)

    fake_window = FakeMoveWindow()
    drag_bar = _UnifiedTopBar(fake_window)
    qtbot.addWidget(drag_bar)
    drag_bar._drag_start_global_pos = QPoint(10, 10)
    drag_bar._drag_start_window_pos = QPoint(100, 200)

    move_event = FakeMouseEvent(point=QPoint(15, 25))
    drag_bar.mouseMoveEvent(move_event)

    assert move_event.accepted is True
    assert fake_window.moves == [QPoint(105, 215)]


def test_top_bar_compact_mode_filter_schedules_once_and_handles_runtime_error(monkeypatch):
    callbacks = []
    run_calls = []
    monkeypatch.setattr(
        ui_module.QTimer,
        'singleShot',
        staticmethod(lambda _delay, callback: callbacks.append(callback)),
    )
    compact_filter = ui_module._TopBarCompactModeFilter(lambda: run_calls.append('updated'))

    compact_filter._schedule_update()
    compact_filter._schedule_update()

    assert len(callbacks) == 1
    assert compact_filter._pending_update is True

    callbacks[0]()
    assert run_calls == ['updated']
    assert compact_filter._pending_update is False

    failing_filter = ui_module._TopBarCompactModeFilter(
        lambda: (_ for _ in ()).throw(RuntimeError('deleted')),
    )
    failing_filter._pending_update = True
    failing_filter._run_update()
    assert failing_filter._pending_update is False


def test_top_bar_action_button_no_menu_and_cached_width(qtbot):
    action = ui_module.QAction('&Run')
    triggered = []
    action.triggered.connect(lambda: triggered.append(True))
    action.setToolTip('Run now')
    button = ui_module._TopBarActionButton(action)
    qtbot.addWidget(button)
    button._full_width_hint = 42

    assert button.action() is action
    assert button.toolTip() == 'Run now'
    assert button.full_width_hint() == 42
    assert button.show_action_menu() is False

    button.click()
    assert triggered == [True]

    button.set_icon_only(True)
    button.set_icon_only(True)
    assert button.property('topBarCompact') is True


def test_top_menu_bar_action_lookup_skips_invisible_separator(qtbot):
    menu_bar = _UnifiedTopMenuBar()
    qtbot.addWidget(menu_bar)
    visible_action = menu_bar.addAction('Visible')
    separator = menu_bar.addSeparator()
    hidden_action = menu_bar.addAction('Hidden')
    hidden_action.setVisible(False)
    menu_bar.resize(200, 30)
    menu_bar.show()
    qtbot.wait(0)

    menu_bar._set_hovered_action(visible_action)
    menu_bar._set_hovered_action(visible_action)
    assert menu_bar._hovered_action is visible_action
    menu_bar._set_hovered_action(None)
    assert menu_bar._hovered_action is None

    assert menu_bar._action_at_full_height(QPoint(0, -1)) is None
    assert menu_bar._action_at_full_height(QPoint(menu_bar.actionGeometry(separator).center())) is None
    assert menu_bar._action_at_full_height(QPoint(menu_bar.actionGeometry(hidden_action).center())) is None
    assert menu_bar._action_at_full_height(QPoint(menu_bar.actionGeometry(visible_action).center())) is visible_action
    full_height_rect = menu_bar._full_height_action_rect(visible_action)
    assert full_height_rect.top() == 0
    assert full_height_rect.height() == menu_bar.height()


def test_inject_snap_styles_ignores_non_windows_and_bad_handles(monkeypatch):
    monkeypatch.setattr(ui_module.sys, 'platform', 'linux')
    ui_module._inject_snap_styles(SimpleNamespace(winId=lambda: 123))

    monkeypatch.setattr(ui_module.sys, 'platform', 'win32')
    ui_module._inject_snap_styles(SimpleNamespace(winId=lambda: (_ for _ in ()).throw(RuntimeError('gone'))))
    ui_module._inject_snap_styles(SimpleNamespace(winId=lambda: 0))


def test_top_bar_menu_buttons_reuse_tools_and_zlut_menus(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    clicks = []
    monkeypatch.setattr(manager, '_can_start_auto_bead_selection', lambda: True)
    monkeypatch.setattr(manager, 'start_auto_bead_selection', lambda: clicks.append(True))
    window = QMainWindow()
    qtbot.addWidget(window)

    manager._create_tools_menu(window)
    manager._create_zlut_menu(window)
    manager._create_search_menu_widget(window)
    window.show()
    qtbot.wait(0)

    tools_button = manager._top_bar_menu_buttons['Tools']
    zlut_button = manager._top_bar_menu_buttons['Z-LUT']
    assert isinstance(tools_button, QToolButton)
    assert isinstance(zlut_button, QToolButton)
    assert tools_button.action().menu() is manager._menus['Tools']
    assert zlut_button.action().menu() is manager._zlut_menu
    assert [action.text() for action in manager._zlut_menu.actions()] == [
        'New',
        'Load',
        'Unload',
        'Show Current',
    ]
    assert not manager._unload_zlut_action.isEnabled()
    assert not manager._show_current_zlut_action.isEnabled()

    qtbot.mouseClick(tools_button, Qt.MouseButton.LeftButton)
    assert clicks == []
    qtbot.waitUntil(lambda: manager._menus['Tools'].isVisible(), timeout=1000)
    manager._menus['Tools'].close()

    assert tools_button.show_action_menu(manager._auto_bead_selection_action)
    assert manager._menus['Tools'].activeAction() is manager._auto_bead_selection_action
    manager._auto_bead_selection_action.trigger()
    assert clicks == [True]

    manager._menus['Tools'].close()
    manager._zlut_menu.close()
    clear_ui_manager_singleton()


def test_search_reveals_menu_actions_through_top_bar_button(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    dock_calls = []
    manager._dock_all_viewers = lambda: dock_calls.append(True)
    window = QMainWindow()
    qtbot.addWidget(window)

    manager._create_view_menu(window)
    manager._create_search_menu_widget(window)
    layout_button = manager._top_bar_menu_buttons['Layout']
    shown_actions = []

    def show_action_menu(self, active_action=None):
        shown_actions.append(active_action)
        return True

    monkeypatch.setattr(
        ui_module.QGuiApplication,
        'platformName',
        staticmethod(lambda: 'windows'),
    )
    monkeypatch.setattr(layout_button, 'show_action_menu', MethodType(show_action_menu, layout_button))

    manager._guide_to_search_result('dock')

    assert dock_calls == []
    assert shown_actions == [manager._layout_menu.activeAction()]
    assert manager._layout_menu.activeAction().text() == 'Dock All Windows'

    clear_ui_manager_singleton()


def _create_compact_top_bar_test_window(qtbot, all_menu_actions=False):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setWindowTitle('MagScope')
    manager._configure_unified_top_bar_window(window)
    if all_menu_actions:
        manager._create_view_menu(window)
        manager._create_tools_menu(window)
        manager._create_zlut_menu(window)
        manager._create_preferences_menu_action(window)
    manager._create_help_menu_action(window)
    manager._create_search_menu_widget(window)
    window.show()
    qtbot.wait(0)
    manager._update_top_bar_compact_mode()
    return manager, window


def _resize_compact_top_bar(manager, window, qtbot, width):
    window.resize(width, 300)
    qtbot.wait(0)
    manager._update_top_bar_compact_mode()


def _compact_reserved_width(manager):
    return (
        manager._widget_width_hint(manager._window_icon_label)
        + manager._widget_width_hint(manager._window_controls)
        + manager._widget_width_hint(manager._title_bar_safe_area_spacer)
        + ui_module.TOP_BAR_COMPACT_WIDTH_BUFFER
    )


def _compact_menu_full_width(manager):
    return sum(button.full_width_hint() for button in manager._top_bar_action_buttons.values())


def test_top_bar_compact_mode_hides_title_before_shrinking_search(qtbot):
    manager, window = _create_compact_top_bar_test_window(qtbot)
    title_width = manager._window_title_label.sizeHint().width()
    menu_width = _compact_menu_full_width(manager)
    search_width = manager._search_inline_total_width(ui_module.MENU_SEARCH_FULL_WIDTH)
    compact_width = _compact_reserved_width(manager) + title_width + menu_width + search_width - 1

    _resize_compact_top_bar(manager, window, qtbot, compact_width)

    assert not manager._window_title_label.isVisible()
    assert manager._search_box.isVisible()
    assert manager._search_box.width() == ui_module.MENU_SEARCH_FULL_WIDTH
    assert not manager._search_toggle_button.isVisible()
    assert manager._top_bar_action_buttons['Help'].text() == 'Help'

    clear_ui_manager_singleton()


def test_top_bar_compact_mode_shrinks_then_collapses_search(qtbot):
    manager, window = _create_compact_top_bar_test_window(qtbot)
    menu_width = _compact_menu_full_width(manager)
    shrunk_search_width = manager._search_inline_total_width(ui_module.MENU_SEARCH_MIN_WIDTH + 20)
    compact_width = _compact_reserved_width(manager) + menu_width + shrunk_search_width

    _resize_compact_top_bar(manager, window, qtbot, compact_width)

    assert not manager._window_title_label.isVisible()
    assert manager._search_box.isVisible()
    assert ui_module.MENU_SEARCH_MIN_WIDTH <= manager._search_box.width() < (
        ui_module.MENU_SEARCH_FULL_WIDTH
    )
    assert not manager._search_toggle_button.isVisible()
    assert manager._top_bar_action_buttons['Help'].text() == 'Help'

    collapsed_width = (
        _compact_reserved_width(manager) + menu_width + manager._search_collapsed_total_width()
    )
    _resize_compact_top_bar(manager, window, qtbot, collapsed_width)

    assert not manager._search_box.isVisible()
    assert manager._search_toggle_button.isVisible()
    manager._search_box.setText('find beads')
    qtbot.mouseClick(manager._search_toggle_button, Qt.MouseButton.LeftButton)
    assert manager._search_popup.isVisible()
    assert manager._search_popup_box.text() == 'find beads'
    manager._hide_search_popup()

    clear_ui_manager_singleton()


def test_top_bar_compact_mode_turns_menu_buttons_into_icons_last(qtbot):
    manager, window = _create_compact_top_bar_test_window(qtbot, all_menu_actions=True)
    menu_width = _compact_menu_full_width(manager)
    compact_width = (
        _compact_reserved_width(manager) + menu_width + manager._search_collapsed_total_width() - 1
    )

    _resize_compact_top_bar(manager, window, qtbot, compact_width)

    help_button = manager._top_bar_action_buttons['Help']
    layout_button = manager._top_bar_menu_buttons['Layout']
    zlut_button = manager._top_bar_menu_buttons['Z-LUT']
    assert not manager._search_box.isVisible()
    assert manager._search_toggle_button.isVisible()
    assert help_button.text() == ui_module.TOP_BAR_ACTION_ICONS['Help']
    assert help_button.action() is manager._help_menu_action
    assert layout_button.text() == ui_module.TOP_BAR_ACTION_ICONS['Layout']
    assert layout_button.action().menu() is manager._layout_menu
    assert zlut_button.text() == 'Z'
    assert zlut_button.font().family() != manager._material_symbols_font(point_size=13).family()
    assert help_button.width() == ui_module.MAIN_TOP_BAR_COMPACT_BUTTON_WIDTH

    clear_ui_manager_singleton()


def test_title_bar_safe_area_returns_zero_without_qt_safe_margin(monkeypatch):
    monkeypatch.setattr(ui_module.sys, 'platform', 'win32')
    window = SimpleNamespace(windowHandle=lambda: None)

    assert UIManager._title_bar_right_safe_area_width(window) == 0


def test_title_bar_safe_area_prefers_qt_safe_margin(monkeypatch):
    monkeypatch.setattr(ui_module.sys, 'platform', 'win32')
    margins = SimpleNamespace(right=lambda: 20)
    window_handle = SimpleNamespace(safeAreaMargins=lambda: margins)
    window = SimpleNamespace(windowHandle=lambda: window_handle)

    assert UIManager._title_bar_right_safe_area_width(window) == 20


def test_default_restored_main_window_geometry_is_smaller_than_screen(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setMinimumSize(300, 300)
    available_geometry = QRect(100, 200, 1000, 800)

    geometry = _default_restored_window_geometry(window, available_geometry)

    assert geometry == QRect(150, 240, 900, 720)
    assert not _is_fullscreenish_geometry(geometry, available_geometry)
    assert _is_fullscreenish_geometry(QRect(100, 200, 1000, 800), available_geometry)


def test_default_restored_geometry_uses_window_screen(qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setMinimumSize(1, 1)

    geometry = _default_restored_window_geometry(window)

    assert geometry.width() > 0
    assert geometry.height() > 0
    assert not geometry.isNull()


def test_restore_default_geometry_if_fullscreenish_handles_missing_screen(monkeypatch):
    class FakeWindow:
        def screen(self):
            return None

    monkeypatch.setattr(ui_module.QGuiApplication, 'primaryScreen', staticmethod(lambda: None))

    ui_module._restore_default_geometry_if_fullscreenish(FakeWindow())


def test_restore_default_geometry_if_fullscreenish_applies_restored_size():
    class FakeScreen:
        def availableGeometry(self):
            return QRect(100, 200, 1000, 800)

    class FakeWindow:
        def __init__(self):
            self.applied_geometry = None

        def screen(self):
            return FakeScreen()

        def geometry(self):
            return QRect(100, 200, 1000, 800)

        def minimumWidth(self):
            return 300

        def minimumHeight(self):
            return 300

        def setGeometry(self, geometry):
            self.applied_geometry = geometry

    window = FakeWindow()

    ui_module._restore_default_geometry_if_fullscreenish(window)

    assert window.applied_geometry == QRect(150, 240, 900, 720)


def test_search_registry_ranks_exact_alias_and_fuzzy_matches():
    registry = SearchRegistry([
        PanelControlTarget(
            label='FFT rmin',
            aliases=('rmin', 'minimum FFT radius'),
            context='Preferences > Tracking',
            panel_id='TrackingOptionsPanel',
            keywords=('fft_profile.rmin',),
        ),
        PanelControlTarget(
            label='ROI Size',
            aliases=('ROI', 'region of interest'),
            context='Preferences > MagScope',
            panel_id='MagScopeSettingsPanel',
        ),
        PanelControlTarget(
            label='Dock All Windows',
            aliases=('dock',),
            context='Layout Menu',
            panel_id='LayoutMenu',
        ),
    ])

    assert registry.best('FFT rmin').label == 'FFT rmin'
    assert registry.best('ROI').label == 'ROI Size'
    assert registry.best('dock').label == 'Dock All Windows'
    assert registry.best('fft rmn').label == 'FFT rmin'
    assert registry.best('') is None
    assert registry.best('   ') is None
    assert registry.labels('minimum radius') == ['FFT rmin - Preferences > Tracking']


def test_search_highlighter_uses_theme_accent(qtbot):
    widget = QLabel('target')
    qtbot.addWidget(widget)

    highlighter = SearchHighlighter()
    highlighter.highlight(widget)

    assert f'border: 2px solid {ACCENT_COLOR};' in widget.styleSheet()


def test_control_panel_highlight_uses_theme_accent(qtbot, ui_manager):
    panel = ControlPanelBase(ui_manager, title='Test Panel')
    qtbot.addWidget(panel)

    panel.set_highlighted(True)

    assert f'border: 2px solid {ACCENT_COLOR};' in panel.groupbox.styleSheet()


def test_panel_search_targets_cover_common_controls():
    target_labels = set()
    for panel_class in (
        AcquisitionPanel,
        PlotSettingsPanel,
        XYLockPanel,
        ZLockPanel,
    ):
        target_labels.update(target.label for target in panel_class.search_targets(object()))
    target_labels.update(target.label for target in TrackingOptionsPanel.search_targets())
    target_labels.update(target.label for target in SavingSettingsPanel.search_targets())

    assert 'Acquire' in target_labels
    assert 'Start New Tracking File' in target_labels
    assert 'Selected Bead' in target_labels
    assert 'FFT rmin' in target_labels
    assert 'XY-Lock Once' in target_labels
    assert 'Z-Lock Target' in target_labels


def test_search_guides_to_auto_bead_menu_without_clicking(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    clicks = []
    monkeypatch.setattr(manager, '_can_start_auto_bead_selection', lambda: True)
    monkeypatch.setattr(manager, 'start_auto_bead_selection', lambda: clicks.append(True))
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._create_tools_menu(window)
    manager._create_search_menu_widget(window)

    manager._guide_to_search_result('Auto Bead')

    tools_menu = manager._menus['Tools']
    assert tools_menu.activeAction().text() == 'Auto Bead Selection'
    assert clicks == []
    tools_menu.setActiveAction(None)
    tools_menu.close()

    clear_ui_manager_singleton()


def test_search_focus_clear_and_status_helpers(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._create_search_menu_widget(window)
    window.show()
    qtbot.wait(0)

    manager._search_box.setText('find beads')
    manager._focus_search_box(manager._search_box)
    assert manager._search_box.selectedText() == 'find beads'
    assert len(manager._search_shortcuts) == 3

    manager._set_search_status('Showing: Auto Bead Selection - Tools Menu')
    assert manager._search_status_label.isVisible()
    assert manager._search_status_label.text() == 'Showing: Auto Bead Selection - Tools Menu'

    manager._clear_search_box(manager._search_box)
    assert manager._search_box.text() == ''
    assert not manager._search_status_label.isVisible()

    clear_ui_manager_singleton()


def test_search_escape_shortcut_is_widget_scoped(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._create_search_menu_widget(window)

    assert manager._search_shortcuts[-1].context() == Qt.ShortcutContext.WidgetShortcut

    clear_ui_manager_singleton()


def test_search_status_mentions_guide_only_metadata(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._create_search_menu_widget(window)

    target = PanelControlTarget(
        label='Demo Control',
        context='Demo Panel',
        description='Shows the demo control.',
        panel_id='MissingPanel',
    )
    manager._guide_to_target(target)

    assert manager._search_status_label.text() == (
        'Showing: Demo Control - Demo Panel Guide only; no action was run. '
        'Shows the demo control.'
    )

    clear_ui_manager_singleton()


def test_search_status_clear_handles_deleted_label(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._create_search_menu_widget(window)

    label = manager._search_status_label
    manager._set_search_status('Showing: Demo')
    label.deleteLater()
    qtbot.wait(0)
    manager._clear_search_status_label_ref()

    manager._set_search_status('')

    assert manager._search_status_label is None
    assert manager._search_status_timer is None

    clear_ui_manager_singleton()


def test_search_suggests_find_beads_alias_and_guides_on_enter(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    clicks = []
    monkeypatch.setattr(manager, '_can_start_auto_bead_selection', lambda: True)
    monkeypatch.setattr(manager, 'start_auto_bead_selection', lambda: clicks.append(True))
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._create_tools_menu(window)
    manager._create_search_menu_widget(window)
    window.show()
    qtbot.wait(0)

    manager._search_box.setFocus()
    qtbot.keyClicks(manager._search_box, 'find beads')

    assert manager._search_completion_labels('find bead') == ['Auto Bead Selection - Tools Menu']
    assert manager._menus['Tools'].activeAction() is None

    qtbot.keyClick(manager._search_box, Qt.Key.Key_Return)
    qtbot.waitUntil(
        lambda: manager._menus['Tools'].activeAction() is manager._auto_bead_selection_action,
        timeout=1000,
    )

    assert manager._search_box.text() == 'Auto Bead Selection'
    assert clicks == []
    manager._menus['Tools'].setActiveAction(None)
    manager._menus['Tools'].close()

    clear_ui_manager_singleton()


def test_search_guides_to_roi_setting_in_preferences(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings()
    manager.windows = []
    highlighted_widgets = []
    manager._highlight_search_widget = lambda widget: highlighted_widgets.append(widget)

    manager._guide_to_search_result('ROI')

    dialog = manager._preferences_dialog
    assert isinstance(dialog, PreferencesDialog)
    qtbot.addWidget(dialog)
    roi_widget = dialog.settings_panel._setting_inputs['ROI']
    assert dialog.stack.currentIndex() == 0
    assert highlighted_widgets == [roi_widget]
    assert roi_widget.selectedText() == roi_widget.text()

    clear_ui_manager_singleton()


def test_search_guides_to_saving_setting_in_preferences(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings()
    manager.windows = []
    highlighted_widgets = []
    manager._highlight_search_widget = lambda widget: highlighted_widgets.append(widget)

    manager._guide_to_search_result('Tracking file duration')

    dialog = manager._preferences_dialog
    assert isinstance(dialog, PreferencesDialog)
    qtbot.addWidget(dialog)
    duration_widget = dialog.saving_settings_panel._setting_inputs[
        TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING
    ]
    assert dialog.stack.currentIndex() == 1
    assert highlighted_widgets == [duration_widget]
    assert duration_widget.selectedText() == duration_widget.text()

    clear_ui_manager_singleton()


def test_preferences_places_reset_layout_in_appearance_layout_tab(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings()
    manager.settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] = False
    manager.windows = []
    commands = []
    manager.send_ipc = lambda command: commands.append(command)
    reset_calls = []
    manager.controls = SimpleNamespace(reset_to_defaults=lambda: reset_calls.append(True))
    monkeypatch.setattr(
        'magscope.ui.controls.QMessageBox.question',
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    dialog = PreferencesDialog(manager)
    qtbot.addWidget(dialog)

    assert dialog.sidebar.count() == 4
    assert dialog.accent_color_input.text() == ACCENT_COLOR
    assert f'background-color: {ACCENT_COLOR};' in dialog.accent_color_swatch.styleSheet()
    assert not dialog.live_plot_progress_indicator_checkbox.checkbox.isChecked()
    assert not hasattr(dialog, 'apply_accent_color_button')
    assert not hasattr(dialog, 'accent_color_status_label')
    assert not hasattr(dialog, 'choose_accent_color_button')
    assert hasattr(dialog, 'reset_all_preferences_button')
    assert hasattr(dialog, 'reset_section_button')
    assert dialog.appearance_layout_tab.layout().indexOf(dialog.appearance_status_label) != -1
    assert len(dialog.settings_panel.findChildren(QFrame, 'preferencesGroupPanel')) == 4
    assert len(dialog.saving_settings_panel.findChildren(QFrame, 'preferencesGroupPanel')) == 1
    assert len(dialog.tracking_options_panel.findChildren(QFrame, 'preferencesGroupPanel')) == 4
    assert len(dialog.appearance_layout_tab.findChildren(QFrame, 'preferencesGroupPanel')) == 2
    assert any(
        'Advanced Tracking Options Guide' in label.text()
        for label in dialog.tracking_options_panel.findChildren(QLabel)
    )
    assert not any(
        'Customize GUI accent color' in label.text()
        for label in dialog.appearance_layout_tab.findChildren(QLabel)
    )

    dialog.sidebar.setCurrentRow(3)
    dialog._on_reset_current_section()

    assert reset_calls == [True]
    assert manager.settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] is True
    assert dialog.live_plot_progress_indicator_checkbox.checkbox.isChecked()
    assert any(isinstance(command, UpdateSettingsCommand) for command in commands)

    clear_ui_manager_singleton()


def test_preferences_applies_accent_color_setting(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    icon_calls = []
    original_icon_factory = PreferencesDialog._make_material_symbol_icon

    def record_icon(font, text, color="#888888", size=16):
        icon_calls.append((text, color))
        return original_icon_factory(font, text, color, size)

    monkeypatch.setattr(
        PreferencesDialog,
        '_make_material_symbol_icon',
        staticmethod(record_icon),
    )

    manager = UIManager()
    manager.settings = MagScopeSettings()
    manager.windows = []
    commands = []
    manager.send_ipc = lambda command: commands.append(command)

    dialog = PreferencesDialog(manager)
    qtbot.addWidget(dialog)

    dialog.accent_color_input.setText('#ffce5a')
    dialog.accent_color_input.editingFinished.emit()

    selection_background = PreferencesDialog._sidebar_selection_background('#ffce5a')
    stylesheet = dialog.styleSheet()
    assert manager.settings[GUI_ACCENT_COLOR_SETTING] == '#ffce5a'
    assert dialog.accent_color_input.text() == '#ffce5a'
    assert 'background-color: #ffce5a;' in dialog.accent_color_swatch.styleSheet()
    assert f'background-color: {selection_background};' in stylesheet
    assert 'border-left: 2px solid #ffce5a;' in stylesheet
    assert '#142033' not in stylesheet
    assert '#2f80ed' not in stylesheet
    assert '#1e2a3a' not in stylesheet
    assert ('tune', '#ffce5a') in icon_calls
    assert len(commands) == 1
    assert isinstance(commands[0], UpdateSettingsCommand)
    assert commands[0].settings[GUI_ACCENT_COLOR_SETTING] == '#ffce5a'

    clear_ui_manager_singleton()


def test_preferences_applies_live_plot_progress_indicator_setting(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings()
    manager.windows = []
    commands = []
    apply_calls = []
    manager.send_ipc = lambda command: commands.append(command)
    manager._apply_live_plot_progress_indicator_enabled = lambda: apply_calls.append(
        manager.settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING]
    )

    dialog = PreferencesDialog(manager)
    qtbot.addWidget(dialog)

    dialog.live_plot_progress_indicator_checkbox.checkbox.setChecked(False)

    assert manager.settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] is False
    assert apply_calls == [False]
    assert dialog.appearance_status_label.text() == 'Live plot loading indicator hidden'
    assert len(commands) == 1
    assert isinstance(commands[0], UpdateSettingsCommand)
    assert commands[0].settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] is False

    clear_ui_manager_singleton()


def test_magscope_preferences_apply_field_edits_immediately(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings()
    manager.windows = []
    commands = []
    manager.send_ipc = lambda command: commands.append(command)

    dialog = PreferencesDialog(manager)
    qtbot.addWidget(dialog)

    magnification = dialog.settings_panel._setting_inputs['magnification']
    saved_label = dialog.settings_panel._setting_value_labels['magnification']
    saved_value = str(manager.settings['magnification'])
    new_value = '2.5' if saved_value != '2.5' else '4.0'

    assert saved_label.text() == ''

    magnification.setText(new_value)
    assert saved_label.text() == saved_value

    magnification.editingFinished.emit()

    assert manager.settings['magnification'] == float(new_value)
    assert saved_label.text() == ''
    assert len(commands) == 1
    assert isinstance(commands[0], UpdateSettingsCommand)
    assert commands[0].settings['magnification'] == float(new_value)

    clear_ui_manager_singleton()


def test_tracking_preferences_apply_field_edits_immediately(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings()
    manager.windows = []
    commands = []
    manager.send_ipc = lambda command: commands.append(command)

    dialog = PreferencesDialog(manager)
    qtbot.addWidget(dialog)

    dialog.tracking_options_panel.line_ratio.lineedit.setText('0.25')
    dialog.tracking_options_panel.line_ratio.lineedit.editingFinished.emit()

    assert dialog.tracking_options_panel._current_options['auto_conv_multiline_sub_pixel']['line_ratio'] == 0.25
    assert len(commands) == 1
    assert isinstance(commands[0], UpdateTrackingOptionsCommand)
    assert commands[0].value['auto_conv_multiline_sub_pixel']['line_ratio'] == 0.25

    clear_ui_manager_singleton()


def test_preferences_reset_all_resets_each_preferences_area(qtbot, monkeypatch):
    clear_ui_manager_singleton()
    settings = MagScopeSettings()
    settings['magnification'] = 4.0
    settings[SAVE_TRACKING_ROI_POSITIONS_SETTING] = True
    settings[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] = False
    settings[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] = 15
    settings[GUI_ACCENT_COLOR_SETTING] = '#336699'
    settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] = False
    manager = UIManager()
    manager.settings = settings
    manager.windows = []
    commands = []
    manager.send_ipc = lambda command: commands.append(command)
    reset_calls = []
    manager.controls = SimpleNamespace(reset_to_defaults=lambda: reset_calls.append(True))
    monkeypatch.setattr(
        'magscope.ui.controls.QMessageBox.question',
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    dialog = PreferencesDialog(manager)
    qtbot.addWidget(dialog)
    dialog.tracking_options_panel._current_options['auto_conv_multiline_sub_pixel']['line_ratio'] = 0.5

    qtbot.mouseClick(dialog.reset_all_preferences_button, Qt.MouseButton.LeftButton)

    assert manager.settings['magnification'] == MagScopeSettings()['magnification']
    assert manager.settings[SAVE_TRACKING_ROI_POSITIONS_SETTING] is False
    assert manager.settings[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] is True
    assert manager.settings[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] == 60
    assert manager.settings[GUI_ACCENT_COLOR_SETTING] == ACCENT_COLOR
    assert manager.settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] is True
    assert dialog.live_plot_progress_indicator_checkbox.checkbox.isChecked()
    assert dialog.tracking_options_panel._current_options == default_tracking_options()
    assert reset_calls == [True]
    assert any(isinstance(command, UpdateSettingsCommand) for command in commands)
    assert any(isinstance(command, UpdateTrackingOptionsCommand) for command in commands)

    clear_ui_manager_singleton()


def test_preferences_import_layout_failure_does_not_apply_other_preferences(
    qtbot,
    monkeypatch,
    tmp_path,
):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings()
    manager.windows = []
    commands = []
    manager.send_ipc = lambda command: commands.append(command)

    loaded_settings = manager.settings.clone()
    loaded_settings['magnification'] = 2.5
    loaded_tracking = default_tracking_options()
    loaded_tracking['auto_conv_multiline_sub_pixel']['line_ratio'] = 0.25
    bundle = {
        'magscope': loaded_settings,
        'tracking': loaded_tracking,
        'appearance_layout': {},
    }
    path = tmp_path / 'preferences.yaml'
    critical_messages = []

    def fail_layout_import(_preferences):
        raise ValueError('layout failed')

    manager.import_appearance_layout_preferences = fail_layout_import
    monkeypatch.setattr(
        'magscope.ui.controls.QFileDialog.getOpenFileName',
        lambda *args, **kwargs: (str(path), ''),
    )
    monkeypatch.setattr(
        'magscope.ui.controls.import_preferences_bundle',
        lambda _path: bundle,
    )
    monkeypatch.setattr(
        'magscope.ui.controls.QMessageBox.critical',
        lambda _parent, _title, message: critical_messages.append(message),
    )

    dialog = PreferencesDialog(manager)
    qtbot.addWidget(dialog)

    dialog._on_load_preferences_clicked()

    assert critical_messages == ['layout failed']
    assert commands == []
    assert manager.settings['magnification'] == MagScopeSettings()['magnification']
    assert dialog.tracking_options_panel._current_options == default_tracking_options()

    clear_ui_manager_singleton()


def test_workflow_layout_import_merges_overflow_columns():
    layout = Controls._normalise_workflow_layout(
        Controls,
        [['Run'], ['Analysis'], ['Locking'], [], ['Custom']],
    )

    assert layout == [['Run'], ['Analysis'], ['Locking'], ['Motors']]


def test_workflow_layout_import_accepts_motors_column():
    layout = Controls._normalise_workflow_layout(
        Controls,
        [['Run'], ['Motors'], ['Analysis'], ['Locking']],
    )

    assert layout == [['Run'], ['Motors'], ['Analysis'], ['Locking']]


def test_search_suggests_dock_all_windows_without_executing(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    dock_calls = []
    manager._dock_all_viewers = lambda: dock_calls.append(True)
    window = QMainWindow()
    qtbot.addWidget(window)

    manager._create_view_menu(window)
    manager._create_search_menu_widget(window)
    window.show()
    qtbot.wait(0)

    assert manager._menus['Layout'] is manager._layout_menu
    assert manager._search_completion_labels('dock') == ['Dock All Windows - Layout Menu']
    menu_container = window.menuWidget()
    assert menu_container.objectName() == 'MainMenuContainer'
    assert menu_container.layout().itemAt(0).widget() is manager._menu_row
    assert manager._menu_row.layout().itemAt(2).widget() is manager._top_bar_menu_controls

    manager._guide_to_search_result('dock')

    assert window.menuWidget() is menu_container
    assert menu_container.layout().itemAt(0).widget() is manager._menu_row
    assert manager._menu_row.layout().itemAt(2).widget() is manager._top_bar_menu_controls
    assert dock_calls == []
    assert manager._layout_menu.activeAction().text() == 'Dock All Windows'

    clear_ui_manager_singleton()


def test_search_logs_unmatched_queries(qtbot, caplog):
    clear_ui_manager_singleton()
    manager = UIManager()
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._create_search_menu_widget(window)
    manager._set_search_status('Showing: Demo')

    with caplog.at_level(logging.DEBUG):
        manager._guide_to_search_result('not a real control')

    assert 'No UI search target matched query' in caplog.text
    assert manager._search_status_label.text() == ''
    assert not manager._search_status_label.isVisible()

    clear_ui_manager_singleton()


def test_search_suggests_reset_viewer_layout_without_executing(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    reset_calls = []
    manager._reset_viewer_layout = lambda: reset_calls.append(True)
    window = QMainWindow()
    qtbot.addWidget(window)

    manager._create_view_menu(window)
    manager._create_search_menu_widget(window)

    assert manager._search_completion_labels('reset layout') == ['Reset Viewer Layout - Layout Menu']

    manager._guide_to_search_result('reset layout')

    assert reset_calls == []
    assert manager._layout_menu.activeAction().text() == 'Reset Viewer Layout'

    clear_ui_manager_singleton()


def test_search_guides_to_fft_rmin_in_tracking_preferences(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings()
    manager.windows = []
    highlighted_widgets = []
    manager._highlight_search_widget = lambda widget: highlighted_widgets.append(widget)

    assert manager._search_completion_labels('FFT Rmin') == ['FFT rmin - Preferences > Tracking']

    manager._guide_to_search_result('FFT Rmin')

    dialog = manager._preferences_dialog
    assert isinstance(dialog, PreferencesDialog)
    qtbot.addWidget(dialog)
    assert dialog.stack.currentIndex() == 2
    assert highlighted_widgets == [dialog.tracking_options_panel.fft_rmin]
    assert dialog.tracking_options_panel.fft_rmin.lineedit.selectedText() == (
        dialog.tracking_options_panel.fft_rmin.lineedit.text()
    )

    clear_ui_manager_singleton()


def test_controls_reveal_panel_expands_and_scrolls(qtbot):
    panel = QWidget()
    qtbot.addWidget(panel)
    panel.groupbox = CollapsibleGroupBox('Search Test Panel', collapsed=True)
    qtbot.addWidget(panel.groupbox)
    panel.groupbox._apply_collapsed_state(True, animate=False, persist=False)
    wrapper = SimpleNamespace(column=SimpleNamespace(name='left'))
    scrolled_widgets = []

    class FakeLayoutManager:
        def wrapper_for_id(self, panel_id: str):
            return wrapper

    class FakeScroll:
        def ensureWidgetVisible(self, widget) -> None:
            scrolled_widgets.append(widget)

    controls = SimpleNamespace(
        panels={'DemoPanel': panel},
        layout_manager=FakeLayoutManager(),
        _column_scrolls={'left': FakeScroll()},
    )

    Controls.reveal_panel(controls, 'DemoPanel')

    assert panel.groupbox.collapsed is False
    assert scrolled_widgets == [wrapper]


def test_search_target_widget_falls_back_for_missing_paths(qtbot, ui_manager):
    toolbar = QWidget()
    qtbot.addWidget(toolbar)
    ui_manager.bead_toolbar = toolbar

    live_target = PanelControlTarget(
        label='Live Toolbar',
        context='Live Camera',
        panel_id='LiveBeadToolbar',
    )
    assert ui_manager._search_target_widget(live_target) is toolbar

    missing_live_target = PanelControlTarget(
        label='Missing Live Button',
        context='Live Camera',
        panel_id='LiveBeadToolbar',
        widget_path=('missing_button',),
    )
    assert ui_manager._search_target_widget(missing_live_target) is toolbar

    ui_manager.bead_instructions_button = object()
    non_widget_live_target = PanelControlTarget(
        label='Non Widget Live Button',
        context='Live Camera',
        panel_id='LiveBeadToolbar',
        widget_path=('bead_instructions_button',),
    )
    assert ui_manager._search_target_widget(non_widget_live_target) is None

    panel = QWidget()
    panel.groupbox = CollapsibleGroupBox('Panel')
    qtbot.addWidget(panel)
    qtbot.addWidget(panel.groupbox)
    ui_manager.controls = SimpleNamespace(panels={'DemoPanel': panel})
    panel_target = PanelControlTarget(label='Demo', context='Panel', panel_id='DemoPanel')
    assert ui_manager._search_target_widget(panel_target) is panel.groupbox

    missing_child_target = PanelControlTarget(
        label='Missing Child',
        context='Panel',
        panel_id='DemoPanel',
        widget_path=('missing_child',),
    )
    assert ui_manager._search_target_widget(missing_child_target) is panel


def test_search_status_handles_deleted_label_and_timer_errors(ui_manager):
    class DeletedLabel:
        def setText(self, _text):
            raise RuntimeError('deleted')

        def setVisible(self, _visible):
            raise AssertionError('should not be called')

    ui_manager._search_status_label = DeletedLabel()
    ui_manager._search_status_timer = object()
    ui_manager._set_search_status('Showing: Demo')
    assert ui_manager._search_status_label is None
    assert ui_manager._search_status_timer is None

    class FakeLabel:
        def __init__(self):
            self.calls = []

        def setText(self, text):
            self.calls.append(('text', text))

        def setVisible(self, visible):
            self.calls.append(('visible', visible))

    class DeletedTimer:
        def start(self):
            raise RuntimeError('deleted')

        def stop(self):
            raise RuntimeError('deleted')

    label = FakeLabel()
    ui_manager._search_status_label = label
    ui_manager._search_status_timer = DeletedTimer()
    ui_manager._set_search_status('Showing: Demo')

    assert label.calls == [('text', 'Showing: Demo'), ('visible', True)]
    assert ui_manager._search_status_timer is None


def test_clear_and_focus_search_helpers(qtbot, ui_manager):
    window = QMainWindow()
    qtbot.addWidget(window)
    search_box = QLineEdit(window)
    popup_box = QLineEdit(window)
    popup = QWidget(window)
    search_box.setText('find beads')
    popup_box.setText('find beads')
    popup.show()
    ui_manager._search_box = search_box
    ui_manager._search_popup_box = popup_box
    ui_manager._search_popup = popup
    ui_manager._search_status_label = QLabel(window)

    ui_manager._clear_search_popup_box()
    assert search_box.text() == ''
    assert popup_box.text() == ''
    assert not popup.isVisible()

    search_box.setText('find beads')
    search_box.show()
    ui_manager._focus_search_box(search_box)
    assert search_box.selectedText() == 'find beads'

    ui_manager._clear_search_box(search_box)
    assert search_box.text() == ''


def test_reveal_menu_action_logs_missing_menu_and_action(ui_manager, caplog):
    missing_menu_target = ui_module.MenuActionTarget(
        label='Missing Menu',
        context='Missing',
        menu_name='Missing',
        action_text='Run',
    )
    with caplog.at_level(logging.WARNING):
        ui_manager._reveal_menu_action(missing_menu_target)
    assert 'Search target menu could not be found' in caplog.text

    menu = ui_module.QMenu('Tools')
    ui_manager._menus['Tools'] = menu
    missing_action_target = ui_module.MenuActionTarget(
        label='Missing Action',
        context='Tools',
        menu_name='Tools',
        action_text='Run',
    )
    with caplog.at_level(logging.WARNING):
        ui_manager._reveal_menu_action(missing_action_target)
    assert 'Search target menu action could not be found' in caplog.text


def test_add_column_drop_target_accepts_and_rejects_fake_drop_events(qtbot):
    class FakeManager:
        def __init__(self):
            self.wrapper = object()

        def wrapper_for_id(self, panel_id):
            return self.wrapper if panel_id == 'PanelA' else None

    class FakeControlsForDrop:
        def __init__(self):
            self.layout_manager = FakeManager()
            self.room = True
            self.created = []

        def has_room_for_new_column(self):
            return self.room

        def create_new_column_with_panel(self, wrapper):
            self.created.append(wrapper)

    class FakeDropEvent:
        def __init__(self, mime_data):
            self._mime_data = mime_data
            self.accepted = False
            self.ignored = False

        def mimeData(self):
            return self._mime_data

        def acceptProposedAction(self):
            self.accepted = True

        def ignore(self):
            self.ignored = True

    controls = FakeControlsForDrop()
    target = ui_module.AddColumnDropTarget(controls)
    qtbot.addWidget(target)
    target.set_drag_active(True)
    assert target.isVisible() is True

    empty_mime = ui_module.QMimeData()
    rejected_event = FakeDropEvent(empty_mime)
    target.dragEnterEvent(rejected_event)
    assert rejected_event.ignored is True

    mime = ui_module.QMimeData()
    mime.setData(ui_module.PANEL_MIME_TYPE, b'PanelA')
    accepted_event = FakeDropEvent(mime)
    target.dragMoveEvent(accepted_event)
    assert accepted_event.accepted is True

    drop_event = FakeDropEvent(mime)
    target.dropEvent(drop_event)
    assert drop_event.accepted is True
    assert controls.created == [controls.layout_manager.wrapper]

    controls.room = False
    target.refresh_visibility()
    assert target.isVisible() is False


def test_viewer_layout_save_restore_and_reset(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)
    manager._apply_default_viewer_layout()

    assert not manager._restore_viewer_layout()

    manager.plots_dock.setFloating(True)
    qtbot.wait(0)
    manager._save_viewer_layout()

    settings = QSettings('MagScope', 'MagScope')
    assert settings.value(UIManager.VIEWER_GEOMETRY_SETTINGS_KEY) is not None
    assert settings.value(UIManager.VIEWER_DOCK_STATE_SETTINGS_KEY) is not None

    manager.plots_dock.setFloating(False)
    qtbot.wait(0)
    assert manager._restore_viewer_layout()
    assert manager.plots_dock.isFloating()

    manager._reset_viewer_layout()

    assert settings.value(UIManager.VIEWER_GEOMETRY_SETTINGS_KEY) is None
    assert settings.value(UIManager.VIEWER_DOCK_STATE_SETTINGS_KEY) is None
    assert not manager.camera_dock.isFloating()
    assert not manager.plots_dock.isFloating()
    assert not manager.camera_dock_header.isVisible()
    assert not manager.plots_dock_header.isVisible()

    clear_ui_manager_singleton()


def test_live_camera_dock_includes_bead_toolbar(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings({'ROI': 32}, persistence_available=False)
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    reset_calls = []
    clear_calls = []
    instruction_calls = []
    manager.reset_bead_ids = lambda: reset_calls.append(True)
    manager.clear_beads = lambda: clear_calls.append(True)
    manager.show_bead_selection_instructions = lambda: instruction_calls.append(True)
    window = QMainWindow()
    qtbot.addWidget(window)

    manager._create_viewer_docks(window)
    manager._bead_rois = {2: (10, 42, 10, 42), 5: (50, 82, 50, 82)}
    manager._bead_next_id = 6
    manager._update_live_bead_toolbar_labels()

    assert manager.bead_toolbar is not None
    assert isinstance(manager.bead_instructions_button, QPushButton)
    assert manager.bead_instructions_button.text() == 'Add/Remove Beads'
    assert manager.bead_roi_size_label.text() == 'ROI: 32 px'
    assert manager.bead_total_count_label.text() == 'Total Beads: 2'
    assert manager.bead_next_id_label.text() == 'Next Bead ID: 6'
    assert manager.bead_reassign_ids_button.text() == 'Reassign IDs'
    assert manager.bead_remove_all_button.text() == 'Remove All'
    assert manager.camera_dock.widget().layout().indexOf(manager.bead_toolbar) != -1

    qtbot.mouseClick(manager.bead_instructions_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(manager.bead_reassign_ids_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(manager.bead_remove_all_button, Qt.MouseButton.LeftButton)

    assert instruction_calls == [True]
    assert reset_calls == [True]
    assert clear_calls == [True]

    clear_ui_manager_singleton()


def test_search_guides_to_live_bead_toolbar(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.settings = MagScopeSettings({'ROI': 32}, persistence_available=False)
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)
    window = QMainWindow()
    qtbot.addWidget(window)
    manager._create_viewer_docks(window)
    manager._create_search_menu_widget(window)
    highlighted_widgets = []
    manager._highlight_search_widget = lambda widget: highlighted_widgets.append(widget)

    manager._guide_to_search_result('renumber beads')

    assert manager._search_box.text() == 'Reassign IDs'
    assert highlighted_widgets == [manager.bead_reassign_ids_button]

    clear_ui_manager_singleton()


def test_reset_viewer_layout_restores_hidden_docks(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)
    manager._apply_default_viewer_layout()

    manager.camera_dock.hide()
    manager.plots_dock.hide()
    assert manager.camera_dock.isHidden()
    assert manager.plots_dock.isHidden()

    manager._reset_viewer_layout()

    assert not manager.camera_dock.isHidden()
    assert not manager.plots_dock.isHidden()
    assert not manager.camera_dock.isFloating()
    assert not manager.plots_dock.isFloating()
    assert not manager.camera_dock_header.isVisible()
    assert not manager.plots_dock_header.isVisible()

    clear_ui_manager_singleton()


def test_invalid_viewer_layout_restore_clears_saved_state(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)

    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(UIManager.VIEWER_GEOMETRY_SETTINGS_KEY, b'invalid geometry')
    settings.setValue(UIManager.VIEWER_DOCK_STATE_SETTINGS_KEY, b'invalid dock state')

    assert not manager._restore_viewer_layout()
    assert settings.value(UIManager.VIEWER_GEOMETRY_SETTINGS_KEY) is None
    assert settings.value(UIManager.VIEWER_DOCK_STATE_SETTINGS_KEY) is None

    clear_ui_manager_singleton()


def test_invalid_viewer_geometry_does_not_apply_saved_dock_state(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)
    manager._apply_default_viewer_layout()

    manager.plots_dock.hide()
    saved_dock_state = window.saveState(UIManager.VIEWER_LAYOUT_STATE_VERSION)
    manager._apply_default_viewer_layout()
    assert not manager.camera_dock.isHidden()
    assert not manager.plots_dock.isHidden()

    settings = QSettings('MagScope', 'MagScope')
    settings.setValue(UIManager.VIEWER_GEOMETRY_SETTINGS_KEY, b'invalid geometry')
    settings.setValue(UIManager.VIEWER_DOCK_STATE_SETTINGS_KEY, saved_dock_state)

    assert not manager._restore_viewer_layout()
    assert not manager.camera_dock.isHidden()
    assert not manager.plots_dock.isHidden()
    assert settings.value(UIManager.VIEWER_GEOMETRY_SETTINGS_KEY) is None
    assert settings.value(UIManager.VIEWER_DOCK_STATE_SETTINGS_KEY) is None

    clear_ui_manager_singleton()


def test_import_appearance_layout_rejects_invalid_viewer_geometry(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)
    manager._apply_default_viewer_layout()

    preferences = {
        'viewer_geometry': UIManager._encode_qbytearray(QByteArray(b'invalid geometry')),
        'viewer_dock_state': UIManager._encode_qbytearray(
            window.saveState(UIManager.VIEWER_LAYOUT_STATE_VERSION)
        ),
    }

    with pytest.raises(ValueError, match='viewer_geometry'):
        manager.import_appearance_layout_preferences(preferences)

    settings = QSettings('MagScope', 'MagScope')
    assert settings.value(UIManager.VIEWER_GEOMETRY_SETTINGS_KEY) is None
    assert settings.value(UIManager.VIEWER_DOCK_STATE_SETTINGS_KEY) is None

    clear_ui_manager_singleton()


def test_import_appearance_layout_rejects_invalid_viewer_dock_state(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)
    manager._apply_default_viewer_layout()

    original_geometry = window.saveGeometry()
    original_dock_state = window.saveState(UIManager.VIEWER_LAYOUT_STATE_VERSION)
    window.setGeometry(100, 120, 720, 520)
    imported_geometry = window.saveGeometry()
    window.restoreGeometry(original_geometry)
    window.restoreState(original_dock_state, UIManager.VIEWER_LAYOUT_STATE_VERSION)
    previous_rect = window.geometry()
    previous_dock_state = window.saveState(UIManager.VIEWER_LAYOUT_STATE_VERSION)

    preferences = {
        'viewer_geometry': UIManager._encode_qbytearray(imported_geometry),
        'viewer_dock_state': UIManager._encode_qbytearray(QByteArray(b'invalid dock state')),
    }

    with pytest.raises(ValueError, match='viewer_dock_state'):
        manager.import_appearance_layout_preferences(preferences)

    assert window.geometry() == previous_rect
    assert window.saveState(UIManager.VIEWER_LAYOUT_STATE_VERSION) == previous_dock_state

    settings = QSettings('MagScope', 'MagScope')
    assert settings.value(UIManager.VIEWER_GEOMETRY_SETTINGS_KEY) is None
    assert settings.value(UIManager.VIEWER_DOCK_STATE_SETTINGS_KEY) is None

    clear_ui_manager_singleton()


def test_validate_appearance_layout_rejects_non_integer_splitter_size():
    clear_ui_manager_singleton()
    manager = UIManager()

    with pytest.raises(ValueError, match='splitter_sizes.Main Grip Splitter Sizes'):
        manager.validate_appearance_layout_preferences(
            {'splitter_sizes': {'Main Grip Splitter Sizes': [None]}},
        )

    clear_ui_manager_singleton()


@pytest.mark.parametrize(
    ('preferences', 'message'),
    [
        ([], 'appearance_layout must be a mapping'),
        ({'viewer_geometry': 123}, 'viewer_geometry must be a string'),
        ({'viewer_dock_state': 123}, 'viewer_dock_state must be a string'),
        ({'splitter_sizes': []}, 'splitter_sizes must be a mapping'),
        ({'splitter_sizes': {'Main': '1,2'}}, 'splitter_sizes.Main must be a list'),
    ],
)
def test_validate_appearance_layout_rejects_invalid_shapes(preferences, message):
    clear_ui_manager_singleton()
    manager = UIManager()

    with pytest.raises(ValueError, match=message):
        manager.validate_appearance_layout_preferences(preferences)

    clear_ui_manager_singleton()


def test_export_appearance_layout_skips_bad_splitter_sizes(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue('Main Grip Splitter Sizes', [100, '200'])
    settings.setValue('Bad Grip Splitter Sizes', [None])
    settings.setValue('Unrelated', [1, 2])
    manager.controls = SimpleNamespace(export_preferences=lambda: {'panels': True})

    preferences = manager.export_appearance_layout_preferences()

    assert preferences['controls'] == {'panels': True}
    assert preferences['splitter_sizes'] == {'Main Grip Splitter Sizes': [100, 200]}

    clear_ui_manager_singleton()


def test_import_appearance_layout_saves_splitter_sizes_and_controls(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    imported_controls = []
    manager.controls = SimpleNamespace(import_preferences=lambda value: imported_controls.append(value))

    manager.import_appearance_layout_preferences(
        {
            'controls': {'panels': True},
            'splitter_sizes': {'Main Grip Splitter Sizes': ['1', 2]},
        },
    )

    settings = QSettings('MagScope', 'MagScope')
    assert imported_controls == [{'panels': True}]
    assert list(map(int, settings.value('Main Grip Splitter Sizes', [], list))) == [1, 2]

    clear_ui_manager_singleton()


def test_controls_load_saved_layout_normalizes_aliases_and_missing_tabs():
    class FakeSettings:
        def value(self, _key, _default='', type=str):
            return '[ ["Run", "Custom", "Run"], "bad", ["Unknown"] ]'

    controls = SimpleNamespace(
        _settings=FakeSettings(),
        WORKFLOW_COLUMNS_SETTINGS_KEY=Controls.WORKFLOW_COLUMNS_SETTINGS_KEY,
        WORKFLOW_ORDER=Controls.WORKFLOW_ORDER,
        _canonical_workflow_tab_id=lambda tab_id: Controls._canonical_workflow_tab_id(tab_id),
    )

    layout = Controls._load_saved_layout(controls)

    assert layout == [['Run', 'Motors'], ['Analysis', 'Locking']]


def test_controls_layout_for_column_count_merges_and_expands_saved_layout():
    controls = SimpleNamespace(DEFAULT_LAYOUTS=Controls.DEFAULT_LAYOUTS, MAX_COLUMNS=Controls.MAX_COLUMNS)
    controls._default_layout_for_count = MethodType(Controls._default_layout_for_count, controls)
    controls._fill_empty_columns = MethodType(Controls._fill_empty_columns, controls)
    controls._tabs_for_empty_column = MethodType(Controls._tabs_for_empty_column, controls)
    controls._expand_layout_to_count = MethodType(Controls._expand_layout_to_count, controls)

    controls._load_saved_layout = lambda: [['Run'], ['Analysis'], ['Locking'], ['Motors']]
    assert Controls._layout_for_column_count(controls, 2) == [['Run'], ['Analysis', 'Locking', 'Motors']]

    controls._load_saved_layout = lambda: [['Run', 'Motors', 'Analysis', 'Locking']]
    assert Controls._layout_for_column_count(controls, 3) == [['Run', 'Motors'], ['Analysis'], ['Locking']]


def test_controls_tabs_for_empty_column_falls_back_to_largest_source():
    columns = [['Run', 'Analysis', 'Locking'], [], ['Motors']]
    moved = Controls._tabs_for_empty_column(
        Controls,
        columns,
        [[], ['Z-LUT'], []],
        1,
    )

    assert moved == ['Locking']
    assert columns == [['Run', 'Analysis'], [], ['Motors']]


@pytest.mark.parametrize(
    ('preferences', 'message'),
    [
        ([], 'appearance_layout.controls must be a mapping'),
        ({'workflow_columns': 'Run'}, 'workflow_columns must be a list'),
        ({'workflow_columns': ['Run']}, 'workflow_columns columns must be lists'),
        ({'panel_collapsed': []}, 'panel_collapsed must be a mapping'),
    ],
)
def test_controls_preferences_reject_invalid_shapes(preferences, message):
    controls = SimpleNamespace(
        MAX_COLUMNS=Controls.MAX_COLUMNS,
        WORKFLOW_ORDER=Controls.WORKFLOW_ORDER,
        _canonical_workflow_tab_id=lambda tab_id: Controls._canonical_workflow_tab_id(tab_id),
    )
    controls._normalise_workflow_layout = MethodType(Controls._normalise_workflow_layout, controls)

    with pytest.raises(ValueError, match=message):
        Controls.validate_preferences(controls, preferences)


def test_controls_preferences_reject_string_panel_collapsed():
    controls = SimpleNamespace()

    with pytest.raises(ValueError, match='panel_collapsed.CameraPanel'):
        Controls.validate_preferences(
            controls,
            {'panel_collapsed': {'CameraPanel': 'false'}},
        )


def test_controls_preferences_apply_false_panel_collapsed(qtbot):
    groupbox = CollapsibleGroupBox('Camera Settings', collapsed=True)
    qtbot.addWidget(groupbox)
    controls = SimpleNamespace(panels={'CameraPanel': SimpleNamespace(groupbox=groupbox)})
    controls.validate_preferences = MethodType(Controls.validate_preferences, controls)

    Controls.import_preferences(
        controls,
        {'panel_collapsed': {'CameraPanel': False}},
    )

    assert groupbox.collapsed is False


def test_quit_saves_viewer_layout(qtbot):
    clear_ui_manager_singleton()
    manager = UIManager()
    manager.controls = QLabel('controls')
    manager.plots_widget = QLabel('plots')
    manager.video_viewer = QLabel('video')
    for widget in (manager.controls, manager.plots_widget, manager.video_viewer):
        qtbot.addWidget(widget)

    manager.create_central_widgets()
    window = QMainWindow()
    qtbot.addWidget(window)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)
    manager._apply_default_viewer_layout()
    manager.plots_dock.setFloating(True)
    qtbot.wait(0)

    manager.quit()

    settings = QSettings('MagScope', 'MagScope')
    assert settings.value(UIManager.VIEWER_GEOMETRY_SETTINGS_KEY) is not None
    assert settings.value(UIManager.VIEWER_DOCK_STATE_SETTINGS_KEY) is not None

    clear_ui_manager_singleton()


def test_ui_manager_n_windows_warns_that_value_is_ignored():
    clear_ui_manager_singleton()
    manager = UIManager()

    with pytest.warns(RuntimeWarning, match='n_windows has been removed'):
        manager.n_windows = 2

    assert manager.n_windows is None
    assert not hasattr(manager, '_n_windows')

    clear_ui_manager_singleton()


def test_material_symbols_font_is_packaged():
    font_resource = resources.files('magscope').joinpath('assets/MaterialSymbolsRounded.ttf')

    assert font_resource.is_file()


def test_app_icons_are_packaged():
    for resource_name in (TASKBAR_ICON_RESOURCE, WINDOW_ICON_RESOURCE):
        icon_resource = resources.files('magscope').joinpath('assets', resource_name)

        assert icon_resource.is_file()


def test_app_icon_loads_small_and_large_sizes(qtbot):
    icon = load_app_icon()

    assert not icon.isNull()
    available_sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
    assert (16, 16) in available_sizes
    assert (32, 32) in available_sizes
    assert (48, 48) in available_sizes
    assert (256, 256) in available_sizes


def test_windows_native_window_icon_sets_small_and_big_icons(monkeypatch):
    window_calls = []
    class_calls = []

    class FakeWindow:
        def winId(self):
            return 1234

    def fake_load_hicon(resource_name: str, size: int) -> int:
        return {
            WINDOW_ICON_RESOURCE: 11,
            TASKBAR_ICON_RESOURCE: 22,
        }[resource_name]

    monkeypatch.setattr(app_icon.sys, 'platform', 'win32')
    monkeypatch.setattr(app_icon, '_WINDOWS_ICON_HANDLES', [])
    monkeypatch.setattr(app_icon, '_load_windows_hicon', fake_load_hicon)
    monkeypatch.setattr(
        app_icon,
        '_set_windows_window_icon',
        lambda hwnd, icon_type, hicon: window_calls.append((hwnd, icon_type, hicon)),
    )
    monkeypatch.setattr(
        app_icon,
        '_set_windows_class_icon',
        lambda hwnd, index, hicon: class_calls.append((hwnd, index, hicon)),
    )

    app_icon.apply_windows_native_window_icon(FakeWindow())

    assert window_calls == [
        (1234, app_icon._ICON_SMALL, 11),
        (1234, app_icon._ICON_BIG, 22),
    ]
    assert class_calls == [
        (1234, app_icon._GCLP_HICONSM, 11),
        (1234, app_icon._GCLP_HICON, 22),
    ]
    assert app_icon._WINDOWS_ICON_HANDLES == [11, 22]


def test_resizable_label_can_ignore_pixmap_size_hint(qtbot):
    label = ResizableLabel(ignore_pixmap_size_hint=True)
    qtbot.addWidget(label)
    label.setPixmap(QPixmap(1200, 800))

    assert label.sizeHint().width() == 1
    assert label.sizeHint().height() == 1
    assert label.minimumSizeHint().width() == 1
    assert label.minimumSizeHint().height() == 1


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
        [2, 300.0, 450.0],
        [0, 50.0, 100.0],
        [np.nan, 999.0, 999.0],
        [2, 350.0, 500.0],
        [1, 100.0, 150.0],
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


def test_update_plot_figure_size_emits_device_pixel_ratio(ui_manager):
    class FakeFigureSizeSignal:
        def __init__(self):
            self.calls = []

        def emit(self, *args) -> None:
            self.calls.append(args)

    signal = FakeFigureSizeSignal()
    ui_manager.plot_worker = SimpleNamespace(figure_size_signal=signal)
    ui_manager.plots_widget = SimpleNamespace(devicePixelRatioF=lambda: 1.5)

    try:
        ui_manager.update_plot_figure_size(320, 180)

        assert signal.calls == [(320, 180, 1.5)]
    finally:
        ui_manager.plots_widget = None


def test_set_plot_image_preserves_device_pixel_ratio(qtbot, ui_manager):
    label = QLabel()
    qtbot.addWidget(label)
    ui_manager.plots_widget = label
    image = QImage(20, 10, QImage.Format.Format_RGBA8888)
    image.setDevicePixelRatio(2.0)

    set_pixmap_calls = []
    label.setPixmap = lambda p: set_pixmap_calls.append(p)

    try:
        ui_manager._set_plot_image(image)

        assert len(set_pixmap_calls) == 1
        assert set_pixmap_calls[0].devicePixelRatio() == pytest.approx(2.0)
    finally:
        ui_manager.plots_widget = None


def test_set_plot_image_resets_live_plot_progress(qtbot, monkeypatch, ui_manager):
    from magscope.ui import ui as ui_module

    label = QLabel()
    progress_indicator = LivePlotProgressIndicator()
    qtbot.addWidget(label)
    qtbot.addWidget(progress_indicator)
    progress_indicator.setRange(0, 1000)
    progress_indicator.setValue(750)
    ui_manager.plots_widget = label
    ui_manager.plots_progress_indicator = progress_indicator
    ui_manager._plot_progress_last_image_time = 100.0
    ui_manager._plot_progress_started_at = 101.0
    ui_manager._plot_progress_interval_seconds = 1.0
    monkeypatch.setattr(ui_module, 'time', lambda: 102.5)
    image = QImage(20, 10, QImage.Format.Format_RGBA8888)

    try:
        ui_manager._set_plot_image(image)

        assert progress_indicator.value() == 0
        assert ui_manager._plot_progress_last_image_time == pytest.approx(102.5)
        assert ui_manager._plot_progress_started_at == pytest.approx(102.5)
        assert ui_manager._plot_progress_interval_seconds == pytest.approx(2.5)
        assert ui_manager._plot_progress_timer is not None
    finally:
        ui_manager._stop_timer(ui_manager._plot_progress_timer)
        ui_manager._plot_progress_timer = None
        ui_manager.plots_widget = None
        ui_manager.plots_progress_indicator = None


def test_live_plot_progress_timer_fills_and_holds(qtbot, monkeypatch, ui_manager):
    from magscope.ui import ui as ui_module

    progress_indicator = LivePlotProgressIndicator()
    qtbot.addWidget(progress_indicator)
    progress_indicator.setRange(0, 1000)
    ui_manager.plots_progress_indicator = progress_indicator
    ui_manager._plot_progress_started_at = 10.0
    ui_manager._plot_progress_interval_seconds = 2.0
    times = iter([11.0, 13.0, 14.0])
    monkeypatch.setattr(ui_module, 'time', lambda: next(times))

    try:
        ui_manager._update_plot_progress()
        assert progress_indicator.value() == 500

        ui_manager._update_plot_progress()
        assert progress_indicator.value() == progress_indicator.maximum()

        ui_manager._update_plot_progress()
        assert progress_indicator.value() == progress_indicator.maximum()
        assert ui_manager._plot_progress_started_at == pytest.approx(10.0)
    finally:
        ui_manager.plots_progress_indicator = None


def test_live_plot_progress_indicator_refreshes_on_updated_accent_color(qtbot, ui_manager):
    class FakeProgressIndicator:
        def __init__(self):
            self.update_calls = 0

        def update(self):
            self.update_calls += 1

    progress_indicator = FakeProgressIndicator()
    ui_manager.plots_progress_indicator = progress_indicator

    try:
        ui_manager._apply_accent_color('#336699')

        assert progress_indicator.update_calls == 1
    finally:
        ui_manager.plots_progress_indicator = None


def test_live_plot_progress_indicator_setting_hides_indicator_and_stops_timer(qtbot, ui_manager):
    progress_indicator = LivePlotProgressIndicator()
    qtbot.addWidget(progress_indicator)
    ui_manager.settings = MagScopeSettings()
    ui_manager.plots_progress_indicator = progress_indicator
    ui_manager._ensure_plot_progress_timer()
    assert ui_manager._plot_progress_timer is not None

    ui_manager.settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] = False
    ui_manager._apply_live_plot_progress_indicator_enabled()

    assert progress_indicator.isHidden()
    assert ui_manager._plot_progress_timer is None

    ui_manager._reset_plot_progress()
    assert ui_manager._plot_progress_timer is None

    ui_manager.settings[GUI_LIVE_PLOT_PROGRESS_BAR_SETTING] = True
    ui_manager._apply_live_plot_progress_indicator_enabled()

    assert not progress_indicator.isHidden()
    assert ui_manager._plot_progress_timer is not None

    ui_manager._stop_timer(ui_manager._plot_progress_timer)
    ui_manager._plot_progress_timer = None
    ui_manager.plots_progress_indicator = None


def test_current_accent_color_falls_back_for_missing_settings(ui_manager):
    ui_manager.settings = None
    assert ui_manager._current_accent_color() == ACCENT_COLOR

    ui_manager.settings = {}
    assert ui_manager._current_accent_color() == ACCENT_COLOR


def test_apply_accent_color_updates_app_palette_and_main_windows(qtbot, ui_manager):
    app = QApplication.instance()
    window = QMainWindow()
    qtbot.addWidget(window)
    style_calls = []
    ui_manager.qt_app = app
    ui_manager.windows = [window, QWidget()]
    ui_manager._apply_viewer_dock_separator_style = lambda target: style_calls.append(target)

    ui_manager._apply_accent_color('#336699')

    assert app.palette().color(ui_module.QPalette.ColorRole.Highlight).name() == '#336699'
    assert style_calls == [window]


def test_plot_image_and_progress_helpers_handle_empty_state(ui_manager, monkeypatch):
    ui_manager.plot_worker = None
    ui_manager.plots_widget = None
    ui_manager.plots_progress_indicator = None
    ui_manager._plot_progress_started_at = None
    monkeypatch.setattr(ui_module, 'time', lambda: 12.0)

    ui_manager.update_plot_figure_size(10, 20)
    ui_manager._set_plot_image(QImage(1, 1, QImage.Format.Format_RGBA8888))
    ui_manager._update_plot_progress()

    progress_indicator = LivePlotProgressIndicator()
    ui_manager.plots_progress_indicator = progress_indicator
    try:
        ui_manager._update_plot_progress()
        assert ui_manager._plot_progress_started_at == pytest.approx(12.0)
        assert progress_indicator.value() == 0
    finally:
        ui_manager.plots_progress_indicator = None


def test_disconnect_stop_and_close_helpers_ignore_deleted_qobjects(ui_manager):
    class FailingSignal:
        def __init__(self, exc):
            self.exc = exc

        def disconnect(self, _callback):
            raise self.exc

    class FailingTimer:
        def __init__(self):
            self.stop_calls = 0
            self.delete_calls = 0

        def stop(self):
            self.stop_calls += 1
            raise RuntimeError('deleted')

        def deleteLater(self):
            self.delete_calls += 1
            raise RuntimeError('deleted')

    class FailingWidget:
        def __init__(self):
            self.close_calls = 0
            self.delete_calls = 0

        def close(self):
            self.close_calls += 1
            raise RuntimeError('deleted')

        def deleteLater(self):
            self.delete_calls += 1
            raise RuntimeError('deleted')

    UIManager._disconnect_signal(FailingSignal(RuntimeError('deleted')), lambda: None)
    UIManager._disconnect_signal(FailingSignal(TypeError('missing')), lambda: None)

    timer = FailingTimer()
    UIManager._stop_timer(timer)
    assert timer.stop_calls == 1
    assert timer.delete_calls == 1

    widget = FailingWidget()
    UIManager._close_widget(widget)
    assert widget.close_calls == 1
    assert widget.delete_calls == 1


def test_shutdown_plot_worker_disconnects_and_disposes_resources(ui_manager):
    class FakeDisconnectSignal:
        def __init__(self):
            self.disconnected = []

        def disconnect(self, callback):
            self.disconnected.append(callback)

    class FakePlotWorker:
        def __init__(self):
            self.image_signal = FakeDisconnectSignal()
            self.stopped = False
            self.disposed = False

        def _stop(self):
            self.stopped = True

        def dispose(self):
            self.disposed = True

    class FakePlotWidget:
        def __init__(self):
            self.resized = FakeDisconnectSignal()

    class FakeThread:
        def __init__(self):
            self.calls = []

        def quit(self):
            self.calls.append('quit')

        def wait(self):
            self.calls.append('wait')

        def deleteLater(self):
            self.calls.append('delete')

    worker = FakePlotWorker()
    widget = FakePlotWidget()
    thread = FakeThread()
    ui_manager.plot_worker = worker
    ui_manager.plots_widget = widget
    ui_manager.plots_thread = thread

    ui_manager._shutdown_plot_worker()

    assert worker.image_signal.disconnected == [ui_manager._set_plot_image]
    assert widget.resized.disconnected == [ui_manager.update_plot_figure_size]
    assert worker.stopped is True
    assert worker.disposed is True
    assert thread.calls == ['quit', 'wait', 'delete']
    assert ui_manager.plot_worker is None
    assert ui_manager.plots_thread is None


def test_shutdown_plot_worker_handles_missing_worker(ui_manager):
    ui_manager.plot_worker = None
    ui_manager._shutdown_plot_worker()


@pytest.mark.skipif(
    sys.platform == 'win32' and sys.version_info >= (3, 13),
    reason='FigureCanvasQTAgg teardown segfaults on Windows Python 3.13 in CI',
)
def test_plot_worker_uses_constrained_zero_gap_layout(qtbot):
    class DummyTimeSeriesPlot(TimeSeriesPlotBase):
        def setup(self):
            self.axes.set_ylabel(self.ylabel)

        def update(self):
            pass

    worker = PlotWorker()
    worker.plots = [
        DummyTimeSeriesPlot('TracksBuffer', 'X (nm)'),
        DummyTimeSeriesPlot('TracksBuffer', 'Y (nm)'),
        DummyTimeSeriesPlot('TracksBuffer', 'Z (nm)'),
    ]
    worker.set_locks({})
    worker.setup()
    qtbot.addWidget(worker.canvas)

    try:
        assert worker.figure.get_constrained_layout()
        assert len(worker.axes) == 3
        assert worker.axes[0].get_shared_x_axes().joined(worker.axes[0], worker.axes[1])
        assert worker.axes[0].get_shared_x_axes().joined(worker.axes[0], worker.axes[2])
        assert not any(line.get_visible() for line in worker.axes[0].xaxis.get_ticklines())
        assert not any(line.get_visible() for line in worker.axes[1].xaxis.get_ticklines())

        worker._update_figure_size(320, 180, 2.0)
        worker._recreate_figure_if_needed()
        assert worker.figure.get_dpi() == pytest.approx(200.0)
        assert worker.canvas.get_width_height() == (640, 360)

        layout_params = worker.figure.get_layout_engine().get()
        assert layout_params['w_pad'] == pytest.approx(0.02)
        assert layout_params['h_pad'] == pytest.approx(0.0)
        assert layout_params['hspace'] == pytest.approx(0.0)
        assert layout_params['wspace'] == pytest.approx(0.0)
    finally:
        worker.dispose()


def test_tracks_time_series_plot_sorts_unsorted_timestamps_before_plotting():
    plot = TracksTimeSeriesPlot('X')
    plot.axes = FakeAxes()
    plot.line = FakeLine()
    plot.buffer = FakeTracksBuffer(
        np.asarray(
            [
                [3.0, 30.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [1.0, 10.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [2.0, 20.0, 0.0, 0.0, 7.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
    )
    plot.parent = SimpleNamespace(
        selected_bead=7,
        reference_bead=None,
        limits={},
        time_mode='absolute',
        relative_window_seconds=300,
        _tracks_snapshot=None,
    )

    plot.update()

    assert plot.line.xdata == [
        datetime.fromtimestamp(1.0),
        datetime.fromtimestamp(2.0),
        datetime.fromtimestamp(3.0),
    ]
    np.testing.assert_allclose(plot.line.ydata, np.asarray([10.0, 20.0, 30.0]))


def test_tracks_time_series_plot_skips_reference_plotting_on_alignment_error(monkeypatch):
    plot = TracksTimeSeriesPlot('X')
    plot.axes = FakeAxes()
    plot.line = FakeLine()
    plot.buffer = FakeTracksBuffer(
        np.asarray(
            [
                [1.0, 10.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [1.0, 11.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [1.0, 4.0, 0.0, 0.0, 8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
    )
    plot.parent = SimpleNamespace(
        selected_bead=7,
        reference_bead=8,
        limits={},
        time_mode='absolute',
        relative_window_seconds=300,
        _tracks_snapshot=None,
    )

    def bad_intersect1d(*args, **kwargs):
        return np.asarray([1.0]), np.asarray([10]), np.asarray([0])

    monkeypatch.setattr(np, 'intersect1d', bad_intersect1d)

    with pytest.warns(RuntimeWarning, match='Duplicate timestamps detected'):
        with pytest.warns(RuntimeWarning, match='Skipping referenced bead track plot update'):
            plot.update()

    assert plot.line.xdata == []
    assert plot.line.ydata == []


# ---------------------------------------------------------------------------
# PlotWorker pure setters
# ---------------------------------------------------------------------------

def test_plot_worker_add_plot_appends_to_list():
    class DummyPlot(TimeSeriesPlotBase):
        def update(self):
            pass

    worker = PlotWorker()
    existing_count = len(worker.plots)
    plot = DummyPlot("Buffer", "Axis")
    worker.add_plot(plot)
    assert len(worker.plots) == existing_count + 1
    assert worker.plots[-1] is plot


def test_plot_worker_set_locks():
    worker = PlotWorker()
    locks = {"CameraManager": object()}
    worker.set_locks(locks)
    assert worker.locks is locks


def test_plot_worker_set_limits():
    worker = PlotWorker()
    limits = {"X (nm)": (0, 1000), "Time": (0, 60)}
    worker._set_limits(limits)
    assert worker.limits == limits


def test_plot_worker_set_selected_bead():
    worker = PlotWorker()
    worker._set_selected_bead(5)
    assert worker.selected_bead == 5


def test_plot_worker_set_reference_bead():
    worker = PlotWorker()
    worker._set_reference_bead(3)
    assert worker.reference_bead == 3


def test_plot_worker_set_reference_bead_none():
    worker = PlotWorker()
    worker._set_reference_bead(None)
    assert worker.reference_bead is None


def test_plot_worker_stop():
    worker = PlotWorker()
    worker._is_running = True
    worker._stop()
    assert worker._is_running is False


def test_plot_worker_set_time_mode_absolute():
    worker = PlotWorker()
    worker.axes = [FakeAxes()]
    worker._set_time_mode("absolute")
    assert worker.time_mode == "absolute"


def test_plot_worker_set_time_mode_relative():
    worker = PlotWorker()
    worker.axes = [FakeAxes()]
    worker._set_time_mode("relative")
    assert worker.time_mode == "relative"


def test_plot_worker_set_relative_window():
    worker = PlotWorker()
    worker._set_relative_window(60.0)
    assert worker.relative_window_seconds == 60.0


def test_plot_worker_set_relative_window_none():
    worker = PlotWorker()
    worker._set_relative_window(None)
    assert worker.relative_window_seconds is None


def test_plot_worker_apply_time_axis_format_absolute():
    worker = PlotWorker()
    axes = FakeAxes()
    worker.axes = [axes]
    worker.time_mode = "absolute"
    worker._set_time_mode("absolute")
    assert worker.time_mode == "absolute"


def test_plot_worker_apply_time_axis_format_relative():
    worker = PlotWorker()
    axes = FakeAxes()
    worker.axes = [axes]
    worker.time_mode = "relative"
    worker._set_relative_window(30.0)
    worker._set_time_mode("relative")
    assert worker.time_mode == "relative"


def test_time_series_plot_base_init():
    class ConcretePlot(TimeSeriesPlotBase):
        def update(self):
            pass

    plot = ConcretePlot("TestBuffer", "Value (px)")
    assert plot.buffer_name == "TestBuffer"
    assert plot.ylabel == "Value (px)"


def test_time_series_plot_base_set_parent():
    class ConcretePlot(TimeSeriesPlotBase):
        def update(self):
            pass

    plot = ConcretePlot("TestBuffer", "Y")
    parent = SimpleNamespace()
    plot.set_parent(parent)
    assert plot.parent is parent


def test_time_series_plot_base_set_axes():
    class ConcretePlot(TimeSeriesPlotBase):
        def update(self):
            pass

    plot = ConcretePlot("TestBuffer", "Y")
    axes = FakeAxes()
    plot.set_axes(axes)
    assert plot.axes is axes


# ---------------------------------------------------------------------------
# TracksTimeSeriesPlot.update() untested branches
# ---------------------------------------------------------------------------

def test_tracks_time_series_plot_update_with_reference_happy_path():
    plot = TracksTimeSeriesPlot("X")
    plot.axes = FakeAxes()
    plot.line = FakeLine()
    plot.buffer = FakeTracksBuffer(
        np.asarray(
            [
                [1.0, 50.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [2.0, 60.0, 0.0, 0.0, 7.0, 0.0, 0.0],
                [1.0, 10.0, 0.0, 0.0, 8.0, 0.0, 0.0],
                [2.0, 15.0, 0.0, 0.0, 8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
    )
    plot.parent = SimpleNamespace(
        selected_bead=7,
        reference_bead=8,
        limits={},
        time_mode="absolute",
        relative_window_seconds=300,
        _tracks_snapshot=None,
    )

    plot.update()

    np.testing.assert_allclose(plot.line.ydata, np.asarray([40.0, 45.0]))


def test_tracks_time_series_plot_update_relative_mode():
    plot = TracksTimeSeriesPlot("X")
    plot.axes = FakeAxes()
    plot.line = FakeLine()
    plot.buffer = FakeTracksBuffer(
        np.asarray(
            [
                [4.0, 10.0, 0.0, 0.0, 9.0, 0.0, 0.0],
                [10.0, 20.0, 0.0, 0.0, 9.0, 0.0, 0.0],
                [12.0, 30.0, 0.0, 0.0, 9.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
    )
    plot.parent = SimpleNamespace(
        selected_bead=9,
        reference_bead=None,
        limits={},
        time_mode="relative",
        relative_window_seconds=10.0,
        _tracks_snapshot=None,
    )

    plot.update()

    assert isinstance(plot.line.xdata, np.ndarray)
    assert plot.line.xdata.min() >= 2.0


def test_tracks_time_series_plot_update_z_axis_negation():
    plot = TracksTimeSeriesPlot("Z")
    plot.axes = FakeAxes()
    plot.line = FakeLine()
    plot.buffer = FakeTracksBuffer(
        np.asarray(
            [
                [1.0, 0.0, 0.0, 30.0, 7.0, 0.0, 0.0],
                [1.0, 0.0, 0.0, 10.0, 8.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
    )
    plot.parent = SimpleNamespace(
        selected_bead=7,
        reference_bead=8,
        limits={},
        time_mode="absolute",
        relative_window_seconds=300,
        _tracks_snapshot=None,
    )

    plot.update()

    # v = v_sel - v_ref = 30 - 10 = 20; Z negation: v *= -1 => -20
    np.testing.assert_allclose(plot.line.ydata, np.asarray([-20.0]))


def test_tracks_time_series_plot_update_nan_and_inf_removal():
    plot = TracksTimeSeriesPlot("X")
    plot.axes = FakeAxes()
    plot.line = FakeLine()
    plot.buffer = FakeTracksBuffer(
        np.asarray(
            [
                [1.0, 10.0, 0.0, 0.0, 9.0, 0.0, 0.0],
                [np.nan, 20.0, 0.0, 0.0, 9.0, 0.0, 0.0],
                [np.inf, 30.0, 0.0, 0.0, 9.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        )
    )
    plot.parent = SimpleNamespace(
        selected_bead=9,
        reference_bead=None,
        limits={},
        time_mode="absolute",
        relative_window_seconds=300,
        _tracks_snapshot=None,
    )

    plot.update()

    assert len(plot.line.ydata) == 1


def test_tracks_time_series_plot_update_uses_snapshot_when_available():
    plot = TracksTimeSeriesPlot("X")
    plot.axes = FakeAxes()
    plot.line = FakeLine()
    snapshot = np.asarray(
        [[1.0, 100.0, 0.0, 0.0, 9.0, 0.0, 0.0]],
        dtype=np.float64,
    )
    plot.parent = SimpleNamespace(
        selected_bead=9,
        reference_bead=None,
        limits={},
        time_mode="absolute",
        relative_window_seconds=300,
        _tracks_snapshot=snapshot,
    )
    # buffer is None — should use snapshot, not crash
    plot.buffer = None

    plot.update()

    np.testing.assert_allclose(plot.line.ydata, np.asarray([100.0]))


def test_tracks_time_series_plot_update_y_axis(qtbot):
    plot = TracksTimeSeriesPlot("Y")
    plot.axes = FakeAxes()
    plot.line = FakeLine()
    plot.buffer = FakeTracksBuffer(
        np.asarray(
            [[1.0, 0.0, 50.0, 0.0, 9.0, 0.0, 0.0]],
            dtype=np.float64,
        )
    )
    plot.parent = SimpleNamespace(
        selected_bead=9,
        reference_bead=None,
        limits={},
        time_mode="absolute",
        relative_window_seconds=300,
        _tracks_snapshot=None,
    )

    plot.update()

    assert plot.axis_index == 2
    np.testing.assert_allclose(plot.line.ydata, np.asarray([50.0]))


def test_allan_deviation_panel_refresh_uses_selected_bead_without_reference(qtbot, monkeypatch, fake_allan_canvas):
    calls = []

    def fake_avar(data, rate=1.0, taus='octave', overlapping=True, edf='approx'):
        calls.append((np.asarray(data, dtype=np.float64), rate, taus, overlapping, edf))
        return np.asarray([1.0, 2.0]), np.asarray([10.0, 10.0]), np.asarray([4.0, 9.0])

    tweezepy_module = ModuleType('tweezepy')
    allanvar_module = ModuleType('tweezepy.allanvar')
    allanvar_module.avar = fake_avar
    tweezepy_module.allanvar = allanvar_module
    monkeypatch.setitem(sys.modules, 'tweezepy', tweezepy_module)
    monkeypatch.setitem(sys.modules, 'tweezepy.allanvar', allanvar_module)
    monkeypatch.setattr('magscope.ui.controls.has_tweezepy_support', lambda: True)

    manager = SimpleNamespace(
        tracks_buffer=FakeTracksBuffer(
            np.asarray(
                [
                    [0.0, 10.0, 20.0, 30.0, 7.0, 0.0, 0.0],
                    [1.0, 11.0, 21.0, 31.0, 7.0, 0.0, 0.0],
                    [2.0, 12.0, 22.0, 32.0, 7.0, 0.0, 0.0],
                    [3.0, 13.0, 23.0, 33.0, 7.0, 0.0, 0.0],
                    [3.0, 99.0, 99.0, 99.0, 9.0, 0.0, 0.0],
                ],
                dtype=np.float64,
            )
        ),
        selected_bead=7,
        reference_bead=None,
    )

    panel = AllanDeviationPanel(manager)
    qtbot.addWidget(panel)
    panel.history_window.setText('10')
    panel.taus_mode.setCurrentText('Octave')

    panel.refresh_plot()

    assert len(calls) == 3
    np.testing.assert_allclose(calls[0][0], np.asarray([10.0, 11.0, 12.0, 13.0]))
    np.testing.assert_allclose(calls[1][0], np.asarray([20.0, 21.0, 22.0, 23.0]))
    np.testing.assert_allclose(calls[2][0], np.asarray([30.0, 31.0, 32.0, 33.0]))
    assert all(call[1] == 1.0 for call in calls)
    assert all(call[2] == 'octave' for call in calls)
    assert len(panel.axes.lines) == 3
    assert panel.status_label.text() == 'Refreshed Allan deviation for X, Y, Z using selected bead 7.'


@pytest.mark.skipif(
    sys.platform == 'win32' and sys.version_info >= (3, 13),
    reason='FigureCanvasQTAgg teardown segfaults on Windows Python 3.13 in CI',
)
def test_allan_deviation_panel_uses_responsive_plot_canvas(qtbot):
    manager = SimpleNamespace(
        tracks_buffer=FakeTracksBuffer(np.asarray([])),
        selected_bead=7,
        reference_bead=None,
    )

    panel = AllanDeviationPanel(manager)
    qtbot.addWidget(panel)
    panel.resize(320, 700)
    panel.show()
    qtbot.wait(50)

    assert panel.figure.get_constrained_layout()
    assert 210 <= panel.canvas.minimumHeight() <= 235
    assert panel.canvas.minimumHeight() == panel.canvas.maximumHeight()
    assert panel.canvas.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert panel.canvas.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed


def test_allan_deviation_panel_refresh_uses_selected_minus_reference(qtbot, monkeypatch, fake_allan_canvas):
    calls = []

    def fake_avar(data, rate=1.0, taus='octave', overlapping=True, edf='approx'):
        calls.append(np.asarray(data, dtype=np.float64))
        return np.asarray([1.0, 2.0]), np.asarray([10.0, 10.0]), np.asarray([4.0, 9.0])

    tweezepy_module = ModuleType('tweezepy')
    allanvar_module = ModuleType('tweezepy.allanvar')
    allanvar_module.avar = fake_avar
    tweezepy_module.allanvar = allanvar_module
    monkeypatch.setitem(sys.modules, 'tweezepy', tweezepy_module)
    monkeypatch.setitem(sys.modules, 'tweezepy.allanvar', allanvar_module)
    monkeypatch.setattr('magscope.ui.controls.has_tweezepy_support', lambda: True)

    manager = SimpleNamespace(
        tracks_buffer=FakeTracksBuffer(
            np.asarray(
                [
                    [0.0, 10.0, 20.0, 30.0, 7.0, 0.0, 0.0],
                    [0.0, 1.0, 2.0, 3.0, 9.0, 0.0, 0.0],
                    [1.0, 11.0, 21.0, 31.0, 7.0, 0.0, 0.0],
                    [1.0, 2.0, 3.0, 4.0, 9.0, 0.0, 0.0],
                    [2.0, 12.0, 22.0, 32.0, 7.0, 0.0, 0.0],
                    [2.0, 3.0, 4.0, 5.0, 9.0, 0.0, 0.0],
                    [3.0, 13.0, 23.0, 33.0, 7.0, 0.0, 0.0],
                    [3.0, 4.0, 5.0, 6.0, 9.0, 0.0, 0.0],
                ],
                dtype=np.float64,
            )
        ),
        selected_bead=7,
        reference_bead=9,
    )

    panel = AllanDeviationPanel(manager)
    qtbot.addWidget(panel)
    panel.history_window.setText('10')

    panel.refresh_plot()

    assert len(calls) == 3
    np.testing.assert_allclose(calls[0], np.asarray([9.0, 9.0, 9.0, 9.0]))
    np.testing.assert_allclose(calls[1], np.asarray([18.0, 18.0, 18.0, 18.0]))
    np.testing.assert_allclose(calls[2], np.asarray([-27.0, -27.0, -27.0, -27.0]))
    assert panel.status_label.text() == (
        'Refreshed Allan deviation for X, Y, Z using selected bead 7 minus reference bead 9.'
    )


def test_allan_deviation_panel_filters_to_recent_history_window(qtbot, monkeypatch, fake_allan_canvas):
    calls = []

    def fake_avar(data, rate=1.0, taus='octave', overlapping=True, edf='approx'):
        calls.append(np.asarray(data, dtype=np.float64))
        return np.asarray([1.0, 2.0]), np.asarray([10.0, 10.0]), np.asarray([4.0, 9.0])

    tweezepy_module = ModuleType('tweezepy')
    allanvar_module = ModuleType('tweezepy.allanvar')
    allanvar_module.avar = fake_avar
    tweezepy_module.allanvar = allanvar_module
    monkeypatch.setitem(sys.modules, 'tweezepy', tweezepy_module)
    monkeypatch.setitem(sys.modules, 'tweezepy.allanvar', allanvar_module)
    monkeypatch.setattr('magscope.ui.controls.has_tweezepy_support', lambda: True)

    manager = SimpleNamespace(
        tracks_buffer=FakeTracksBuffer(
            np.asarray(
                [
                    [0.0, 10.0, 20.0, 30.0, 7.0, 0.0, 0.0],
                    [1.0, 11.0, 21.0, 31.0, 7.0, 0.0, 0.0],
                    [2.0, 12.0, 22.0, 32.0, 7.0, 0.0, 0.0],
                    [3.0, 13.0, 23.0, 33.0, 7.0, 0.0, 0.0],
                    [4.0, 14.0, 24.0, 34.0, 7.0, 0.0, 0.0],
                ],
                dtype=np.float64,
            )
        ),
        selected_bead=7,
        reference_bead=None,
    )

    panel = AllanDeviationPanel(manager)
    qtbot.addWidget(panel)
    panel.history_window.setText('3')

    panel.refresh_plot()

    np.testing.assert_allclose(calls[0], np.asarray([11.0, 12.0, 13.0, 14.0]))


def test_allan_deviation_panel_plots_xy_when_z_has_insufficient_samples(qtbot, monkeypatch, fake_allan_canvas):
    calls = []

    def fake_avar(data, rate=1.0, taus='octave', overlapping=True, edf='approx'):
        calls.append(np.asarray(data, dtype=np.float64))
        return np.asarray([1.0, 2.0]), np.asarray([10.0, 10.0]), np.asarray([4.0, 9.0])

    tweezepy_module = ModuleType('tweezepy')
    allanvar_module = ModuleType('tweezepy.allanvar')
    allanvar_module.avar = fake_avar
    tweezepy_module.allanvar = allanvar_module
    monkeypatch.setitem(sys.modules, 'tweezepy', tweezepy_module)
    monkeypatch.setitem(sys.modules, 'tweezepy.allanvar', allanvar_module)
    monkeypatch.setattr('magscope.ui.controls.has_tweezepy_support', lambda: True)

    manager = SimpleNamespace(
        tracks_buffer=FakeTracksBuffer(
            np.asarray(
                [
                    [0.0, 10.0, 20.0, np.nan, 7.0, 0.0, 0.0],
                    [1.0, 11.0, 21.0, np.nan, 7.0, 0.0, 0.0],
                    [2.0, 12.0, 22.0, 30.0, 7.0, 0.0, 0.0],
                    [3.0, 13.0, 23.0, 31.0, 7.0, 0.0, 0.0],
                    [4.0, 14.0, 24.0, 32.0, 7.0, 0.0, 0.0],
                ],
                dtype=np.float64,
            )
        ),
        selected_bead=7,
        reference_bead=None,
    )

    panel = AllanDeviationPanel(manager)
    qtbot.addWidget(panel)
    panel.history_window.setText('10')

    panel.refresh_plot()

    assert len(calls) == 2
    assert len(panel.axes.lines) == 2
    assert panel.status_label.text() == (
        'Refreshed Allan deviation for X, Y using selected bead 7. '
        'Skipped Z: insufficient aligned track samples.'
    )


def test_allan_deviation_panel_reports_when_all_axes_are_skipped(qtbot, monkeypatch, fake_allan_canvas):
    def fake_avar(data, rate=1.0, taus='octave', overlapping=True, edf='approx'):
        raise AssertionError('avar should not be called when all axes are invalid')

    tweezepy_module = ModuleType('tweezepy')
    allanvar_module = ModuleType('tweezepy.allanvar')
    allanvar_module.avar = fake_avar
    tweezepy_module.allanvar = allanvar_module
    monkeypatch.setitem(sys.modules, 'tweezepy', tweezepy_module)
    monkeypatch.setitem(sys.modules, 'tweezepy.allanvar', allanvar_module)
    monkeypatch.setattr('magscope.ui.controls.has_tweezepy_support', lambda: True)

    manager = SimpleNamespace(
        tracks_buffer=FakeTracksBuffer(
            np.asarray(
                [
                    [0.0, np.nan, np.nan, np.nan, 7.0, 0.0, 0.0],
                    [1.0, 11.0, np.nan, np.nan, 7.0, 0.0, 0.0],
                    [2.0, np.nan, 22.0, np.nan, 7.0, 0.0, 0.0],
                ],
                dtype=np.float64,
            )
        ),
        selected_bead=7,
        reference_bead=None,
    )

    panel = AllanDeviationPanel(manager)
    qtbot.addWidget(panel)
    panel.history_window.setText('10')

    panel.refresh_plot()

    assert len(panel.axes.lines) == 0
    assert panel.status_label.text() == (
        'Could not plot Allan deviation. '
        'Skipped X: insufficient aligned track samples. '
        'Skipped Y: insufficient aligned track samples. '
        'Skipped Z: insufficient aligned track samples.'
    )


def test_allan_deviation_panel_reports_tweezepy_import_failure(qtbot, monkeypatch, fake_allan_canvas):
    manager = SimpleNamespace(
        tracks_buffer=FakeTracksBuffer(np.asarray([])),
        selected_bead=7,
        reference_bead=None,
    )

    panel = AllanDeviationPanel(manager)
    qtbot.addWidget(panel)

    monkeypatch.setattr('magscope.ui.controls.has_tweezepy_support', lambda: True)
    monkeypatch.setattr(
        'magscope.ui.controls.load_tweezepy_avar',
        lambda: (None, "No module named 'numba'"),
    )

    panel.refresh_plot()

    assert panel.status_label.text() == "Tweezepy import failed: No module named 'numba'"


def test_allan_deviation_panel_loads_allanvar_without_tweezepy_init(qtbot, monkeypatch, tmp_path, fake_allan_canvas):
    package_dir = tmp_path / 'tweezepy'
    package_dir.mkdir()
    (package_dir / '__init__.py').write_text("raise ModuleNotFoundError('numba')\n", encoding='utf-8')
    (package_dir / 'allanvar.py').write_text(
        "import numpy as np\n"
        "\n"
        "def avar(data, rate=1.0, taus='octave', overlapping=True, edf='approx'):\n"
        "    values = np.asarray(data, dtype=np.float64)\n"
        "    return np.asarray([1.0, 2.0]), np.asarray([1.0, 1.0]), np.asarray([4.0, 9.0])\n",
        encoding='utf-8',
    )

    original_sys_path = sys.path[:]
    sys.path.insert(0, str(tmp_path))
    for module_name in ['tweezepy', 'tweezepy.allanvar', 'magscope_optional_tweezepy_allanvar']:
        sys.modules.pop(module_name, None)

    manager = SimpleNamespace(
        tracks_buffer=FakeTracksBuffer(
            np.asarray(
                [
                    [0.0, 10.0, 20.0, 30.0, 7.0, 0.0, 0.0],
                    [1.0, 11.0, 21.0, 31.0, 7.0, 0.0, 0.0],
                    [2.0, 12.0, 22.0, 32.0, 7.0, 0.0, 0.0],
                    [3.0, 13.0, 23.0, 33.0, 7.0, 0.0, 0.0],
                ],
                dtype=np.float64,
            )
        ),
        selected_bead=7,
        reference_bead=None,
    )

    panel = AllanDeviationPanel(manager)
    qtbot.addWidget(panel)
    panel.history_window.setText('10')

    try:
        panel.refresh_plot()
    finally:
        sys.path[:] = original_sys_path
        for module_name in ['tweezepy', 'tweezepy.allanvar', 'magscope_optional_tweezepy_allanvar']:
            sys.modules.pop(module_name, None)

    assert len(panel.axes.lines) == 3
    assert panel.status_label.text() == 'Refreshed Allan deviation for X, Y, Z using selected bead 7.'


def test_controls_only_register_allan_panel_when_tweezepy_available(qtbot, monkeypatch):
    from magscope.ui import ui as ui_module

    class StubPanel(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()

    for name in [
        'AcquisitionPanel',
        'CameraPanel',
        'HistogramPanel',
        'PlotSettingsPanel',
        'ProfilePanel',
        'ScriptPanel',
        'StatusPanel',
        'XYLockPanel',
        'ZLockPanel',
        'AllanDeviationPanel',
    ]:
        monkeypatch.setattr(ui_module, name, StubPanel)

    manager = SimpleNamespace(
        settings=MagScopeSettings(),
        plot_worker=SimpleNamespace(plots=[]),
        controls_to_add=[],
    )

    monkeypatch.setattr(ui_module, 'has_tweezepy_support', lambda: True)
    controls = Controls(manager)
    qtbot.addWidget(controls)
    assert 'AllanDeviationPanel' in controls.panels
    assert 'BeadSelectionPanel' not in controls.panels

    monkeypatch.setattr(ui_module, 'has_tweezepy_support', lambda: False)
    controls_without_tweezepy = Controls(manager)
    qtbot.addWidget(controls_without_tweezepy)
    assert 'AllanDeviationPanel' not in controls_without_tweezepy.panels
    assert 'BeadSelectionPanel' not in controls_without_tweezepy.panels


def test_controls_show_motors_placeholder_only_without_hardware(qtbot, monkeypatch):
    from magscope.ui import ui as ui_module

    class StubPanel(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()

    for name in [
        'AcquisitionPanel',
        'CameraPanel',
        'HistogramPanel',
        'PlotSettingsPanel',
        'ProfilePanel',
        'ScriptPanel',
        'StatusPanel',
        'XYLockPanel',
        'ZLockPanel',
        'AllanDeviationPanel',
    ]:
        monkeypatch.setattr(ui_module, name, StubPanel)

    monkeypatch.setattr(ui_module, 'has_tweezepy_support', lambda: False)
    manager = SimpleNamespace(
        settings=MagScopeSettings(),
        plot_worker=SimpleNamespace(plots=[]),
        controls_to_add=[],
        hardware_types={},
    )

    controls = Controls(manager)
    qtbot.addWidget(controls)

    assert 'MotorsPlaceholderPanel' in controls.panels
    assert isinstance(controls.panels['MotorsPlaceholderPanel'], ControlPanelBase)
    assert controls.panels['MotorsPlaceholderPanel'].groupbox is not None
    assert controls._panel_to_tab['MotorsPlaceholderPanel'] == 'Motors'

    manager_with_hardware = SimpleNamespace(
        settings=MagScopeSettings(),
        plot_worker=SimpleNamespace(plots=[]),
        controls_to_add=[],
        hardware_types={'focus': object},
    )

    controls_with_hardware = Controls(manager_with_hardware)
    qtbot.addWidget(controls_with_hardware)

    assert 'MotorsPlaceholderPanel' not in controls_with_hardware.panels


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


@pytest.mark.skipif(QT_GRAPHICS_WINDOWS_PY313, reason=QT_GRAPHICS_WINDOWS_PY313_REASON)
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


@pytest.mark.skipif(QT_GRAPHICS_WINDOWS_PY313, reason=QT_GRAPHICS_WINDOWS_PY313_REASON)
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


@pytest.mark.skipif(QT_GRAPHICS_WINDOWS_PY313, reason=QT_GRAPHICS_WINDOWS_PY313_REASON)
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


@pytest.mark.skipif(QT_GRAPHICS_WINDOWS_PY313, reason=QT_GRAPHICS_WINDOWS_PY313_REASON)
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


@pytest.mark.skipif(QT_GRAPHICS_WINDOWS_PY313, reason=QT_GRAPHICS_WINDOWS_PY313_REASON)
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


@pytest.mark.skipif(QT_GRAPHICS_WINDOWS_PY313, reason=QT_GRAPHICS_WINDOWS_PY313_REASON)
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


@pytest.mark.skipif(QT_GRAPHICS_WINDOWS_PY313, reason=QT_GRAPHICS_WINDOWS_PY313_REASON)
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


@pytest.mark.skipif(QT_GRAPHICS_WINDOWS_PY313, reason=QT_GRAPHICS_WINDOWS_PY313_REASON)
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
    qtbot.wait(1)
    ui_manager.video_viewer = FakeVideoViewer()
    ui_manager.settings['ROI'] = 40
    ui_manager._bead_roi_capacity = 10000
    ui_manager._add_bead_roi = lambda bead_id, roi: None
    ui_manager._update_bead_highlight = lambda bead_id: None
    ui_manager._update_bead_highlights = lambda **kwargs: None
    ui_manager._set_active_bead = lambda bead_id: setattr(ui_manager, '_active_bead_id', bead_id)
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
    assert ui_manager.bead_next_id_label.text == 'Next Bead ID: 2'
    assert ui_manager.selected_bead == 1
    assert ui_manager._active_bead_id == 1
    assert ui_manager.video_viewer.viewport_updates >= 3


@pytest.mark.skipif(QT_GRAPHICS_WINDOWS_PY313, reason=QT_GRAPHICS_WINDOWS_PY313_REASON)
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

    ui_manager.set_acquisition_mode(AcquisitionMode.VIDEO_FULL)
    assert ui_manager._acquisition_mode == AcquisitionMode.VIDEO_FULL
    assert panel.acquisition_mode_combobox.block_calls == [True, False]
    assert panel.acquisition_mode_combobox.current_text == AcquisitionMode.VIDEO_FULL


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


def test_scene_rect_helpers_cover_viewer_fallbacks(ui_manager):
    ui_manager.video_viewer = None
    assert ui_manager._current_scene_rect().isNull()

    ui_manager.video_viewer = SimpleNamespace(scene=SimpleNamespace(sceneRect=lambda: QRectF(1, 2, 30, 40)))
    assert ui_manager._current_scene_rect() == QRectF(1, 2, 30, 40)

    ui_manager.video_viewer = SimpleNamespace(
        image_scene_rect=lambda: QRectF(),
        scene=SimpleNamespace(sceneRect=lambda: QRectF(0, 0, 100, 100)),
        viewport=lambda: None,
    )
    assert ui_manager._current_visible_scene_rect() == QRectF(0, 0, 100, 100)

    ui_manager.video_viewer = SimpleNamespace(
        image_scene_rect=lambda: QRectF(0, 0, 100, 100),
        viewport=lambda: SimpleNamespace(rect=lambda: QRect()),
    )
    assert ui_manager._current_visible_scene_rect() == QRectF(0, 0, 100, 100)

    ui_manager.video_viewer = SimpleNamespace(
        image_scene_rect=lambda: QRectF(0, 0, 100, 100),
        viewport=lambda: SimpleNamespace(rect=lambda: QRect(0, 0, 10, 10)),
        mapToScene=lambda _rect: SimpleNamespace(boundingRect=lambda: QRectF(200, 200, 5, 5)),
    )
    assert ui_manager._current_visible_scene_rect() == QRectF(0, 0, 100, 100)


def test_snapshot_and_image_scale_helpers(ui_manager):
    assert ui_manager._snapshot_recent_image() is None
    assert ui_manager._current_image_display_scale() == 1

    image = np.asarray([[1, 2], [3, 4]], dtype=np.uint16)
    ui_manager.video_buffer = SimpleNamespace(
        peak_image=lambda: (7, image.tobytes()),
        image_shape=(2, 2),
        dtype=np.dtype(np.uint16),
    )
    ui_manager.camera_type = SimpleNamespace(bits=12)

    np.testing.assert_array_equal(ui_manager._snapshot_recent_image(), image)
    assert ui_manager._current_image_display_scale() == 16


def test_next_random_bead_roi_handles_missing_settings_and_tiny_view(ui_manager):
    rng = np.random.default_rng(1)
    ui_manager.settings = None
    assert ui_manager._next_random_bead_roi(rng, QRectF(0, 0, 100, 100)) is None

    ui_manager.settings = {'ROI': 50}
    ui_manager.video_viewer = FakeVideoViewer()
    assert ui_manager._next_random_bead_roi(rng, QRectF(0, 0, 10, 10)) is None


def test_hit_test_bead_prioritizes_active_selected_reference_then_highest_id(ui_manager):
    pos = SimpleNamespace(x=lambda: 15, y=lambda: 15)
    ui_manager._bead_rois = {
        1: (0, 30, 0, 30),
        2: (0, 30, 0, 30),
        3: (0, 30, 0, 30),
        4: (0, 30, 0, 30),
    }
    ui_manager.selected_bead = 2
    ui_manager.reference_bead = 3
    ui_manager._active_bead_id = 1
    assert ui_manager._hit_test_bead(pos) == 1

    ui_manager._active_bead_id = None
    assert ui_manager._hit_test_bead(pos) == 2

    ui_manager.selected_bead = -1
    assert ui_manager._hit_test_bead(pos) == 3

    ui_manager.reference_bead = None
    assert ui_manager._hit_test_bead(pos) == 4
    assert ui_manager._hit_test_bead(SimpleNamespace(x=lambda: 200, y=lambda: 200)) is None


def test_write_bead_rois_to_local_cache_handles_empty_and_sorted_values(ui_manager):
    ui_manager.bead_roi_buffer = None

    ui_manager._write_bead_rois_to_buffer({})
    assert ui_manager._bead_roi_ids.shape == (0,)
    assert ui_manager._bead_roi_values.shape == (0, 4)

    ui_manager._write_bead_rois_to_buffer({5: (50, 60, 70, 80), 2: (20, 30, 40, 50)})
    np.testing.assert_array_equal(ui_manager._bead_roi_ids, np.asarray([2, 5], dtype=np.uint32))
    np.testing.assert_array_equal(
        ui_manager._bead_roi_values,
        np.asarray([[20, 30, 40, 50], [50, 60, 70, 80]], dtype=np.uint32),
    )


def test_broadcast_and_live_profile_helpers_noop_without_resources(ui_manager):
    commands = []
    ui_manager.send_ipc = commands.append
    ui_manager._command_registry = None
    ui_manager._broadcast_bead_roi_update()
    assert commands == []

    ui_manager._command_registry = object()
    ui_manager._pipe = object()
    ui_manager._magscope_quitting = object()
    ui_manager._broadcast_bead_roi_update()
    assert commands == [UpdateBeadRoisCommand()]

    cleared = []
    ui_manager.live_profile_buffer = SimpleNamespace(clear=lambda: cleared.append(True))
    ui_manager._clear_live_profile_buffer()
    assert cleared == [True]


def test_add_random_beads_handles_non_positive_and_missing_visible_area(ui_manager, monkeypatch):
    errors = []
    monkeypatch.setattr(ui_manager, 'show_error', lambda text, details: errors.append((text, details)))
    ui_manager._current_visible_scene_rect = lambda: QRectF()

    ui_manager.add_random_beads(0, seed=1)
    assert errors == []

    ui_manager.add_random_beads(2, seed=1)
    assert errors[0][0] == 'No visible field of view'


def test_on_active_bead_move_completed_ignores_unknown_id(ui_manager):
    ui_manager._bead_rois = {1: (0, 10, 0, 10)}
    updates = []
    ui_manager._update_bead_roi = lambda bead_id, roi: updates.append((bead_id, roi))

    ui_manager.on_active_bead_move_completed(99, (1, 11, 1, 11))

    assert updates == []


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
    assert ui_manager.bead_next_id_label.text == 'Next Bead ID: 0'


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
    ui_manager._auto_bead_selection_action = FakeButton()

    ui_manager._update_auto_bead_selection_action_state()
    assert ui_manager._auto_bead_selection_action.enabled is True

    ui_manager._pending_bead_add_id = 4
    ui_manager._update_auto_bead_selection_action_state()
    assert ui_manager._auto_bead_selection_action.enabled is False

    ui_manager._pending_bead_add_id = None
    ui_manager._auto_bead_selection_dialog = object()
    ui_manager._update_auto_bead_selection_action_state()
    assert ui_manager._auto_bead_selection_action.enabled is False


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
    ui_manager._auto_bead_selection_action = FakeButton()
    ui_manager.settings['ROI'] = 20
    monkeypatch.setattr(ui_manager, '_snapshot_recent_image', lambda: np.zeros((32, 32), dtype=np.uint16))
    monkeypatch.setattr('magscope.ui.ui.AutoBeadSelectionDialog', lambda **kwargs: created.append(FakeDialog(**kwargs)) or created[-1])

    ui_manager.start_auto_bead_selection()

    assert len(created) == 1
    assert created[0].opened is True
    assert created[0].kwargs['roi_size'] == 20
    assert ui_manager._auto_bead_selection_action.enabled is False

    created[0].finished.emit(0)

    assert ui_manager._auto_bead_selection_action.enabled is True


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
    monkeypatch.setattr(ui_manager, '_add_bead_roi', lambda bead_id, roi: (_ for _ in ()).throw(RuntimeError('boom')))

    with pytest.raises(RuntimeError, match='boom'):
        ui_manager.add_bead(SimpleNamespace(x=lambda: 10, y=lambda: 20))

    assert ui_manager.bead_next_id_label.text == 'Next Bead ID: 0'


def test_start_zlut_generation_sends_command(ui_manager):
    commands = []
    ui_manager.send_ipc = commands.append
    ui_manager.windows = [QMainWindow()]

    ui_manager.start_zlut_generation(start_nm=1.0, step_nm=2.0, stop_nm=3.0, profiles_per_bead=4)

    assert commands == [
        StartZLUTGenerationCommand(start_nm=1.0, step_nm=2.0, stop_nm=3.0, profiles_per_bead=4)
    ]
    assert ui_manager._zlut_generation_dialog is not None
    assert ui_manager._zlut_generation_dialog.isVisible()
    assert not ui_manager._zlut_generation_dialog.close_button.isEnabled()


def test_cancel_zlut_generation_sends_command(ui_manager):
    commands = []
    ui_manager.send_ipc = commands.append

    ui_manager.cancel_zlut_generation()

    assert commands == [CancelZLUTGenerationCommand()]


def test_discard_generated_zlut_evaluation_sends_command(ui_manager):
    commands = []
    ui_manager.send_ipc = commands.append

    ui_manager.discard_generated_zlut_evaluation()

    assert commands == [CancelGeneratedZLUTEvaluationCommand()]


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

    assert commands == [
        SaveGeneratedZLUTCommand(
            filepath='C:/tmp/test.txt',
            bead_id=5,
            load_after_save=True,
            load_request_id=1,
        )
    ]

    ui_manager.update_zlut_metadata(filepath='C:/tmp/test.txt', load_request_id=1)

    settings = QSettings('MagScope', 'MagScope')
    assert settings.value(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY, type=str) == 'C:/tmp/test.txt'


def test_generated_save_load_failure_clear_allows_later_untagged_metadata(
    ui_manager,
    monkeypatch,
    qtbot,
):
    commands = []
    ui_manager.send_ipc = commands.append
    window = QMainWindow()
    qtbot.addWidget(window)
    ui_manager.windows = [window]
    monkeypatch.setattr(
        'magscope.ui.ui.QFileDialog.getSaveFileName',
        lambda *args, **kwargs: ('C:/tmp/generated.txt', ''),
    )

    ui_manager.save_generated_zlut(5, load_after_save=True)
    command = commands[0]

    assert command == SaveGeneratedZLUTCommand(
        filepath='C:/tmp/generated.txt',
        bead_id=5,
        load_after_save=True,
        load_request_id=1,
    )
    assert ui_manager._pending_zlut_load_request_id == 1

    clear_command = ClearPendingZLUTLoadRequestCommand(
        load_request_id=command.load_request_id
    )
    ui_manager.clear_pending_zlut_load_request(clear_command.load_request_id)
    ui_manager.update_zlut_metadata(
        filepath='C:/tmp/later.txt',
        z_min=0.0,
        z_max=10.0,
        step_size=5.0,
        profile_length=32,
        load_request_id=None,
    )

    assert ui_manager._pending_zlut_load_request_id is None
    assert ui_manager._pending_zlut_filepath_to_remember is None
    assert ui_manager._current_zlut_filepath == 'C:/tmp/later.txt'
    assert ui_manager._current_zlut_metadata == {
        'z_min': 0.0,
        'z_max': 10.0,
        'step_size': 5.0,
        'profile_length': 32,
    }


def test_stale_generated_save_load_failure_does_not_clear_newer_pending_request(ui_manager):
    ui_manager._start_pending_zlut_filepath_to_remember('C:/tmp/old.txt')
    ui_manager._start_pending_zlut_filepath_to_remember('C:/tmp/new.txt')

    ui_manager.clear_pending_zlut_load_request(1)

    assert ui_manager._pending_zlut_load_request_id == 2
    assert (
        ui_manager._pending_zlut_filepath_to_remember
        == ui_manager._normalized_zlut_filepath('C:/tmp/new.txt')
    )


def test_save_generated_zlut_without_loading_sends_command(ui_manager, monkeypatch, qtbot):
    commands = []
    ui_manager.send_ipc = commands.append
    window = QMainWindow()
    qtbot.addWidget(window)
    ui_manager.windows = [window]
    monkeypatch.setattr('magscope.ui.ui.QFileDialog.getSaveFileName', lambda *args, **kwargs: ('C:/tmp/test.txt', ''))

    pending_request_id = ui_manager._start_pending_zlut_filepath_to_remember(
        'C:/tmp/existing.txt'
    )

    ui_manager.save_generated_zlut(5, load_after_save=False)

    assert commands == [
        SaveGeneratedZLUTCommand(filepath='C:/tmp/test.txt', bead_id=5, load_after_save=False)
    ]
    assert ui_manager._pending_zlut_load_request_id == pending_request_id
    assert (
        ui_manager._pending_zlut_filepath_to_remember
        == ui_manager._normalized_zlut_filepath('C:/tmp/existing.txt')
    )

    ui_manager.update_zlut_metadata(filepath='C:/tmp/test.txt')

    settings = QSettings('MagScope', 'MagScope')
    assert not settings.contains(ui_module.LAST_ZLUT_FILEPATH_SETTINGS_KEY)


def test_update_zlut_generation_state_tracks_axis_metadata_without_panel(ui_manager):
    del ui_manager.controls.z_lut_generation_panel

    ui_manager.update_zlut_generation_state(
        'Running',
        detail='Collecting step 1',
        running=True,
        can_cancel=True,
        phase='capturing',
        z_axis_min_nm=10.0,
        z_axis_max_nm=30.0,
        z_axis_descending=True,
    )

    assert ui_manager._zlut_generation_z_axis_min_nm == 10.0
    assert ui_manager._zlut_generation_z_axis_max_nm == 30.0
    assert ui_manager._zlut_generation_z_axis_descending is True


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
        z_axis_descending=True,
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


def test_update_zlut_generation_evaluation_clears_preview_when_inactive(ui_manager):
    class FakeDatasetHandle:
        def __init__(self):
            self.closed = False

        def close(self) -> None:
            self.closed = True

    dataset = FakeDatasetHandle()
    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_sweep_dataset = dataset

    ui_manager.update_zlut_generation_evaluation(False, [], selected_bead_id=None)

    assert dataset.closed
    assert ui_manager._zlut_sweep_dataset is None
    assert ui_manager._zlut_generation_dialog.preview_widget.clear_calls == [
        'Waiting for Z-LUT sweep data...'
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


def test_update_zlut_generation_dialog_clears_preview_when_idle_without_evaluation(ui_manager):
    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'idle'
    ui_manager._zlut_evaluation_bead_ids = []

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

        def read_preview(self, selected_bead_id=None):
            assert selected_bead_id is None
            return {
                'state': self.state,
                'count': 3,
                'capacity': 8,
                'n_steps': self.n_steps,
                'n_beads': self.n_beads,
                'profiles_per_bead': self.profiles_per_bead,
                'profile_length': self.profile_length,
                'available_bead_ids': [5, 7],
                'selected_bead_id': 5,
                'motor_z_min': 10.0,
                'motor_z_max': 30.0,
                'step_indices': np.asarray([0, 1], dtype=np.uint32),
                'motor_z_values': np.asarray([10.0, 20.0], dtype=np.float64),
                'profiles': np.asarray(
                    [
                        [1.0, 2.0, 3.0],
                        [4.0, 5.0, 6.0],
                    ],
                    dtype=np.float64,
                ),
            }

        def peak(self):
            raise AssertionError('Preview refresh should use read_preview instead of peak')

        def get_capacity(self):
            return 8

        def close(self):
            pass

    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'evaluating'
    ui_manager._zlut_evaluation_bead_ids = [5, 7]
    ui_manager._zlut_generation_z_axis_min_nm = 10.0
    ui_manager._zlut_generation_z_axis_max_nm = 20.0
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
    assert preview_call['x_axis_min'] == 5.0
    assert preview_call['x_axis_max'] == 25.0
    assert preview_call['image_x_min'] == 5.0
    assert preview_call['image_x_max'] == 25.0
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


def test_live_preview_sorts_descending_z_positions_low_to_high(ui_manager, monkeypatch):
    from magscope.ui import ui as ui_module

    class FakeDataset:
        STATE_CAPTURING = 3
        STATE_COMPLETE = 4

        def __init__(self):
            self.state = self.STATE_CAPTURING
            self.n_steps = 3
            self.n_beads = 1
            self.profiles_per_bead = 1
            self.profile_length = 2

        @staticmethod
        def attach(*, locks):
            return FakeDataset()

        def peak(self):
            return {
                'bead_ids': np.asarray([5, 5, 5], dtype=np.uint32),
                'step_indices': np.asarray([0, 1, 2], dtype=np.uint32),
                'timestamps': np.asarray([1.0, 2.0, 3.0], dtype=np.float64),
                'motor_z_values': np.asarray([30.0, 20.0, 10.0], dtype=np.float64),
                'valid_flags': np.asarray([1, 1, 1], dtype=np.uint8),
                'profiles': np.asarray([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], dtype=np.float64),
            }

        def get_capacity(self):
            return 3

        def close(self):
            pass

    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'capturing'
    ui_manager._zlut_generation_z_axis_min_nm = 10.0
    ui_manager._zlut_generation_z_axis_max_nm = 30.0
    ui_manager._zlut_generation_z_axis_descending = True
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', FakeDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    preview_call = ui_manager._zlut_generation_dialog.preview_widget.preview_calls[-1]
    np.testing.assert_allclose(
        preview_call['preview_image'],
        np.asarray([[5.0, 3.0, 1.0], [6.0, 4.0, 2.0]], dtype=np.float64),
    )
    assert preview_call['x_axis_min'] == 5.0
    assert preview_call['x_axis_max'] == 35.0
    assert preview_call['image_x_min'] == 5.0
    assert preview_call['image_x_max'] == 35.0


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
    ui_manager._zlut_generation_z_axis_descending = False
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', FakeDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    preview_call = ui_manager._zlut_generation_dialog.preview_widget.preview_calls[-1]
    assert preview_call['mode'] == 'Raw sweep'
    assert preview_call['expected_capture_count'] == 12
    assert preview_call['x_axis_min'] == 5.0
    assert preview_call['x_axis_max'] == 45.0
    assert preview_call['image_x_min'] == 5.0
    assert preview_call['image_x_max'] == pytest.approx(18.3333333333)
    np.testing.assert_allclose(
        preview_call['preview_image'],
        np.asarray([[1.0, np.nan, np.nan, 3.0], [2.0, np.nan, np.nan, 4.0]], dtype=np.float64),
        equal_nan=True,
    )


def test_live_preview_descending_partial_capture_aligns_right(ui_manager, monkeypatch):
    from magscope.ui import ui as ui_module

    class FakeDataset:
        STATE_CAPTURING = 3
        STATE_COMPLETE = 4

        def __init__(self):
            self.state = self.STATE_CAPTURING
            self.n_steps = 3
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
                'motor_z_values': np.asarray([30.0, 20.0], dtype=np.float64),
                'valid_flags': np.asarray([1, 1], dtype=np.uint8),
                'profiles': np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
            }

        def get_capacity(self):
            return 3

        def close(self):
            pass

    ui_manager._zlut_generation_dialog = FakeZLutGenerationDialog()
    ui_manager._zlut_generation_phase = 'capturing'
    ui_manager._zlut_generation_z_axis_min_nm = 10.0
    ui_manager._zlut_generation_z_axis_max_nm = 30.0
    ui_manager._zlut_generation_z_axis_descending = True
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', FakeDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    preview_call = ui_manager._zlut_generation_dialog.preview_widget.preview_calls[-1]
    np.testing.assert_allclose(
        preview_call['preview_image'],
        np.asarray([[3.0, 1.0], [4.0, 2.0]], dtype=np.float64),
    )
    assert preview_call['x_axis_min'] == 5.0
    assert preview_call['x_axis_max'] == 35.0
    assert preview_call['image_x_min'] == 15.0
    assert preview_call['image_x_max'] == 35.0


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
    ui_manager._zlut_generation_z_axis_min_nm = 10.0
    ui_manager._zlut_generation_z_axis_max_nm = 20.0
    ui_manager.locks = {}
    monkeypatch.setattr(ui_module, 'ZLUTSweepDataset', FakeDataset)
    monkeypatch.setattr(ui_module, 'time', lambda: 10.0)

    ui_manager._update_zlut_generation_dialog()

    preview_call = ui_manager._zlut_generation_dialog.preview_widget.preview_calls[-1]
    assert preview_call['x_axis_min'] == 5.0
    assert preview_call['x_axis_max'] == 25.0
    assert preview_call['image_x_min'] == 5.0
    assert preview_call['image_x_max'] == 25.0
    np.testing.assert_allclose(
        preview_call['preview_image'],
        np.asarray([[3.0, 1.0], [4.0, 2.0]], dtype=np.float64),
    )
