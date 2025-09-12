from __future__ import annotations
import numpy as np
import os
from pathlib import Path
import pyqtgraph as pg
import time
import traceback
from typing import TYPE_CHECKING
from warnings import warn
from PyQt6.QtCore import Qt, QObject, QPoint, QRectF, QTimer, QVariant, pyqtSignal, QSettings, QPropertyAnimation
from PyQt6.QtGui import (QBrush, QColor, QCursor, QDoubleValidator, QGuiApplication,
                         QImage, QIntValidator, QPixmap, QTextOption)
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFrame,
                             QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
                             QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QMainWindow, QProgressDialog, QPushButton,
                             QRadioButton, QTextEdit, QVBoxLayout, QWidget, QComboBox, QProgressBar,
                             QGraphicsOpacityEffect)

from magscope import Message, AcquisitionMode, ManagerProcess
from magscope.gui import (CollapsibleGroupBox, FlashingLabel, LabeledCheckbox, LabeledLineEditWithValue,
                          LabeledStepperLineEdit, LabeledLineEdit)
from magscope.gui.widgets import FlashLabel

# Import only for the type check to avoid circular import
if TYPE_CHECKING:
    from magscope.gui.windows import WindowManager

class ControlPanel(QObject):
    def __init__(self, parent: WindowManager):
        super().__init__()
        self._parent: WindowManager = parent


class AcquisitionPanel(ControlPanel):
    no_file_str = 'No directory to save to selected'

    def __init__(self, parent: WindowManager):
        super().__init__(parent)

        # Group Box
        self.groupbox = CollapsibleGroupBox('Acquisition', collapsed=False)

        # Layout
        self.layout = QVBoxLayout()
        self.groupbox.setContentLayout(self.layout)

        # --- Row 0 ---
        self.layout_row_0 = QHBoxLayout()
        self.layout.addLayout(self.layout_row_0)

        # Acquisition On Checkbox
        self.acquisition_on_checkbox = LabeledCheckbox(
            label_text='Acquire',
            default=self._parent._acquisition_on,
            callback=self.callback_acquisition_on)
        self.layout_row_0.addWidget(self.acquisition_on_checkbox)

        # Mode group selection
        mode_layout = QHBoxLayout()
        self.layout_row_0.addLayout(mode_layout)
        mode_label = QLabel('Mode:')
        mode_layout.addWidget(mode_label)
        self.acquisition_mode_combobox = QComboBox()
        mode_layout.addWidget(self.acquisition_mode_combobox, stretch=1)
        modes = [AcquisitionMode.TRACK,
                 AcquisitionMode.TRACK_AND_CROP_VIDEO,
                 AcquisitionMode.TRACK_AND_FULL_VIDEO,
                 AcquisitionMode.CROP_VIDEO,
                 AcquisitionMode.FULL_VIDEO]
        for mode in modes:
            self.acquisition_mode_combobox.addItem(mode)
        self.acquisition_mode_combobox.setCurrentText(self._parent._acquisition_mode)
        self.acquisition_mode_combobox.currentIndexChanged.connect(self.callback_acquisition_mode) # type: ignore

        # --- Row 1 ---
        self.layout_row_1 = QHBoxLayout()
        self.layout.addLayout(self.layout_row_1)

        # Acquisition Directory On Checkbox
        self.acquisition_dir_on_checkbox = LabeledCheckbox(
            label_text='Save',
            default=self._parent._acquisition_dir_on,
            callback=self.callback_acquisition_dir_on)
        self.layout_row_1.addWidget(self.acquisition_dir_on_checkbox)

        # Acquisition - Folder selector
        self.acquisition_dir_button = QPushButton('Select Directory to Save To')
        self.acquisition_dir_button.setMinimumWidth(200)
        self.acquisition_dir_button.clicked.connect(self.callback_acquisition_dir)  # type: ignore
        self.layout_row_1.addWidget(self.acquisition_dir_button)

        self.acquisition_dir_textedit = QTextEdit(self.no_file_str)
        self.acquisition_dir_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.acquisition_dir_textedit.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.acquisition_dir_textedit.setFixedHeight(40)
        self.acquisition_dir_textedit.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.acquisition_dir_textedit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout.addWidget(self.acquisition_dir_textedit)

    def callback_acquisition_on(self):
        value: bool = self.acquisition_on_checkbox.checkbox.isChecked()
        self._parent._send(Message(ManagerProcess, ManagerProcess.set_acquisition_on, value))

    def callback_acquisition_dir_on(self):
        value: bool = self.acquisition_dir_on_checkbox.checkbox.isChecked()
        self._parent._send(Message(ManagerProcess, ManagerProcess.set_acquisition_dir_on, value))

    def callback_acquisition_mode(self):
        value: AcquisitionMode = self.acquisition_mode_combobox.currentText()
        self._parent._send(Message(ManagerProcess, ManagerProcess.set_acquisition_mode, value))

    def callback_acquisition_dir(self):
        settings = QSettings('MagScope', 'MagScope')
        last_value = settings.value('last acquisition_dir',
                                    os.path.expanduser("~"),
                                    type=str)
        value = QFileDialog.getExistingDirectory(None,
                                                 'Select Folder',
                                                 last_value)
        if value:
            self.acquisition_dir_textedit.setText(value)
            settings.setValue('last acquisition_dir', QVariant(value))
        else:
            value = None
            self.acquisition_dir_textedit.setText(self.no_file_str)
        self._parent._send(Message(ManagerProcess, ManagerProcess.set_acquisition_dir, value))


class BeadSelectionPanel(ControlPanel):

    auto_center_signal: 'pyqtSignal' = pyqtSignal(bool)
    center_signal: 'pyqtSignal' = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)

        # Groupbox
        self.groupbox = CollapsibleGroupBox('Bead Selection', collapsed=False)

        # Layout
        self.layout = QVBoxLayout()
        self.groupbox.setContentLayout(self.layout)

        # Instructions
        instructions = """
        Add a bead: left-click on the video
        Remove a bead: right-click on the bead
        """
        instructions = '\n'.join([l.strip() for l in instructions.splitlines()]).strip()
        self.layout.addWidget(QLabel(instructions))

        # Lock/Unlock
        self.lock_button = QPushButton('🔓')
        self.lock_button.setCheckable(True)
        self.lock_button.setStyleSheet("""
            QPushButton:checked {
            background-color: #333;
            }""")
        self.lock_button.clicked.connect(self.callback_lock)  # type: ignore
        self.layout.addWidget(self.lock_button)

        # Remove All Beads
        self.clear_button = QPushButton('Remove All Beads')
        self.clear_button.setEnabled(True)
        self.clear_button.clicked.connect(self._parent.clear_beads)  # type: ignore
        self.layout.addWidget(self.clear_button)

        # # Center beads
        # self.center_beads_button = QPushButton('Center beads')
        # self.center_beads_button.setToolTip(
        #     'The beads must be locked, the camera must be acquiring, and in a mode that tracks.'
        # )
        # self.center_beads_button.clicked.connect(self.callback_center) # type: ignore
        # self.center_beads_button.setEnabled(False)
        # self.layout.addWidget(self.center_beads_button)
        #
        # # Auto-center beads
        # self.auto_center_checkbox = QCheckBox('Auto center beads')
        # self.auto_center_checkbox.setToolTip(
        #     'Automatically centers the beads ROIs every 10 seconds. The camera must be acquiring and in a mode that tracks. This is particularly useful when performing long timelapses to compensate for stage drift.'
        # )
        # self.auto_center_checkbox.toggled.connect(self.callback_auto_center)
        # self.auto_center_checkbox.setEnabled(False)
        # self.layout.addWidget(self.auto_center_checkbox)

    def callback_lock(self):
        locked = self.lock_button.isChecked()
        text = '🔒' if locked else '🔓'
        self.lock_button.setText(text)
        self.clear_button.setEnabled(not locked)
        self._parent.lock_beads(locked)

    def callback_center(self):
        self.center_signal.emit()

    def callback_auto_center(self, state):
        self.auto_center_signal.emit(state)


class CameraSettingsPanel(ControlPanel):

    def __init__(self, parent: WindowManager):
        super().__init__(parent)

        self.groupbox = CollapsibleGroupBox('Camera Settings',
                                                         collapsed=False)
        self.layout = QVBoxLayout()
        self.layout.setSpacing(2)
        self.groupbox.setContentLayout(self.layout)
        
        # Individual controls
        self.settings = {}
        for name in self._parent._camera_type.settings:
            self.settings[name] = LabeledLineEditWithValue(
                label_text=name,
                widths=(0, 100, 50),
                callback=lambda n=name:self.callback_set_camera_setting(n))
            self.layout.addWidget(self.settings[name])

        # Refresh button
        self.refresh_button = QPushButton('↺')
        self.refresh_button.setFlat(True)
        self.refresh_button.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }")
        self.refresh_button.clicked.connect(self.callback_refresh) # noqa PyUnresolvedReferences
        self.layout.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignRight)
            
    def callback_refresh(self):
        for name in self._parent._camera_type.settings:
            from magscope import CameraManager
            message = Message(to=CameraManager,
                              func=CameraManager.get_camera_setting,
                              args=(name,))
            self._parent._send(message)

    def callback_set_camera_setting(self, name):
        value = self.settings[name].lineedit.text()
        if value == '':
            return
        self.settings[name].lineedit.setText('')
        self.settings[name].value_label.setText('')
        from magscope import CameraManager
        message = Message(to=CameraManager,
                          func=CameraManager.set_camera_setting,
                          args=(name, value))
        self._parent._send(message)
        
    def update_camera_setting(self, name: str, value: str):
        self.settings[name].value_label.setText(value)


class StatusPanel(ControlPanel):
    def __init__(self, parent: WindowManager):
        super().__init__(parent)
        self.groupbox = CollapsibleGroupBox('Status')
        self.layout = QVBoxLayout()
        self.layout.setSpacing(0)
        self.groupbox.setContentLayout(self.layout)
        self.dot = 0

        # GUI display rate
        self.display_rate_status = QLabel()
        self.layout.addWidget(self.display_rate_status)

        # Video Processors
        self.video_processors_status = QLabel()
        self.layout.addWidget(self.video_processors_status)

        # Video Buffer
        self.video_buffer_status = QLabel()
        self.layout.addWidget(self.video_buffer_status)
        self.video_buffer_status_bar = QProgressBar()
        self.video_buffer_status_bar.setOrientation(Qt.Orientation.Horizontal)
        self.layout.addWidget(self.video_buffer_status_bar)

        # Video Buffer Purge
        self.video_buffer_purge_label = FlashLabel('Video Buffer Purged at: ')
        self.layout.addWidget(self.video_buffer_purge_label)

    def update_display_rate(self, text):
        self.dot = (self.dot + 1) % 4
        dot_text = '.'*self.dot
        self.display_rate_status.setText(f'Display Rate: {text} {dot_text}')

    def update_video_processors_status(self, text):
        self.video_processors_status.setText(f'Video Processors: {text}')

    def update_video_buffer_status(self, text):
        self.video_buffer_status.setText(f'Video Buffer: {text}')
        value = int(text.split('%')[0])
        self.video_buffer_status_bar.setValue(value)

    def update_video_buffer_purge(self, t: float):
        string = time.strftime("%I:%M:%S %p", time.localtime(t))
        self.video_buffer_purge_label.setText(f'Video Buffer Purged at: {string}')



class ForceCalibartionPanel:
    no_file_str = 'No force calibrant selected'

    def __init__(self, parent):
        self._parent = parent

        # Panel
        self.groupbox = CollapsibleGroupBox('Force Calibration')

        # Layout - Panel
        layout = QVBoxLayout()
        self.groupbox.setContentLayout(layout)

        # Load Button
        load_button = QPushButton('Load Force Calibrant')
        load_button.clicked.connect(self.load)  # type: ignore
        layout.addWidget(load_button)

        # File path
        self.filepath_textedit = QTextEdit(self.no_file_str)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filepath_textedit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filepath_textedit.setFixedHeight(40)
        self.filepath_textedit.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.filepath_textedit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.filepath_textedit)

        # Plot Button
        plot_button = QPushButton('Plot')
        plot_button.clicked.connect(self.plot)  # type: ignore
        layout.addWidget(plot_button)

        # Force target
        layout.addWidget(QLabel('_' * 52))
        self.target = LabeledLineEdit(label_text='Target(pN)',
                                                   callback=self.set_target)
        layout.addWidget(self.target)

        # Force ramp
        layout.addWidget(QLabel('_' * 52))

        button_layout = QHBoxLayout()
        layout.addLayout(button_layout)

        self.run_fr_button = QPushButton('Ramp A->B')
        self.run_fr_button.clicked.connect(lambda: self.run_force_ramp('A->B'))
        button_layout.addWidget(self.run_fr_button)

        self.run_bw_button = QPushButton('Ramp A<-B')
        self.run_bw_button.clicked.connect(lambda: self.run_force_ramp('A<-B'))
        button_layout.addWidget(self.run_bw_button)

        #
        start_end_layout = QHBoxLayout()
        layout.addLayout(start_end_layout)

        self.start = LabeledLineEdit(label_text='A(pN)')
        start_end_layout.addWidget(self.start)

        self.end = LabeledLineEdit(label_text='B(pN)')
        start_end_layout.addWidget(self.end)

        self.rate = LabeledLineEdit(label_text='Rate(pN/s)')
        layout.addWidget(self.rate)

    def load(self):
        last_path = self._parent.app.settings.value('last_force_calibrant_load_filepath',
                                                    os.path.expanduser("~"))

        path, _ = QFileDialog.getOpenFileName(self._parent,
                                              'Select Force Calibrant',
                                              last_path,
                                              'Force Calibrant (*.txt)')

        try:
            self._parent.app.force_converter.load(path)
        except Exception as e:
            print(e)
            path = None

        if not path:  # user selected a file
            path = self.no_file_str
            self._parent.app.force_converter.unload()
        self._parent.app.settings.setValue('last_force_calibrant_load_filepath',
                                           QVariant(path))
        self.filepath_textedit.setText(path)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def plot(self):
        self._parent.app.force_converter.plot()

    def set_target(self):
        target_text = self.target.lineedit.text()
        if len(target_text) == 0: return
        target = float(target_text)

        speed_text = self._parent.lin_panel.speed.lineedit.text()
        if len(speed_text) == 0: speed_text = 1
        speed = float(speed_text)

        self._parent.app.move_motor_signal.emit('linear_motor', 'force_clamp',
                                                (target, speed))

    def run_force_ramp(self, direction):
        start_text = self.start.lineedit.text()
        if len(start_text) == 0: return
        start = float(start_text)

        stop_text = self.end.lineedit.text()
        if len(stop_text) == 0: return
        stop = float(stop_text)

        rate_text = self.rate.lineedit.text()
        if len(rate_text) == 0: return
        rate = float(rate_text)

        speed_text = self._parent.lin_panel.speed.lineedit.text()
        if len(speed_text) == 0: speed_text = 1
        speed = float(speed_text)

        if direction == 'A->B':
            self._parent.app.move_motor_signal.emit('linear_motor', 'force_ramp',
                                                    (start, stop, rate, speed))
        elif direction == 'A<-B':
            self._parent.app.move_motor_signal.emit('linear_motor', 'force_ramp',
                                                    (stop, start, rate, speed))


class HistogramPanel:

    def __init__(self, parent):
        self._parent = parent

        # Groupbox
        self.groupbox = CollapsibleGroupBox('Histogram')

        # Layout
        layout = QVBoxLayout()
        self.groupbox.setContentLayout(layout)

        # Enable
        self.enable = LabeledCheckbox(label_text='Enabled',
                                                   callback=self.clear,
                                                   widths=(50, 0))
        layout.addWidget(self.enable)

        # Only beads
        self.only_beads = LabeledCheckbox(
            label_text='Only Measure Bead ROIs', default=True)
        layout.addWidget(self.only_beads)

        # Histogram
        self.n_bins = 256
        self.histogram_widget = pg.PlotWidget()
        self.histogram_widget.setMaximumHeight(150)
        self.histogram_widget.setLabel('left', 'Count')
        self.histogram_widget.setLabel('bottom', 'Intensity')
        self.histogram_widget.getAxis('left').setStyle(showValues=False,
                                                       tickLength=0)
        self.histogram_widget.getAxis('bottom').setStyle(showValues=False,
                                                         tickLength=0)
        self.histogram_widget.setMouseEnabled(False, False)
        self.histogram_widget.setMenuEnabled(False)
        self.histogram_widget.hideButtons()
        self.histogram_widget.getViewBox().setMouseEnabled(False, False)
        self.histogram_widget.getViewBox().setMenuEnabled(False)
        self.histogram_widget.enableAutoRange(False)
        layout.addWidget(self.histogram_widget)
        bins = np.arange(self.n_bins)
        self.histogram_item = pg.BarGraphItem(
            x0=bins,  # Left edges of bins
            x1=bins + 1,  # Right edges of bins
            height=np.zeros(self.n_bins),
            brush='w')
        self.histogram_widget.addItem(self.histogram_item)

    def update(self, data):
        if self.enable.checkbox.isChecked() and not self.groupbox.collapsed:
            dtype = magscope.camera.ImplementedCamera.dtype
            max_int = 2**magscope.camera.ImplementedCamera.bits
            shape = self._parent.app.video_buf.image_shape
            image = np.frombuffer(data, dtype).reshape(shape)

            if self.only_beads.checkbox.isChecked():
                bead_rois = self._parent.app.bead_manager.rois
                if bead_rois.shape[0] > 0:
                    image = magscope.utils.crop_stack_to_rois(
                        np.swapaxes(image, 0, 1)[:, :, None], bead_rois[:, 1:])
                else:
                    self.clear()
                    return

            binned_data, _ = np.histogram(image, bins=256, range=(0, max_int))
            # fast safe log to prevent log(0)
            binned_data = np.log(binned_data + 1)
            self.histogram_item.setOpts(height=binned_data)

    def clear(self):
        self.histogram_item.setOpts(height=np.zeros(self.n_bins))


class LinearMotorPanel:

    def __init__(self, parent):
        self._parent = parent

        # Warning Label
        self.warning_label = FlashingLabel(
            'WARNING: Not Connected!!!')

        self.speed = LabeledLineEdit(
            label_text='Speed (mm/s)',
            default=str(self._parent.settings['linear motor default speed']),
            validator=QDoubleValidator(
                float(self._parent.settings['linear motor default speed']),
                float(self._parent.settings['linear motor max speed']),
                int(self._parent.settings['linear motor position decimal places'])))

        self.target_min = LabeledLineEdit(
            label_text='Target from Zero (mm)',
            validator=QDoubleValidator(
                float(self._parent.settings['linear motor min position']),
                float(self._parent.settings['linear motor max position']),
                int(self._parent.settings['linear motor position decimal places'])),
            callback=self.callback_move_absolute_min,
        )

        self.target_max = LabeledLineEdit(
            label_text='Target from Max (mm)',
            validator=QDoubleValidator(
                float(self._parent.settings['linear motor min position']),
                float(self._parent.settings['linear motor max position']),
                int(self._parent.settings['linear motor position decimal places'])),
            callback=self.callback_move_absolute_max,
        )

        self.step = LabeledStepperLineEdit(
            label_text='Step (mm)',
            validator=QDoubleValidator(
                float(self._parent.settings['linear motor min step']),
                float(self._parent.settings['linear motor max step']),
                int(self._parent.settings['linear motor step decimal places'])),
            widths=(0, 50, 0, 50),
            left_button_text='v',
            right_button_text='^',
            callbacks=(lambda: self.callback_move_relative(1.), None,
                       lambda: self.callback_move_relative(-1.)),
        )

        ## Fixed Value Buttons
        # Home/Min
        min_ = self._parent.settings['linear motor min position']
        self.lin_motor_fixed_home_button = QPushButton(f'Min ({min_})')
        self.lin_motor_fixed_home_button.clicked.connect(  # type: ignore
            lambda: self.callback_move_absolute_min(0.))
        # Max
        max_ = self._parent.settings['linear motor max position']
        self.lin_motor_fixed_max_button = QPushButton(f'Max ({max_})')
        self.lin_motor_fixed_max_button.clicked.connect(  # type: ignore
            lambda: self.callback_move_absolute_max(0.))
        # Layout
        self.lin_motor_fixed_value_layout = QHBoxLayout()
        self.lin_motor_fixed_value_layout.addWidget(
            self.lin_motor_fixed_home_button)
        self.lin_motor_fixed_value_layout.addWidget(
            self.lin_motor_fixed_max_button)

        # Stop
        self.stop = QPushButton('Stop')
        self.stop.clicked.connect(self.callback_stop)

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(self.warning_label)
        layout.addWidget(self.speed)
        layout.addWidget(self.target_min)
        layout.addWidget(self.target_max)
        layout.addWidget(self.step)
        layout.addLayout(self.lin_motor_fixed_value_layout)
        layout.addWidget(self.stop)

        # Groupbox
        self.groupbox = CollapsibleGroupBox('Linear Motor')
        self.groupbox.setContentLayout(layout)

    def callback_stop(self):
        self._parent.app.move_motor_signal.emit('linear_motor', 'stop', ())

    def callback_move_absolute_min(self, target=None):
        speed_text = self.speed.lineedit.text()
        if len(speed_text) == 0:
            return
        speed = float(speed_text)

        if target is None:
            target_text = self.target_min.lineedit.text()
            if len(target_text) == 0:
                return
            target = float(target_text)

        self._parent.app.move_motor_signal.emit('linear_motor',
                                                'move_absolute',
                                                (target, speed))

        self.target_min.lineedit.setText('')
        self.target_max.lineedit.setText('')

    def callback_move_absolute_max(self, target=None):
        speed_text = self.speed.lineedit.text()
        if len(speed_text) == 0:
            return
        speed = float(speed_text)

        if target is None:
            target_text = self.target_max.lineedit.text()
            if len(target_text) == 0:
                return
            target = float(target_text)
        max_ = magscope.settings.LINEAR_MOTOR_MAX_POSITION
        target = max_ - target

        self._parent.app.move_motor_signal.emit('linear_motor',
                                                'move_absolute',
                                                (target, speed))

        self.target_min.lineedit.setText('')
        self.target_max.lineedit.setText('')

    def callback_move_relative(self, direction):
        speed_text = self.speed.lineedit.text()
        if len(speed_text) == 0:
            return
        speed = float(speed_text)

        step_text = self.step.lineedit.text()
        if len(step_text) == 0:
            return
        step = float(step_text)

        self._parent.app.move_motor_signal.emit('linear_motor',
                                                'move_relative',
                                                (direction * step, speed))

        self.target_min.lineedit.setText('')
        self.target_max.lineedit.setText('')


class ObjectiveMotorPanel:

    def __init__(self, parent):
        self._parent = parent

        # Warning Label
        self.warning_label = FlashingLabel(
            'WARNING: Not Connected!!!')

        # Target
        target_min = self._parent.settings['objective motor min position']
        target_max = self._parent.settings['objective motor max position']
        self.target = LabeledLineEdit(
            label_text='Target (nm)',
            validator=QIntValidator(target_min, target_max),
            callback=self.callback_move_absolute,
        )

        self.step = LabeledStepperLineEdit(
            label_text='Step (nm)',
            default=str(self._parent.settings['objective motor step decimal places']),
            validator=QDoubleValidator(
                self._parent.settings['objective motor min step size'],
                self._parent.settings['objective motor max step size'],
                self._parent.settings['objective motor step decimal places']),
            widths=(0, 50, 0, 50),
            left_button_text='-',
            right_button_text='+',
            callbacks=(lambda: self.callback_move_relative(-1.), None,
                       lambda: self.callback_move_relative(1.)))

        ## Fixed Value Buttons
        # Min
        min_ = self._parent.settings['objective motor min position']
        self.fixed_min_button = QPushButton(f'Min ({min_})')
        self.fixed_min_button.clicked.connect(  # type: ignore
            lambda: self.callback_move_absolute(float(min_)))
        # Mid
        mid = self._parent.settings['objective motor mid position']
        self.fixed_mid_button = QPushButton(f'Mid ({mid})')
        self.fixed_mid_button.clicked.connect(  # type: ignore
            lambda: self.callback_move_absolute(float(mid)))
        # Max
        max_ = self._parent.settings['objective motor max position']
        self.fixed_max_button = QPushButton(f'Max ({max_})')
        self.fixed_max_button.clicked.connect(  # type: ignore
            lambda: self.callback_move_absolute(float(max_)))
        # Layout
        self.fixed_value_layout = QHBoxLayout()
        self.fixed_value_layout.addWidget(self.fixed_min_button)
        self.fixed_value_layout.addWidget(self.fixed_mid_button)
        self.fixed_value_layout.addWidget(self.fixed_max_button)

        # Bead-Z Lock
        self.bead_z_lock_enable = LabeledCheckbox(
            label_text='Bead-Z Lock',
            widths=(125, 0),
            callback=self.callback_bead_z_lock)
        self.bead_z_lock_bead = LabeledLineEdit(
            label_text='Bead',
            widths=(125, 0),
            validator=QIntValidator(),
            callback=self.callback_bead_z_lock)
        self.bead_z_lock_rate = LabeledLineEdit(
            label_text='Update Rate (seconds)',
            default='5',
            widths=(125, 0),
            validator=QDoubleValidator(),
            callback=self.callback_bead_z_lock)
        self.bead_z_lock_bead.setEnabled(False)
        self.bead_z_lock_rate.setEnabled(False)

        # Layout
        self.layout = QVBoxLayout()
        self.layout.addWidget(self.warning_label)
        self.layout.addWidget(self.target)
        self.layout.addWidget(self.step)
        self.layout.addLayout(self.fixed_value_layout)
        self.layout.addWidget(self.bead_z_lock_enable)
        self.layout.addWidget(self.bead_z_lock_bead)
        self.layout.addWidget(self.bead_z_lock_rate)

        # Groupbox
        self.groupbox = CollapsibleGroupBox('Objective Motor')
        self.groupbox.setContentLayout(self.layout)

    def callback_stop(self):
        self._parent.app.move_motor_signal.emit('objective_motor', 'stop', ())

    def callback_move_relative(self, direction):
        step_text = self.step.lineedit.text()
        if len(step_text) == 0:
            return
        step = float(step_text)
        self._parent.app.move_motor_signal.emit('objective_motor',
                                                'move_relative',
                                                (direction * step, ))
        self.target.lineedit.setText('')

    def callback_move_absolute(self, value=None):
        if value is None:
            value = float(self.target.lineedit.text())
        self._parent.app.move_motor_signal.emit('objective_motor',
                                                'move_absolute', (value, ))
        self.target.lineedit.setText('')

    def callback_bead_z_lock(self):
        # Collect values
        enable = self.bead_z_lock_enable.checkbox.isChecked()
        bead = self.bead_z_lock_bead.lineedit.text()
        bead = int(bead) if bead != '' else -1
        rate = self.bead_z_lock_rate.lineedit.text()
        rate = float(rate) if rate != '' else 0.

        # Send values to motor manager
        self._parent.app.move_motor_signal.emit('objective_motor',
                                                'set_bead_z_lock',
                                                (enable, bead, rate))

        # Enable or disable GUI
        self.bead_z_lock_bead.setEnabled(enable)
        self.bead_z_lock_rate.setEnabled(enable)


class PlotSettingsPanel:

    def __init__(self, parent):
        super().__init__()
        self._parent = parent

        # Groupbox
        self.groupbox = CollapsibleGroupBox('Plot Settings',
                                                         collapsed=False)

        # Layout
        layout = QVBoxLayout()
        self.groupbox.setContentLayout(layout)

        # Selected bead label
        self.selected_bead = LabeledLineEdit(
            label_text='Selected Bead',
            validator=QIntValidator(),
            default='0',
            widths=(150, 0))
        layout.addWidget(self.selected_bead)

        # Selected bead label
        self.reference_bead = LabeledLineEdit(
            label_text='Subtract Reference Bead',
            validator=QIntValidator(),
            widths=(150, 0))
        layout.addWidget(self.reference_bead)

        # Max duration
        self.max_duration = LabeledLineEdit(
            label_text='Max Duration (seconds)',
            default='60',
            validator=QIntValidator(0, 2147483647),  # max QIntValidator
            widths=(150, 0))
        layout.addWidget(self.max_duration)

        # Max datapoints
        max_ = magscope.settings.N_MAX_DATAPOINTS_PER_PLOT
        self.max_datapoints = LabeledLineEdit(
            label_text='Max Points Displayed',
            default='10000',
            validator=QIntValidator(0, max_),
            widths=(150, 0))
        layout.addWidget(self.max_datapoints)

        # Show relative time
        self.relative_time = LabeledCheckbox(
            label_text='Relative Time', widths=(150, 0))
        layout.addWidget(self.relative_time)


class RotaryMotorPanel:

    def __init__(self, parent):
        self._parent = parent

        # Warning Label
        self.warning_label = FlashingLabel(
            'WARNING: Not Connected!!!')

        # Speed
        self.speed = LabeledLineEdit(
            label_text='Speed (turns/s)',
            default=str(self._parent.settings['rotary motor default speed']),
            validator=QDoubleValidator(
                self._parent.settings['rotary motor min speed'],
                self._parent.settings['rotary motor max speed'],
                self._parent.settings['rotary motor speed decimal places']))

        ## Turns
        # Label
        self.turns_label = QLabel('Turns')
        # Line Edit
        self.turns_lineedit = QLineEdit('1')
        self.turns_lineedit.setValidator(QDoubleValidator(0, 1000, 3))
        # "+" button
        self.plus_button = QPushButton('+')
        self.plus_button.clicked.connect(
            lambda: self.callback_move(1.))  # type: ignore
        self.plus_button.setFixedWidth(50)
        # "-" button
        self.minus_button = QPushButton('-')
        self.minus_button.clicked.connect(
            lambda: self.callback_move(-1.))  # type: ignore
        self.minus_button.setFixedWidth(50)
        # Layout
        self.turns_layout = QHBoxLayout()
        self.turns_layout.addWidget(self.turns_label)
        self.turns_layout.addWidget(self.minus_button)
        self.turns_layout.addWidget(self.turns_lineedit)
        self.turns_layout.addWidget(self.plus_button)

        # Stop
        self.stop = QPushButton('Stop')
        self.stop.clicked.connect(self.callback_stop)

        # Layout
        layout = QVBoxLayout()
        layout.addWidget(self.warning_label)
        layout.addWidget(self.speed)
        layout.addLayout(self.turns_layout)
        layout.addWidget(self.stop)

        # Groupbox
        self.groupbox = CollapsibleGroupBox('Rotary Motor')
        self.groupbox.setContentLayout(layout)

    def callback_stop(self):
        self._parent.app.move_motor_signal.emit('rotary_motor', 'stop', ())

    def callback_move(self, direction: float):
        speed_text = self.speed.lineedit.text()
        if len(speed_text) == 0:
            return
        speed = float(speed_text)

        step_text = self.turns_lineedit.text()
        if len(step_text) == 0:
            return
        step = float(step_text)

        self._parent.app.move_motor_signal.emit('rotary_motor',
                                                'move_relative',
                                                (direction * step, speed))


class ScriptPanel:
    no_file_str = 'No Script Loaded'

    def __init__(self, parent):
        self._parent = parent

        self.groupbox = CollapsibleGroupBox('Scripting',
                                                         collapsed=False)
        self.layout = QVBoxLayout()
        self.groupbox.setContentLayout(self.layout)

        # Status
        self.status_base_text = 'Status'
        self.status = QLabel('Status: Empty')
        self.layout.addWidget(self.status)
        self._parent.app.script_manager.status_signal.connect(self.update_status)

        # Button Layout
        self.button_layout = QHBoxLayout()
        self.layout.addLayout(self.button_layout)

        # Buttons
        self.load_button = QPushButton('Load')
        self.start_button = QPushButton('Start')
        self.pause_button = QPushButton('Pause')
        self.button_layout.addWidget(self.load_button)
        self.button_layout.addWidget(self.start_button)
        self.button_layout.addWidget(self.pause_button)
        self.load_button.clicked.connect(self.callback_load)
        self.start_button.clicked.connect(self.callback_start)
        self.pause_button.clicked.connect(self.callback_pause)

        # Filepath
        self.filepath_textedit = QTextEdit(self.no_file_str)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filepath_textedit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filepath_textedit.setFixedHeight(40)
        self.filepath_textedit.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.filepath_textedit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout.addWidget(self.filepath_textedit)

    def update_status(self, status):
        self.status.setText(f'{self.status_base_text}: {status}')
        if status == magscope.scripting.ScriptStatus.PAUSED:
            self.pause_button.setText('Resume')
        else:
            self.pause_button.setText('Pause')

    def callback_load(self):
        last_path = self._parent.app.settings.value('last_script_load_filepath',
                                                    os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self._parent,
                                              'Select Script File',
                                              last_path,
                                              'Script (*.py)')

        try:
            self._parent.app.script_manager.load_script(path)
        except Exception as e:
            print(e)
            path = None

        if not path:  # user selected a file
            path = self.no_file_str
            self._parent.app.zlut.unload()
        self._parent.app.settings.setValue('last_script_load_filepath',
                                           QVariant(path))
        self.filepath_textedit.setText(path)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)


    def callback_start(self):
        self._parent.app.script_manager.start_script()

    def callback_pause(self):
        if self.pause_button.text() == 'Pause':
            self._parent.app.script_manager.pause_script()
        else:
            self._parent.app.script_manager.resume_script()


class ZlutPanel:
    no_file_str = 'No Z-LUT selected'

    def __init__(self, parent):
        self._parent = parent
        self.in_progress = False

        # ZLUT generation progress dialog
        self.progress_dialog = None
        self._parent.app.motor_manager.zlut_progress_signal.connect(
            self.update_zlut_progress)
        self._parent.app.motor_manager.zlut_finished_signal.connect(
            self.finish)

        # Panel
        self.groupbox = CollapsibleGroupBox('Z-LUT')

        # Layout - Panel
        layout = QVBoxLayout()
        self.groupbox.setContentLayout(layout)

        # First row
        row_1 = QHBoxLayout()
        layout.addLayout(row_1)

        # Start
        start_label = QLabel('Start')
        self.start_textedit = QLineEdit(
            str(magscope.settings.OBJECTIVE_MOTOR_MIN_POSITION))
        obj_validator = QIntValidator(
            magscope.settings.OBJECTIVE_MOTOR_MIN_POSITION,
            magscope.settings.OBJECTIVE_MOTOR_MAX_POSITION)
        self.start_textedit.setValidator(obj_validator)
        row_1.addWidget(start_label)
        row_1.addWidget(self.start_textedit)

        # End
        end_label = QLabel('End')
        self.end_textedit = QLineEdit(
            str(magscope.settings.OBJECTIVE_MOTOR_MAX_POSITION))
        self.end_textedit.setValidator(obj_validator)
        row_1.addWidget(end_label)
        row_1.addWidget(self.end_textedit)

        # Second row
        row_2 = QHBoxLayout()
        layout.addLayout(row_2)

        # Step
        step_label = QLabel('Step')
        self.step_textedit = QLineEdit('100')
        self.step_textedit.setValidator(obj_validator)
        row_2.addWidget(step_label)
        row_2.addWidget(self.step_textedit)

        # Wait
        wait_label = QLabel('Wait')
        self.wait_textedit = QLineEdit(str(0.1))
        self.wait_textedit.setValidator(QDoubleValidator(0, 10, 3))
        row_2.addWidget(wait_label)
        row_2.addWidget(self.wait_textedit)

        # Generate Button
        generate_button = QPushButton('Generate Z-LUT')
        generate_button.clicked.connect(self.start)  # type: ignore
        layout.addWidget(generate_button)

        # Load Button
        load_button = QPushButton('Load Z-LUT')
        load_button.clicked.connect(self.load)  # type: ignore
        layout.addWidget(load_button)

        # ZLUT file path textedit
        self.filepath_textedit = QTextEdit(self.no_file_str)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filepath_textedit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filepath_textedit.setFixedHeight(40)
        self.filepath_textedit.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.filepath_textedit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.filepath_textedit)

    def load(self):
        last_path = self._parent.app.settings.value('last_zlut_load_filepath',
                                                    os.path.expanduser("~"))

        path, _ = QFileDialog.getOpenFileName(self._parent, 'Select Z-LUT',
                                              last_path, 'Z-LUT (*.txt)')

        try:
            self._parent.app.zlut.load(path)
        except Exception as e:
            print(e)
            path = None

        if not path:  # user selected a file
            path = self.no_file_str
            self._parent.app.zlut.unload()
        self._parent.app.settings.setValue('last_zlut_load_filepath',
                                           QVariant(path))
        self.filepath_textedit.setText(path)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def start(self):
        # Check if we can start
        if not (self._parent.bead_panel.lock_button.isChecked()):
            warn('Failed to generate Z-LUT: beads are unlocked')
            return
        if len(self._parent.app.bead_manager.beads) == 0:
            warn('Failed to generate Z-LUT: no beads selected')
            return
        if len(self._parent.app.bead_manager.beads) > 1:
            warn('Failed to generate Z-LUT: multiple beads selected')
            return
        if self._parent.acquisition_panel.start_stop_button.isChecked():
            warn(
                'Failed to generate Z-LUT: acquisition is already started')
            return
        if self._parent.obj_panel.bead_z_lock_enable.checkbox.isChecked():
            warn(
                'Failed to generate Z-LUT: Bead-Z Lock must be disabled')
            return
        if self._parent.script_inprogress:
            warn(
                'Failed to generate Z-LUT: a script is already running')
            return
        if self.in_progress:
            warn(
                'Failed to generate Z-LUT: a zlut is already being generated')
            return
        folder_path = Path(
            self._parent.acquisition_panel.folder_textedit.toPlainText())
        if not folder_path.exists():
            warn(
                f'Failed to generate Z-LUT: acquisition folder is not valid, "{folder_path}"'
            )
            return

        # Show a progress bar
        self.progress_dialog = ZlutProgressDialog(parent=self._parent)
        self.progress_dialog.show()

        # Get ZLUT parameters
        zlut_start = float(self.start_textedit.text())
        zlut_step = float(self.step_textedit.text())
        zlut_end = float(self.end_textedit.text())
        zlut_wait = float(self.wait_textedit.text())

        # Start saving the profiles
        self._parent.app.set_save_profiles(True)

        # Move to starting location (before recording)
        self._parent.app.move_motor_signal.emit('objective_motor',
                                                'move_absolute',
                                                (zlut_start, ))
        time.sleep(1)

        # Start acquisition
        self._parent.acquisition_panel.mode_button_track.setChecked(True)
        self.in_progress = True
        self._parent.acquisition_panel.start_stop_button.setChecked(True)
        self._parent.acquisition_panel.callback_start_stop()

        # Move motor
        self._parent.app.move_motor_signal.emit('objective_motor',
                                                'zlut',
                                                (zlut_start, zlut_end, zlut_step, zlut_wait))

    def finish(self):
        # Stop acquisition
        self._parent.acquisition_panel.start_stop_button.setChecked(False)
        self._parent.acquisition_panel.callback_start_stop()
        time.sleep(1)

        #
        self._parent.app.set_save_profiles(False)

        # Get the 'save' folder
        folder_path = Path(
            self._parent.acquisition_panel.folder_textedit.toPlainText())
        zlut_path = folder_path / 'zlut.txt'

        try:

            # Import
            (txyzb, profiles,
             motors) = magscope.utils.import_positions(folder_path)

            # Convert
            zlut = self._parent.app.zlut.calculate(txyzb, profiles, motors)

            # Save
            np.savetxt(zlut_path, zlut)

        except Exception as e:
            warn(traceback.format_exc())

        self.in_progress = False

        self.progress_dialog.close()
        self.progress_dialog = None

    def update_zlut_progress(self, progress):
        if self.progress_dialog is not None:
            self.progress_dialog.update_progress(progress)


class ZlutProgressDialog(QProgressDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZLUT Generation Progress")
        self.setLabelText("Generating ZLUT...")
        self.setMinimum(0)
        self.setMaximum(100)
        self.setCancelButton(None)  # Remove cancel button
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setValue(0)

    def update_progress(self, progress):
        self.setValue(int(progress * 100))
