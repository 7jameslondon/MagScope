from collections import OrderedDict
from math import floor, ceil
import sys
from time import time
import traceback
from typing import Callable, Iterable
from warnings import warn

import numpy as np
from PyQt6.QtCore import QPoint, QRectF, QSettings, Qt, QThread, QTimer
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from magscope._logging import get_logger
from magscope.datatypes import VideoBuffer
from magscope.ipc import Delivery, register_ipc_command
from magscope.ipc_commands import *
from magscope.ui import (
    AcquisitionPanel,
    BeadGraphic,
    BeadSelectionPanel,
    CameraPanel,
    ControlPanelBase,
    GripSplitter,
    HistogramPanel,
    PlotWorker,
    ResizableLabel,
    ScriptPanel,
    StatusPanel,
    TimeSeriesPlotBase,
    VideoViewer,
)
from magscope.ui.controls import (
    HelpPanel,
    MagScopeSettingsPanel,
    PlotSettingsPanel,
    ProfilePanel,
    ResetPanel,
    TrackingOptionsPanel,
    XYLockPanel,
    ZLUTGenerationPanel,
    ZLUTPanel,
    ZLockPanel,
)
from magscope.ui.panel_layout import (
    PANEL_MIME_TYPE,
    PanelLayoutManager,
    PanelWrapper,
    ReorderableColumn,
)
from magscope.ui.widgets import CollapsibleGroupBox
from magscope.processes import ManagerProcessBase
from magscope.scripting import ScriptStatus, register_script_command
from magscope.settings import MagScopeSettings
from magscope.utils import AcquisitionMode, numpy_type_to_qt_image_type

logger = get_logger("ui.ui")

class UIManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self._active_bead_graphic: BeadGraphic | None = None
        self._active_bead_id: int | None = None
        self._bead_rois: dict[int, tuple[int, int, int, int]] = {}
        self._pending_bead_add_id: int | None = None
        self._pending_bead_add_roi: tuple[int, int, int, int] | None = None
        self._bead_next_id: int = 0
        self.beads_in_view_on = False
        self.beads_in_view_count = 1
        self.beads_in_view_marker_size = 20
        self.central_widgets: list[QWidget] = []
        self.central_layouts: list[QLayout] = []
        self.controls: Controls | None = None
        self.controls_to_add = []
        self._display_rate_counter: int = 0
        self._display_rate_last_time: float = time()
        self._display_rate_last_rate: float = 0
        self._n_windows: int | None = None
        self.plot_worker: PlotWorker
        self.plot_thread: QThread
        self.plots_widget: QLabel
        self.plots_to_add: list[TimeSeriesPlotBase] = []
        self.qt_app: QApplication | None = None
        self.selected_bead = 0
        self.reference_bead: int | None = None
        self._timer: QTimer | None = None
        self._timer_video_view: QTimer | None = None
        self._video_buffer_last_index: int = 0
        self._video_viewer_need_reset: bool = True
        self.video_viewer: VideoViewer | None = None
        self.windows: list[QMainWindow] = []
        self._suppress_bead_roi_updates: bool = False
        self._last_applied_roi: int | None = None
        self._settings_persistence_warning_shown = False
        self._bead_roi_capacity = 10000

    def setup(self):
        self.qt_app = QApplication.instance()
        if not self.qt_app:
            self.qt_app = QApplication(sys.argv)
        QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)

        if self.settings is not None:
            self._last_applied_roi = self.settings["ROI"]

        # If the number of windows is not specified, then use the number of screens
        if self._n_windows is None:
            self._n_windows = len(QApplication.screens())

        # Create the live plots in a separate thread (but dont start it)
        self.plots_widget = ResizableLabel()
        self.plots_widget.setScaledContents(True)
        self.plots_thread = QThread()
        self.plot_worker = PlotWorker()
        for plot in self.plots_to_add:
            self.plot_worker.add_plot(plot)
        self.plot_worker.set_locks(self.locks)
        self.plot_worker.setup()

        # Create controls panel
        self.controls = Controls(self)

        # Create the video viewer
        self.video_viewer = VideoViewer()
        self._refresh_bead_overlay()

        # Finally start the live plots
        self.plot_worker.moveToThread(self.plots_thread)
        self.plots_thread.started.connect(self.plot_worker.run)  # noqa
        self.plot_worker.image_signal.connect(
            lambda img: self.plots_widget.setPixmap(QPixmap.fromImage(img))
        )
        self.plots_widget.resized.connect(self.update_plot_figure_size)
        self.plots_thread.start(QThread.Priority.LowPriority)

        # Create the layouts for each window
        self.create_central_widgets()

        # Create the windows
        for i in range(self._n_windows):
            window = QMainWindow()
            window.setWindowTitle("MagScope")
            screen = QApplication.screens()[i % len(QApplication.screens())]
            geometry = screen.geometry()
            window.setGeometry(
                geometry.x(), geometry.y(), geometry.width(), geometry.height()
            )
            window.setMinimumWidth(300)
            window.setMinimumHeight(300)
            window.closeEvent = lambda _, w=window: self.quit()
            window.showMaximized()
            window.setCentralWidget(self.central_widgets[i])
            self.windows.append(window)

        self._show_settings_persistence_warning_if_needed()

        # Connect the video viewer
        self.video_viewer.coordinatesChanged.connect(self.update_view_coords)
        self.video_viewer.sceneClicked.connect(self.callback_view_clicked)

        # Timer
        self._timer = QTimer()
        self._timer.timeout.connect(self._main_loop_tick)  # noqa
        self._timer.setInterval(0)
        self._timer.start()

        # Timer - Video Display
        self._timer_video_view = QTimer()
        self._timer_video_view.timeout.connect(self._update_view_and_hist_tick)
        self._timer_video_view.setInterval(25)
        self._timer_video_view.start()

        # Start app
        self._running = True
        self.qt_app.exec()

    @register_ipc_command(
        SetSettingsCommand, delivery=Delivery.BROADCAST, target="ManagerProcessBase"
    )
    def set_settings(self, settings: MagScopeSettings):
        """Apply new settings and clear beads if the ROI size changed."""

        previous_roi = self._last_applied_roi
        super().set_settings(settings)
        self._show_settings_persistence_warning_if_needed()

        new_roi = self.settings["ROI"]
        if previous_roi is not None and new_roi != previous_roi:
            self.clear_beads()

        self._last_applied_roi = new_roi
        self._update_roi_labels(new_roi)

    def _show_settings_persistence_warning_if_needed(self) -> None:
        if self._settings_persistence_warning_shown:
            return
        if self.settings is None or self.settings.persistence_available:
            return
        if not self.windows:
            return

        self._settings_persistence_warning_shown = True
        self._show_settings_persistence_warning()

    def _show_settings_persistence_warning(self) -> None:
        msg = QMessageBox(self.windows[0])
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Settings Persistence Unavailable")
        msg.setText(
            "Some settings may not automatically load or save for this session."
        )
        msg.setInformativeText(
            "MagScope will continue running with in-memory settings."
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.show()

    def update_plot_figure_size(self, w, h):
        self.plot_worker.figure_size_signal.emit(w, h)

    def quit(self):
        super().quit()

        # Stop the plot worker
        self.plot_worker._stop()
        self.plots_thread.quit()
        self.plots_thread.wait()

        for window in self.windows:
            window.close()

    def do_main_loop(self):
        # Because the UIManager is a special case with a GUI
        # the main loop is actually called by a timer, not the
        # run method of it's super()

        if self._running:
            self._update_display_rate()
            self.update_video_buffer_status()
            self.update_video_processors_status()
            self.controls.profile_panel.update_plot()
            self.receive_ipc()

    def _handle_timer_exception(self, exc: BaseException) -> None:
        """Surface exceptions that occur inside Qt timer callbacks."""

        self._running = False
        self._report_exception(exc)
        if self.qt_app is not None:
            self.qt_app.quit()

    def _run_safe(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as exc:
            self._handle_timer_exception(exc)

    def _main_loop_tick(self) -> None:
        self._run_safe(self.do_main_loop)

    def _update_view_and_hist_tick(self) -> None:
        self._run_safe(self._update_view_and_hist)

    def set_selected_bead(self, bead: int):
        old_selected = self._normalize_bead_id(self.selected_bead)
        old_reference = self._normalize_bead_id(self.reference_bead)
        self.selected_bead = bead
        normalized_bead = self._normalize_bead_id(bead)
        if hasattr(self, 'plot_worker') and self.plot_worker is not None:
            self.plot_worker.selected_bead_signal.emit(bead)
        self._sync_plot_settings_selected_bead(bead)
        if self.shared_values is not None:
            self.shared_values.live_profile_bead.value = bead
        self._clear_live_profile_buffer()
        if self._beads_locked():
            self._set_active_bead(None)
        else:
            self._set_active_bead(normalized_bead)
        self._update_bead_highlights(
            old_selected=old_selected,
            old_reference=old_reference,
        )

    def set_live_profile_monitor_enabled(self, enabled: bool) -> None:
        if self.shared_values is not None:
            self.shared_values.live_profile_enabled.value = 1 if enabled else 0
        if not enabled:
            self._clear_live_profile_buffer()

    def set_reference_bead(self, bead: int | None):
        old_selected = self._normalize_bead_id(self.selected_bead)
        old_reference = self._normalize_bead_id(self.reference_bead)
        self.reference_bead = bead
        emitted_bead = -1 if bead is None else bead
        if hasattr(self, 'plot_worker') and self.plot_worker is not None:
            self.plot_worker.reference_bead_signal.emit(emitted_bead)
        self._sync_plot_settings_reference_bead(bead)
        self._update_bead_highlights(
            old_selected=old_selected,
            old_reference=old_reference,
        )

    def _sync_plot_settings_selected_bead(self, bead: int) -> None:
        if self.controls is None or not hasattr(self.controls, 'plot_settings_panel'):
            return
        lineedit = self.controls.plot_settings_panel.selected_bead.lineedit
        lineedit.blockSignals(True)
        lineedit.setText(str(bead))
        lineedit.blockSignals(False)

    def _sync_plot_settings_reference_bead(self, bead: int | None) -> None:
        if self.controls is None or not hasattr(self.controls, 'plot_settings_panel'):
            return
        lineedit = self.controls.plot_settings_panel.reference_bead.lineedit
        lineedit.blockSignals(True)
        lineedit.setText('' if bead is None or bead < 0 else str(bead))
        lineedit.blockSignals(False)

    def _normalize_bead_id(self, bead: int | None) -> int | None:
        if bead is None or bead < 0:
            return None
        return bead

    def _get_bead_highlight_state(self, bead_id: int) -> str:
        selected_id = self._normalize_bead_id(self.selected_bead)
        reference_id = self._normalize_bead_id(self.reference_bead)

        if bead_id == selected_id:
            return 'selected'
        if bead_id == reference_id:
            return 'reference'
        return 'default'

    def _refresh_bead_overlay(self) -> None:
        if self.video_viewer is not None:
            self.video_viewer.set_bead_overlay(
                self._bead_rois,
                self._active_bead_id,
                self._normalize_bead_id(self.selected_bead),
                self._normalize_bead_id(self.reference_bead),
            )
            self.video_viewer.viewport().update()

    def _current_scene_rect(self) -> QRectF:
        if self.video_viewer is None:
            return QRectF()
        image_scene_rect = getattr(self.video_viewer, 'image_scene_rect', None)
        if callable(image_scene_rect):
            rect = image_scene_rect()
            if not rect.isNull():
                return rect
        scene = getattr(self.video_viewer, 'scene', None)
        if scene is None or not hasattr(scene, 'sceneRect'):
            return QRectF()
        return scene.sceneRect()

    def _current_visible_scene_rect(self) -> QRectF:
        scene_rect = self._current_scene_rect()
        if self.video_viewer is None or scene_rect.isNull():
            return scene_rect

        viewport = self.video_viewer.viewport()
        if viewport is None or not hasattr(viewport, 'rect'):
            return scene_rect

        viewport_rect = viewport.rect()
        if viewport_rect.isNull():
            return scene_rect

        visible_rect = self.video_viewer.mapToScene(viewport_rect).boundingRect()
        visible_rect = visible_rect.intersected(scene_rect)
        return scene_rect if visible_rect.isEmpty() else visible_rect

    def _beads_locked(self) -> bool:
        if self.controls is None:
            return False
        return self.controls.bead_selection_panel.lock_button.isChecked()

    def _next_random_bead_roi(
        self,
        rng: np.random.Generator,
        visible_rect: QRectF,
    ) -> tuple[int, int, int, int] | None:
        if self.settings is None:
            return None

        roi_width = int(self.settings['ROI'])
        half_width = roi_width / 2
        min_x = ceil(visible_rect.left() + half_width)
        max_x = floor(visible_rect.right() - half_width)
        min_y = ceil(visible_rect.top() + half_width)
        max_y = floor(visible_rect.bottom() - half_width)
        if min_x > max_x or min_y > max_y:
            return None

        center_x = int(rng.integers(min_x, max_x + 1))
        center_y = int(rng.integers(min_y, max_y + 1))
        return BeadGraphic.clamp_roi_to_scene(
            BeadGraphic.roi_from_center(center_x, center_y, roi_width),
            self._current_scene_rect(),
        )

    def _set_active_bead(self, bead_id: int | None) -> None:
        normalized_id = self._normalize_bead_id(bead_id)
        if normalized_id is not None and normalized_id not in self._bead_rois:
            normalized_id = None

        if self._active_bead_graphic is not None:
            self._active_bead_graphic.remove()
            self._active_bead_graphic = None

        self._active_bead_id = normalized_id
        if normalized_id is None or self.video_viewer is None:
            self._refresh_bead_overlay()
            return

        roi = self._bead_rois[normalized_id]
        self._active_bead_graphic = BeadGraphic(self, normalized_id, roi, self.video_viewer.scene)
        self._active_bead_graphic.locked = (
            self.controls is not None and self.controls.bead_selection_panel.lock_button.isChecked()
        )
        self._active_bead_graphic.set_selection_state(
            self._get_bead_highlight_state(normalized_id)
        )
        self._refresh_bead_overlay()

    def on_active_bead_move_completed(
        self,
        bead_id: int,
        roi: tuple[int, int, int, int],
    ) -> None:
        if bead_id not in self._bead_rois:
            return
        self._bead_rois[bead_id] = roi
        self._update_bead_roi(bead_id, roi)
        self._refresh_bead_overlay()

    @register_ipc_command(AddRandomBeadsCommand)
    @register_script_command(AddRandomBeadsCommand)
    def add_random_beads(self, count: int, seed: int | None = None) -> None:
        if count <= 0:
            return
        if self._beads_locked():
            self.show_error('Beads are locked', 'Unlock beads before adding scripted bead ROIs.')
            return

        visible_rect = self._current_visible_scene_rect()
        if visible_rect.isNull() or visible_rect.isEmpty():
            self.show_error('No visible field of view', 'Cannot add random beads without a visible image area.')
            return

        remaining_capacity = self._bead_roi_capacity - self._bead_next_id
        if remaining_capacity <= 0:
            self.show_error(
                'Maximum bead count reached',
                'Remove beads or use Reassign IDs before adding more than 10000 beads.',
            )
            return

        rng = np.random.default_rng(seed)
        bead_rois: dict[int, tuple[int, int, int, int]] = {}
        next_bead_id = self._bead_next_id
        count_to_add = min(count, remaining_capacity)
        for _ in range(count_to_add):
            bead_id = next_bead_id
            roi = self._next_random_bead_roi(rng, visible_rect)
            if roi is None:
                break
            bead_rois[bead_id] = roi
            next_bead_id += 1

        if not bead_rois:
            return

        try:
            if self.bead_roi_buffer is None:
                updated_bead_rois = {**self._bead_rois, **bead_rois}
                self._write_bead_rois_to_buffer(updated_bead_rois)
                self._broadcast_bead_roi_update()
            else:
                self.bead_roi_buffer.add_beads(bead_rois)
                self._broadcast_bead_roi_update()
        except Exception:
            self._update_next_bead_id_label()
            raise

        self._bead_rois.update(bead_rois)
        self._bead_next_id = next_bead_id
        self._update_next_bead_id_label()
        if not self._beads_locked():
            self._set_active_bead(self._normalize_bead_id(self.selected_bead))
        self._refresh_bead_overlay()

    def _hit_test_bead(self, pos: QPoint) -> int | None:
        if not self._bead_rois:
            return None

        selected_id = self._normalize_bead_id(self.selected_bead)
        reference_id = self._normalize_bead_id(self.reference_bead)
        best_match: tuple[int, float, int] | None = None
        best_bead_id: int | None = None

        for bead_id, (x0, x1, y0, y1) in self._bead_rois.items():
            if not (x0 <= pos.x() <= x1 and y0 <= pos.y() <= y1):
                continue

            if bead_id == self._active_bead_id:
                priority = 0
            elif bead_id == selected_id:
                priority = 1
            elif bead_id == reference_id:
                priority = 2
            else:
                priority = 3

            center_x = (x0 + x1) / 2
            center_y = (y0 + y1) / 2
            distance_sq = (center_x - pos.x()) ** 2 + (center_y - pos.y()) ** 2
            candidate = (priority, distance_sq, -bead_id)
            if best_match is None or candidate < best_match:
                best_match = candidate
                best_bead_id = bead_id

        return best_bead_id

    def _update_bead_highlight(self, bead_id: int) -> None:
        if bead_id == self._active_bead_id and self._active_bead_graphic is not None:
            self._active_bead_graphic.set_selection_state(
                self._get_bead_highlight_state(bead_id)
            )

    def _update_bead_highlights(
        self,
        *,
        old_selected: int | None = None,
        old_reference: int | None = None,
    ):
        selected_id = self._normalize_bead_id(self.selected_bead)
        reference_id = self._normalize_bead_id(self.reference_bead)

        affected_ids = {
            bead_id
            for bead_id in (old_selected, old_reference, selected_id, reference_id)
            if bead_id is not None
        }

        for bead_id in affected_ids:
            self._update_bead_highlight(bead_id)
        self._refresh_bead_overlay()

    def _clear_live_profile_buffer(self) -> None:
        if self.live_profile_buffer is not None:
            self.live_profile_buffer.clear()

    @property
    def n_windows(self):
        return self._n_windows

    @n_windows.setter
    def n_windows(self, value):
        if self._running:
            warn("Application already running", RuntimeWarning)
            return

        if not 1 <= value <= 3:
            warn("Number of windows must be between 1 and 3")
            return

        self._n_windows = value

    @property
    def bead_roi_updates_suppressed(self) -> bool:
        return self._suppress_bead_roi_updates

    @property
    def bead_next_id(self) -> int:
        return self._bead_next_id

    def _broadcast_bead_roi_update(self) -> None:
        if self._command_registry is None or self._pipe is None or self._magscope_quitting is None:
            return
        self.send_ipc(UpdateBeadRoisCommand())

    def _write_bead_rois_to_buffer(self, bead_rois: dict[int, tuple[int, int, int, int]]) -> None:
        if self.bead_roi_buffer is None:
            bead_ids = np.asarray(sorted(bead_rois), dtype=np.uint32)
            bead_roi_values = np.asarray([bead_rois[int(bead_id)] for bead_id in bead_ids], dtype=np.uint32)
            self._bead_roi_ids = bead_ids
            if bead_roi_values.size == 0:
                self._bead_roi_values = np.zeros((0, 4), dtype=np.uint32)
            else:
                self._bead_roi_values = bead_roi_values.reshape((-1, 4))
            return
        self.bead_roi_buffer.replace_beads(bead_rois)
        self._refresh_bead_roi_cache()

    def create_central_widgets(self):
        match self.n_windows:
            case 1:
                self.create_one_window_widgets()
            case 2:
                self.create_two_window_widgets()
            case 3:
                self.create_three_window_widgets()

    def create_one_window_widgets(self):
        for i in range(1):
            self.central_widgets.append(QWidget())
            self.central_layouts.append(QVBoxLayout())
            self.central_widgets[i].setLayout(self.central_layouts[i])

        # Left-right split
        lr_splitter = GripSplitter(name='One Window Left-Right Splitter',
                                   orientation=Qt.Orientation.Horizontal)
        self.central_layouts[0].addWidget(lr_splitter)

        # Left
        left_widget = QWidget()
        left_widget.setMinimumWidth(150)
        lr_splitter.addWidget(left_widget)
        left_layout = QHBoxLayout()
        left_widget.setLayout(left_layout)

        # Add controls to left
        left_layout.addWidget(self.controls)

        # Right
        right_widget = QWidget()
        right_widget.setMinimumWidth(150)
        lr_splitter.addWidget(right_widget)
        right_layout = QHBoxLayout()
        right_widget.setLayout(right_layout)

        # Right: top-bottom split
        ud_splitter = GripSplitter(name='One Window Top-Bottom Splitter',
                                   orientation=Qt.Orientation.Vertical)
        right_layout.addWidget(ud_splitter)

        # Right-top
        right_top_widget = QWidget()
        right_top_widget.setMinimumHeight(150)
        ud_splitter.addWidget(right_top_widget)
        right_top_layout = QHBoxLayout()
        right_top_widget.setLayout(right_top_layout)

        # Add plots to right-top
        right_top_layout.addWidget(self.plots_widget)

        # Right-bottom
        right_bottom_widget = QWidget()
        right_bottom_widget.setMinimumHeight(150)
        ud_splitter.addWidget(right_bottom_widget)
        right_bottom_layout = QHBoxLayout()
        right_bottom_widget.setLayout(right_bottom_layout)

        # Add video viewer to right-bottom
        right_bottom_layout.addWidget(self.video_viewer)

    def create_two_window_widgets(self):
        for i in range(2):
            self.central_widgets.append(QWidget())
            self.central_layouts.append(QVBoxLayout())
            self.central_widgets[i].setLayout(self.central_layouts[i])

        ### Window 0 ###

        # Left-right split
        lr_splitter = GripSplitter(name='Two Window Left-Right Splitter',
                                   orientation=Qt.Orientation.Horizontal)
        self.central_layouts[0].addWidget(lr_splitter)

        # Left
        left_widget = QWidget()
        left_widget.setMinimumWidth(150)
        lr_splitter.addWidget(left_widget)
        left_layout = QHBoxLayout()
        left_widget.setLayout(left_layout)

        # Add controls to left
        left_layout.addWidget(self.controls)

        # Right
        right_widget = QWidget()
        right_widget.setMinimumWidth(150)
        lr_splitter.addWidget(right_widget)
        right_layout = QHBoxLayout()
        right_widget.setLayout(right_layout)

        # Add video viewer to right
        right_layout.addWidget(self.video_viewer)

        ### Window 1 ###

        # Add plots to window-1
        self.central_layouts[1].addWidget(self.plots_widget)

    def create_three_window_widgets(self):
        for i in range(3):
            self.central_widgets.append(QWidget())
            self.central_layouts.append(QVBoxLayout())
            self.central_widgets[i].setLayout(self.central_layouts[i])

        ### Window 0 ###
        # Add controls to window-0
        self.central_layouts[0].addWidget(self.controls)

        ### Window 1 ###
        # Add video viewer to window-1
        self.central_layouts[1].addWidget(self.video_viewer)

        ### Window 2 ###
        # Add plots to window-2
        self.central_layouts[2].addWidget(self.plots_widget)

    def update_view_coords(self):
        pass

    def _update_view_and_hist(self):
        # Get image and _write position
        index, image_bytes = self.video_buffer.peak_image()

        # Check if _write has changed (a new image is ready)
        if self._video_buffer_last_index != index:
            # Update the stored index
            self._video_buffer_last_index = index

            cam_bits = self.camera_type.bits
            dtype_bits = np.iinfo(self.video_buffer.dtype).bits
            scale = (2 ** (dtype_bits - cam_bits))

            # Update the view
            qt_img = QImage(
                np.frombuffer(image_bytes, self.video_buffer.dtype).copy() *
                scale, *self.video_buffer.image_shape,
                numpy_type_to_qt_image_type(self.video_buffer.dtype))
            self.video_viewer.set_pixmap(QPixmap.fromImage(qt_img))

            if self._video_viewer_need_reset:
                self.video_viewer.reset_view()
                self._video_viewer_need_reset = False

            # Update the bead position overlay
            self._update_beads_in_view()

            # Update the histogram
            self.controls.histogram_panel.update_plot(image_bytes)

            # Increment the display rate counter
            self._display_rate_counter += 1

    def callback_view_clicked(self, pos: QPoint, button=Qt.MouseButton.LeftButton):
        if self.controls is None or self.controls.bead_selection_panel.lock_button.isChecked():
            return
        if self._pending_bead_add_id is not None:
            return

        bead_id = self._hit_test_bead(pos)
        if button == Qt.MouseButton.RightButton:
            if bead_id is not None:
                self.remove_bead(bead_id)
            return

        if bead_id is not None:
            self._set_active_bead(bead_id)
            self.set_selected_bead(bead_id)
            return

        self.add_bead(pos)

    def refresh_bead_rois(self):
        super().refresh_bead_rois()
        if self._pending_bead_add_id is None or self._pending_bead_add_roi is None:
            return

        bead_ids, bead_rois = self.get_cached_bead_rois()
        pending_id = self._pending_bead_add_id
        pending_roi = self._pending_bead_add_roi

        matches = bead_ids == pending_id
        if not np.any(matches):
            return

        roi = bead_rois[np.flatnonzero(matches)[0]]
        if tuple(int(value) for value in roi) != pending_roi:
            return

        self._clear_pending_bead_add()

    def update_bead_rois(self):
        self._write_bead_rois_to_buffer(self._bead_rois)
        self._broadcast_bead_roi_update()

    def _add_bead_roi(self, bead_id: int, roi: tuple[int, int, int, int]) -> None:
        if self.bead_roi_buffer is None:
            self.update_bead_rois()
            return
        self.bead_roi_buffer.add_beads({bead_id: roi})
        self._broadcast_bead_roi_update()

    def _update_bead_roi(self, bead_id: int, roi: tuple[int, int, int, int]) -> None:
        if self.bead_roi_buffer is None:
            self.update_bead_rois()
            return
        self.bead_roi_buffer.update_beads({bead_id: roi})
        self._broadcast_bead_roi_update()

    def _update_multiple_bead_rois(
        self,
        bead_rois: dict[int, tuple[int, int, int, int]],
    ) -> None:
        if not bead_rois:
            return
        if self.bead_roi_buffer is None:
            self.update_bead_rois()
            return
        self.bead_roi_buffer.update_beads(bead_rois)
        self._broadcast_bead_roi_update()

    def _remove_bead_roi(self, bead_id: int) -> None:
        if self.bead_roi_buffer is None:
            self.update_bead_rois()
            return
        self.bead_roi_buffer.remove_beads([bead_id])
        self._broadcast_bead_roi_update()

    @register_ipc_command(MoveBeadsCommand)
    def move_beads(self, moves: list[tuple[int, int, int]]):
        moved_ids: list[int] = []
        moved_rois: dict[int, tuple[int, int, int, int]] = {}
        scene_rect = self._current_scene_rect()

        self._suppress_bead_roi_updates = True
        try:
            for id, dx, dy in moves:
                if id not in self._bead_rois:
                    continue

                roi = BeadGraphic.move_roi(self._bead_rois[id], dx, dy, scene_rect)
                self._bead_rois[id] = roi
                if id == self._active_bead_id and self._active_bead_graphic is not None:
                    self._active_bead_graphic.set_roi_bounds(roi)
                moved_ids.append(id)
                moved_rois[id] = roi
        finally:
            self._suppress_bead_roi_updates = False

        if not moved_ids:
            return

        self._update_multiple_bead_rois(moved_rois)
        self._refresh_bead_overlay()

        command = RemoveBeadsFromPendingMovesCommand(ids=moved_ids)
        self.send_ipc(command)

    def add_bead(self, pos: QPoint):
        if self._bead_next_id >= self._bead_roi_capacity:
            self.show_error(
                'Maximum bead count reached',
                'Remove beads or use Reassign IDs before adding more than 10000 beads.',
            )
            return

        id = self._bead_next_id
        x = pos.x()
        y = pos.y()
        w = self.settings['ROI']
        scene_rect = self._current_scene_rect()
        roi = BeadGraphic.clamp_roi_to_scene(
            BeadGraphic.roi_from_center(x, y, w),
            scene_rect,
        )
        self._bead_rois[id] = roi
        previous_next_bead_id = self._bead_next_id
        self._bead_next_id += 1
        self._update_next_bead_id_label()

        # Update the bead ROI
        self._pending_bead_add_id = id
        self._pending_bead_add_roi = roi
        try:
            self._add_bead_roi(id, roi)
        except Exception:
            self._bead_rois.pop(id, None)
            self._bead_next_id = previous_next_bead_id
            self._update_next_bead_id_label()
            self._clear_pending_bead_add()
            raise
        if id == self._normalize_bead_id(self.selected_bead):
            self._set_active_bead(id)
        self._refresh_bead_overlay()

    def remove_bead(self, id: int):
        old_selected = self._normalize_bead_id(self.selected_bead)
        old_reference = self._normalize_bead_id(self.reference_bead)

        if id not in self._bead_rois:
            return

        self._bead_rois.pop(id)
        if id == self._active_bead_id:
            self._set_active_bead(None)

        # Update highlight colors to reflect selection/reference
        self._update_bead_highlights(
            old_selected=old_selected,
            old_reference=old_reference,
        )

        # Update bead ROI
        self._remove_bead_roi(id)

    def clear_beads(self):
        self._clear_pending_bead_add()
        self._set_active_bead(None)

        self._bead_rois.clear()
        self._bead_next_id = 0
        self._update_next_bead_id_label()

        # Update bead ROIs
        if self.bead_roi_buffer is not None:
            self.bead_roi_buffer.clear_beads()
            self._refresh_bead_roi_cache()
        else:
            self._bead_roi_ids = np.zeros((0,), dtype=np.uint32)
            self._bead_roi_values = np.zeros((0, 4), dtype=np.uint32)
        self._broadcast_bead_roi_update()
        self.set_reference_bead(None)
        self.set_selected_bead(0)
        self._refresh_bead_overlay()

    def reset_bead_ids(self):
        self._clear_pending_bead_add()
        if not self._bead_rois:
            self._bead_next_id = 0
            self._update_next_bead_id_label()
            return

        old_active_bead = self._active_bead_id
        new_rois: dict[int, tuple[int, int, int, int]] = {}
        id_mapping: dict[int, int] = {}
        for new_id, (old_id, roi) in enumerate(sorted(self._bead_rois.items())):
            id_mapping[old_id] = new_id
            new_rois[new_id] = roi

        self._bead_rois = new_rois
        self._bead_next_id = len(self._bead_rois)

        if self.selected_bead is not None:
            new_selected = id_mapping.get(self.selected_bead, -1)
            self.set_selected_bead(new_selected)

        if self.reference_bead is not None:
            new_reference = id_mapping.get(self.reference_bead)
            self.set_reference_bead(new_reference)

        self._update_bead_highlights()
        self.update_bead_rois()
        self._update_next_bead_id_label()
        self._set_active_bead(id_mapping.get(old_active_bead))
        self._refresh_bead_overlay()

    def _update_roi_labels(self, roi: int) -> None:
        if self.controls is None:
            return

        self.controls.bead_selection_panel.roi_size_label.setText(
            f"{roi} x {roi} pixels"
        )
        self.controls.z_lut_generation_panel.roi_size_label.setText(
            f"{roi} x {roi} pixels"
        )

    def _update_next_bead_id_label(self) -> None:
        if self.controls is None:
            return

        self.controls.bead_selection_panel.update_next_bead_id_label(
            self._bead_next_id
        )

    def _clear_pending_bead_add(self) -> None:
        self._pending_bead_add_id = None
        self._pending_bead_add_roi = None

    def _calculate_next_bead_id(self) -> int:
        if not self._bead_rois:
            return 0

        return max(self._bead_rois.keys()) + 1

    def lock_beads(self, locked: bool):
        if self.video_viewer is not None:
            self.video_viewer.set_locked_overlay(locked)
        if self._active_bead_graphic is not None:
            self._active_bead_graphic.locked = locked
        if locked:
            self._set_active_bead(None)
        else:
            self._set_active_bead(self._normalize_bead_id(self.selected_bead))

    def update_video_processors_status(self):
        busy = self.shared_values.video_process_busy_count.value
        total = self.settings['video processors n']
        text = f'{busy}/{total} busy'
        self.controls.status_panel.update_video_processors_status(text)

    def update_video_buffer_status(self):
        level = self.video_buffer.get_level()
        size = self.video_buffer.n_total_images
        text = f'{level:.0%} full, {size} max images'
        self.controls.status_panel.update_video_buffer_status(text)

    def _update_display_rate(self):
        # If it has been more than a second, re-calculate the display rate
        if (now := time()) - self._display_rate_last_time > 1:
            dt = now - self._display_rate_last_time
            rate = self._display_rate_counter / dt
            self._display_rate_last_time = now
            self._display_rate_counter = 0
            self._display_rate_last_rate = rate
            self.controls.status_panel.update_display_rate(f'{rate:.0f} updates/sec')
        else:
            # This is used to force the "..." to update
            self.controls.status_panel.update_display_rate(f'{self._display_rate_last_rate:.0f} updates/sec')

    def _update_beads_in_view(self):
        # Enabled?
        if not self.beads_in_view_on or self.beads_in_view_count is None:
            self.video_viewer.clear_crosshairs()
            return
        n = self.beads_in_view_count

        # Get latest n timepoints
        tracks = self.tracks_buffer.peak_unsorted()
        t = tracks[:, 0]
        unique_t = np.unique(t)
        top_n_t = unique_t[np.isfinite(unique_t)][-n:]

        # Get corresponding values
        try:
            mask = np.isin(t, top_n_t, assume_unique=False, kind='sort')
            x = tracks[mask, 1]
            y = tracks[mask, 2]

            # Calculate relative x & y
            nm_per_px = self.camera_type.nm_per_px / self.settings['magnification']
            x /= nm_per_px
            y /= nm_per_px

            # Plot points
            self.video_viewer.plot(x, y, self.beads_in_view_marker_size)
        except Exception as e:
            print(traceback.format_exc())

    @register_ipc_command(UpdateCameraSettingCommand)
    def update_camera_setting(self, name: str, value: str):
        self.controls.camera_panel.update_camera_setting(name, value)

    @register_ipc_command(UpdateVideoBufferPurgeCommand)
    def update_video_buffer_purge(self, t: float):
        self.controls.status_panel.update_video_buffer_purge(t)

    @register_ipc_command(UpdateScriptStatusCommand)
    def update_script_status(self, status: ScriptStatus):
        self.controls.script_panel.update_status(status)

    @register_ipc_command(UpdateScriptStepCommand)
    def update_script_step(self, current_step: int | None, total_steps: int, description: str | None):
        self.controls.script_panel.update_step(current_step, total_steps, description)

    @register_ipc_command(ShowMessageCommand)
    @register_script_command(ShowMessageCommand)
    def print(self, text: str, details: str | None = None):
        msg = QMessageBox(self.windows[0])
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("Information")
        msg.setText(text)
        if details:
            logger.info('%s: %s', text, details)
            msg.setDetailedText(details)
        else:
            logger.info('%s', text)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.show()

    @register_ipc_command(ShowErrorCommand)
    def show_error(self, text: str, details: str | None = None):
        msg = QMessageBox(self.windows[0])
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("Error")
        msg.setText(text)
        if details:
            logger.error('%s: %s', text, details)
            msg.setDetailedText(details)
        else:
            logger.error('%s', text)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.show()

    @register_ipc_command(SetAcquisitionOnCommand, delivery=Delivery.BROADCAST, target='ManagerProcessBase')
    def set_acquisition_on(self, value: bool):
        super().set_acquisition_on(value)
        checkbox = self.controls.acquisition_panel.acquisition_on_checkbox.checkbox
        checkbox.blockSignals(True) # to prevent a loop
        checkbox.setChecked(value)
        checkbox.blockSignals(False)

    @register_ipc_command(SetAcquisitionDirCommand, delivery=Delivery.BROADCAST, target='ManagerProcessBase')
    def set_acquisition_dir(self, value: str | None):
        super().set_acquisition_dir(value)
        textedit = self.controls.acquisition_panel.acquisition_dir_textedit
        textedit.blockSignals(True) # to prevent a loop
        textedit.setText(value or '')
        textedit.blockSignals(False)

    @register_ipc_command(SetAcquisitionDirOnCommand, delivery=Delivery.BROADCAST, target='ManagerProcessBase')
    def set_acquisition_dir_on(self, value: bool):
        super().set_acquisition_dir_on(value)
        checkbox = self.controls.acquisition_panel.acquisition_dir_on_checkbox.checkbox
        checkbox.blockSignals(True)  # to prevent a loop
        checkbox.setChecked(value)
        checkbox.blockSignals(False)
        self.controls.acquisition_panel.update_save_highlight(value)

    @register_ipc_command(SetAcquisitionModeCommand, delivery=Delivery.BROADCAST, target='ManagerProcessBase')
    def set_acquisition_mode(self, mode: AcquisitionMode):
        super().set_acquisition_mode(mode)
        combobox = self.controls.acquisition_panel.acquisition_mode_combobox
        combobox.blockSignals(True)  # to prevent a loop
        combobox.setCurrentText(mode)
        combobox.blockSignals(False)

    @register_ipc_command(UpdateXYLockEnabledCommand)
    def update_xy_lock_enabled(self, value: bool):
        self.controls.xy_lock_panel.update_enabled(value)

    @register_ipc_command(UpdateXYLockIntervalCommand)
    def update_xy_lock_interval(self, value: float):
        self.controls.xy_lock_panel.update_interval(value)

    @register_ipc_command(UpdateXYLockMaxCommand)
    def update_xy_lock_max(self, value: float):
        self.controls.xy_lock_panel.update_max(value)

    @register_ipc_command(UpdateXYLockWindowCommand)
    def update_xy_lock_window(self, value: int):
        self.controls.xy_lock_panel.update_window(value)

    @register_ipc_command(UpdateZLockEnabledCommand)
    def update_z_lock_enabled(self, value: bool):
        self.controls.z_lock_panel.update_enabled(value)

    @register_ipc_command(UpdateZLockBeadCommand)
    def update_z_lock_bead(self, value: int):
        self.controls.z_lock_panel.update_bead(value)

    @register_ipc_command(UpdateZLockTargetCommand)
    def update_z_lock_target(self, value: float):
        self.controls.z_lock_panel.update_target(value)

    @register_ipc_command(UpdateZLockIntervalCommand)
    def update_z_lock_interval(self, value: float):
        self.controls.z_lock_panel.update_interval(value)

    @register_ipc_command(UpdateZLockMaxCommand)
    def update_z_lock_max(self, value: float):
        self.controls.z_lock_panel.update_max(value)

    def request_zlut_file(self, filepath: str) -> None:
        if not filepath:
            return

        command = LoadZLUTCommand(filepath=filepath)
        self.send_ipc(command)

    def clear_zlut(self) -> None:
        command = UnloadZLUTCommand()
        self.send_ipc(command)

    def request_profile_length(self) -> None:
        self.send_ipc(RequestProfileLengthCommand())

    @register_ipc_command(UpdateZLUTMetadataCommand)
    def update_zlut_metadata(self,
                             filepath: str | None = None,
                             z_min: float | None = None,
                             z_max: float | None = None,
                             step_size: float | None = None,
                             profile_length: int | None = None) -> None:
        if self.controls is None:
            return

        panel = self.controls.zlut_panel
        panel.set_filepath(filepath)
        panel.update_metadata(z_min, z_max, step_size, profile_length)

    @register_ipc_command(ReportProfileLengthCommand)
    def report_profile_length(self, profile_length: int | None = None) -> None:
        print(f'Temporary development behavior: profile length = {profile_length}')

class LoadingWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        # Set up the window
        self.setWindowTitle('Loading...')
        self.setFixedSize(700, 300)
        self.setStyleSheet('background-color: white;')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)

    # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Layout
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Loading label
        self.label = QLabel('MagScope' + '\n\n' + 'loading ...')
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet('color: black; font-_count: 20px;')
        layout.addWidget(self.label)

        # Center the window on the screen
        frame_geometry = self.frameGeometry()
        center_point = self.screen().availableGeometry().center()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())

class AddColumnDropTarget(QFrame):
    """Drop target that creates a new column when a panel is dropped."""

    def __init__(self, controls: "Controls") -> None:
        super().__init__()
        self._controls = controls
        self._drag_active = False
        self.setObjectName("add_column_drop_target")
        self.setAcceptDrops(True)
        self.setMinimumWidth(300)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        layout.addStretch(1)
        label = QLabel("Drop here to create a new column")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addStretch(1)

        self._set_active(False)
        self.setVisible(False)

    def set_drag_active(self, active: bool) -> None:
        """Toggle visibility based on whether a panel is being dragged."""

        self._drag_active = active
        self._update_visibility()

    def refresh_visibility(self) -> None:
        self._update_visibility()

    def _update_visibility(self) -> None:
        should_show = self._drag_active and self._controls.has_room_for_new_column()
        self.setVisible(should_show)
        if not should_show:
            self._set_active(False)

    def _set_active(self, active: bool) -> None:
        color = "palette(highlight)" if active else "palette(midlight)"
        self.setStyleSheet(
            "#add_column_drop_target { border: 2px dashed %s; border-radius: 6px; }" % color
        )

    def _wrapper_from_event(self, event) -> PanelWrapper | None:
        manager = self._controls.layout_manager
        if manager is None:
            return None
        if not self._controls.has_room_for_new_column():
            return None
        mime_data = event.mimeData()
        if not mime_data.hasFormat(PANEL_MIME_TYPE):
            return None
        panel_id_bytes = mime_data.data(PANEL_MIME_TYPE)
        if panel_id_bytes.isEmpty():
            return None
        panel_id = bytes(panel_id_bytes).decode("utf-8")
        return manager.wrapper_for_id(panel_id)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._wrapper_from_event(event) is not None:
            self._set_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._wrapper_from_event(event) is not None:
            self._set_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        wrapper = self._wrapper_from_event(event)
        self._set_active(False)
        if wrapper is None:
            event.ignore()
            return
        if not self._controls.has_room_for_new_column():
            event.ignore()
            return
        self._controls.create_new_column_with_panel(wrapper)
        event.acceptProposedAction()

class Controls(QWidget):
    """Container widget hosting draggable, persistent control panels."""

    LAYOUT_SETTINGS_GROUP = "controls/layout"

    def __init__(self, manager: UIManager):
        super().__init__()
        self.manager = manager
        self.panels: dict[str, ControlPanelBase | QWidget] = {}

        self._settings = QSettings("MagScope", "MagScope")

        layout = QHBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)
        self._columns_layout = layout

        self._column_scrolls: dict[str, QScrollArea] = {}
        self._column_prefix = "column"
        self._column_counter = 1
        self._base_columns = {"left"}
        self._suppress_layout_callback = False

        self.layout_manager = PanelLayoutManager(
            self._settings,
            self.LAYOUT_SETTINGS_GROUP,
            [],
            on_layout_changed=self._on_layout_changed,
            on_drag_active_changed=self._on_drag_active_changed,
        )

        self._add_column_target = AddColumnDropTarget(self)
        layout.addWidget(self._add_column_target)
        layout.addStretch(1)

        stored_layout = self.layout_manager.stored_layout()
        self._update_column_counter(stored_layout.keys())

        self._add_column("left", pinned_ids={"HelpPanel", "ResetPanel"}, index=0)
        for name in stored_layout.keys():
            if name in self.layout_manager.columns:
                continue
            self._add_column(name)
        if "right" not in self.layout_manager.columns and len(self.layout_manager.columns) < 2:
            self._add_column("right")

        # Instantiate standard panels
        self.help_panel = HelpPanel(self.manager)
        self.reset_panel = ResetPanel(self.manager)
        self.settings_panel = MagScopeSettingsPanel(self.manager)
        self.acquisition_panel = AcquisitionPanel(self.manager)
        self.bead_selection_panel = BeadSelectionPanel(self.manager)
        self.camera_panel = CameraPanel(self.manager)
        self.histogram_panel = HistogramPanel(self.manager)
        self.tracking_options_panel = TrackingOptionsPanel(self.manager)
        self.plot_settings_panel = PlotSettingsPanel(self.manager)
        self.profile_panel = ProfilePanel(self.manager)
        self.script_panel = ScriptPanel(self.manager)
        self.status_panel = StatusPanel(self.manager)
        self.xy_lock_panel = XYLockPanel(self.manager)
        self.z_lock_panel = ZLockPanel(self.manager)
        self.zlut_panel = ZLUTPanel(self.manager)
        self.z_lut_generation_panel = ZLUTGenerationPanel(self.manager)

        self.zlut_panel.zlut_file_selected.connect(self.manager.request_zlut_file)
        self.zlut_panel.zlut_clear_requested.connect(self.manager.clear_zlut)

        definitions: list[tuple[str, QWidget, str, bool]] = [
            ("HelpPanel", self.help_panel, "left", False),
            ("ResetPanel", self.reset_panel, "left", False),
            ("MagScopeSettingsPanel", self.settings_panel, "left", True),
            ("StatusPanel", self.status_panel, "left", True),
            ("BeadSelectionPanel", self.bead_selection_panel, "left", True),
            ("CameraPanel", self.camera_panel, "left", True),
            ("AcquisitionPanel", self.acquisition_panel, "left", True),
            ("TrackingOptionsPanel", self.tracking_options_panel, "left", True),
            ("HistogramPanel", self.histogram_panel, "left", True),
            ("ProfilePanel", self.profile_panel, "left", True),
            ("PlotSettingsPanel", self.plot_settings_panel, "right", True),
            ("ZLUTPanel", self.zlut_panel, "right", True),
            ("ZLUTGenerationPanel", self.z_lut_generation_panel, "right", True),
            ("ScriptPanel", self.script_panel, "right", True),
            ("XYLockPanel", self.xy_lock_panel, "right", True),
            ("ZLockPanel", self.z_lock_panel, "right", True),
        ]

        column_names = list(self.layout_manager.columns.keys())
        fallback_column = column_names[0]

        for panel_id, widget, column_name, draggable in definitions:
            self.panels[panel_id] = widget
            target_column = column_name if column_name in self.layout_manager.columns else fallback_column
            self.layout_manager.register_panel(
                panel_id,
                widget,
                target_column,
                draggable=draggable,
            )

        column_names = list(self.layout_manager.columns.keys())

        for control_factory, column in self.manager.controls_to_add:
            widget = control_factory(self.manager)
            panel_id = widget.__class__.__name__
            if isinstance(column, int):
                index = min(max(column, 0), len(column_names) - 1)
                column_name = column_names[index]
            else:
                column_name = str(column)
                if column_name not in self.layout_manager.columns:
                    column_name = column_names[0]
            self.panels[panel_id] = widget
            self.layout_manager.register_panel(panel_id, widget, column_name)

        self.layout_manager.restore_layout()
        self._prune_empty_columns()

    @property
    def settings(self):
        return self.manager.settings

    @settings.setter
    def settings(self, value):
        raise AttributeError("Read-only attribute.")

    def _update_column_counter(self, column_names: Iterable[str]) -> None:
        prefix = f"{self._column_prefix}_"
        for name in column_names:
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix) :]
            try:
                value = int(suffix)
            except ValueError:
                continue
            if value >= self._column_counter:
                self._column_counter = value + 1

    def _layout_insert_index(self, name: str) -> int:
        drop_index = self._columns_layout.indexOf(self._add_column_target)
        if drop_index == -1:
            drop_index = self._columns_layout.count()
        column_names = list(self.layout_manager.columns.keys())
        target_index = column_names.index(name)
        count_before = sum(
            1 for existing in column_names[:target_index] if existing in self._column_scrolls
        )
        return min(drop_index, count_before)

    def _add_column(
        self,
        name: str,
        *,
        pinned_ids: Iterable[str] | None = None,
        index: int | None = None,
    ) -> ReorderableColumn:
        if name in self.layout_manager.columns:
            column = self.layout_manager.columns[name]
        else:
            column = ReorderableColumn(name, pinned_ids=pinned_ids)
            column.setFixedWidth(300)
            self.layout_manager.add_column(name, column, index=index)

        if name not in self._column_scrolls:
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(column)
            scroll.setFixedWidth(320)
            insert_index = self._layout_insert_index(name)
            self._columns_layout.insertWidget(insert_index, scroll)
            self._column_scrolls[name] = scroll
            self._add_column_target.refresh_visibility()
        return column

    def create_new_column_with_panel(self, wrapper: PanelWrapper) -> None:
        name = self._generate_column_name()
        column = self._add_column(name)
        column.add_panel(wrapper)
        wrapper.mark_drop_accepted()
        self.layout_manager.layout_changed()

    def _generate_column_name(self) -> str:
        while True:
            name = f"{self._column_prefix}_{self._column_counter}"
            self._column_counter += 1
            if name not in self.layout_manager.columns:
                return name

    def _on_layout_changed(self, _layout: dict[str, list[str]]) -> None:
        if self._suppress_layout_callback:
            return
        self._prune_empty_columns()

    def _on_drag_active_changed(self, active: bool) -> None:
        self._add_column_target.set_drag_active(active)

    def _prune_empty_columns(self) -> None:
        removable = [
            name
            for name, column in list(self.layout_manager.columns.items())
            if name not in self._base_columns and not column.panels()
        ]
        for name in removable:
            self._remove_column(name)

    def _remove_column(self, name: str) -> None:
        scroll = self._column_scrolls.pop(name, None)
        if scroll is not None:
            self._columns_layout.removeWidget(scroll)
            scroll.hide()
            scroll.deleteLater()
        column = self.layout_manager.columns.get(name)
        if column is None:
            return
        column.clear_placeholder()
        column.hide()
        column.setParent(None)
        column.deleteLater()
        self._suppress_layout_callback = True
        try:
            self.layout_manager.remove_column(name)
        finally:
            self._suppress_layout_callback = False
        self.layout_manager.layout_changed()
        self._add_column_target.refresh_visibility()

    def reset_to_defaults(self) -> None:
        """Restore panel visibility, order, and columns to defaults."""

        settings = QSettings("MagScope", "MagScope")
        settings.beginGroup(self.LAYOUT_SETTINGS_GROUP)
        settings.remove("")
        settings.endGroup()

        for panel in self.panels.values():
            groupbox = getattr(panel, "groupbox", None)
            if isinstance(groupbox, CollapsibleGroupBox):
                settings.remove(groupbox.settings_key)
                groupbox.reset_to_default()

        for column in list(self.layout_manager.columns.values()):
            column.clear_placeholder()
            column.clear_panels()

        for scroll in list(self._column_scrolls.values()):
            self._columns_layout.removeWidget(scroll)
            scroll.hide()
            scroll.deleteLater()
        self._column_scrolls.clear()

        self.layout_manager.columns = OrderedDict()
        self._column_counter = 1

        self._add_column("left", pinned_ids={"HelpPanel", "ResetPanel"}, index=0)
        self._add_column("right")

        for panel_id in self.layout_manager._default_order:
            wrapper = self.layout_manager.wrapper_for_id(panel_id)
            if wrapper is None:
                continue
            column_name = self.layout_manager._default_columns.get(panel_id, "left")
            if column_name not in self.layout_manager.columns:
                self._add_column(column_name)
            column = self.layout_manager.columns[column_name]
            column.add_panel(wrapper)

        self.layout_manager.layout_changed()

    def has_room_for_new_column(self) -> bool:
        """Return True if a new column can fit beside the existing ones."""

        layout_width = self._columns_layout.contentsRect().width()
        if layout_width <= 0:
            layout_width = self.width()

        spacing = max(0, self._columns_layout.spacing())
        visible_scrolls = [scroll for scroll in self._column_scrolls.values() if scroll.isVisible()]
        if not visible_scrolls:
            return layout_width >= self._add_column_target.minimumWidth()

        column_width = visible_scrolls[0].width() or visible_scrolls[0].sizeHint().width()
        required_width = (len(visible_scrolls) + 1) * (column_width + spacing)
        return layout_width >= required_width

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._add_column_target.refresh_visibility()
