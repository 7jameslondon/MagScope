from collections import OrderedDict
from importlib import resources
import json
from math import floor, ceil
import os
import sys
from time import time
import traceback
from typing import Any, Callable, Iterable, Mapping
from warnings import warn

import numpy as np
from PyQt6.QtCore import (
    QByteArray,
    QEvent,
    QObject,
    QMimeData,
    QPoint,
    QRectF,
    QSettings,
    QStringListModel,
    Qt,
    QThread,
    QTimer,
    QUrl,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QDrag,
    QFont,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPalette,
    QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCompleter,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QLayout,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from magscope._logging import get_logger
from magscope.auto_bead_selection import copy_latest_image, roi_overlaps
from magscope.datatypes import DatasetNotReadyError, VideoBuffer, ZLUTSweepDataset
from magscope.ipc import Delivery, register_ipc_command
from magscope.ipc_commands import *
from magscope.ui.auto_bead_selection_dialog import AutoBeadSelectionDialog
from magscope.ui.controls import (
    AcquisitionPanel,
    AllanDeviationPanel,
    BeadSelectionPanel,
    CameraPanel,
    ControlPanelBase,
    CurrentZLUTDialog,
    HistogramPanel,
    PlotSettingsPanel,
    PreferencesDialog,
    ProfilePanel,
    ScriptPanel,
    StatusPanel,
    MagScopeSettingsPanel,
    TrackingOptionsPanel,
    XYLockPanel,
    ZLUTGenerationDialog,
    ZLUTGenerationSetupDialog,
    ZLockPanel,
    has_tweezepy_support,
)
from magscope.ui.panel_layout import (
    PANEL_MIME_TYPE,
    PanelLayoutManager,
    PanelWrapper,
    ReorderableColumn,
)
from magscope.ui.plots import PlotWorker, TimeSeriesPlotBase
from magscope.ui.search import (
    MenuActionTarget,
    PanelControlTarget,
    PreferencesSettingTarget,
    PreferencesWidgetTarget,
    SearchHighlighter,
    SearchRegistry,
    SearchTarget,
    normalize_search_text,
)
from magscope.ui.theme import APP_BACKGROUND_COLOR, get_accent_color, set_accent_color
from magscope.ui.video_viewer import VideoViewer
from magscope.ui.widgets import BeadGraphic, CollapsibleGroupBox, ResizableLabel
from magscope.processes import ManagerProcessBase
from magscope.scripting import ScriptStatus, register_script_command
from magscope.settings import (
    GUI_ACCENT_COLOR_SETTING,
    MagScopeSettings,
    tracking_options_from_qsettings,
)
from magscope.utils import AcquisitionMode, numpy_type_to_qt_image_type

logger = get_logger("ui.ui")


VIEWER_DOCK_SEPARATOR_HOVER_DELAY_MS = 500
VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY = "viewerDockSeparatorHoverReady"


def _set_widget_background(widget: QWidget, color_name: str) -> None:
    palette = widget.palette()
    color = QColor(color_name)
    palette.setColor(QPalette.ColorRole.Window, color)
    palette.setColor(QPalette.ColorRole.Base, color)
    widget.setPalette(palette)
    widget.setAutoFillBackground(True)


class _StartupReadyWindow(QMainWindow):
    def __init__(self, on_ready: Callable[[], None]):
        super().__init__()
        self._on_ready = on_ready
        self._startup_ready_scheduled = False
        self._startup_shown = False

    def event(self, event):
        event_type = event.type()
        if event_type == QEvent.Type.Show:
            self._startup_shown = True
            self._maybe_schedule_startup_ready(after_paint=False)
        elif event_type == QEvent.Type.Paint:
            self._maybe_schedule_startup_ready(after_paint=True)
        return super().event(event)

    def _maybe_schedule_startup_ready(self, *, after_paint: bool) -> None:
        if self._startup_ready_scheduled or not self._startup_shown or not self.isVisible():
            return

        platform_name = QGuiApplication.platformName()
        window_handle = self.windowHandle()
        is_exposed = window_handle is not None and window_handle.isExposed()
        if not (is_exposed or after_paint or platform_name == "offscreen"):
            return

        self._startup_ready_scheduled = True
        QTimer.singleShot(0, self._on_ready)


class _DockSeparatorHoverDelayFilter(QObject):
    def __init__(self, window: QMainWindow):
        super().__init__(window)
        self._window = window
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(VIEWER_DOCK_SEPARATOR_HOVER_DELAY_MS)
        self._timer.timeout.connect(lambda: self._set_hover_ready(True))
        self._set_hover_ready(False)

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is self._window:
            event_type = event.type()
            if event_type in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):
                self._set_hover_ready(False)
                self._timer.start()
            elif event_type in (
                QEvent.Type.Leave,
                QEvent.Type.HoverLeave,
                QEvent.Type.Hide,
                QEvent.Type.WindowDeactivate,
            ):
                self._timer.stop()
                self._set_hover_ready(False)
        return super().eventFilter(watched, event)

    def _set_hover_ready(self, ready: bool) -> None:
        if self._window.property(VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY) == ready:
            return

        self._window.setProperty(VIEWER_DOCK_SEPARATOR_HOVER_READY_PROPERTY, ready)
        self._window.style().unpolish(self._window)
        self._window.style().polish(self._window)
        self._window.update()


class UIManager(ManagerProcessBase):
    _material_symbols_font_family: str | None = None
    VIEWER_LAYOUT_STATE_VERSION = 1
    VIEWER_GEOMETRY_SETTINGS_KEY = "viewer/main_window_geometry"
    VIEWER_DOCK_STATE_SETTINGS_KEY = "viewer/dock_state"
    _ZLUT_TRACKING_ACQUISITION_MODES = {
        AcquisitionMode.TRACK,
        AcquisitionMode.TRACK_AND_VIDEO_ROIS,
        AcquisitionMode.TRACK_AND_VIDEO_FULL,
    }

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
        self.camera_dock: QDockWidget | None = None
        self.camera_dock_header: QWidget | None = None
        self.bead_toolbar: QWidget | None = None
        self.bead_instructions_button: QPushButton | None = None
        self.bead_roi_size_label: QLabel | None = None
        self.bead_total_count_label: QLabel | None = None
        self.bead_next_id_label: QLabel | None = None
        self.bead_reassign_ids_button: QPushButton | None = None
        self.bead_remove_all_button: QPushButton | None = None
        self._display_rate_counter: int = 0
        self._display_rate_last_time: float = time()
        self._display_rate_last_rate: float = 0
        self.plot_worker: PlotWorker
        self.plot_thread: QThread
        self.plots_widget: QLabel
        self.plots_dock: QDockWidget | None = None
        self.plots_dock_header: QWidget | None = None
        self.plots_to_add: list[TimeSeriesPlotBase] = []
        self._preferences_dialog: PreferencesDialog | None = None
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
        self._auto_bead_selection_dialog: AutoBeadSelectionDialog | None = None
        self._startup_ready_sent = False
        self._zlut_generation_dialog: ZLUTGenerationDialog | None = None
        self._zlut_generation_setup_dialog: ZLUTGenerationSetupDialog | None = None
        self._current_zlut_dialog: CurrentZLUTDialog | None = None
        self._current_zlut_filepath: str | None = None
        self._current_zlut_metadata: dict[str, float | int | None] = {
            'z_min': None,
            'z_max': None,
            'step_size': None,
            'profile_length': None,
        }
        self._zlut_generation_phase = 'idle'
        self._zlut_generation_z_axis_min_nm: float | None = None
        self._zlut_generation_z_axis_max_nm: float | None = None
        self._zlut_generation_z_axis_descending = False
        self._zlut_sweep_dataset: ZLUTSweepDataset | None = None
        self._zlut_evaluation_bead_ids: list[int] = []
        self._zlut_evaluation_selected_bead_id: int | None = None
        self._zlut_preview_last_poll: float = 0.0
        self._shutdown_complete = False
        self._search_box: QLineEdit | None = None
        self._search_status_label: QLabel | None = None
        self._search_status_timer: QTimer | None = None
        self._menu_row: QWidget | None = None
        self._menu_bar: QMenuBar | None = None
        self._layout_menu: QMenu | None = None
        self._auto_bead_selection_action: QAction | None = None
        self._zlut_menu: QMenu | None = None
        self._new_zlut_action: QAction | None = None
        self._load_zlut_action: QAction | None = None
        self._unload_zlut_action: QAction | None = None
        self._show_current_zlut_action: QAction | None = None
        self._menus: dict[str, QMenu] = {}
        self._search_shortcuts: list[QShortcut] = []
        self._search_registry = SearchRegistry()
        self._search_highlighter = SearchHighlighter()

    @classmethod
    def _material_symbols_font(cls, point_size: int = 18) -> QFont:
        if cls._material_symbols_font_family is None:
            font_resource = resources.files("magscope").joinpath(
                "assets/MaterialSymbolsRounded.ttf"
            )
            font_id = QFontDatabase.addApplicationFont(str(font_resource))
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            cls._material_symbols_font_family = families[0] if families else "Material Symbols Rounded"

        font = QFont(cls._material_symbols_font_family, point_size)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        return font

    @staticmethod
    def _material_symbols_filled_stylesheet() -> str:
        return """
            QToolButton {
                border: none;
                background: transparent;
                color: #d0d0d0;
                padding: 0px;
            }
            QToolButton:hover {
                color: #9a9a9a;
            }
            QToolButton:pressed {
                color: #606060;
            }
        """

    @staticmethod
    def _viewer_dock_separator_stylesheet() -> str:
        accent_color = get_accent_color()
        return f"""
            QMainWindow::separator {{
                background: transparent;
                width: 5px;
                height: 5px;
            }}
            QMainWindow::separator:vertical {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 transparent,
                    stop: 0.4 transparent,
                    stop: 0.4 #808080,
                    stop: 0.6 #808080,
                    stop: 0.6 transparent,
                    stop: 1 transparent
                );
            }}
            QMainWindow::separator:horizontal {{
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 0, y2: 1,
                    stop: 0 transparent,
                    stop: 0.4 transparent,
                    stop: 0.4 #808080,
                    stop: 0.6 #808080,
                    stop: 0.6 transparent,
                    stop: 1 transparent
                );
            }}
            QMainWindow[viewerDockSeparatorHoverReady="true"]::separator:hover {{
                background: {accent_color};
            }}
        """

    @staticmethod
    def _install_viewer_dock_separator_hover_delay(window: QMainWindow) -> None:
        if getattr(window, "_viewer_dock_separator_hover_delay_filter", None) is not None:
            return

        window.setMouseTracking(True)
        window.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        hover_filter = _DockSeparatorHoverDelayFilter(window)
        window.installEventFilter(hover_filter)
        window._viewer_dock_separator_hover_delay_filter = hover_filter

    def _apply_viewer_dock_separator_style(self, window: QMainWindow) -> None:
        self._install_viewer_dock_separator_hover_delay(window)
        separator_style = self._viewer_dock_separator_stylesheet().strip()
        existing_style = window.styleSheet().strip()
        previous_style = getattr(window, "_viewer_dock_separator_stylesheet", "")
        if previous_style and previous_style in existing_style:
            existing_style = existing_style.replace(previous_style, "").strip()
        if separator_style in existing_style:
            return
        window.setStyleSheet(
            f"{existing_style}\n\n{separator_style}" if existing_style else separator_style
        )
        window._viewer_dock_separator_stylesheet = separator_style

    @staticmethod
    def _zlut_requested_sweep_edges(
        z_axis_min_nm: float | None,
        z_axis_max_nm: float | None,
        n_steps: int,
    ) -> tuple[float | None, float | None]:
        if z_axis_min_nm is None or z_axis_max_nm is None or int(n_steps) <= 0:
            return None, None

        if int(n_steps) == 1:
            return float(z_axis_min_nm), float(z_axis_max_nm)

        step_spacing = float(z_axis_max_nm - z_axis_min_nm) / float(int(n_steps) - 1)
        half_step = 0.5 * step_spacing
        return float(z_axis_min_nm - half_step), float(z_axis_max_nm + half_step)

    @staticmethod
    def _build_zlut_preview_payload(
        preview_snapshot: dict[str, object],
        *,
        z_axis_min_nm: float | None,
        z_axis_max_nm: float | None,
        z_axis_descending: bool,
    ) -> dict[str, object]:
        state = int(preview_snapshot['state'])
        n_steps = int(preview_snapshot['n_steps'])
        profiles_per_bead = int(preview_snapshot['profiles_per_bead'])
        x_axis_min, x_axis_max = UIManager._zlut_requested_sweep_edges(
            z_axis_min_nm,
            z_axis_max_nm,
            n_steps,
        )

        preview_image = None
        image_x_min = None
        image_x_max = None
        mode = 'Raw sweep'
        profiles = np.asarray(preview_snapshot['profiles'], dtype=np.float64)

        if profiles.size > 0:
            step_indices = np.asarray(preview_snapshot['step_indices'], dtype=np.uint32)
            selected_motor_z_values = np.asarray(
                preview_snapshot['motor_z_values'],
                dtype=np.float64,
            )
            if state == ZLUTSweepDataset.STATE_COMPLETE:
                mode = 'Averaged sweep'
                unique_steps = np.unique(step_indices)
                averaged_profiles = []
                averaged_z_positions = []
                for step_index in unique_steps:
                    step_profiles = profiles[step_indices == step_index]
                    step_motor_z_values = selected_motor_z_values[step_indices == step_index]
                    if step_profiles.size == 0:
                        continue
                    averaged_profiles.append(np.nanmean(step_profiles, axis=0))
                    averaged_z_positions.append(float(np.nanmean(step_motor_z_values)))
                if averaged_profiles:
                    averaged_profiles_array = np.asarray(averaged_profiles, dtype=np.float64)
                    averaged_z_positions_array = np.asarray(averaged_z_positions, dtype=np.float64)
                    order = np.argsort(averaged_z_positions_array)
                    preview_image = averaged_profiles_array[order].T
                    image_x_min = x_axis_min
                    image_x_max = x_axis_max
            else:
                slot_indices = np.zeros((profiles.shape[0],), dtype=np.int64)
                per_step_capture_counts: dict[int, int] = {}
                for row_index, step_index in enumerate(step_indices):
                    step_index_int = int(step_index)
                    within_step_index = per_step_capture_counts.get(step_index_int, 0)
                    per_step_capture_counts[step_index_int] = within_step_index + 1
                    if z_axis_descending:
                        step_rank = n_steps - 1 - step_index_int
                    else:
                        step_rank = step_index_int
                    slot_indices[row_index] = step_rank * profiles_per_bead + within_step_index

                sorted_order = np.argsort(slot_indices, kind='stable')
                sorted_slot_indices = np.asarray(slot_indices[sorted_order], dtype=np.int64)
                sorted_profiles = np.asarray(profiles[sorted_order], dtype=np.float64)
                if x_axis_min is not None and x_axis_max is not None and sorted_profiles.shape[0] > 0:
                    total_slots = n_steps * profiles_per_bead
                    if total_slots > 0:
                        slot_width = float(x_axis_max - x_axis_min) / float(total_slots)
                        min_slot = int(np.min(sorted_slot_indices))
                        max_slot = int(np.max(sorted_slot_indices))
                        sparse_width = max_slot - min_slot + 1
                        preview_image = np.full(
                            (sorted_profiles.shape[1], sparse_width),
                            np.nan,
                            dtype=np.float64,
                        )
                        for profile_row, slot_index in zip(
                            sorted_profiles,
                            sorted_slot_indices,
                            strict=False,
                        ):
                            preview_image[:, int(slot_index - min_slot)] = profile_row
                        image_x_min = float(x_axis_min + min_slot * slot_width)
                        image_x_max = float(x_axis_min + (max_slot + 1) * slot_width)
                elif sorted_profiles.shape[0] > 0:
                    preview_image = sorted_profiles.T

        return {
            'state': state,
            'count': int(preview_snapshot['count']),
            'capacity': int(preview_snapshot['capacity']),
            'n_steps': n_steps,
            'n_beads': int(preview_snapshot['n_beads']),
            'profiles_per_bead': profiles_per_bead,
            'profile_length': int(preview_snapshot['profile_length']),
            'preview_image': preview_image,
            'selected_bead_id': preview_snapshot['selected_bead_id'],
            'mode': mode,
            'motor_z_min': preview_snapshot['motor_z_min'],
            'motor_z_max': preview_snapshot['motor_z_max'],
            'expected_capture_count': n_steps * profiles_per_bead,
            'x_axis_label': 'Z Position (nm)',
            'x_axis_min': x_axis_min,
            'x_axis_max': x_axis_max,
            'image_x_min': image_x_min,
            'image_x_max': image_x_max,
        }

    def setup(self):
        self.qt_app = QApplication.instance()
        if not self.qt_app:
            self.qt_app = QApplication(sys.argv)
        QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
        palette = self.qt_app.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(APP_BACKGROUND_COLOR))
        self.qt_app.setPalette(palette)
        self._apply_accent_color(self._current_accent_color())

        if self.settings is not None:
            self._last_applied_roi = self.settings["ROI"]

        # Create the live plots in a separate thread (but dont start it)
        self.plots_widget = ResizableLabel(ignore_pixmap_size_hint=True)
        self.plots_widget.setScaledContents(True)
        self.plots_widget.setMinimumSize(1, 1)
        self.plots_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.plots_thread = QThread()
        self.plot_worker = PlotWorker()
        for plot in self.plots_to_add:
            self.plot_worker.add_plot(plot)
        self.plot_worker.set_locks(self.locks)
        self.plot_worker.setup()

        # Create controls panel
        self.controls = Controls(self)
        if self._command_registry is not None and self._pipe is not None:
            self.send_ipc(UpdateTrackingOptionsCommand(value=tracking_options_from_qsettings()))

        # Create the video viewer
        self.video_viewer = VideoViewer()
        self._refresh_bead_overlay()

        # Finally start the live plots
        self.plot_worker.moveToThread(self.plots_thread)
        self.plots_thread.started.connect(self.plot_worker.run)  # noqa
        self.plot_worker.image_signal.connect(self._set_plot_image)
        self.plots_widget.resized.connect(self.update_plot_figure_size)
        self.plots_thread.start(QThread.Priority.LowPriority)

        # Create the layouts for each window
        self.create_central_widgets()

        # Create the main window. Viewer panes are dock widgets owned by this window.
        window = _StartupReadyWindow(self._notify_startup_ready)
        window.setWindowTitle("MagScope")
        screen = QApplication.screens()[0]
        geometry = screen.geometry()
        window.setGeometry(
            geometry.x(), geometry.y(), geometry.width(), geometry.height()
        )
        window.setMinimumWidth(300)
        window.setMinimumHeight(300)
        window.closeEvent = lambda _, w=window: self.quit()
        window.setCentralWidget(self.central_widgets[0])
        self.windows.append(window)
        self._create_viewer_docks(window)
        self._create_preferences_menu_action(window)
        self._create_view_menu(window)
        self._create_tools_menu(window)
        self._create_zlut_menu(window)
        self._create_help_menu_action(window)
        self._create_search_menu_widget(window)
        self._apply_default_viewer_layout()
        if self._restore_viewer_layout():
            window.show()
        else:
            window.showMaximized()

        self._show_settings_persistence_warning_if_needed()

        # Connect the video viewer
        self.video_viewer.coordinatesChanged.connect(self.update_view_coords)
        self.video_viewer.sceneClicked.connect(self.callback_view_clicked)

        # Timer
        self._timer = QTimer(self.qt_app)
        self._timer.timeout.connect(self._main_loop_tick)  # noqa
        self._timer.setInterval(0)
        self._timer.start()

        # Timer - Video Display
        self._timer_video_view = QTimer(self.qt_app)
        self._timer_video_view.timeout.connect(self._update_view_and_hist_tick)
        self._timer_video_view.setInterval(25)
        self._timer_video_view.start()

        # Start app
        self._running = True
        self.qt_app.exec()

    def _notify_startup_ready(self) -> None:
        if self._startup_ready_sent:
            return
        if self._command_registry is None or self._pipe is None or self._magscope_quitting is None:
            return

        self._startup_ready_sent = True
        self.send_ipc(StartupReadyCommand(process_name=self.name))

    @register_ipc_command(
        SetSettingsCommand, delivery=Delivery.BROADCAST, target="ManagerProcessBase"
    )
    def set_settings(self, settings: MagScopeSettings):
        """Apply new settings and clear beads if the ROI size changed."""

        previous_roi = self._last_applied_roi
        super().set_settings(settings)
        self._apply_accent_color(self._current_accent_color())
        self._show_settings_persistence_warning_if_needed()

        new_roi = self.settings["ROI"]
        if previous_roi is not None and new_roi != previous_roi:
            self.clear_beads()

        self._last_applied_roi = new_roi
        self._update_roi_labels(new_roi)

    def _current_accent_color(self) -> str:
        if self.settings is None:
            return get_accent_color()
        try:
            return self.settings[GUI_ACCENT_COLOR_SETTING]
        except (KeyError, TypeError):
            return get_accent_color()

    def _apply_accent_color(self, color: str) -> None:
        accent_color = set_accent_color(color)
        if getattr(self, 'qt_app', None) is not None:
            palette = self.qt_app.palette()
            palette.setColor(QPalette.ColorRole.Highlight, QColor(accent_color))
            palette.setColor(QPalette.ColorRole.Link, QColor(accent_color))
            accent_role = getattr(QPalette.ColorRole, "Accent", None)
            if accent_role is not None:
                palette.setColor(accent_role, QColor(accent_color))
            self.qt_app.setPalette(palette)
        for window in getattr(self, 'windows', []):
            if isinstance(window, QMainWindow):
                self._apply_viewer_dock_separator_style(window)

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
        if hasattr(self, 'plot_worker') and self.plot_worker is not None:
            self.plot_worker.figure_size_signal.emit(w, h)

    def _set_plot_image(self, img: QImage) -> None:
        if self.plots_widget is None:
            return
        self.plots_widget.setPixmap(QPixmap.fromImage(img))

    @staticmethod
    def _disconnect_signal(signal, callback) -> None:
        try:
            signal.disconnect(callback)
        except (RuntimeError, TypeError):
            pass

    @staticmethod
    def _stop_timer(timer: QTimer | None) -> None:
        if timer is None:
            return
        try:
            timer.stop()
        except RuntimeError:
            pass
        try:
            timer.deleteLater()
        except RuntimeError:
            pass

    @staticmethod
    def _close_widget(widget: QWidget | None) -> None:
        if widget is None:
            return
        try:
            widget.close()
        except (AttributeError, RuntimeError):
            pass
        try:
            widget.deleteLater()
        except (AttributeError, RuntimeError):
            pass

    def _shutdown_plot_worker(self) -> None:
        plot_worker = getattr(self, 'plot_worker', None)
        plots_thread = getattr(self, 'plots_thread', None)
        plots_widget = getattr(self, 'plots_widget', None)

        if plot_worker is None:
            return

        if plots_widget is not None:
            self._disconnect_signal(plot_worker.image_signal, self._set_plot_image)
            self._disconnect_signal(plots_widget.resized, self.update_plot_figure_size)

        stop = getattr(plot_worker, '_stop', None)
        if callable(stop):
            stop()

        if plots_thread is not None:
            try:
                plots_thread.quit()
            except RuntimeError:
                pass
            try:
                plots_thread.wait()
            except RuntimeError:
                pass
            try:
                plots_thread.deleteLater()
            except RuntimeError:
                pass

        dispose = getattr(plot_worker, 'dispose', None)
        if callable(dispose):
            dispose()

        self.plot_worker = None
        self.plots_thread = None

    def quit(self):
        if self._shutdown_complete or self._quitting.is_set():
            return

        can_use_process_quit = (
            self._command_registry is not None
            and hasattr(self._command_registry, 'route_for')
            and (self._pipe is None or hasattr(self._pipe, 'close'))
            and (self._magscope_quitting is None or hasattr(self._magscope_quitting, 'is_set'))
        )

        if not can_use_process_quit:
            self._quitting.set()
            self._running = False
        else:
            super().quit()

        self._running = False
        self._stop_timer(self._timer)
        self._timer = None
        self._stop_timer(self._timer_video_view)
        self._timer_video_view = None

        if self.video_viewer is not None:
            coordinates_changed = getattr(self.video_viewer, 'coordinatesChanged', None)
            if coordinates_changed is not None:
                self._disconnect_signal(coordinates_changed, self.update_view_coords)
            scene_clicked = getattr(self.video_viewer, 'sceneClicked', None)
            if scene_clicked is not None:
                self._disconnect_signal(scene_clicked, self.callback_view_clicked)

        self._shutdown_plot_worker()

        if self._auto_bead_selection_dialog is not None:
            force_close = getattr(self._auto_bead_selection_dialog, 'force_close', None)
            if callable(force_close):
                force_close()
            self._auto_bead_selection_dialog = None

        self._detach_zlut_sweep_dataset()
        self._zlut_generation_phase = 'idle'
        self._zlut_generation_z_axis_min_nm = None
        self._zlut_generation_z_axis_max_nm = None
        self._zlut_generation_z_axis_descending = False
        self._zlut_evaluation_bead_ids = []
        self._zlut_evaluation_selected_bead_id = None
        if self._zlut_generation_dialog is not None:
            force_close = getattr(self._zlut_generation_dialog, 'force_close', None)
            if callable(force_close):
                force_close()
            self._zlut_generation_dialog = None

        self._save_viewer_layout()
        self._search_highlighter.clear()
        if self._search_status_timer is not None:
            try:
                self._search_status_timer.stop()
            except RuntimeError:
                pass
        self._search_status_timer = None
        self._search_status_label = None

        for window in self.windows:
            self._close_widget(window)
        self.windows = []
        self.camera_dock = None
        self.plots_dock = None

        for central_widget in self.central_widgets:
            self._close_widget(central_widget)
        self.central_widgets = []
        self.central_layouts = []

        self._close_widget(self.controls)
        self.controls = None
        self._close_widget(self.video_viewer)
        self.video_viewer = None
        self._close_widget(getattr(self, 'plots_widget', None))
        self.plots_widget = None

        if self.qt_app is not None:
            self.qt_app.quit()

        self._shutdown_complete = True

    def do_main_loop(self):
        # Because the UIManager is a special case with a GUI
        # the main loop is actually called by a timer, not the
        # run method of it's super()

        if self._running:
            self._update_display_rate()
            self.update_video_buffer_status()
            self.update_video_processors_status()
            self.controls.profile_panel.update_plot()
            self._update_zlut_generation_dialog()
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
        self._update_bead_count_label()
        self._update_auto_bead_selection_action_state()

    def _update_auto_bead_selection_action_state(self) -> None:
        action = self._auto_bead_selection_action
        if action is None:
            return
        action.setEnabled(self._can_start_auto_bead_selection())

    def _can_start_auto_bead_selection(self) -> bool:
        return (
            self.controls is not None
            and self.video_viewer is not None
            and self.video_buffer is not None
            and self._auto_bead_selection_dialog is None
            and self._pending_bead_add_id is None
            and not self._current_scene_rect().isNull()
        )

    def _snapshot_recent_image(self) -> np.ndarray | None:
        if self.video_buffer is None:
            return None
        # Intentionally use peak_image() as a lightweight snapshot. It does not
        # verify that a frame has been written yet, but by the time a user
        # starts auto bead selection the buffer is expected to already contain
        # a recent frame.
        _index, image_bytes = self.video_buffer.peak_image()
        return copy_latest_image(
            image_bytes,
            self.video_buffer.image_shape,
            self.video_buffer.dtype,
        )

    def _current_image_display_scale(self) -> int:
        if self.video_buffer is None:
            return 1
        cam_bits = self.camera_type.bits
        dtype_bits = np.iinfo(self.video_buffer.dtype).bits
        return 2 ** (dtype_bits - cam_bits)

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
        count_to_add = min(count, remaining_capacity)
        for _ in range(count_to_add):
            roi = self._next_random_bead_roi(rng, visible_rect)
            if roi is None:
                break
            bead_rois[len(bead_rois)] = roi

        if not bead_rois:
            return

        self._add_new_bead_batch(list(bead_rois.values()))

    def _add_new_bead_batch(
        self,
        rois: Iterable[tuple[int, int, int, int]],
    ) -> dict[int, tuple[int, int, int, int]]:
        bead_rois: dict[int, tuple[int, int, int, int]] = {}
        next_bead_id = self._bead_next_id
        for roi in rois:
            if next_bead_id >= self._bead_roi_capacity:
                break
            bead_rois[next_bead_id] = tuple(int(value) for value in roi)
            next_bead_id += 1

        if not bead_rois:
            return {}

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
        self._set_active_bead(self._normalize_bead_id(self.selected_bead))
        self._refresh_bead_overlay()
        return bead_rois

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
        return None

    @n_windows.setter
    def n_windows(self, value):
        warn(
            "UIManager.n_windows has been removed; MagScope now uses one main window "
            "with dockable Live Camera and Live Plots panes. This value is ignored.",
            RuntimeWarning,
            stacklevel=2,
        )

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
        central_widget = QWidget()
        _set_widget_background(central_widget, APP_BACKGROUND_COLOR)
        central_layout = QVBoxLayout()
        central_widget.setLayout(central_layout)
        central_layout.addWidget(self.controls)
        self.central_widgets.append(central_widget)
        self.central_layouts.append(central_layout)

    def _create_viewer_docks(self, window: QMainWindow) -> None:
        if self.video_viewer is None or self.plots_widget is None:
            return

        self._apply_viewer_dock_separator_style(window)

        self.video_viewer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_viewer.setMaximumSize(16777215, 16777215)
        self.plots_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.plots_widget.setMinimumSize(1, 1)
        self.plots_widget.setMaximumSize(16777215, 16777215)

        self.camera_dock = QDockWidget("Live Camera", window)
        self.camera_dock.setObjectName("LiveCameraDock")
        self.bead_toolbar = self._create_live_bead_toolbar()
        camera_container, self.camera_dock_header = self._create_viewer_dock_content(
            self.camera_dock,
            self.video_viewer,
            self.bead_toolbar,
        )
        self.camera_dock.setWidget(camera_container)
        self.camera_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.camera_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.camera_dock.topLevelChanged.connect(
            lambda floating, dock=self.camera_dock: self._schedule_floating_dock_window_configuration(dock, floating)
        )

        self.plots_dock = QDockWidget("Live Plots", window)
        self.plots_dock.setObjectName("LivePlotsDock")
        plots_container, self.plots_dock_header = self._create_viewer_dock_content(
            self.plots_dock,
            self.plots_widget,
        )
        self.plots_dock.setWidget(plots_container)
        self.plots_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.plots_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.plots_dock.topLevelChanged.connect(
            lambda floating, dock=self.plots_dock: self._schedule_floating_dock_window_configuration(dock, floating)
        )

        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.camera_dock)
        window.splitDockWidget(self.camera_dock, self.plots_dock, Qt.Orientation.Vertical)

    def _create_viewer_dock_content(
        self,
        dock: QDockWidget,
        viewer: QWidget,
        toolbar: QWidget | None = None,
    ) -> tuple[QWidget, QWidget]:
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        header = QWidget(container)
        header.setFixedHeight(22)
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(6, 1, 6, 1)
        header_layout.setSpacing(4)
        header_layout.addStretch(1)
        dock_button = QToolButton(header)
        dock_button.setObjectName(f"{dock.objectName()}DockButton")
        dock_button.setText("push_pin")
        dock_button.setToolTip("Dock this viewer")
        dock_button.setFont(self._material_symbols_font(point_size=11))
        dock_button.setFixedSize(20, 20)
        dock_button.setCursor(Qt.CursorShape.PointingHandCursor)
        dock_button.setStyleSheet(self._material_symbols_filled_stylesheet())
        dock_button.clicked.connect(lambda _checked=False, target=dock: self._dock_viewer_pane(target))
        header_layout.addWidget(dock_button)
        header.hide()

        container_layout.addWidget(header, 0)
        if toolbar is not None:
            container_layout.addWidget(toolbar, 0)
        container_layout.addWidget(viewer, 1)
        return container, header

    def _create_live_bead_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setObjectName("LiveBeadToolbar")
        toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(6, 3, 6, 3)
        toolbar_layout.setSpacing(8)

        self.bead_instructions_button = QPushButton("Add/Remove Beads", toolbar)
        self.bead_instructions_button.setObjectName("LiveBeadInstructionsButton")
        self.bead_instructions_button.setToolTip("Show bead selection instructions")
        self.bead_instructions_button.clicked.connect(
            lambda _checked=False: self.show_bead_selection_instructions()
        )
        toolbar_layout.addWidget(self.bead_instructions_button)

        self.bead_roi_size_label = QLabel(toolbar)
        self.bead_roi_size_label.setObjectName("LiveBeadRoiSizeLabel")
        toolbar_layout.addWidget(self.bead_roi_size_label)

        self.bead_total_count_label = QLabel(toolbar)
        self.bead_total_count_label.setObjectName("LiveBeadTotalCountLabel")
        toolbar_layout.addWidget(self.bead_total_count_label)

        self.bead_next_id_label = QLabel(toolbar)
        self.bead_next_id_label.setObjectName("LiveBeadNextIdLabel")
        toolbar_layout.addWidget(self.bead_next_id_label)

        toolbar_layout.addStretch(1)

        self.bead_reassign_ids_button = QPushButton("Reassign IDs", toolbar)
        self.bead_reassign_ids_button.setObjectName("LiveBeadReassignIdsButton")
        self.bead_reassign_ids_button.clicked.connect(
            lambda _checked=False: self.reset_bead_ids()
        )
        toolbar_layout.addWidget(self.bead_reassign_ids_button)

        self.bead_remove_all_button = QPushButton("Remove All", toolbar)
        self.bead_remove_all_button.setObjectName("LiveBeadRemoveAllButton")
        self.bead_remove_all_button.clicked.connect(
            lambda _checked=False: self.clear_beads()
        )
        toolbar_layout.addWidget(self.bead_remove_all_button)

        self._update_live_bead_toolbar_labels()
        return toolbar

    def show_bead_selection_instructions(self) -> None:
        parent = self.camera_dock or (self.windows[0] if self.windows else None)
        msg = QMessageBox(parent)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle("Add/Remove Beads")
        msg.setText("Use the live camera view to manage bead ROIs.")
        msg.setInformativeText(
            "Add a bead: left-click an empty location in the live image.\n"
            "Activate/select a bead: left-click its ROI.\n"
            "Move a bead: drag the active ROI.\n"
            "Remove a bead: right-click its ROI.\n\n"
            "For automatic bead detection, use Tools > Auto Bead Selection."
        )
        msg.exec()

    def _dock_viewer_pane(self, dock: QDockWidget) -> None:
        dock.setFloating(False)
        dock.show()

    def _set_viewer_dock_header_visible(self, dock: QDockWidget, visible: bool) -> None:
        if dock is self.camera_dock:
            header = self.camera_dock_header
        elif dock is self.plots_dock:
            header = self.plots_dock_header
        else:
            header = None
        if header is not None:
            header.setVisible(visible)

    def _schedule_floating_dock_window_configuration(self, dock: QDockWidget, floating: bool) -> None:
        self._set_viewer_dock_header_visible(dock, floating)
        if not floating:
            return

        self._configure_floating_dock_window(dock, floating)
        QTimer.singleShot(0, lambda: self._configure_floating_dock_window(dock, floating))

    def _configure_floating_dock_window(self, dock: QDockWidget, floating: bool) -> None:
        if not floating:
            return

        dock.setMinimumSize(300, 300)
        dock.setMaximumSize(16777215, 16777215)
        dock.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        child = dock.widget()
        if child is not None:
            child.setMaximumSize(16777215, 16777215)
            child.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        dock.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        dock.show()

    def _create_view_menu(self, window: QMainWindow) -> None:
        view_menu = window.menuBar().addMenu("Layout")
        self._register_menu("Layout", view_menu)
        if self.camera_dock is not None:
            view_menu.addAction(self.camera_dock.toggleViewAction())
        if self.plots_dock is not None:
            view_menu.addAction(self.plots_dock.toggleViewAction())
        view_menu.addSeparator()
        dock_all_action = QAction("Dock All Windows", window)
        dock_all_action.triggered.connect(lambda _checked=False: self._dock_all_viewers())
        view_menu.addAction(dock_all_action)
        reset_action = QAction("Reset Viewer Layout", window)
        reset_action.triggered.connect(lambda _checked=False: self._reset_viewer_layout())
        view_menu.addAction(reset_action)

    def _create_tools_menu(self, window: QMainWindow) -> None:
        tools_menu = window.menuBar().addMenu("Tools")
        self._register_menu("Tools", tools_menu)
        auto_bead_selection_action = QAction("Auto Bead Selection", window)
        auto_bead_selection_action.triggered.connect(
            lambda _checked=False: self.start_auto_bead_selection()
        )
        tools_menu.addAction(auto_bead_selection_action)
        self._auto_bead_selection_action = auto_bead_selection_action
        self._update_auto_bead_selection_action_state()

    def _create_zlut_menu(self, window: QMainWindow) -> None:
        zlut_menu = window.menuBar().addMenu("Z-LUT")
        self._register_menu("Z-LUT", zlut_menu)

        new_action = QAction("New", window)
        new_action.triggered.connect(lambda _checked=False: self.show_new_zlut_dialog())
        zlut_menu.addAction(new_action)

        load_action = QAction("Load", window)
        load_action.triggered.connect(lambda _checked=False: self.load_zlut_file_dialog())
        zlut_menu.addAction(load_action)

        unload_action = QAction("Unload", window)
        unload_action.triggered.connect(lambda _checked=False: self.unload_zlut())
        zlut_menu.addAction(unload_action)

        show_current_action = QAction("Show Current", window)
        show_current_action.triggered.connect(lambda _checked=False: self.show_current_zlut_dialog())
        zlut_menu.addAction(show_current_action)

        self._zlut_menu = zlut_menu
        self._new_zlut_action = new_action
        self._load_zlut_action = load_action
        self._unload_zlut_action = unload_action
        self._show_current_zlut_action = show_current_action
        self._update_zlut_menu_action_state()

    def _update_zlut_menu_action_state(self) -> None:
        has_loaded_zlut = self._current_zlut_filepath is not None
        if self._unload_zlut_action is not None:
            self._unload_zlut_action.setEnabled(has_loaded_zlut)
        if self._show_current_zlut_action is not None:
            self._show_current_zlut_action.setEnabled(has_loaded_zlut)

    def _register_menu(self, name: str, menu: QMenu) -> None:
        self._menus[name] = menu
        if name == "Layout":
            self._layout_menu = menu
        elif name == "Z-LUT":
            self._zlut_menu = menu

    def _create_preferences_menu_action(self, window: QMainWindow) -> None:
        preferences_action = QAction("Preferences", window)
        preferences_action.triggered.connect(lambda _checked=False: self._show_preferences_dialog())
        window.menuBar().addAction(preferences_action)

    def _create_help_menu_action(self, window: QMainWindow) -> None:
        help_action = QAction("Help", window)
        help_action.triggered.connect(
            lambda _checked=False: QDesktopServices.openUrl(QUrl("https://magscope.readthedocs.io"))
        )
        window.menuBar().addAction(help_action)

    def _create_search_menu_widget(self, window: QMainWindow) -> None:
        self._refresh_search_registry()
        menu_bar = window.menuBar()
        self._menu_bar = menu_bar
        menu_bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        menu_container = QWidget(window)
        menu_container.setObjectName("MainMenuContainer")
        menu_container_layout = QVBoxLayout(menu_container)
        menu_container_layout.setContentsMargins(0, 0, 0, 0)
        menu_container_layout.setSpacing(0)

        menu_row = QWidget(menu_container)
        menu_row.setObjectName("MainMenuRow")
        menu_row_layout = QHBoxLayout(menu_row)
        menu_row_layout.setContentsMargins(0, 0, 0, 0)
        menu_row_layout.setSpacing(0)
        menu_row_layout.addWidget(menu_bar)

        search_container = QWidget(window)
        search_container.setObjectName("MenuSearchContainer")
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(6, 2, 0, 2)
        search_layout.setSpacing(0)

        search_box = QLineEdit(search_container)
        search_box.setObjectName("MenuSearchBox")
        search_box.setPlaceholderText("Search for controls ...")
        search_box.setToolTip("Search shows where controls are; it does not run actions.")
        search_box.setClearButtonEnabled(True)
        search_box.setFixedWidth(300)
        search_layout.addWidget(search_box)

        completion_model = QStringListModel(self._search_completion_labels(""), search_box)
        completer = QCompleter(completion_model, search_box)
        completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        search_box.setCompleter(completer)

        search_box.returnPressed.connect(lambda: self._guide_to_search_result(search_box.text()))
        search_box.textEdited.connect(
            lambda text: self._update_search_completion_model(completion_model, completer, text)
        )
        completer.activated.connect(lambda text: self._guide_to_search_result(str(text)))

        menu_row_layout.addWidget(search_container)
        search_status_label = QLabel(menu_row)
        search_status_label.setObjectName("MenuSearchStatusLabel")
        search_status_label.setVisible(False)
        menu_row_layout.addWidget(search_status_label)
        menu_row_layout.addStretch(1)

        menu_divider = QFrame(menu_container)
        menu_divider.setObjectName("MainMenuDivider")
        menu_divider.setFrameShape(QFrame.Shape.NoFrame)
        menu_divider.setFixedHeight(1)
        menu_divider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        menu_divider.setStyleSheet("#MainMenuDivider { background-color: #808080; }")

        menu_container_layout.addWidget(menu_row)
        menu_container_layout.addWidget(menu_divider)
        window.setMenuWidget(menu_container)
        self._menu_row = menu_row
        self._search_box = search_box
        self._search_status_label = search_status_label
        search_status_label.destroyed.connect(lambda _obj=None: self._clear_search_status_label_ref())
        self._search_status_timer = QTimer(menu_row)
        self._search_status_timer.setSingleShot(True)
        self._search_status_timer.setInterval(3000)
        self._search_status_timer.timeout.connect(lambda: self._set_search_status(""))
        self._install_search_shortcuts(window, search_box)

    def _refresh_search_registry(self) -> None:
        registry = SearchRegistry()
        registry.register_many(self._menu_search_targets())
        registry.register_many(MagScopeSettingsPanel.search_targets())
        registry.register_many(TrackingOptionsPanel.search_targets())
        registry.register_many(self._generic_panel_search_targets())
        registry.register_many(self._core_control_search_targets())

        panels = getattr(self.controls, "panels", {}) if self.controls is not None else {}
        if panels:
            for panel in panels.values():
                search_targets = getattr(panel, "search_targets", None)
                if callable(search_targets):
                    registry.register_many(search_targets())

        self._search_registry = registry

    def _ensure_search_registry(self) -> None:
        if not self._search_registry.targets:
            self._refresh_search_registry()

    def _menu_search_targets(self) -> list[SearchTarget]:
        return [
            MenuActionTarget(
                label="Auto Bead Selection",
                aliases=(
                    "auto bead",
                    "automatic bead selection",
                    "find bead",
                    "find beads",
                    "detect beads",
                ),
                context="Tools Menu",
                description="Opens automatic bead selection.",
                keywords=("bead finder", "select beads automatically"),
                menu_name="Tools",
                action_text="Auto Bead Selection",
            ),
            MenuActionTarget(
                label="New Z-LUT",
                aliases=("new zlut", "generate zlut", "generate z-lut", "z lut generation"),
                context="Z-LUT Menu",
                menu_name="Z-LUT",
                action_text="New",
            ),
            MenuActionTarget(
                label="Load Z-LUT",
                aliases=("load zlut", "select z-lut file", "choose z-lut file"),
                context="Z-LUT Menu",
                menu_name="Z-LUT",
                action_text="Load",
            ),
            MenuActionTarget(
                label="Unload Z-LUT",
                aliases=("clear zlut", "clear z-lut", "remove zlut", "remove z-lut"),
                context="Z-LUT Menu",
                menu_name="Z-LUT",
                action_text="Unload",
            ),
            MenuActionTarget(
                label="Show Current Z-LUT",
                aliases=("current zlut", "current z-lut", "show zlut", "show z-lut"),
                context="Z-LUT Menu",
                menu_name="Z-LUT",
                action_text="Show Current",
            ),
            MenuActionTarget(
                label="Dock All Windows",
                aliases=("dock", "dock windows", "dock all", "dock viewers"),
                context="Layout Menu",
                menu_name="Layout",
                action_text="Dock All Windows",
            ),
            MenuActionTarget(
                label="Reset Viewer Layout",
                aliases=("reset layout", "viewer layout", "reset windows"),
                context="Layout Menu",
                menu_name="Layout",
                action_text="Reset Viewer Layout",
            ),
        ]

    def _generic_panel_search_targets(self) -> list[SearchTarget]:
        panel_definitions = [
            ("Status", "StatusPanel", ()),
            ("Camera Settings", "CameraPanel", ("camera",)),
            ("Recording and Saving", "AcquisitionPanel", ("acquisition", "recording", "saving")),
            ("Histogram", "HistogramPanel", ()),
            ("Radial Profile Monitor", "ProfilePanel", ("profile",)),
            ("Plot Settings", "PlotSettingsPanel", ("plots",)),
            ("Scripting", "ScriptPanel", ("scripts",)),
            ("XY-Lock", "XYLockPanel", ("xy lock",)),
            ("Z-Lock", "ZLockPanel", ("z lock",)),
            ("Allan Deviation", "AllanDeviationPanel", ("allan",)),
        ]
        panels = getattr(self.controls, "panels", {}) if self.controls is not None else {}
        if panels:
            panel_definitions = [
                definition for definition in panel_definitions if definition[1] in panels
            ]
        return [
            PanelControlTarget(
                label=label,
                aliases=aliases,
                context="Panel",
                panel_id=panel_id,
            )
            for label, panel_id, aliases in panel_definitions
        ]

    def _core_control_search_targets(self) -> list[SearchTarget]:
        return [
            PanelControlTarget(
                label="Add/Remove Beads",
                aliases=("bead instructions", "bead controls", "manage beads"),
                context="Live Camera",
                description="Shows live camera bead selection instructions.",
                panel_id="LiveBeadToolbar",
                widget_path=("bead_instructions_button",),
            ),
            PanelControlTarget(
                label="Remove All Beads",
                aliases=("clear beads", "delete beads"),
                context="Live Camera",
                panel_id="LiveBeadToolbar",
                widget_path=("bead_remove_all_button",),
            ),
            PanelControlTarget(
                label="Reassign IDs",
                aliases=("reset bead ids", "renumber beads"),
                context="Live Camera",
                panel_id="LiveBeadToolbar",
                widget_path=("bead_reassign_ids_button",),
            ),
        ]

    @staticmethod
    def _normalize_search_text(text: str) -> str:
        return normalize_search_text(text)

    def _find_search_target(self, text: str) -> SearchTarget | None:
        self._ensure_search_registry()
        return self._search_registry.best(text)

    def _find_exact_search_target(self, text: str) -> SearchTarget | None:
        self._ensure_search_registry()
        query = normalize_search_text(text)
        if not query:
            return None
        for target in self._search_registry.targets:
            if query in {normalize_search_text(value) for value in target.search_values}:
                return target
        return None

    def _search_completion_labels(self, text: str) -> list[str]:
        self._ensure_search_registry()
        return self._search_registry.labels(text)

    def _update_search_completion_model(
        self,
        model: QStringListModel,
        completer: QCompleter,
        text: str,
    ) -> None:
        labels = self._search_completion_labels(text)
        model.setStringList(labels)
        if labels and text.strip():
            completer.complete()

    def _guide_to_search_result(self, text: str) -> None:
        target = self._find_search_target(text)
        if target is None:
            logger.debug("No UI search target matched query %r", text)
            self._set_search_status("")
            return

        logger.debug("Guiding to UI search target %s", target.display_label)
        self._guide_to_target(target)

    def _guide_to_target(self, target: SearchTarget) -> None:
        self._reveal_search_target(target)
        if self._search_box is not None:
            self._search_box.setText(target.label)
            self._search_box.selectAll()
        status_parts = [f"Showing: {target.display_label}"]
        if target.guide_only:
            status_parts.append("Guide only; no action was run.")
        if target.description:
            status_parts.append(target.description)
        self._set_search_status(" ".join(status_parts))

    def _reveal_search_target(self, target: SearchTarget) -> None:
        if isinstance(target, PreferencesSettingTarget):
            self._reveal_preference_setting(target)
            return
        if isinstance(target, PreferencesWidgetTarget):
            self._reveal_preference_widget(target)
            return
        if isinstance(target, MenuActionTarget):
            self._reveal_menu_action(target)
            return
        if not isinstance(target, PanelControlTarget):
            return

        if target.panel_id == "LiveBeadToolbar":
            if self.camera_dock is not None:
                self.camera_dock.show()
                self.camera_dock.raise_()
            widget = self._search_target_widget(target)
            if widget is not None:
                self._highlight_search_widget(widget)
            else:
                logger.warning("Search target widget could not be found: %s", target.display_label)
            return

        if self.controls is None:
            return

        reveal_panel = getattr(self.controls, "reveal_panel", None)
        if callable(reveal_panel):
            reveal_panel(target.panel_id)
        else:
            logger.debug("Controls object cannot reveal search panel %s", target.panel_id)

        widget = self._search_target_widget(target)
        if widget is not None:
            self._highlight_search_widget(widget)
        else:
            logger.warning("Search target widget could not be found: %s", target.display_label)

    def _search_target_widget(self, target: PanelControlTarget) -> QWidget | None:
        if target.panel_id == "LiveBeadToolbar":
            if not target.widget_path:
                return self.bead_toolbar

            widget = self
            for attr_name in target.widget_path:
                widget = getattr(widget, attr_name, None)
                if widget is None:
                    return self.bead_toolbar
            return widget if isinstance(widget, QWidget) else None

        if self.controls is None:
            return None

        panel = getattr(self.controls, "panels", {}).get(target.panel_id)
        if panel is None:
            return None

        if not target.widget_path:
            groupbox = getattr(panel, "groupbox", None)
            if isinstance(groupbox, QWidget):
                return groupbox
            return panel if isinstance(panel, QWidget) else None

        widget = panel
        for attr_name in target.widget_path:
            widget = getattr(widget, attr_name, None)
            if widget is None:
                return panel if isinstance(panel, QWidget) else None
        return widget if isinstance(widget, QWidget) else None

    def _reveal_preference_setting(self, target: PreferencesSettingTarget) -> None:
        self._show_preferences_dialog()
        if self._preferences_dialog is None:
            return

        reveal_setting = getattr(self._preferences_dialog, "reveal_setting", None)
        if callable(reveal_setting):
            reveal_setting(target.setting_key)
        else:
            logger.warning("Preferences dialog cannot reveal setting %s", target.setting_key)

    def _reveal_preference_widget(self, target: PreferencesWidgetTarget) -> None:
        self._show_preferences_dialog()
        if self._preferences_dialog is None:
            return

        reveal_widget = getattr(self._preferences_dialog, "reveal_widget", None)
        if callable(reveal_widget):
            reveal_widget(target.tab_name, target.widget_attr)
        else:
            logger.warning(
                "Preferences dialog cannot reveal widget %s > %s",
                target.tab_name,
                target.widget_attr,
            )

    def _reveal_menu_action(self, target: MenuActionTarget) -> None:
        menu = self._menus.get(target.menu_name)
        if menu is None:
            logger.warning("Search target menu could not be found: %s", target.menu_name)
            return

        action = next(
            (action for action in menu.actions() if action.text() == target.action_text),
            None,
        )
        if action is None:
            logger.warning(
                "Search target menu action could not be found: %s > %s",
                target.menu_name,
                target.action_text,
            )
            return

        menu.setActiveAction(action)
        if QGuiApplication.platformName() == "offscreen":
            return

        menu_bar = self._menu_bar
        if menu_bar is None:
            logger.warning("Search target menu bar is not available for %s", target.display_label)
            return

        completer = self._search_box.completer() if self._search_box is not None else None
        if completer is not None:
            completer.popup().hide()
        menu_action_geometry = menu_bar.actionGeometry(menu.menuAction())
        menu.popup(menu_bar.mapToGlobal(menu_action_geometry.bottomLeft()))

    def _highlight_search_widget(self, widget: QWidget) -> None:
        self._search_highlighter.highlight(widget)

    def _clear_search_highlight(self, widget: QWidget) -> None:
        self._search_highlighter.clear_widget(widget)

    def _install_search_shortcuts(self, window: QMainWindow, search_box: QLineEdit) -> None:
        for shortcut_text in ("Ctrl+K", "Ctrl+F"):
            shortcut = QShortcut(QKeySequence(shortcut_text), window)
            shortcut.activated.connect(lambda box=search_box: self._focus_search_box(box))
            self._search_shortcuts.append(shortcut)
        escape_shortcut = QShortcut(QKeySequence("Escape"), search_box)
        escape_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        escape_shortcut.activated.connect(lambda box=search_box: self._clear_search_box(box))
        self._search_shortcuts.append(escape_shortcut)

    def _focus_search_box(self, search_box: QLineEdit) -> None:
        search_box.setFocus()
        search_box.selectAll()

    def _clear_search_box(self, search_box: QLineEdit) -> None:
        search_box.clear()
        search_box.clearFocus()
        self._set_search_status("")

    def _set_search_status(self, text: str) -> None:
        label = self._search_status_label
        if label is None:
            return
        try:
            label.setText(text)
            label.setVisible(bool(text))
        except RuntimeError:
            self._clear_search_status_label_ref()
            return

        timer = self._search_status_timer
        if timer is None:
            return
        try:
            if text:
                timer.start()
            else:
                timer.stop()
        except RuntimeError:
            self._search_status_timer = None

    def _clear_search_status_label_ref(self) -> None:
        self._search_status_label = None
        self._search_status_timer = None

    def _show_preferences_dialog(self) -> None:
        if self._preferences_dialog is None:
            self._preferences_dialog = PreferencesDialog(self)
        self._preferences_dialog.show()
        self._preferences_dialog.raise_()
        self._preferences_dialog.activateWindow()

    def _dock_all_viewers(self) -> None:
        for dock in (self.camera_dock, self.plots_dock):
            if dock is not None:
                self._dock_viewer_pane(dock)

    def _viewer_layout_settings(self) -> QSettings:
        return QSettings("MagScope", "MagScope")

    def _save_viewer_layout(self) -> None:
        if not self.windows:
            return
        window = self.windows[0]
        if not hasattr(window, 'saveGeometry') or not hasattr(window, 'saveState'):
            return
        settings = self._viewer_layout_settings()
        settings.setValue(self.VIEWER_GEOMETRY_SETTINGS_KEY, window.saveGeometry())
        settings.setValue(
            self.VIEWER_DOCK_STATE_SETTINGS_KEY,
            window.saveState(self.VIEWER_LAYOUT_STATE_VERSION),
        )

    def _restore_viewer_layout(self) -> bool:
        if not self.windows:
            return False
        settings = self._viewer_layout_settings()
        geometry = settings.value(self.VIEWER_GEOMETRY_SETTINGS_KEY)
        dock_state = settings.value(self.VIEWER_DOCK_STATE_SETTINGS_KEY)
        if geometry is None or dock_state is None:
            return False

        window = self.windows[0]
        geometry_restored = window.restoreGeometry(geometry)
        if not geometry_restored:
            self._clear_viewer_layout()
            self._apply_default_viewer_layout()
            return False

        state_restored = window.restoreState(dock_state, self.VIEWER_LAYOUT_STATE_VERSION)
        if not state_restored:
            self._clear_viewer_layout()
            self._apply_default_viewer_layout()
            return False

        self._sync_viewer_dock_headers()
        return True

    def _clear_viewer_layout(self) -> None:
        settings = self._viewer_layout_settings()
        settings.remove(self.VIEWER_GEOMETRY_SETTINGS_KEY)
        settings.remove(self.VIEWER_DOCK_STATE_SETTINGS_KEY)

    @staticmethod
    def _encode_qbytearray(value: QByteArray) -> str:
        return bytes(value.toBase64()).decode('ascii')

    @staticmethod
    def _decode_qbytearray(value: str) -> QByteArray:
        return QByteArray.fromBase64(value.encode('ascii'))

    def export_appearance_layout_preferences(self) -> dict[str, Any]:
        settings = self._viewer_layout_settings()
        preferences: dict[str, Any] = {}

        if self.windows:
            window = self.windows[0]
            preferences['viewer_geometry'] = self._encode_qbytearray(window.saveGeometry())
            preferences['viewer_dock_state'] = self._encode_qbytearray(
                window.saveState(self.VIEWER_LAYOUT_STATE_VERSION)
            )

        controls = self.controls
        if controls is not None and hasattr(controls, 'export_preferences'):
            preferences['controls'] = controls.export_preferences()

        splitter_sizes: dict[str, list[int]] = {}
        for key in settings.allKeys():
            key = str(key)
            if not key.endswith(' Grip Splitter Sizes'):
                continue
            raw_sizes = settings.value(key, [], list)
            try:
                splitter_sizes[key] = [int(size) for size in raw_sizes]
            except (TypeError, ValueError):
                continue
        if splitter_sizes:
            preferences['splitter_sizes'] = splitter_sizes

        return preferences

    def import_appearance_layout_preferences(self, preferences: Mapping[str, Any]) -> None:
        self.validate_appearance_layout_preferences(preferences)

        settings = self._viewer_layout_settings()
        window = self.windows[0] if self.windows else None
        previous_geometry = window.saveGeometry() if window is not None else None
        previous_dock_state = (
            window.saveState(self.VIEWER_LAYOUT_STATE_VERSION) if window is not None else None
        )

        def restore_previous_layout() -> None:
            if window is None:
                return
            if previous_geometry is not None:
                window.restoreGeometry(previous_geometry)
            if previous_dock_state is not None:
                window.restoreState(previous_dock_state, self.VIEWER_LAYOUT_STATE_VERSION)
            self._sync_viewer_dock_headers()

        viewer_geometry = preferences.get('viewer_geometry')
        viewer_dock_state = preferences.get('viewer_dock_state')
        if viewer_geometry is not None:
            geometry = self._decode_qbytearray(viewer_geometry)
        else:
            geometry = None

        if viewer_dock_state is not None:
            dock_state = self._decode_qbytearray(viewer_dock_state)
        else:
            dock_state = None

        if window is not None and geometry is not None:
            if not window.restoreGeometry(geometry):
                restore_previous_layout()
                raise ValueError('appearance_layout.viewer_geometry is invalid')
        if window is not None and dock_state is not None:
            if not window.restoreState(dock_state, self.VIEWER_LAYOUT_STATE_VERSION):
                restore_previous_layout()
                raise ValueError('appearance_layout.viewer_dock_state is invalid')
            self._sync_viewer_dock_headers()

        if geometry is not None:
            settings.setValue(self.VIEWER_GEOMETRY_SETTINGS_KEY, geometry)
        if dock_state is not None:
            settings.setValue(self.VIEWER_DOCK_STATE_SETTINGS_KEY, dock_state)

        splitter_sizes = preferences.get('splitter_sizes', {})
        if splitter_sizes is not None:
            for key, raw_sizes in splitter_sizes.items():
                settings.setValue(str(key), [int(size) for size in raw_sizes])

        controls_preferences = preferences.get('controls', {})
        controls = self.controls
        if controls_preferences and controls is not None and hasattr(controls, 'import_preferences'):
            controls.import_preferences(controls_preferences)

        settings.sync()

    def validate_appearance_layout_preferences(self, preferences: Mapping[str, Any]) -> None:
        if not isinstance(preferences, Mapping):
            raise ValueError('appearance_layout must be a mapping')

        viewer_geometry = preferences.get('viewer_geometry')
        viewer_dock_state = preferences.get('viewer_dock_state')
        if viewer_geometry is not None:
            if not isinstance(viewer_geometry, str):
                raise ValueError('appearance_layout.viewer_geometry must be a string')

        if viewer_dock_state is not None:
            if not isinstance(viewer_dock_state, str):
                raise ValueError('appearance_layout.viewer_dock_state must be a string')

        splitter_sizes = preferences.get('splitter_sizes', {})
        if splitter_sizes is not None:
            if not isinstance(splitter_sizes, Mapping):
                raise ValueError('appearance_layout.splitter_sizes must be a mapping')
            for key, raw_sizes in splitter_sizes.items():
                if not isinstance(raw_sizes, list):
                    raise ValueError(f'appearance_layout.splitter_sizes.{key} must be a list')
                try:
                    [int(size) for size in raw_sizes]
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f'appearance_layout.splitter_sizes.{key} must contain integers'
                    ) from exc

        controls_preferences = preferences.get('controls', {})
        controls = self.controls
        if controls_preferences and controls is not None and hasattr(controls, 'validate_preferences'):
            controls.validate_preferences(controls_preferences)

    def reset_appearance_layout_preferences(self) -> None:
        settings = self._viewer_layout_settings()
        self._clear_viewer_layout()
        for key in settings.allKeys():
            key = str(key)
            if key.endswith(' Grip Splitter Sizes'):
                settings.remove(key)
        controls = self.controls
        if controls is not None and hasattr(controls, 'reset_to_defaults'):
            controls.reset_to_defaults()
        self._apply_default_viewer_layout()
        settings.sync()

    def _sync_viewer_dock_headers(self) -> None:
        for dock in (self.camera_dock, self.plots_dock):
            if dock is not None:
                self._set_viewer_dock_header_visible(dock, dock.isFloating())

    def _apply_default_viewer_layout(self) -> None:
        if not self.windows or self.camera_dock is None or self.plots_dock is None:
            return

        window = self.windows[0]
        self.camera_dock.show()
        self.plots_dock.show()
        self.camera_dock.setFloating(False)
        self.plots_dock.setFloating(False)
        window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.camera_dock)
        window.splitDockWidget(self.camera_dock, self.plots_dock, Qt.Orientation.Vertical)
        window.resizeDocks(
            [self.camera_dock, self.plots_dock],
            [max(1, window.height() * 2 // 3), max(1, window.height() // 3)],
            Qt.Orientation.Vertical,
        )
        window.resizeDocks(
            [self.camera_dock],
            [max(300, window.width() - self.central_widgets[0].sizeHint().width())],
            Qt.Orientation.Horizontal,
        )
        self._sync_viewer_dock_headers()

    def _reset_viewer_layout(self) -> None:
        self._clear_viewer_layout()
        self._apply_default_viewer_layout()

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
        if self.controls is None:
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

    def start_auto_bead_selection(self) -> None:
        if not self._can_start_auto_bead_selection():
            return

        image = self._snapshot_recent_image()
        if image is None:
            self.show_error('No live image available', 'Cannot start auto bead selection without a recent frame.')
            return

        dialog_parent = self.windows[0] if self.windows else None
        dialog = AutoBeadSelectionDialog(
            parent=dialog_parent,
            image=image,
            roi_size=self.settings['ROI'],
            existing_rois=self._bead_rois,
            display_scale=self._current_image_display_scale(),
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.finished.connect(self._on_auto_bead_selection_dialog_finished)
        dialog.selectionAccepted.connect(self._apply_auto_bead_selection)
        self._auto_bead_selection_dialog = dialog
        self._update_auto_bead_selection_action_state()
        dialog.open()

    def _apply_auto_bead_selection(self, rois: list[tuple[int, int, int, int]]) -> None:
        remaining_capacity = self._bead_roi_capacity - self._bead_next_id
        existing_rois = list(self._bead_rois.values())
        accepted_rois: list[tuple[int, int, int, int]] = []
        for roi in rois:
            normalized_roi = tuple(int(value) for value in roi)
            if any(roi_overlaps(normalized_roi, existing_roi) for existing_roi in existing_rois):
                continue
            if any(roi_overlaps(normalized_roi, kept_roi) for kept_roi in accepted_rois):
                continue
            accepted_rois.append(normalized_roi)

        if not accepted_rois:
            return

        rois_to_add = accepted_rois[:max(0, remaining_capacity)]
        skipped_due_to_capacity = len(accepted_rois) - len(rois_to_add)
        if not rois_to_add:
            if skipped_due_to_capacity > 0:
                self._show_auto_bead_selection_capacity_warning(skipped_due_to_capacity)
            return
        self._add_new_bead_batch(rois_to_add)
        if skipped_due_to_capacity > 0:
            self._show_auto_bead_selection_capacity_warning(skipped_due_to_capacity)

    def _show_auto_bead_selection_capacity_warning(self, skipped_count: int) -> None:
        bead_label = 'bead' if skipped_count == 1 else 'beads'
        self.show_warning(
            'Maximum bead count reached',
            f'{skipped_count} {bead_label} could not be added because they would exceed '
            f'the maximum allowed bead count of {self._bead_roi_capacity} beads.',
        )

    def _on_auto_bead_selection_dialog_finished(self, _result: int) -> None:
        self._auto_bead_selection_dialog = None
        self._update_auto_bead_selection_action_state()

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
            self._update_bead_count_label()
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
        self._update_bead_roi_size_label(roi)
        if self.controls is None:
            return

        zlut_generation_panel = getattr(self.controls, 'z_lut_generation_panel', None)
        if zlut_generation_panel is not None:
            zlut_generation_panel.roi_size_label.setText(f"{roi} x {roi} pixels")

    def _update_next_bead_id_label(self) -> None:
        if self.bead_next_id_label is not None:
            self.bead_next_id_label.setText(f"Next Bead ID: {self._bead_next_id}")

    def _update_bead_roi_size_label(self, roi: int | None = None) -> None:
        if self.bead_roi_size_label is None:
            return

        if roi is None:
            try:
                roi = self.settings["ROI"]
            except (KeyError, TypeError, AttributeError):
                roi = None

        roi_text = "--" if roi is None else str(roi)
        self.bead_roi_size_label.setText(f"ROI: {roi_text} px")

    def _update_bead_count_label(self) -> None:
        if self.bead_total_count_label is not None:
            self.bead_total_count_label.setText(f"Total Beads: {len(self._bead_rois)}")

    def _update_live_bead_toolbar_labels(self) -> None:
        self._update_bead_roi_size_label()
        self._update_bead_count_label()
        self._update_next_bead_id_label()

    def _clear_pending_bead_add(self) -> None:
        self._pending_bead_add_id = None
        self._pending_bead_add_roi = None
        self._update_auto_bead_selection_action_state()

    def _calculate_next_bead_id(self) -> int:
        if not self._bead_rois:
            return 0

        return max(self._bead_rois.keys()) + 1

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
        tracks = tracks[np.argsort(tracks[:, 0], kind='stable')]
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
            msg.setInformativeText(details)
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
            msg.setInformativeText(details)
        else:
            logger.error('%s', text)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.show()

    def show_warning(self, text: str, details: str | None = None):
        msg = QMessageBox(self.windows[0])
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle("Warning")
        msg.setText(text)
        if details:
            logger.warning('%s: %s', text, details)
            msg.setInformativeText(details)
        else:
            logger.warning('%s', text)
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
        panel = self.controls.acquisition_panel
        textedit = panel.acquisition_dir_textedit
        textedit.blockSignals(True) # to prevent a loop
        panel.set_acquisition_dir_text(value)
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

    @register_ipc_command(UpdateZLockWindowCommand)
    def update_z_lock_window(self, value: int):
        self.controls.z_lock_panel.update_window(value)

    def request_zlut_file(self, filepath: str) -> None:
        if not filepath:
            return

        self._set_current_zlut(filepath=filepath)
        command = LoadZLUTCommand(filepath=filepath)
        self.send_ipc(command)

    def clear_zlut(self) -> None:
        self.unload_zlut()

    def unload_zlut(self) -> None:
        self._set_current_zlut(filepath=None)
        command = UnloadZLUTCommand()
        self.send_ipc(command)

    def load_zlut_file_dialog(self) -> None:
        settings = QSettings('MagScope', 'MagScope')
        last_value = settings.value(
            'last zlut directory',
            os.path.expanduser('~'),
            type=str,
        )
        path, _ = QFileDialog.getOpenFileName(
            self.windows[0] if self.windows else None,
            'Load Z-LUT',
            last_value,
            'Text Files (*.txt)',
        )
        if not path:
            return

        directory = os.path.dirname(path) or last_value
        settings.setValue('last zlut directory', directory)
        self.request_zlut_file(path)

    def show_current_zlut_dialog(self) -> None:
        if self._current_zlut_filepath is None:
            return

        parent = self.windows[0] if self.windows else None
        if (
            self._current_zlut_dialog is not None
            and getattr(self._current_zlut_dialog, '_matplotlib_disposed', False)
        ):
            self._current_zlut_dialog = None

        if self._current_zlut_dialog is None:
            dialog = CurrentZLUTDialog(parent)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.destroyed.connect(lambda *_: self._clear_current_zlut_dialog_ref())
            self._current_zlut_dialog = dialog
        self._update_current_zlut_dialog()
        self._current_zlut_dialog.show()
        self._current_zlut_dialog.raise_()
        self._current_zlut_dialog.activateWindow()

    def _clear_current_zlut_dialog_ref(self) -> None:
        self._current_zlut_dialog = None

    def show_new_zlut_dialog(self) -> None:
        preflight_error = self._zlut_generation_preflight_error()
        if preflight_error is not None:
            self.show_warning('Cannot generate Z-LUT', preflight_error)
            return

        parent = self.windows[0] if self.windows else None
        dialog = ZLUTGenerationSetupDialog(
            parent,
            roi_size=int(self.settings['ROI']),
            default_measurements=int(self.settings['video buffer n images']),
        )
        self._zlut_generation_setup_dialog = dialog
        try:
            if not dialog.exec():
                return
            values = dialog.values
            if values is None:
                return
            start_nm, step_nm, stop_nm, profiles_per_bead = values
            self.start_zlut_generation(
                start_nm=start_nm,
                step_nm=step_nm,
                stop_nm=stop_nm,
                profiles_per_bead=profiles_per_bead,
            )
        finally:
            self._zlut_generation_setup_dialog = None

    def _zlut_generation_preflight_error(self) -> str | None:
        if not self._bead_rois:
            return 'At least one bead ROI must be selected before generating a Z-LUT.'
        if self.video_buffer is None:
            return 'Video buffer is not available.'
        if self._acquisition_mode not in self._ZLUT_TRACKING_ACQUISITION_MODES:
            return (
                'Z-LUT generation requires a tracking acquisition mode. '
                'Switch to Track, Track and Video (ROIs), or Track and Video (Full).'
            )

        focus_motor_names = self._registered_focus_motor_names()
        if not focus_motor_names:
            return 'No FocusMotorBase hardware is registered.'
        if len(focus_motor_names) > 1:
            return 'Z-LUT generation requires exactly one registered FocusMotorBase hardware manager.'
        return None

    def _registered_focus_motor_names(self) -> list[str]:
        from magscope.hardware import FocusMotorBase

        focus_motor_names: list[str] = []
        for name, hardware_type in self.hardware_types.items():
            try:
                if issubclass(hardware_type, FocusMotorBase):
                    focus_motor_names.append(name)
            except TypeError:
                continue
        return focus_motor_names

    def _set_current_zlut(
        self,
        *,
        filepath: str | None,
        z_min: float | None = None,
        z_max: float | None = None,
        step_size: float | None = None,
        profile_length: int | None = None,
    ) -> None:
        self._current_zlut_filepath = filepath
        self._current_zlut_metadata = {
            'z_min': z_min,
            'z_max': z_max,
            'step_size': step_size,
            'profile_length': profile_length,
        }
        self._update_zlut_menu_action_state()
        self._update_current_zlut_dialog()

    def _update_current_zlut_dialog(self) -> None:
        if self._current_zlut_dialog is None:
            return
        self._current_zlut_dialog.update_zlut(
            self._current_zlut_filepath,
            z_min=self._current_zlut_metadata['z_min'],
            z_max=self._current_zlut_metadata['z_max'],
            step_size=self._current_zlut_metadata['step_size'],
            profile_length=self._current_zlut_metadata['profile_length'],
        )

    def _clear_zlut_generation_preview(
        self,
        message: str = 'Waiting for Z-LUT sweep data...',
    ) -> None:
        if self._zlut_generation_dialog is not None:
            self._zlut_generation_dialog.preview_widget.clear(message)

    def _read_zlut_preview_snapshot(self, dataset: ZLUTSweepDataset) -> dict[str, object]:
        if hasattr(dataset, 'read_preview'):
            return dataset.read_preview(selected_bead_id=self._zlut_evaluation_selected_bead_id)

        snapshot = dataset.peak()
        count = snapshot['bead_ids'].shape[0]
        available_bead_ids: list[int] = []
        selected_bead_id: int | None = None
        motor_z_min: float | None = None
        motor_z_max: float | None = None
        step_indices = np.zeros((0,), dtype=np.uint32)
        motor_z_values = np.zeros((0,), dtype=np.float64)
        profiles = np.zeros((0, int(dataset.profile_length)), dtype=np.float64)

        if count > 0:
            bead_ids = snapshot['bead_ids']
            available_bead_ids = [int(bead_id) for bead_id in np.unique(bead_ids)]

            if (
                self._zlut_evaluation_selected_bead_id is not None
                and self._zlut_evaluation_selected_bead_id in bead_ids
            ):
                selected_bead_id = int(self._zlut_evaluation_selected_bead_id)
            else:
                selected_bead_id = int(np.min(bead_ids))

            all_motor_z_values = snapshot['motor_z_values']
            finite_motor_z = all_motor_z_values[np.isfinite(all_motor_z_values)]
            if finite_motor_z.size > 0:
                motor_z_min = float(np.min(finite_motor_z))
                motor_z_max = float(np.max(finite_motor_z))

            selected_rows = bead_ids == selected_bead_id
            step_indices = snapshot['step_indices'][selected_rows]
            motor_z_values = all_motor_z_values[selected_rows]
            profiles = snapshot['profiles'][selected_rows]

        return {
            'state': dataset.state,
            'count': count,
            'capacity': dataset.get_capacity(),
            'n_steps': dataset.n_steps,
            'n_beads': dataset.n_beads,
            'profiles_per_bead': dataset.profiles_per_bead,
            'profile_length': dataset.profile_length,
            'available_bead_ids': available_bead_ids,
            'selected_bead_id': selected_bead_id,
            'motor_z_min': motor_z_min,
            'motor_z_max': motor_z_max,
            'step_indices': step_indices,
            'motor_z_values': motor_z_values,
            'profiles': profiles,
        }

    def show_zlut_generation_dialog(self) -> None:
        if not self.windows:
            return
        self._detach_zlut_sweep_dataset()
        if self._zlut_generation_dialog is None:
            dialog = ZLUTGenerationDialog(self.windows[0])
            dialog.set_cancel_callback(self.cancel_zlut_generation)
            dialog.set_close_callback(self.discard_generated_zlut_evaluation)
            dialog.set_save_callback(
                lambda bead_id: self.save_generated_zlut(bead_id, load_after_save=False)
            )
            dialog.set_save_and_load_callback(
                lambda bead_id: self.save_generated_zlut(bead_id, load_after_save=True)
            )
            dialog.set_select_bead_callback(self.select_generated_zlut_bead)
            dialog.destroyed.connect(lambda *_: self._handle_zlut_dialog_destroyed())
            self._zlut_generation_dialog = dialog
        self._zlut_generation_dialog.mark_starting()
        self._zlut_generation_dialog.show()
        self._zlut_generation_dialog.raise_()
        self._zlut_generation_dialog.activateWindow()
        self._clear_zlut_generation_preview()
        self._zlut_generation_phase = 'waiting_profile_length'
        self._zlut_preview_last_poll = 0.0

    def start_zlut_generation(
        self,
        *,
        start_nm: float,
        step_nm: float,
        stop_nm: float,
        profiles_per_bead: int,
    ) -> None:
        self.show_zlut_generation_dialog()
        self.send_ipc(
            StartZLUTGenerationCommand(
                start_nm=float(start_nm),
                step_nm=float(step_nm),
                stop_nm=float(stop_nm),
                profiles_per_bead=int(profiles_per_bead),
            )
        )

    def cancel_zlut_generation(self) -> None:
        self.send_ipc(CancelZLUTGenerationCommand())

    def discard_generated_zlut_evaluation(self) -> None:
        self.send_ipc(CancelGeneratedZLUTEvaluationCommand())

    def select_generated_zlut_bead(self, bead_id: int) -> None:
        self._zlut_evaluation_selected_bead_id = int(bead_id)
        self.send_ipc(SelectGeneratedZLUTBeadCommand(bead_id=int(bead_id)))
        self._zlut_preview_last_poll = 0.0

    def save_generated_zlut(self, bead_id: int, load_after_save: bool = True) -> None:
        settings = QSettings('MagScope', 'MagScope')
        last_value = settings.value('last zlut directory', os.path.expanduser('~'), type=str)
        default_path = os.path.join(last_value, f'generated_zlut_bead_{int(bead_id)}.txt')
        filepath, _ = QFileDialog.getSaveFileName(
            self.windows[0] if self.windows else None,
            'Save Generated Z-LUT',
            default_path,
            'Text Files (*.txt)',
        )
        if not filepath:
            return

        directory = os.path.dirname(filepath) or last_value
        settings.setValue('last zlut directory', directory)
        self.send_ipc(
            SaveGeneratedZLUTCommand(
                filepath=filepath,
                bead_id=int(bead_id),
                load_after_save=bool(load_after_save),
            )
        )

    def request_profile_length(self) -> None:
        self.send_ipc(RequestProfileLengthCommand())

    @register_ipc_command(UpdateZLUTMetadataCommand)
    def update_zlut_metadata(self,
                             filepath: str | None = None,
                             z_min: float | None = None,
                             z_max: float | None = None,
                             step_size: float | None = None,
                             profile_length: int | None = None) -> None:
        self._set_current_zlut(
            filepath=filepath,
            z_min=z_min,
            z_max=z_max,
            step_size=step_size,
            profile_length=profile_length,
        )

    @register_ipc_command(ReportProfileLengthCommand)
    def report_profile_length(self, profile_length: int | None = None) -> None:
        print(f'Temporary development behavior: profile length = {profile_length}')

    @register_ipc_command(UpdateZLUTGenerationStateCommand)
    def update_zlut_generation_state(
        self,
        status: str,
        detail: str | None = None,
        running: bool = False,
        can_cancel: bool = False,
        phase: str = 'idle',
        z_axis_min_nm: float | None = None,
        z_axis_max_nm: float | None = None,
        z_axis_descending: bool = False,
    ) -> None:
        self._zlut_generation_phase = phase
        self._zlut_generation_z_axis_min_nm = z_axis_min_nm
        self._zlut_generation_z_axis_max_nm = z_axis_max_nm
        self._zlut_generation_z_axis_descending = bool(z_axis_descending)
        panel = getattr(self.controls, 'z_lut_generation_panel', None) if self.controls is not None else None
        if panel is not None:
            panel.update_state(
                status,
                detail,
                running=running,
                can_cancel=can_cancel,
                phase=phase,
            )
        if self._zlut_generation_dialog is not None:
            self._zlut_generation_dialog.update_state(
                status,
                detail,
                running=running,
                can_cancel=can_cancel,
                phase=phase,
            )

    @register_ipc_command(UpdateZLUTGenerationEvaluationCommand)
    def update_zlut_generation_evaluation(
        self,
        active: bool,
        bead_ids: list[int],
        selected_bead_id: int | None = None,
    ) -> None:
        self._zlut_evaluation_bead_ids = [int(bead_id) for bead_id in bead_ids]
        self._zlut_evaluation_selected_bead_id = None if selected_bead_id is None else int(selected_bead_id)
        if not active:
            self._detach_zlut_sweep_dataset()
            self._clear_zlut_generation_preview()
        if self._zlut_generation_dialog is not None:
            self._zlut_generation_dialog.update_evaluation(
                active=active,
                bead_ids=self._zlut_evaluation_bead_ids,
                selected_bead_id=self._zlut_evaluation_selected_bead_id,
            )
        self._zlut_preview_last_poll = 0.0

    @register_ipc_command(UpdateZLUTGenerationProgressCommand)
    def update_zlut_generation_progress(
        self,
        current_step: int,
        total_steps: int,
        capture_count: int,
        capture_capacity: int,
        motor_z_value: float | None = None,
    ) -> None:
        if self._zlut_generation_dialog is not None:
            self._zlut_generation_dialog.update_progress(
                current_step,
                total_steps,
                capture_count,
                capture_capacity,
                motor_z_value,
            )

    def _handle_zlut_dialog_destroyed(self) -> None:
        self._zlut_generation_dialog = None

    def _detach_zlut_sweep_dataset(self) -> None:
        if self._zlut_sweep_dataset is not None:
            self._zlut_sweep_dataset.close()
            self._zlut_sweep_dataset = None

    def _update_zlut_generation_dialog(self) -> None:
        if self._zlut_generation_dialog is None or not self._zlut_generation_dialog.isVisible():
            return
        if self._zlut_generation_phase in {'idle', 'complete'} and not self._zlut_evaluation_bead_ids:
            self._clear_zlut_generation_preview()
            return
        now = time()
        if now - self._zlut_preview_last_poll < 1.0:
            return
        self._zlut_preview_last_poll = now

        if self._zlut_sweep_dataset is None:
            try:
                self._zlut_sweep_dataset = ZLUTSweepDataset.attach(locks=self.locks)
            except (DatasetNotReadyError, FileNotFoundError):
                self._clear_zlut_generation_preview()
                return

        try:
            self._refresh_zlut_preview_from_dataset()
        except FileNotFoundError:
            self._detach_zlut_sweep_dataset()
            self._clear_zlut_generation_preview()

    def _refresh_zlut_preview_from_dataset(self) -> None:
        if self._zlut_generation_dialog is None or self._zlut_sweep_dataset is None:
            return

        dataset = self._zlut_sweep_dataset
        preview_snapshot = self._read_zlut_preview_snapshot(dataset)
        count = int(preview_snapshot['count'])
        available_bead_ids = list(preview_snapshot['available_bead_ids'])
        if available_bead_ids != self._zlut_evaluation_bead_ids:
            self._zlut_evaluation_bead_ids = available_bead_ids
            if self._zlut_evaluation_selected_bead_id not in self._zlut_evaluation_bead_ids:
                self._zlut_evaluation_selected_bead_id = (
                    None if not self._zlut_evaluation_bead_ids else self._zlut_evaluation_bead_ids[0]
                )
            self._zlut_generation_dialog.update_evaluation(
                active=self._zlut_generation_phase == 'evaluating',
                bead_ids=self._zlut_evaluation_bead_ids,
                selected_bead_id=self._zlut_evaluation_selected_bead_id,
            )

        preview_payload = self._build_zlut_preview_payload(
            preview_snapshot,
            z_axis_min_nm=self._zlut_generation_z_axis_min_nm,
            z_axis_max_nm=self._zlut_generation_z_axis_max_nm,
            z_axis_descending=self._zlut_generation_z_axis_descending,
        )
        preview_payload['count'] = count
        self._zlut_generation_dialog.preview_widget.update_preview(**preview_payload)

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
        color = get_accent_color() if active else "palette(midlight)"
        self.setStyleSheet(
            "#add_column_drop_target { border: 2px dashed %s; border-radius: 0px; }" % color
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

class LegacyDraggableControls(QWidget):
    """Container widget hosting draggable, persistent control panels."""

    LAYOUT_SETTINGS_GROUP = "controls/layout"

    def __init__(self, manager: UIManager):
        super().__init__()
        self.manager = manager
        self.panels: dict[str, ControlPanelBase | QWidget] = {}
        _set_widget_background(self, APP_BACKGROUND_COLOR)

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

        self._add_column("left", index=0)
        for name in stored_layout.keys():
            if name in self.layout_manager.columns:
                continue
            self._add_column(name)
        if "right" not in self.layout_manager.columns and len(self.layout_manager.columns) < 2:
            self._add_column("right")

        # Instantiate standard panels
        self.acquisition_panel = AcquisitionPanel(self.manager)
        self.camera_panel = CameraPanel(self.manager)
        self.histogram_panel = HistogramPanel(self.manager)
        self.plot_settings_panel = PlotSettingsPanel(self.manager)
        self.allan_deviation_panel = (
            AllanDeviationPanel(self.manager) if has_tweezepy_support() else None
        )
        self.profile_panel = ProfilePanel(self.manager)
        self.script_panel = ScriptPanel(self.manager)
        self.status_panel = StatusPanel(self.manager)
        self.xy_lock_panel = XYLockPanel(self.manager)
        self.z_lock_panel = ZLockPanel(self.manager)

        definitions: list[tuple[str, QWidget, str, bool]] = [
            ("StatusPanel", self.status_panel, "left", True),
            ("CameraPanel", self.camera_panel, "left", True),
            ("AcquisitionPanel", self.acquisition_panel, "left", True),
            ("HistogramPanel", self.histogram_panel, "left", True),
            ("ProfilePanel", self.profile_panel, "left", True),
            ("PlotSettingsPanel", self.plot_settings_panel, "right", True),
            ("ScriptPanel", self.script_panel, "right", True),
            ("XYLockPanel", self.xy_lock_panel, "right", True),
            ("ZLockPanel", self.z_lock_panel, "right", True),
        ]
        if self.allan_deviation_panel is not None:
            definitions.insert(
                definitions.index(("ScriptPanel", self.script_panel, "right", True)),
                ("AllanDeviationPanel", self.allan_deviation_panel, "right", True),
            )

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
            _set_widget_background(scroll.viewport(), APP_BACKGROUND_COLOR)
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

    def reveal_panel(self, panel_id: str) -> None:
        panel = self.panels.get(panel_id)
        if panel is None:
            return

        groupbox = getattr(panel, "groupbox", None)
        if isinstance(groupbox, CollapsibleGroupBox) and groupbox.collapsed:
            groupbox._apply_collapsed_state(False, animate=False, persist=True)

        wrapper = self.layout_manager.wrapper_for_id(panel_id)
        if wrapper is None or wrapper.column is None:
            return
        scroll = self._column_scrolls.get(wrapper.column.name)
        if scroll is not None:
            scroll.ensureWidgetVisible(wrapper)

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

        self._add_column("left", index=0)
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


WORKFLOW_TAB_MIME_TYPE = "application/x-magscope-workflow-tab"


class WorkflowTabBar(QTabBar):
    """Tab bar that supports moving workflow tabs between control columns."""

    def __init__(self, tab_widget: "WorkflowTabWidget") -> None:
        super().__init__(tab_widget)
        self._tab_widget = tab_widget
        self._drag_start_pos: QPoint | None = None
        self.setAcceptDrops(True)

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        if self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        if (event.position().toPoint() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return

        index = self.tabAt(self._drag_start_pos)
        tab_id = self._tab_widget.tab_id_at(index)
        if tab_id is None:
            super().mouseMoveEvent(event)
            return

        drag = QDrag(self)
        mime = QMimeData()
        payload = json.dumps(
            {
                "tab_id": tab_id,
                "source_column": self._tab_widget.column_index,
            }
        )
        mime.setData(WORKFLOW_TAB_MIME_TYPE, payload.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(WORKFLOW_TAB_MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(WORKFLOW_TAB_MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if not event.mimeData().hasFormat(WORKFLOW_TAB_MIME_TYPE):
            event.ignore()
            return

        try:
            payload = json.loads(bytes(event.mimeData().data(WORKFLOW_TAB_MIME_TYPE)).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError):
            event.ignore()
            return

        tab_id = payload.get("tab_id")
        if not isinstance(tab_id, str):
            event.ignore()
            return

        target_index = self.tabAt(event.position().toPoint())
        if target_index < 0:
            target_index = self.count()
        self._tab_widget.controls.move_workflow_tab(
            tab_id,
            self._tab_widget.column_index,
            target_index,
        )
        event.acceptProposedAction()


class WorkflowTabWidget(QTabWidget):
    """A tab widget representing one adaptive workflow control column."""

    def __init__(self, controls: "Controls", column_index: int) -> None:
        super().__init__(controls)
        self.controls = controls
        self.column_index = column_index
        _set_widget_background(self, APP_BACKGROUND_COLOR)
        self.setTabBar(WorkflowTabBar(self))
        self.setDocumentMode(True)
        self.setUsesScrollButtons(True)
        self.setAcceptDrops(True)
        self.setMinimumWidth(Controls.MIN_COLUMN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def tab_id_at(self, index: int) -> str | None:
        if index < 0:
            return None
        value = self.tabBar().tabData(index)
        return str(value) if value else None

    def _dragged_tab_id(self, event) -> str | None:
        if not event.mimeData().hasFormat(WORKFLOW_TAB_MIME_TYPE):
            return None
        try:
            payload = json.loads(bytes(event.mimeData().data(WORKFLOW_TAB_MIME_TYPE)).decode("utf-8"))
        except (TypeError, ValueError, UnicodeDecodeError):
            return None
        tab_id = payload.get("tab_id")
        return tab_id if isinstance(tab_id, str) else None

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._dragged_tab_id(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._dragged_tab_id(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        tab_id = self._dragged_tab_id(event)
        if tab_id is None:
            event.ignore()
            return
        self.controls.move_workflow_tab(tab_id, self.column_index, self.count())
        event.acceptProposedAction()


class Controls(QWidget):
    """Adaptive workflow controls with movable tabs and responsive columns."""

    LAYOUT_SETTINGS_GROUP = "controls/layout"
    WORKFLOW_COLUMNS_SETTINGS_KEY = "controls/workflow_columns"
    MIN_COLUMN_WIDTH = 360
    MAX_COLUMNS = 4
    WORKFLOW_ORDER = ["Run", "Analysis", "Locking", "Custom"]

    DEFAULT_LAYOUTS = {
        1: [["Run", "Analysis", "Locking", "Custom"]],
        2: [["Run", "Custom"], ["Analysis", "Locking"]],
        3: [["Run", "Custom"], ["Analysis"], ["Locking"]],
        4: [["Run"], ["Analysis"], ["Locking"], ["Custom"]],
    }

    def __init__(self, manager: UIManager):
        super().__init__()
        self.manager = manager
        self.panels: dict[str, ControlPanelBase | QWidget] = {}
        _set_widget_background(self, APP_BACKGROUND_COLOR)
        self._settings = QSettings("MagScope", "MagScope")
        self._tab_widgets: list[WorkflowTabWidget] = []
        self._tab_pages: dict[str, QScrollArea] = {}
        self._tab_content_layouts: dict[str, QVBoxLayout] = {}
        self._panel_to_tab: dict[str, str] = {}
        self._current_column_count = 0
        self._loading_layout = False

        self.setMinimumWidth(self.MIN_COLUMN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(6)

        self._columns_layout = QHBoxLayout()
        self._columns_layout.setContentsMargins(0, 0, 0, 0)
        self._columns_layout.setSpacing(6)
        root_layout.addLayout(self._columns_layout, 1)

        self._create_standard_panels()
        self._create_workflow_pages()
        self._populate_workflow_pages()
        self._apply_workflow_layout(self._default_layout_for_count(1), save=False)

    @property
    def settings(self):
        return self.manager.settings

    @settings.setter
    def settings(self, value):
        raise AttributeError("Read-only attribute.")

    def _create_standard_panels(self) -> None:
        self.acquisition_panel = AcquisitionPanel(self.manager)
        self.camera_panel = CameraPanel(self.manager)
        self.histogram_panel = HistogramPanel(self.manager)
        self.plot_settings_panel = PlotSettingsPanel(self.manager)
        self.allan_deviation_panel = (
            AllanDeviationPanel(self.manager) if has_tweezepy_support() else None
        )
        self.profile_panel = ProfilePanel(self.manager)
        self.script_panel = ScriptPanel(self.manager)
        self.status_panel = StatusPanel(self.manager)
        self.xy_lock_panel = XYLockPanel(self.manager)
        self.z_lock_panel = ZLockPanel(self.manager)

        panel_tabs: list[tuple[str, QWidget, str]] = [
            ("StatusPanel", self.status_panel, "Run"),
            ("AcquisitionPanel", self.acquisition_panel, "Run"),
            ("CameraPanel", self.camera_panel, "Run"),
            ("ScriptPanel", self.script_panel, "Run"),
            ("PlotSettingsPanel", self.plot_settings_panel, "Analysis"),
            ("HistogramPanel", self.histogram_panel, "Analysis"),
            ("ProfilePanel", self.profile_panel, "Analysis"),
            ("XYLockPanel", self.xy_lock_panel, "Locking"),
            ("ZLockPanel", self.z_lock_panel, "Locking"),
        ]
        if self.allan_deviation_panel is not None:
            panel_tabs.insert(
                panel_tabs.index(("XYLockPanel", self.xy_lock_panel, "Locking")),
                ("AllanDeviationPanel", self.allan_deviation_panel, "Analysis"),
            )

        for panel_id, panel, tab_id in panel_tabs:
            self.panels[panel_id] = panel
            self._panel_to_tab[panel_id] = tab_id

        for control_factory, _column in self.manager.controls_to_add:
            widget = control_factory(self.manager)
            panel_id = widget.__class__.__name__
            self.panels[panel_id] = widget
            self._panel_to_tab[panel_id] = "Custom"

    def _create_workflow_pages(self) -> None:
        for tab_id in self.WORKFLOW_ORDER:
            content = QWidget(self)
            _set_widget_background(content, APP_BACKGROUND_COLOR)
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(0, 6, 0, 6)
            content_layout.setSpacing(6)
            content_layout.addStretch(1)

            scroll = QScrollArea(self)
            _set_widget_background(scroll.viewport(), APP_BACKGROUND_COLOR)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(content)

            self._tab_pages[tab_id] = scroll
            self._tab_content_layouts[tab_id] = content_layout

    def _populate_workflow_pages(self) -> None:
        for panel_id, panel in self.panels.items():
            tab_id = self._panel_to_tab.get(panel_id, "Custom")
            layout = self._tab_content_layouts[tab_id]
            layout.insertWidget(max(0, layout.count() - 1), panel)

    def _desired_column_count(self) -> int:
        width = max(self.width(), self.MIN_COLUMN_WIDTH)
        count = max(1, width // self.MIN_COLUMN_WIDTH)
        return min(self.MAX_COLUMNS, len(self.WORKFLOW_ORDER), int(count))

    def _default_layout_for_count(self, count: int) -> list[list[str]]:
        return [list(column) for column in self.DEFAULT_LAYOUTS.get(count, self.DEFAULT_LAYOUTS[self.MAX_COLUMNS])]

    def _load_saved_layout(self) -> list[list[str]] | None:
        raw_value = self._settings.value(self.WORKFLOW_COLUMNS_SETTINGS_KEY, "", type=str)
        if not raw_value:
            return None
        try:
            value = json.loads(raw_value)
        except (TypeError, ValueError):
            return None
        if not isinstance(value, list):
            return None
        normalized: list[list[str]] = []
        used: set[str] = set()
        for column in value:
            if not isinstance(column, list):
                continue
            normalized_column: list[str] = []
            for tab_id in column:
                tab_id = str(tab_id)
                if tab_id in self.WORKFLOW_ORDER and tab_id not in used:
                    normalized_column.append(tab_id)
                    used.add(tab_id)
            normalized.append(normalized_column)
        for tab_id in self.WORKFLOW_ORDER:
            if tab_id not in used:
                if not normalized:
                    normalized.append([])
                normalized[-1].append(tab_id)
        return normalized

    def _save_workflow_layout(self) -> None:
        if self._loading_layout:
            return
        self._settings.setValue(
            self.WORKFLOW_COLUMNS_SETTINGS_KEY,
            json.dumps(self._current_workflow_layout()),
        )

    def _current_workflow_layout(self) -> list[list[str]]:
        columns: list[list[str]] = []
        for tab_widget in self._tab_widgets:
            column: list[str] = []
            tab_bar = tab_widget.tabBar()
            for index in range(tab_widget.count()):
                value = tab_bar.tabData(index)
                if value:
                    column.append(str(value))
            columns.append(column)
        return columns

    def _layout_for_column_count(self, count: int) -> list[list[str]]:
        saved = self._load_saved_layout()
        if saved is None:
            return self._default_layout_for_count(count)

        columns = [list(column) for column in saved]
        if len(columns) > count:
            merged = columns[: count - 1]
            overflow: list[str] = []
            for column in columns[count - 1 :]:
                overflow.extend(column)
            merged.append(overflow)
            columns = merged
        elif len(columns) < count:
            columns = self._expand_layout_to_count(columns, count)
        else:
            columns = self._fill_empty_columns(columns, self._default_layout_for_count(count))
        return columns[:count]

    def _expand_layout_to_count(self, columns: list[list[str]], count: int) -> list[list[str]]:
        expanded = [list(column) for column in columns]
        while len(expanded) < count:
            expanded.append([])
        return self._fill_empty_columns(expanded, self._default_layout_for_count(count))

    def _fill_empty_columns(
        self,
        columns: list[list[str]],
        preferred_layout: list[list[str]],
    ) -> list[list[str]]:
        for empty_index, column in enumerate(columns):
            if column:
                continue

            moved_tabs = self._tabs_for_empty_column(columns, preferred_layout, empty_index)
            if not moved_tabs:
                continue
            columns[empty_index].extend(moved_tabs)
        return columns

    def _tabs_for_empty_column(
        self,
        columns: list[list[str]],
        preferred_layout: list[list[str]],
        empty_index: int,
    ) -> list[str]:
        preferred_tabs = preferred_layout[empty_index] if empty_index < len(preferred_layout) else []
        for source in columns:
            if len(source) <= 1:
                continue
            movable_tabs = [tab_id for tab_id in preferred_tabs if tab_id in source]
            if not movable_tabs or len(source) - len(movable_tabs) < 1:
                continue
            for tab_id in movable_tabs:
                source.remove(tab_id)
            return movable_tabs

        source = max((column for column in columns if len(column) > 1), key=len, default=None)
        if source is None:
            return []
        return [source.pop()]

    def _clear_tab_widgets(self) -> None:
        while self._tab_widgets:
            tab_widget = self._tab_widgets.pop()
            while tab_widget.count():
                tab_widget.removeTab(0)
            self._columns_layout.removeWidget(tab_widget)
            tab_widget.deleteLater()

    def _apply_workflow_layout(self, layout: list[list[str]], *, save: bool) -> None:
        self._loading_layout = True
        try:
            self._clear_tab_widgets()
            for column_index, tab_ids in enumerate(layout):
                tab_widget = WorkflowTabWidget(self, column_index)
                self._columns_layout.addWidget(tab_widget, 1)
                self._tab_widgets.append(tab_widget)
                for tab_id in tab_ids:
                    page = self._tab_pages.get(tab_id)
                    if page is None:
                        continue
                    index = tab_widget.addTab(page, tab_id)
                    tab_widget.tabBar().setTabData(index, tab_id)
        finally:
            self._current_column_count = len(layout)
            self._loading_layout = False
        if save:
            self._save_workflow_layout()

    def _sync_column_count_to_width(self) -> None:
        desired = self._desired_column_count()
        if desired == self._current_column_count:
            return
        self._apply_workflow_layout(self._layout_for_column_count(desired), save=False)

    def move_workflow_tab(self, tab_id: str, target_column: int, target_index: int) -> None:
        if tab_id not in self._tab_pages or not self._tab_widgets:
            return
        target_column = max(0, min(target_column, len(self._tab_widgets) - 1))
        target_widget = self._tab_widgets[target_column]
        source_widget: WorkflowTabWidget | None = None
        source_index = -1
        for tab_widget in self._tab_widgets:
            for index in range(tab_widget.count()):
                if tab_widget.tab_id_at(index) == tab_id:
                    source_widget = tab_widget
                    source_index = index
                    break
            if source_widget is not None:
                break
        if source_widget is None or source_index < 0:
            return

        page = self._tab_pages[tab_id]
        if source_widget is target_widget and source_index < target_index:
            target_index -= 1
        source_widget.removeTab(source_index)
        target_index = max(0, min(target_index, target_widget.count()))
        new_index = target_widget.insertTab(target_index, page, tab_id)
        target_widget.tabBar().setTabData(new_index, tab_id)
        target_widget.setCurrentIndex(new_index)
        self._save_workflow_layout()

    def export_preferences(self) -> dict[str, Any]:
        panel_collapsed: dict[str, bool] = {}
        for panel_id, panel in self.panels.items():
            groupbox = getattr(panel, 'groupbox', None)
            if isinstance(groupbox, CollapsibleGroupBox):
                panel_collapsed[panel_id] = bool(groupbox.collapsed)

        return {
            'workflow_columns': self._current_workflow_layout(),
            'panel_collapsed': panel_collapsed,
        }

    def import_preferences(self, preferences: Mapping[str, Any]) -> None:
        self.validate_preferences(preferences)

        workflow_columns = preferences.get('workflow_columns')
        if workflow_columns is not None:
            layout = self._normalise_workflow_layout(workflow_columns)
            self._apply_workflow_layout(layout, save=True)

        panel_collapsed = preferences.get('panel_collapsed', {})
        if panel_collapsed is not None:
            for panel_id, collapsed in panel_collapsed.items():
                panel = self.panels.get(str(panel_id))
                if panel is None:
                    continue
                groupbox = getattr(panel, 'groupbox', None)
                if isinstance(groupbox, CollapsibleGroupBox):
                    groupbox._apply_collapsed_state(collapsed, animate=False, persist=True)

    def validate_preferences(self, preferences: Mapping[str, Any]) -> None:
        if not isinstance(preferences, Mapping):
            raise ValueError('appearance_layout.controls must be a mapping')

        workflow_columns = preferences.get('workflow_columns')
        if workflow_columns is not None:
            self._normalise_workflow_layout(workflow_columns)

        panel_collapsed = preferences.get('panel_collapsed', {})
        if panel_collapsed is not None:
            if not isinstance(panel_collapsed, Mapping):
                raise ValueError('appearance_layout.controls.panel_collapsed must be a mapping')
            for panel_id, collapsed in panel_collapsed.items():
                if not isinstance(collapsed, bool):
                    raise ValueError(
                        f'appearance_layout.controls.panel_collapsed.{panel_id} must be a boolean'
                    )

    def _normalise_workflow_layout(self, raw_layout: Any) -> list[list[str]]:
        if not isinstance(raw_layout, list):
            raise ValueError('appearance_layout.controls.workflow_columns must be a list')

        normalized: list[list[str]] = []
        used: set[str] = set()
        for raw_column in raw_layout:
            if not isinstance(raw_column, list):
                raise ValueError('appearance_layout.controls.workflow_columns columns must be lists')
            if len(normalized) < self.MAX_COLUMNS:
                column: list[str] = []
                normalized.append(column)
            else:
                column = normalized[-1]
            for raw_tab_id in raw_column:
                tab_id = str(raw_tab_id)
                if tab_id in self.WORKFLOW_ORDER and tab_id not in used:
                    column.append(tab_id)
                    used.add(tab_id)

        if not normalized:
            normalized.append([])
        for tab_id in self.WORKFLOW_ORDER:
            if tab_id not in used:
                normalized[-1].append(tab_id)
        return normalized

    def reveal_panel(self, panel_id: str) -> None:
        if not hasattr(self, "_panel_to_tab"):
            Controls._reveal_legacy_panel(self, panel_id)
            return

        tab_id = self._panel_to_tab.get(panel_id)
        if tab_id is None:
            return
        for tab_widget in self._tab_widgets:
            for index in range(tab_widget.count()):
                if tab_widget.tab_id_at(index) == tab_id:
                    tab_widget.setCurrentIndex(index)
                    panel = self.panels.get(panel_id)
                    page = self._tab_pages.get(tab_id)
                    if isinstance(panel, QWidget) and page is not None:
                        QTimer.singleShot(0, lambda p=page, w=panel: p.ensureWidgetVisible(w))
                    return

    def _reveal_legacy_panel(self, panel_id: str) -> None:
        panel = getattr(self, "panels", {}).get(panel_id)
        if panel is None:
            return

        groupbox = getattr(panel, "groupbox", None)
        if isinstance(groupbox, CollapsibleGroupBox):
            groupbox._apply_collapsed_state(False, animate=False, persist=True)

        layout_manager = getattr(self, "layout_manager", None)
        wrapper_for_id = getattr(layout_manager, "wrapper_for_id", None)
        wrapper = wrapper_for_id(panel_id) if callable(wrapper_for_id) else None
        column = getattr(wrapper, "column", None)
        column_name = getattr(column, "name", None)
        scroll = getattr(self, "_column_scrolls", {}).get(column_name)
        ensure_visible = getattr(scroll, "ensureWidgetVisible", None)
        if callable(ensure_visible):
            ensure_visible(wrapper if wrapper is not None else panel)

    def reset_to_defaults(self) -> None:
        self._settings.remove(self.WORKFLOW_COLUMNS_SETTINGS_KEY)
        for panel in self.panels.values():
            groupbox = getattr(panel, "groupbox", None)
            if isinstance(groupbox, CollapsibleGroupBox):
                self._settings.remove(groupbox.settings_key)
                groupbox.reset_to_default()
        self._apply_workflow_layout(self._default_layout_for_count(self._desired_column_count()), save=False)

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._sync_column_count_to_width()
