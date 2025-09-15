import numpy as np
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QMessageBox,
                             QHBoxLayout, QPushButton, QLabel, QTextEdit, QGroupBox, QSplitter, QLayout)
from PyQt6.QtCore import QObject, QPoint, QPointF, QTimer, pyqtSignal, Qt
from PyQt6.QtGui import QImage, QPixmap, QGuiApplication
import sys
from time import time
from warnings import warn

from magscope import AcquisitionMode
from magscope.datatypes import VideoBuffer
from magscope.gui import (VideoViewer, Plots, CameraSettingsPanel, GripSplitter, BeadSelectionPanel, AcquisitionPanel,
                          ObjectiveMotorPanel, LinearMotorPanel, RotaryMotorPanel, ScriptPanel, ZlutPanel,
                          ForceCalibartionPanel, HistogramPanel, PlotSettingsPanel, CollapsibleGroupBox, StatusPanel,
                          BeadGraphic)
from magscope.processes import ManagerProcess
from magscope.scripting import ScriptStatus, registerwithscript
from magscope.utils import Message, numpy_type_to_qt_image_type


class WindowManager(ManagerProcess):
    def __init__(self):
        super().__init__()
        self._bead_graphics: dict[int, BeadGraphic] = {}
        self._bead_next_id: int = 0
        self.central_widgets: list[QWidget] = []
        self.central_layouts: list[QLayout] = []
        self.controls: Controls | None = None
        self._display_rate_counter: int = 0
        self._display_rate_last_time: float = time()
        self._display_rate_last_rate: float = 0
        self._n_windows: int | None = None
        self.plots: Plots | None = None
        self.qt_app: QApplication | None = None
        self._timer: QTimer | None = None
        self._video_buffer: VideoBuffer | None = None
        self._video_buffer_last_index: int = 0
        self.video_viewer: VideoViewer | None = None
        self.windows: list[QMainWindow] = []

    def run(self):
        super().run()

        self.qt_app = QApplication.instance()
        if not self.qt_app:
            self.qt_app = QApplication(sys.argv)
        QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)

        # Dark GUI Style
        style_path = os.path.join(os.path.dirname(__file__), 'style.qss')
        with open(style_path, 'r') as f:
            self.qt_app.setStyleSheet(f.read())

        # If the number of windows is not specified, then use the number of screens
        if self._n_windows is None:
            self._n_windows = len(QApplication.screens())

        # Create the GUI widgets
        self.controls = Controls(self)
        self.video_viewer = VideoViewer()
        self.plots = Plots()

        # Create the layouts for each window
        self.create_central_widgets()

        # Create the windows
        for i in range(self._n_windows):
            window = QMainWindow()
            window.setWindowTitle(f"MagScope: Window {i}")
            screen = QApplication.screens()[i % len(QApplication.screens())]
            geometry = screen.geometry()
            window.setGeometry(geometry.x(), geometry.y(), geometry.width(), geometry.height())
            window.closeEvent = lambda _, w=window: self.quit()
            window.showMaximized()
            window.setCentralWidget(self.central_widgets[i])
            self.windows.append(window)

        # Connect the video viewer
        self.video_viewer.coordinatesChanged.connect(self.update_view_coords)
        self.video_viewer.clicked.connect(self.callback_view_clicked)

        # Pipe timer
        self._timer = QTimer()
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._do_main_loop) # noqa PyUnresolvedReferences
        self._timer.start()

        return self.qt_app.exec()

    def quit(self):
        super().quit()
        for window in self.windows:
            window.close()

    def _do_main_loop(self):
        if self._running:
            self._update_view_and_hist()
            self._update_display_rate()
            self.update_video_buffer_status()
            self._check_pipe()

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
        right_top_layout.addWidget(self.plots)

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
        self.central_layouts[1].addWidget(self.plots)

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
        self.central_layouts[2].addWidget(self.plots)

    def update_view_coords(self):
        pass

    def _update_view_and_hist(self):
        # Get image and _write position
        index, image_bytes = self._video_buffer.peak_image()

        # Check if _write has changed (a new image is ready)
        if self._video_buffer_last_index != index:
            # Update the stored index
            self._video_buffer_last_index = index

            cam_bits = self._camera_type.bits
            dtype_bits = np.iinfo(self._video_buffer.dtype).bits
            scale = (2 ** (dtype_bits - cam_bits))

            # Update the view
            qt_img = QImage(
                np.frombuffer(image_bytes, self._video_buffer.dtype).copy() *
                scale, *self._video_buffer.image_shape,
                numpy_type_to_qt_image_type(self._video_buffer.dtype))
            self.video_viewer.set_pixmap(QPixmap.fromImage(qt_img))

            # Update the histogram
            self.controls.histogram_panel.update(image_bytes)

            # Increment the display rate counter
            self._display_rate_counter += 1

    def callback_view_clicked(self, pos: QPoint):
        if not self.controls.bead_panel.lock_button.isChecked():
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
        message = Message(ManagerProcess, ManagerProcess.set_bead_rois, bead_rois)
        self._send(message)

    def move_bead(self, id: int, x, y):
        graphic = self._bead_graphics[id]
        graphic.moveBy(x, y)

    def add_bead(self, pos: QPoint):
        # Add a bead graphic
        id = self._bead_next_id
        x = pos.x()
        y = pos.y()
        w = self._settings['bead roi width']
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
        message = Message(ManagerProcess, ManagerProcess.set_bead_rois, rois)
        self._send(message)

    def clear_beads(self):
        # Update graphics
        for graphics in self._bead_graphics.values():
            graphics.remove()
        self._bead_graphics.clear()
        self._bead_next_id = 0

        # Update bead ROIs
        message = Message(ManagerProcess, ManagerProcess.set_bead_rois, {})
        self._send(message)

    def lock_beads(self, locked: bool):
        for graphic in self._bead_graphics.values():
            graphic.locked = locked

    def update_video_processors_status(self, text):
        self.controls.status_panel.update_video_processors_status(text)

    def update_video_buffer_status(self):
        level = self._video_buffer.get_level()
        size = self._video_buffer.n_total_images
        text = f'{level:.0%} full, {size} max images'
        self.controls.status_panel.update_video_buffer_status(text)

    def _update_display_rate(self):
        # If it has been more than a second, re-calculate the display rate
        if (t := time()) - self._display_rate_last_time > 1:
            dt = t - self._display_rate_last_time
            rate = self._display_rate_counter / dt
            self._display_rate_last_time = t
            self._display_rate_counter = 0
            self._display_rate_last_rate = rate
            self.controls.status_panel.update_display_rate(f'{rate:.0f} updates/sec')
        else:
            # This is used to force the "..." to update
            self.controls.status_panel.update_display_rate(f'{self._display_rate_last_rate:.0f} updates/sec')

    def update_camera_setting(self, name: str, value: str):
        self.controls.cam_panel.update_camera_setting(name, value)

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
            print(f'{text}: {details}')
            msg.setDetailedText(details)
        else:
            print(f'{text}')
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


class Controls(QWidget):
    def __init__(self, parent: WindowManager):
        super().__init__()
        self._parent:WindowManager = parent

        # Columns
        layout = QHBoxLayout()
        layout.setSpacing(2)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)
        self.columns = [QVBoxLayout(), QVBoxLayout()]
        for column in self.columns:
            column.setSpacing(2)
            column.setContentsMargins(0, 0, 0, 0)
            column_widget = QWidget()
            column_widget.setStyleSheet('CollapsibleGroupBox{margin: 0;}')
            column_widget.setContentsMargins(0, 0, 0, 0)
            column_widget.setLayout(column)
            column_widget.setFixedWidth(300)
            layout.addWidget(column_widget)
        layout.addStretch(1)

        # Add control panels
        self.status_panel = StatusPanel(self._parent)
        self.add_panel(self.status_panel, column_id=0)

        self.cam_panel = CameraSettingsPanel(self._parent)
        self.add_panel(self.cam_panel, column_id=0)

        self.acquisition_panel = AcquisitionPanel(self._parent)
        self.add_panel(self.acquisition_panel, column_id=1)

        self.bead_panel = BeadSelectionPanel(self._parent)
        self.add_panel(self.bead_panel, column_id=1)

        self.histogram_panel = HistogramPanel(self._parent)
        self.add_panel(self.histogram_panel, column_id=1)

        self.script_panel = ScriptPanel(self._parent)
        self.add_panel(self.script_panel, column_id=1)

        # Add a stretch to the bottom of each column
        for column in self.columns:
            column.addStretch(1)

        # Create Panels
        # self.obj_panel = ObjectiveMotorPanel(self)
        # self.lin_panel = LinearMotorPanel(self)
        # self.rot_panel = RotaryMotorPanel(self)
        # self.script_panel = ScriptPanel(self)
        # self.zlut_panel = ZlutPanel(self)
        # self.force_calibration_panel = ForceCalibartionPanel(self)
        # self.hist_panel = HistogramPanel(self)
        # self.plot_settings_panel = PlotSettingsPanel(self)

    @property
    def settings(self):
        return self._parent._settings

    @settings.setter
    def settings(self, value):
        raise AttributeError("Read-only attribute.")

    def add_panel(self, panel, column_id: int):
        self.columns[column_id].addWidget(panel.groupbox)