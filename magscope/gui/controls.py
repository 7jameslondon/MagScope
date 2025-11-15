from __future__ import annotations

import datetime
import os
import time
import traceback
from typing import TYPE_CHECKING

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtCore import QSettings, Qt, QUrl, QVariant, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont, QTextOption
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QLineEdit, QProgressBar, QPushButton, QStackedLayout, QTextEdit,
                             QVBoxLayout, QWidget)

from magscope.gui import (CollapsibleGroupBox, LabeledCheckbox, LabeledLineEdit,
                          LabeledLineEditWithValue, LabeledStepperLineEdit)
from magscope.gui.widgets import FlashLabel
from magscope.processes import ManagerProcessBase
from magscope.scripting import ScriptManager, ScriptStatus
from magscope.utils import AcquisitionMode, Message, crop_stack_to_rois

# Import only for the type check to avoid circular import
if TYPE_CHECKING:
    from magscope.gui.windows import WindowManager

class ControlPanelBase(QWidget):
    def __init__(self, manager: 'WindowManager', title: str):
        super().__init__()
        self.manager: WindowManager = manager
        self.groupbox: CollapsibleGroupBox = CollapsibleGroupBox(title=title)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.groupbox)
        super().setLayout(layout)
        self.setLayout(QVBoxLayout())

    def set_title(self, text: str):
        self.groupbox.setTitle(text)

    def setLayout(self, layout):
        self.groupbox.setContentLayout(layout)

    def layout(self) -> QVBoxLayout | QHBoxLayout | QGridLayout | QStackedLayout:
        return self.groupbox.content_area.layout()


class HelpPanel(QFrame):
    """Clickable panel that links to the MagScope documentation."""

    HELP_URL = QUrl("https://magscope.readthedocs.io")

    def __init__(self, manager: 'WindowManager'):
        super().__init__()
        self.manager = manager
        self.setObjectName("HelpPanelFrame")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.setLayout(layout)

        self.title_label = QLabel("Need help?")
        font = self.title_label.font()
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        self.title_label.setFont(font)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.description_label = QLabel("Click to open the MagScope documentation")
        self.description_label.setWordWrap(True)

        layout.addWidget(self.title_label)
        layout.addWidget(self.description_label)

        self._hovered = False
        self._apply_styles()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event.pos()):
                QDesktopServices.openUrl(self.HELP_URL)
                event.accept()
                return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self._apply_styles()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_styles()
        super().leaveEvent(event)

    def _apply_styles(self):
        text_color = "black" if self._hovered else "white"
        background_color = "white" if self._hovered else "transparent"
        self.setStyleSheet(
            f"""
            #HelpPanelFrame {{
                border: 1px solid #5b5b5b;
                border-radius: 6px;
                background-color: {background_color};
            }}
            #HelpPanelFrame QLabel {{
                color: {text_color};
            }}
            """
        )


class AcquisitionPanel(ControlPanelBase):
    no_file_str = 'No directory to save to selected'

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Acquisition')
        # --- Row 0 ---
        self.layout_row_0 = QHBoxLayout()
        self.layout().addLayout(self.layout_row_0)

        # Acquisition On Checkbox
        self.acquisition_on_checkbox = LabeledCheckbox(
            label_text='Acquire',
            default=self.manager._acquisition_on,
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
        self.acquisition_mode_combobox.setCurrentText(self.manager._acquisition_mode)
        self.acquisition_mode_combobox.currentIndexChanged.connect(self.callback_acquisition_mode) # type: ignore

        # --- Row 1 ---
        self.layout_row_1 = QHBoxLayout()
        self.layout().addLayout(self.layout_row_1)

        # Acquisition Directory On Checkbox
        self.acquisition_dir_on_checkbox = LabeledCheckbox(
            label_text='Save',
            default=self.manager._acquisition_dir_on,
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
        self.layout().addWidget(self.acquisition_dir_textedit)

    def callback_acquisition_on(self):
        value: bool = self.acquisition_on_checkbox.checkbox.isChecked()
        self.manager.send_ipc(Message(ManagerProcessBase, ManagerProcessBase.set_acquisition_on, value))

    def callback_acquisition_dir_on(self):
        value: bool = self.acquisition_dir_on_checkbox.checkbox.isChecked()
        self.manager.send_ipc(Message(ManagerProcessBase, ManagerProcessBase.set_acquisition_dir_on, value))

    def callback_acquisition_mode(self):
        value: AcquisitionMode = self.acquisition_mode_combobox.currentText()
        self.manager.send_ipc(Message(ManagerProcessBase, ManagerProcessBase.set_acquisition_mode, value))

    def callback_acquisition_dir(self):
        settings = QSettings('MagScope', 'MagScope')
        last_value = settings.value(
            'last acquisition_dir',
            os.path.expanduser("~"),
            type=str
        )
        value = QFileDialog.getExistingDirectory(None,
                                                 'Select Folder',
                                                 last_value)
        if value:
            self.acquisition_dir_textedit.setText(value)
            settings.setValue('last acquisition_dir', QVariant(value))
        else:
            value = None
            self.acquisition_dir_textedit.setText(self.no_file_str)
        self.manager.send_ipc(Message(ManagerProcessBase, ManagerProcessBase.set_acquisition_dir, value))


class BeadSelectionPanel(ControlPanelBase):

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Bead Selection')

        # Instructions
        instructions = """
        Add a bead: left-click on the video
        Remove a bead: right-click on the bead
        """
        instructions = '\n'.join([l.strip() for l in instructions.splitlines()]).strip()
        self.layout().addWidget(QLabel(instructions))

        # ROI
        row = QHBoxLayout()
        self.layout().addLayout(row)
        row.addWidget(QLabel('Current bead-ROI:'))
        roi = self.manager.settings['bead roi width']
        self.roi_size_label = QLabel(f'{roi} x {roi} pixels')
        row.addWidget(self.roi_size_label)
        row.addStretch(1)

        # Row
        row = QHBoxLayout()
        self.layout().addLayout(row)

        # Lock/Unlock
        self.lock_button = QPushButton('🔓')
        self.lock_button.setCheckable(True)
        self.lock_button.setStyleSheet("""
            QPushButton:checked {
            background-color: #333;
            }""")
        self.lock_button.clicked.connect(self.callback_lock)  # type: ignore
        row.addWidget(self.lock_button)

        # Remove All Beads
        self.clear_button = QPushButton('Remove All Beads')
        self.clear_button.setEnabled(True)
        self.clear_button.clicked.connect(self.manager.clear_beads)  # type: ignore
        row.addWidget(self.clear_button)

    def callback_lock(self):
        locked = self.lock_button.isChecked()
        text = '🔒' if locked else '🔓'
        self.lock_button.setText(text)
        self.clear_button.setEnabled(not locked)
        self.manager.lock_beads(locked)


class CameraPanel(ControlPanelBase):

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Camera Settings')

        self.layout().setSpacing(2)

        # Individual controls
        self.settings = {}
        for name in self.manager.camera_type.settings:
            self.settings[name] = LabeledLineEditWithValue(
                label_text=name,
                widths=(0, 100, 50),
                callback=lambda n=name:self.callback_set_camera_setting(n))
            self.layout().addWidget(self.settings[name])

        # Refresh button
        self.refresh_button = QPushButton('↺')
        self.refresh_button.setFlat(True)
        self.refresh_button.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }")
        self.refresh_button.clicked.connect(self.callback_refresh) # noqa PyUnresolvedReferences
        self.layout().addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignRight) # type: ignore
            
    def callback_refresh(self):
        for name in self.manager.camera_type.settings:
            from magscope import CameraManager
            message = Message(to=CameraManager,
                              meth=CameraManager.get_camera_setting,
                              args=(name,))
            self.manager.send_ipc(message)

    def callback_set_camera_setting(self, name):
        value = self.settings[name].lineedit.text()
        if value == '':
            return
        self.settings[name].lineedit.setText('')
        self.settings[name].value_label.setText('')
        from magscope import CameraManager
        message = Message(to=CameraManager,
                          meth=CameraManager.set_camera_setting,
                          args=(name, value))
        self.manager.send_ipc(message)
        
    def update_camera_setting(self, name: str, value: str):
        self.settings[name].value_label.setText(value)


class HistogramPanel(ControlPanelBase):

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Histogram')

        self.update_interval: float = 1 # seconds
        self._update_last_time: float = 0

        # ===== First Row ===== #
        row_1 = QHBoxLayout()
        self.layout().addLayout(row_1)

        # Enable
        self.enable = LabeledCheckbox(
            label_text='Enabled',
            callback=self.clear,
            widths=(50, 0),
            default=False)
        row_1.addWidget(self.enable)

        # Only beads
        self.only_beads = LabeledCheckbox(
            label_text='Only Bead ROIs', default=False)
        row_1.addWidget(self.only_beads)

        # ===== Plot ===== #
        self.n_bins = 256
        self.figure = Figure(dpi=100, facecolor='#1e1e1e')
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setFixedHeight(100)
        self.axes = self.figure.subplots(nrows=1, ncols=1)
        self.figure.tight_layout()
        self.figure.subplots_adjust(bottom=0.2, top=1)

        _, _, self.bars = self.axes.hist(
            [],
            bins=self.n_bins,
            edgecolor=None,
            facecolor='white'
        )

        self.axes.set_facecolor('#1e1e1e')
        self.axes.set_xlabel('Intensity')
        self.axes.set_ylabel('Count')
        self.axes.set_yticks([])
        self.axes.set_xticks([])
        self.axes.spines['left'].set_visible(False)
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)
        self.axes.set_xlim(0, 1)

        self.layout().addWidget(self.canvas)

    def update_plot(self, data):
        # Check if its enabled
        if not self.enable.checkbox.isChecked() or self.groupbox.collapsed:
            return

        # Check if it has been enough time
        if (now:=time.time()) - self._update_last_time < self.update_interval:
            return
        self._update_last_time = now

        dtype = self.manager.camera_type.dtype
        max_int = 2**self.manager.camera_type.bits
        shape = self.manager.video_buffer.image_shape
        image = np.frombuffer(data, dtype).reshape(shape)

        if self.only_beads.checkbox.isChecked():
            bead_rois = self.manager._bead_rois
            if len(bead_rois) > 0:
                image = crop_stack_to_rois(
                    np.swapaxes(image, 0, 1)[:, :, None], list(bead_rois.values()))
            else:
                self.clear()
                return

        # Perform histogram binning
        counts, _ = np.histogram(image, bins=256, range=(0, max_int))
        # fast safe log to prevent log(0)
        counts = np.log(counts + 1)

        # Update the plot with the new bin counts
        for count, rect in zip(counts, self.bars.patches):
            rect.set_height(count)

        # Update y-limit
        max_count = counts.max() if len(counts) > 0 else 1
        self.axes.set_ylim(0, max_count * 1.1)

        # Re-draw the graphic
        self.canvas.draw()

    def clear(self):
        for rect in self.bars.patches:
            rect.set_height(0)
        self.canvas.draw()


class PlotSettingsPanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Plot Settings')

        # Selected Bead
        self.selected_bead = LabeledLineEdit(
            label_text='Selected Bead',
            default='0',
            callback=self.selected_bead_callback,
        )
        self.layout().addWidget(self.selected_bead)

        # Selected Reference Bead
        self.reference_bead = LabeledLineEdit(
            label_text='Reference Bead',
            callback=self.reference_bead_callback,
        )
        self.layout().addWidget(self.reference_bead)

        # =============== Limits ===============
        self.limits: dict[str, tuple[QLineEdit, QLineEdit]] = {}

        # Limits Grid
        self.grid_layout = QGridLayout()
        self.layout().addLayout(self.grid_layout)

        # First row of labels
        r = 0
        limit_label_font = QFont()
        limit_label_font.setBold(True)
        limit_label = QLabel('Limits')
        limit_label.setFont(limit_label_font)
        self.grid_layout.addWidget(limit_label, r, 0)
        self.grid_layout.addWidget(QLabel('Min'), r, 1)
        self.grid_layout.addWidget(QLabel('Max'), r, 2)

        # One row for each y-axis
        for i, plot in enumerate(self.manager.plot_worker.plots):
            r += 1
            ylabel = plot.ylabel
            self.limits[ylabel] = (QLineEdit(), QLineEdit())
            self.limits[ylabel][0].textChanged.connect(self.limits_callback)
            self.limits[ylabel][1].textChanged.connect(self.limits_callback)
            self.limits[ylabel][0].setPlaceholderText('auto')
            self.limits[ylabel][1].setPlaceholderText('auto')
            self.grid_layout.addWidget(QLabel(ylabel), r, 0)
            self.grid_layout.addWidget(self.limits[ylabel][0], r, 1)
            self.grid_layout.addWidget(self.limits[ylabel][1], r, 2)

        # Last row for "Time"
        r += 1
        self.limits['Time'] = (QLineEdit(), QLineEdit())
        self.limits['Time'][0].textChanged.connect(self.limits_callback)
        self.limits['Time'][1].textChanged.connect(self.limits_callback)
        self.limits['Time'][0].setPlaceholderText('auto')
        self.limits['Time'][1].setPlaceholderText('auto')
        self.grid_layout.addWidget(QLabel('Time (H:M:S)'), r, 0)
        self.grid_layout.addWidget(self.limits['Time'][0], r, 1)
        self.grid_layout.addWidget(self.limits['Time'][1], r, 2)

        # Show beads on view
        self.beads_in_view_on = LabeledCheckbox(
            label_text='Show beads on video? (slow)',
            default=False,
            callback=self.beads_in_view_on_callback,
        )
        self.layout().addWidget(self.beads_in_view_on)

        # Number of timepoints to show
        self.beads_in_view_count = LabeledLineEdit(
            label_text='Number of timepoints to show',
            default='1',
            callback=self.beads_in_view_count_callback,
        )
        self.layout().addWidget(self.beads_in_view_count)

        # Marker size
        self.beads_in_view_marker_size = LabeledLineEdit(
            label_text='Marker size',
            default='20',
            callback=self.beads_in_view_marker_size_callback,
        )
        self.layout().addWidget(self.beads_in_view_marker_size)

    def selected_bead_callback(self, value):
        try: value = int(value)
        except: value = -1
        self.manager.plot_worker.selected_bead_signal.emit(value)
        self.manager.selected_bead = value

    def reference_bead_callback(self, value):
        value = self.reference_bead.lineedit.text()
        try: value = int(value)
        except: value = -1
        self.manager.plot_worker.reference_bead_signal.emit(value)

    def limits_callback(self, _):
        values = {}
        for name, limit in self.limits.items():
            min_max = [limit[0].text(), limit[1].text()]
            for i in range(2):
                value = min_max[i]
                if name == 'Time':
                    today = datetime.date.today()
                    try:
                        value = value.replace('.', ':').split(':')
                        value = datetime.datetime.combine(today, datetime.time(*map(int, value)))
                        value = value.timestamp()
                    except:
                        value = None

                else:
                    try:
                        value = float(value)
                    except:
                        value = None
                min_max[i] = value
            values[name] = tuple(min_max)
        self.manager.plot_worker.limits_signal.emit(values)

    def beads_in_view_on_callback(self):
        value = self.beads_in_view_on.checkbox.isChecked()
        self.manager.beads_in_view_on = value

    def beads_in_view_count_callback(self):
        value = self.beads_in_view_count.lineedit.text()
        try: value = int(value)
        except ValueError: value = None
        self.manager.beads_in_view_count = value

    def beads_in_view_marker_size_callback(self):
        value = self.beads_in_view_marker_size.lineedit.text()
        try: value = int(value)
        except ValueError: value = 100
        self.manager.beads_in_view_marker_size = value


class ProfilePanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Radial Profile Monitor')

        # Enable
        self.enable = LabeledCheckbox(
            label_text='Enabled',
            callback=self.clear,
        )
        self.layout().addWidget(self.enable)

        # Selected bead
        row = QHBoxLayout()
        self.layout().addLayout(row)
        row.addWidget(QLabel('Selected bead:'))
        self.selected_bead_label = QLabel('')
        row.addWidget(self.selected_bead_label)

        # Figure
        self.figure = Figure(dpi=100, facecolor='#1e1e1e')
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setFixedHeight(100)
        self.figure.tight_layout()
        self.layout().addWidget(self.canvas)

        # Plot
        self.axes = self.figure.subplots(nrows=1, ncols=1)
        self.axes.set_facecolor('#1e1e1e')
        self.axes.set_xlabel('Radius (pixels)')
        self.axes.set_ylabel('Intensity')
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)
        self.axes.spines['left'].set_visible(False)
        self.axes.set_yticks([])
        self.line, = self.axes.plot([], [], 'w')

    def update_plot(self):
        # Is enabled?
        if not self.enable.checkbox.isChecked() or self.groupbox.collapsed:
            return

        # Get the selected bead
        selected_bead = self.manager.selected_bead
        if selected_bead == -1:
            self.selected_bead_label.setText('')
        else:
            self.selected_bead_label.setText(str(selected_bead))

        # Get timestamps, bead-IDs and profiles from buffer data
        data = self.manager.profiles_buffer.peak_unsorted()
        t = data[:, 0]
        b = data[:, 1]
        p = data[:, 2:]

        # Get data from the selected bead
        sel = b == selected_bead
        t = t[sel]
        p = p[sel]

        # Check there is data
        if len(t) > 0:

            # Find the most recent timepoints
            p = p[np.argmax(t), :]

            # Remove non-finite values from the profile (such as "nan")
            p = p[np.isfinite(p)]

            # Create array of radial distances
            r = np.arange(len(p))

            # Update the plot data
            self.line.set_xdata(r)
            self.line.set_ydata(p)

            # Update the plot axis limits (if the plot has data)
            if len(p) > 0:
                self.axes.set_xlim(0, max(r))
                self.axes.set_ylim(0, max(p))
        else:
            self.line.set_xdata([])
            self.line.set_ydata([])

        # Re-draw the plot
        self.canvas.draw()

    def clear(self):
        self.selected_bead_label.setText('')
        self.line.set_xdata([])
        self.line.set_ydata([])
        self.canvas.draw()


class ScriptPanel(ControlPanelBase):
    no_file_str = 'No Script Loaded'

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Scripting')

        # Status
        self.status_base_text = 'Status'
        self.status = QLabel('Status: Empty')
        self.layout().addWidget(self.status)

        # Button Layout
        self.button_layout = QHBoxLayout()
        self.layout().addLayout(self.button_layout)

        # Buttons
        self.load_button = QPushButton('Load')
        self.start_button = QPushButton('Start')
        self.pause_button = QPushButton('Pause')
        self.button_layout.addWidget(self.load_button)
        self.button_layout.addWidget(self.start_button)
        self.button_layout.addWidget(self.pause_button)
        self.load_button.clicked.connect(self.callback_load) # type: ignore
        self.start_button.clicked.connect(self.callback_start) # type: ignore
        self.pause_button.clicked.connect(self.callback_pause) # type: ignore

        # Filepath
        self.filepath_textedit = QTextEdit(self.no_file_str)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filepath_textedit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filepath_textedit.setFixedHeight(40)
        self.filepath_textedit.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.filepath_textedit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout().addWidget(self.filepath_textedit)

    def update_status(self, status: ScriptStatus):
        self.status.setText(f'{self.status_base_text}: {status}')
        if status == ScriptStatus.PAUSED:
            self.pause_button.setText('Resume')
        else:
            self.pause_button.setText('Pause')

        if status == ScriptStatus.EMPTY:
            self.filepath_textedit.setText(self.no_file_str)
            self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def callback_load(self):
        settings = QSettings('MagScope', 'MagScope')
        last_value = settings.value(
            'last script filepath',
            os.path.expanduser("~"),
            type=str
        )
        path, _ = QFileDialog.getOpenFileName(None,
                                              'Select Script File',
                                              last_value,
                                              'Script (*.py)')

        message = Message(ScriptManager, ScriptManager.load_script, path)
        self.manager.send_ipc(message)

        if not path:  # user selected cancel
            path = self.no_file_str
        else:
            settings.setValue('last script filepath', QVariant(path))
        self.filepath_textedit.setText(path)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def callback_start(self):
        message = Message(ScriptManager, ScriptManager.start_script)
        self.manager.send_ipc(message)

    def callback_pause(self):
        if self.pause_button.text() == 'Pause':
            message = Message(ScriptManager, ScriptManager.pause_script)
            self.manager.send_ipc(message)
        else:
            message = Message(ScriptManager, ScriptManager.resume_script)
            self.manager.send_ipc(message)


class StatusPanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Status')

        self.layout().setSpacing(0)
        self.dot = 0

        # GUI display rate
        self.display_rate_status = QLabel()
        self.layout().addWidget(self.display_rate_status)

        # Video Processors
        self.video_processors_status = QLabel()
        self.layout().addWidget(self.video_processors_status)

        # Video Buffer
        self.video_buffer_size_status = QLabel()
        self._update_video_buffer_size_label()
        self.layout().addWidget(self.video_buffer_size_status)
        self.video_buffer_status = QLabel()
        self.layout().addWidget(self.video_buffer_status)
        self.video_buffer_status_bar = QProgressBar()
        self.video_buffer_status_bar.setOrientation(Qt.Orientation.Horizontal)
        self.layout().addWidget(self.video_buffer_status_bar)

        # Video Buffer Purge
        self.video_buffer_purge_label = FlashLabel('Video Buffer Purged at: ')
        self.layout().addWidget(self.video_buffer_purge_label)

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

    def _update_video_buffer_size_label(self) -> None:
        video_buffer = getattr(self.manager, 'video_buffer', None)
        if video_buffer is None or getattr(video_buffer, 'buffer_size', None) is None:
            self.video_buffer_size_status.setText('Video Buffer Size: Unknown')
            return

        size_mb = video_buffer.buffer_size / 1e6
        self.video_buffer_size_status.setText(f'Video Buffer Size: {size_mb:.1f} MB')

    def update_video_buffer_purge(self, t: float):
        string = time.strftime("%I:%M:%S %p", time.localtime(t))
        self.video_buffer_purge_label.setText(f'Video Buffer Purged at: {string}')


class XYLockPanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='XY-Lock')

        # Note
        note_text = '''
        Periodically moves the bead-boxes to the center the bead.
        '''.replace('\n', ' ').replace('  ', '').strip()
        note = QLabel(note_text)
        note.setWordWrap(True)
        self.layout().addWidget(note)

        # Row 1
        row_1 = QHBoxLayout()
        self.layout().addLayout(row_1)

        # Enabled
        self.enabled = LabeledCheckbox(
            label_text='Enabled',
            callback=self.enabled_callback,
        )
        row_1.addWidget(self.enabled)

        # Once
        once = QPushButton('Once')
        once.clicked.connect(self.once_callback)
        row_1.addWidget(once)

        # Interval
        self.interval = LabeledLineEditWithValue(
            label_text='Interval (sec)',
            default=str(self.manager.settings['xy-lock default interval']) + ' sec',
            callback=self.interval_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.interval)

        # Max
        self.max = LabeledLineEditWithValue(
            label_text='Max (pixels)',
            default=str(self.manager.settings['xy-lock default max']) + ' pixels',
            callback=self.max_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.max)

    def enabled_callback(self):
        value = self.enabled.checkbox.isChecked()

        # Change panel background color
        if value:
            self.groupbox.setStyleSheet('QGroupBox { background-color: #1e3322 }')
        else:
            self.groupbox.setStyleSheet('QGroupBox { background-color: none }')

        # Send value
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.set_xy_lock_on,
            args=(value,),
        )
        self.manager.send_ipc(message)

    def once_callback(self):
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.do_xy_lock,
        )
        self.manager.send_ipc(message)

    def interval_callback(self):
        # Get value
        value = self.interval.lineedit.text()
        self.interval.lineedit.setText('')

        # Check value
        try: value = float(value)
        except ValueError: return
        if value < 0: return

        # Send value
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.set_xy_lock_interval,
            args=(value,),
        )
        self.manager.send_ipc(message)

    def max_callback(self):
        # Get value
        value = self.max.lineedit.text()
        self.max.lineedit.setText('')

        # Check value
        try: value = float(value)
        except ValueError: return
        if value <= 1: return

        # Send value
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.set_xy_lock_max,
            args=(value,),
        )
        self.manager.send_ipc(message)

    def update_enabled(self, value: bool):
        # Set checkbox
        self.enabled.checkbox.blockSignals(True)
        self.enabled.checkbox.setChecked(value)
        self.enabled.checkbox.blockSignals(False)

        # Change panel background color
        if value:
            self.groupbox.setStyleSheet('QGroupBox { background-color: #1e3322 }')
        else:
            self.groupbox.setStyleSheet('QGroupBox { background-color: none }')

    def update_interval(self, value: float):
        if value is None:
            value = ''
        self.interval.value_label.setText(f'{value} sec')

    def update_max(self, value: float):
        if value is None:
            value = ''
        self.max.value_label.setText(f'{value} pixels')


class ZLockPanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Z-Lock')

        # Note
        note_text = '''
        When enabled the Z-Lock overrides the "Z motor" target
        and adjusts the motor target to maintain the chosen
        bead at a fixed Z value. The adjustment is completed on
        a timer with the chosen interval between updates.
        '''.replace('\n', ' ').replace('  ', '').strip()
        note = QLabel(note_text)
        note.setWordWrap(True)
        self.layout().addWidget(note)

        # Enabled
        self.enabled = LabeledCheckbox(
            label_text='Enabled',
            callback=self.enabled_callback,
        )
        self.layout().addWidget(self.enabled)

        # Bead
        self.bead = LabeledLineEditWithValue(
            label_text='Bead',
            default='0',
            callback=self.bead_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.bead)

        # Target
        self.target = LabeledLineEditWithValue(
            label_text='Target (nm)',
            default='',
            callback=self.target_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.target)

        # Interval
        self.interval = LabeledLineEditWithValue(
            label_text='Interval (sec)',
            default=str(self.manager.settings['z-lock default interval']) + ' sec',
            callback=self.interval_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.interval)

        # Max
        self.max = LabeledLineEditWithValue(
            label_text='Max (nm)',
            default=str(self.manager.settings['z-lock default max']) + ' nm',
            callback=self.max_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.max)

    def enabled_callback(self):
        value = self.enabled.checkbox.isChecked()

        # Change panel background color
        if value:
            self.groupbox.setStyleSheet('QGroupBox { background-color: #1e3322 }')
        else:
            self.groupbox.setStyleSheet('QGroupBox { background-color: none }')

        # Send value
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.set_z_lock_on,
            args=(value,),
        )
        self.manager.send_ipc(message)

    def bead_callback(self):
        # Get value
        value = self.bead.lineedit.text()
        self.bead.lineedit.setText('')

        # Check value
        try:
            value = int(value)
        except ValueError:
            return
        if value < 0: return

        # Send value
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.set_z_lock_bead,
            args=(value,),
        )
        self.manager.send_ipc(message)

    def target_callback(self):
        # Get value
        value = self.target.lineedit.text()
        self.target.lineedit.setText('')

        # Check value
        try:
            value = float(value)
        except ValueError:
            return

        # Send value
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.set_z_lock_target,
            args=(value,),
        )
        self.manager.send_ipc(message)

    def interval_callback(self):
        # Get value
        value = self.interval.lineedit.text()
        self.interval.lineedit.setText('')

        # Check value
        try:
            value = float(value)
        except ValueError:
            return
        if value < 0: return

        # Send value
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.set_z_lock_interval,
            args=(value,),
        )
        self.manager.send_ipc(message)

    def max_callback(self):
        # Get value
        value = self.max.lineedit.text()
        self.max.lineedit.setText('')

        # Check value
        try:
            value = float(value)
        except ValueError:
            return
        if value <= 1: return

        # Send value
        from magscope.beadlock import BeadLockManager
        message = Message(
            to=BeadLockManager,
            meth=BeadLockManager.set_z_lock_max,
            args=(value,),
        )
        self.manager.send_ipc(message)

    def update_enabled(self, value: bool):
        # Set checkbox
        self.enabled.checkbox.blockSignals(True)
        self.enabled.checkbox.setChecked(value)
        self.enabled.checkbox.blockSignals(False)

        # Change panel background color
        if value:
            self.groupbox.setStyleSheet('QGroupBox { background-color: #1e3322 }')
        else:
            self.groupbox.setStyleSheet('QGroupBox { background-color: none }')

    def update_bead(self, value: int):
        if value is None:
            value = ''
        self.bead.value_label.setText(f'{value}')

    def update_target(self, value: float):
        if value is None:
            value = ''
        self.target.value_label.setText(f'{value} nm')

    def update_interval(self, value: float):
        if value is None:
            value = ''
        self.interval.value_label.setText(f'{value} sec')

    def update_max(self, value: float):
        if value is None:
            value = ''
        self.max.value_label.setText(f'{value} nm')


class ZLUTGenerationPanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Z-LUT Generation')

        # ROI
        row = QHBoxLayout()
        self.layout().addLayout(row)
        row.addWidget(QLabel('Current bead-ROI:'))
        roi = self.manager.settings['bead roi width']
        self.roi_size_label = QLabel(f'{roi} x {roi} pixels')
        row.addWidget(self.roi_size_label)
        row.addStretch(1)

        # Start
        self.start = LabeledLineEdit(label_text='Start (nm):')
        self.layout().addWidget(self.start)

        # Step
        self.step = LabeledLineEdit(label_text='Step (nm):')
        self.layout().addWidget(self.step)

        # Stop
        self.stop = LabeledLineEdit(label_text='Stop (nm):')
        self.layout().addWidget(self.stop)

        # Generate button
        button = QPushButton('Generate')
        button.clicked.connect(self.generate_callback)
        self.layout().addWidget(button)

    def generate_callback(self):
        # Start
        start = self.start.lineedit.text()
        try: start = float(start)
        except ValueError: return

        # Step
        step = self.step.lineedit.text()
        try: step = float(step)
        except ValueError: return

        # Stop
        stop = self.stop.lineedit.text()
        try: stop = float(stop)
        except ValueError: return

        # Output file name
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H-%M-%S')
        roi = self.manager.settings['bead roi width']
        filename = f'Z-LUT {timestamp} {roi} {start:.0f} {step:.0f} {stop:.0f}.txt'

        raise NotImplementedError