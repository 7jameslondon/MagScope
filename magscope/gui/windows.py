import sys
import traceback
from time import time
from typing import Iterable
from warnings import warn

import numpy as np
from PyQt6.QtCore import QPoint, QSettings, Qt, QThread, QTimer
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLayout, QMainWindow,
                             QMessageBox, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from magscope._logging import get_logger
from magscope.datatypes import VideoBuffer
from magscope.gui import (AcquisitionPanel, BeadGraphic, BeadSelectionPanel, CameraPanel,
                          ControlPanelBase, GripSplitter, HistogramPanel, PlotWorker,
                          ResizableLabel, ScriptPanel, StatusPanel, TimeSeriesPlotBase, VideoViewer)
from magscope.gui.controls import (HelpPanel, PlotSettingsPanel, ProfilePanel, XYLockPanel,
                                   ZLockPanel, ZLUTGenerationPanel)
from magscope.gui.panel_layout import (PANEL_MIME_TYPE, PanelLayoutManager, PanelWrapper,
                                       ReorderableColumn)
from magscope.processes import ManagerProcessBase
from magscope.scripting import ScriptStatus, registerwithscript
from magscope.utils import AcquisitionMode, Message, numpy_type_to_qt_image_type

logger = get_logger("gui.windows")

class WindowManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self._bead_graphics: dict[int, BeadGraphic] = {}
        self._bead_next_id: int = 0
        self.beads_in_view_on = False
        self.beads_in_view_count = 1
        self.beads_in_view_marker_size = 100
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
        self._timer: QTimer | None = None
        self._video_buffer_last_index: int = 0
        self._video_viewer_need_reset: bool = True
        self.video_viewer: VideoViewer | None = None
        self.windows: list[QMainWindow] = []


    def setup(self):
        self.qt_app = QApplication.instance()
        if not self.qt_app:
            self.qt_app = QApplication(sys.argv)
        QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)

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

        # Connect the video viewer
        self.video_viewer.coordinatesChanged.connect(self.update_view_coords)
        self.video_viewer.clicked.connect(self.callback_view_clicked)

        # Timer
        self._timer = QTimer()
        self._timer.timeout.connect(self.do_main_loop)  # noqa
        self._timer.setInterval(0)
        self._timer.start()

        # Timer - Video Display
        timer_video_view = QTimer()
        timer_video_view.timeout.connect(self._update_view_and_hist)
        timer_video_view.setInterval(25)
        timer_video_view.start()

        # Start app
        self._running = True
        self.qt_app.exec()

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
        # Because the WindowManager is a special case with a GUI
        # the main loop is actually called by a timer, not the
        # run method of it's super()

        if self._running:
            self._update_display_rate()
            self.update_video_buffer_status()
            self.update_video_processors_status()
            self.controls.profile_panel.update_plot()
            self.receive_ipc()


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

    def callback_view_clicked(self, pos: QPoint):
        if not self.controls.bead_selection_panel.lock_button.isChecked():
            self.add_bead(pos)

    def set_bead_rois(self, _):
        pass

    def update_bead_rois(self):
        bead_rois = {}
        for id, graphic in self._bead_graphics.items():
            tl = graphic.mapToScene(graphic.rect().topLeft())
            br = graphic.mapToScene(graphic.rect().bottomRight())
            x0 = int(round(tl.x() - graphic.pen_width / 2))
            x1 = int(round(br.x() + graphic.pen_width / 2))
            y0 = int(round(tl.y() - graphic.pen_width / 2))
            y1 = int(round(br.y() + graphic.pen_width / 2))
            bead_rois[id] = (x0, x1, y0, y1)
        self._bead_rois = bead_rois
        message = Message(ManagerProcessBase, ManagerProcessBase.set_bead_rois, bead_rois)
        self.send_ipc(message)

    def move_bead(self, id: int, dx, dy):
        # Move the bead
        self._bead_graphics[id].move(dx, dy)

        # Update the ROIs
        self.update_bead_rois()

        # Confirm with the xy-lock
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.remove_bead_from_xy_lock_pending_moves,
            args=(id,),
        )
        self.send_ipc(message)

    def add_bead(self, pos: QPoint):
        # Add a bead graphic
        id = self._bead_next_id
        x = pos.x()
        y = pos.y()
        w = self.settings['bead roi width']
        view_scene = self.video_viewer.scene
        graphic = BeadGraphic(self, id, x, y, w, view_scene)
        self._bead_graphics[id] = graphic
        self._bead_next_id += 1

        # Update the bead ROIs
        self.update_bead_rois()

    def remove_bead(self, id: int):
        # Update graphics
        graphic = self._bead_graphics.pop(id)
        graphic.remove()

        # Update bead ROIs
        rois = self._bead_rois
        rois.pop(id)
        message = Message(ManagerProcessBase, ManagerProcessBase.set_bead_rois, rois)
        self.send_ipc(message)

    def clear_beads(self):
        # Update graphics
        for graphics in self._bead_graphics.values():
            graphics.remove()
        self._bead_graphics.clear()
        self._bead_next_id = 0

        # Update bead ROIs
        message = Message(ManagerProcessBase, ManagerProcessBase.set_bead_rois, {})
        self.send_ipc(message)

    def lock_beads(self, locked: bool):
        for graphic in self._bead_graphics.values():
            graphic.locked = locked

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

    def update_camera_setting(self, name: str, value: str):
        self.controls.camera_panel.update_camera_setting(name, value)

    def update_video_buffer_purge(self, t: float):
        self.controls.status_panel.update_video_buffer_purge(t)

    def update_script_status(self, status: ScriptStatus):
        self.controls.script_panel.update_status(status)

    @registerwithscript('print')
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

    def set_acquisition_on(self, value: bool):
        super().set_acquisition_on(value)
        checkbox = self.controls.acquisition_panel.acquisition_on_checkbox.checkbox
        checkbox.blockSignals(True) # to prevent a loop
        checkbox.setChecked(value)
        checkbox.blockSignals(False)

    def set_acquisition_dir(self, value: str):
        super().set_acquisition_dir(value)
        textedit = self.controls.acquisition_panel.acquisition_dir_textedit
        textedit.blockSignals(True) # to prevent a loop
        textedit.setText(value)
        textedit.blockSignals(False)

    def set_acquisition_dir_on(self, value: bool):
        super().set_acquisition_dir_on(value)
        checkbox = self.controls.acquisition_panel.acquisition_dir_on_checkbox.checkbox
        checkbox.blockSignals(True)  # to prevent a loop
        checkbox.setChecked(value)
        checkbox.blockSignals(False)

    def set_acquisition_mode(self, value: AcquisitionMode):
        super().set_acquisition_mode(value)
        combobox = self.controls.acquisition_panel.acquisition_mode_combobox
        combobox.blockSignals(True)  # to prevent a loop
        combobox.setCurrentText(value)
        combobox.blockSignals(False)

    def update_xy_lock_enabled(self, value: bool):
        self.controls.xy_lock_panel.update_enabled(value)

    def update_xy_lock_interval(self, value: float):
        self.controls.xy_lock_panel.update_interval(value)

    def update_xy_lock_max(self, value: float):
        self.controls.xy_lock_panel.update_max(value)

    def update_z_lock_enabled(self, value: bool):
        self.controls.z_lock_panel.update_enabled(value)

    def update_z_lock_bead(self, value: int):
        self.controls.z_lock_panel.update_bead(value)

    def update_z_lock_target(self, value: float):
        self.controls.z_lock_panel.update_target(value)

    def update_z_lock_interval(self, value: float):
        self.controls.z_lock_panel.update_interval(value)

    def update_z_lock_max(self, value: float):
        self.controls.z_lock_panel.update_max(value)


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

    def __init__(self, manager: WindowManager):
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

        self._add_column("left", pinned_ids={"HelpPanel"}, index=0)
        for name in stored_layout.keys():
            if name in self.layout_manager.columns:
                continue
            self._add_column(name)
        if "right" not in self.layout_manager.columns and len(self.layout_manager.columns) < 2:
            self._add_column("right")

        # Instantiate standard panels
        self.help_panel = HelpPanel(self.manager)
        self.acquisition_panel = AcquisitionPanel(self.manager)
        self.bead_selection_panel = BeadSelectionPanel(self.manager)
        self.camera_panel = CameraPanel(self.manager)
        self.histogram_panel = HistogramPanel(self.manager)
        self.plot_settings_panel = PlotSettingsPanel(self.manager)
        self.profile_panel = ProfilePanel(self.manager)
        self.script_panel = ScriptPanel(self.manager)
        self.status_panel = StatusPanel(self.manager)
        self.xy_lock_panel = XYLockPanel(self.manager)
        self.z_lock_panel = ZLockPanel(self.manager)
        self.z_lut_generation_panel = ZLUTGenerationPanel(self.manager)

        definitions: list[tuple[str, QWidget, str, bool]] = [
            ("HelpPanel", self.help_panel, "left", False),
            ("StatusPanel", self.status_panel, "left", True),
            ("CameraPanel", self.camera_panel, "left", True),
            ("AcquisitionPanel", self.acquisition_panel, "left", True),
            ("HistogramPanel", self.histogram_panel, "left", True),
            ("BeadSelectionPanel", self.bead_selection_panel, "left", True),
            ("ProfilePanel", self.profile_panel, "left", True),
            ("PlotSettingsPanel", self.plot_settings_panel, "right", True),
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
