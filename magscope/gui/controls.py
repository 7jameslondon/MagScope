from __future__ import annotations

import datetime
import os
import textwrap
import time
from typing import TYPE_CHECKING

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt6.QtCore import QSettings, QUrl, Qt, QVariant, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont, QPalette, QTextOption
from PyQt6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QPushButton,
    QStackedLayout,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from magscope.gui import (
    CollapsibleGroupBox,
    LabeledCheckbox,
    LabeledLineEdit,
    LabeledLineEditWithValue,
)
from magscope.gui.widgets import FlashLabel
from magscope.ipc_commands import (
    ExecuteXYLockCommand,
    GetCameraSettingCommand,
    LoadScriptCommand,
    PauseScriptCommand,
    ResumeScriptCommand,
    SetAcquisitionDirCommand,
    SetAcquisitionDirOnCommand,
    SetAcquisitionModeCommand,
    SetAcquisitionOnCommand,
    SetCameraSettingCommand,
    SetXYLockIntervalCommand,
    SetXYLockMaxCommand,
    SetXYLockOnCommand,
    SetXYLockWindowCommand,
    SetZLockBeadCommand,
    SetZLockIntervalCommand,
    SetZLockMaxCommand,
    SetZLockOnCommand,
    SetZLockTargetCommand,
    StartScriptCommand,
    UpdateSettingsCommand,
)
from magscope.scripting import ScriptStatus
from magscope.settings import MagScopeSettings
from magscope.utils import AcquisitionMode, crop_stack_to_rois

# Import only for the type check to avoid circular import
if TYPE_CHECKING:
    from magscope.gui.windows import WindowManager


class ControlPanelBase(QWidget):
    def __init__(self, manager: 'WindowManager', title: str, collapsed_by_default: bool = False):
        super().__init__()
        self.manager: WindowManager = manager
        self.groupbox: CollapsibleGroupBox = CollapsibleGroupBox(
            title=title,
            collapsed=collapsed_by_default,
        )

        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.groupbox)
        super().setLayout(outer_layout)

        content_layout = QVBoxLayout()
        self.setLayout(content_layout)

    def set_title(self, text: str) -> None:
        self.groupbox.setTitle(text)

    def setLayout(self, layout: QBoxLayout) -> None:
        self.groupbox.setContentLayout(layout)

    def layout(self) -> QBoxLayout:
        return self.groupbox.content_area.layout()

    def set_highlighted(self, enabled: bool) -> None:
        highlight_color = self.palette().color(QPalette.ColorRole.Highlight)
        if enabled:
            color_name = highlight_color.name()
            self.groupbox.setStyleSheet(
                f"QGroupBox {{ border: 2px solid {color_name}; border-radius: 6px; }}"
            )
        else:
            self.groupbox.setStyleSheet("")


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

        self._is_hovered = False
        self._apply_styles()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event.pos()):
                QDesktopServices.openUrl(self.HELP_URL)
                event.accept()
                return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._is_hovered = True
        self._apply_styles()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self._apply_styles()
        super().leaveEvent(event)

    def _apply_styles(self):
        text_color = "black" if self._is_hovered else "white"
        background_color = "white" if self._is_hovered else "transparent"
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


class ResetPanel(QFrame):
    """Clickable panel that resets the GUI layout to defaults."""

    def __init__(self, manager: "WindowManager"):
        super().__init__()
        self.manager = manager
        self.setObjectName("ResetPanelFrame")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(2)
        self.setLayout(layout)

        self.title_label = QLabel("Reset the GUI")
        font = self.title_label.font()
        font.setPointSize(font.pointSize() + 1)
        font.setBold(True)
        self.title_label.setFont(font)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)


        layout.addWidget(self.title_label)

        self._is_hovered = False
        self._apply_styles()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.rect().contains(event.pos()):
                confirmation = QMessageBox.question(
                    self,
                    "Reset GUI",
                    "Reset panels to their default layout and states?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if confirmation == QMessageBox.StandardButton.Yes:
                    self.manager.controls.reset_to_defaults()
                event.accept()
                return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._is_hovered = True
        self._apply_styles()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._is_hovered = False
        self._apply_styles()
        super().leaveEvent(event)

    def _apply_styles(self):
        text_color = "black" if self._is_hovered else "white"
        background_color = "white" if self._is_hovered else "transparent"
        self.setStyleSheet(
            f"""
            #ResetPanelFrame {{
                border: 1px solid #5b5b5b;
                border-radius: 6px;
                background-color: {background_color};
            }}
            #ResetPanelFrame QLabel {{
                color: {text_color};
            }}
            """
        )


class MagScopeSettingsPanel(ControlPanelBase):
    """Allow loading, saving, and editing MagScope configuration values."""

    def __init__(self, manager: "WindowManager"):
        super().__init__(manager=manager, title="MagScope Settings", collapsed_by_default=True)

        self._current_settings = manager.settings.clone()
        self._setting_inputs: dict[str, LabeledLineEditWithValue] = {}
        self._last_settings_update: datetime.datetime | None = None

        button_layout = QVBoxLayout()
        self.layout().addLayout(button_layout)

        top_row = QHBoxLayout()
        button_layout.addLayout(top_row)

        self.load_button = QPushButton("Load")
        self.load_button.clicked.connect(self._on_load_clicked)  # type: ignore
        top_row.addWidget(self.load_button)

        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._on_save_clicked)  # type: ignore
        top_row.addWidget(self.save_button)

        self.defaults_button = QPushButton("Set to Defaults")
        self.defaults_button.clicked.connect(self._on_defaults_clicked)  # type: ignore
        top_row.addWidget(self.defaults_button)

        bottom_row = QHBoxLayout()
        button_layout.addLayout(bottom_row)

        self.apply_button = QPushButton("Apply Changes")
        self.apply_button.clicked.connect(self._on_apply_clicked)  # type: ignore
        self.apply_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        bottom_row.addWidget(self.apply_button)

        for key in MagScopeSettings.defined_keys():
            spec = MagScopeSettings.spec_for(key)
            widget = LabeledLineEditWithValue(
                label_text=spec.label,
                widths=(180, 100, 80),
            )
            widget.lineedit.setText(str(self._current_settings[key]))
            widget.value_label.setText(str(self._current_settings[key]))
            self._setting_inputs[key] = widget
            self.layout().addWidget(widget)

        self.status_label = FlashLabel()
        self.status_label.setText(self._format_last_updated_text())
        self.layout().addWidget(self.status_label)

    def _notify(self, text: str) -> None:
        self.status_label.setText(text)

    def _format_last_updated_text(self) -> str:
        if self._last_settings_update is None:
            return "Last Updated: "
        return f"Last Updated: {self._last_settings_update.strftime('%Y-%m-%d %H:%M:%S')}"

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Settings", message)

    def _collect_settings_from_inputs(self) -> MagScopeSettings | None:
        updated = MagScopeSettings(self._current_settings.to_dict())
        for key, widget in self._setting_inputs.items():
            text = widget.lineedit.text().strip()
            if not text:
                continue
            try:
                updated[key] = text
            except (KeyError, ValueError) as exc:
                self._show_error(str(exc))
                return None
        return updated

    def _push_settings(self, settings: MagScopeSettings) -> None:
        self._current_settings = settings.clone()
        self.manager.settings = settings.clone()
        command = UpdateSettingsCommand(settings=settings.clone())
        self.manager.send_ipc(command)
        self._refresh_fields()
        self._last_settings_update = datetime.datetime.now()
        self._notify(self._format_last_updated_text())

    def _refresh_fields(self) -> None:
        for key, widget in self._setting_inputs.items():
            value = self._current_settings[key]
            widget.value_label.setText(str(value))
            widget.lineedit.setText(str(value))

    def _on_apply_clicked(self) -> None:
        pending = self._collect_settings_from_inputs()
        if pending is None:
            return
        self._push_settings(pending)

    def _on_defaults_clicked(self) -> None:
        self._push_settings(MagScopeSettings())

    def _on_load_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load settings",
            "",
            "YAML Files (*.yaml);;All Files (*)",
        )
        if not path:
            return
        try:
            settings = MagScopeSettings.from_yaml(path)
        except (OSError, ValueError) as exc:
            self._show_error(str(exc))
            return
        self._push_settings(settings)
        self._notify(
            f"Loaded settings from {os.path.basename(path)}; {self._format_last_updated_text()}"
        )

    def _on_save_clicked(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save settings",
            "settings.yaml",
            "YAML Files (*.yaml);;All Files (*)",
        )
        if not path:
            return
        try:
            self._current_settings.save(path)
        except OSError as exc:
            self._show_error(str(exc))
            return
        self._notify(f"Saved settings to {os.path.basename(path)}")


class AcquisitionPanel(ControlPanelBase):
    NO_DIRECTORY_SELECTED_TEXT = 'No save directory selected'

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Acquisition', collapsed_by_default=True)
        acquisition_controls_row = QHBoxLayout()
        self.layout().addLayout(acquisition_controls_row)

        self.acquisition_on_checkbox = LabeledCheckbox(
            label_text='Acquire',
            default=self.manager._acquisition_on,
            callback=self.callback_acquisition_on)
        acquisition_controls_row.addWidget(self.acquisition_on_checkbox)

        mode_selection_layout = QHBoxLayout()
        acquisition_controls_row.addLayout(mode_selection_layout)
        mode_selection_label = QLabel('Mode:')
        mode_selection_layout.addWidget(mode_selection_label)
        self.acquisition_mode_combobox = QComboBox()
        mode_selection_layout.addWidget(self.acquisition_mode_combobox, stretch=1)
        acquisition_modes = [
            AcquisitionMode.TRACK,
            AcquisitionMode.TRACK_AND_CROP_VIDEO,
            AcquisitionMode.TRACK_AND_FULL_VIDEO,
            AcquisitionMode.CROP_VIDEO,
            AcquisitionMode.FULL_VIDEO,
        ]
        for mode in acquisition_modes:
            self.acquisition_mode_combobox.addItem(mode)
        self.acquisition_mode_combobox.setCurrentText(self.manager._acquisition_mode)
        self.acquisition_mode_combobox.currentIndexChanged.connect(
            self.callback_acquisition_mode)  # type: ignore

        save_controls_row = QHBoxLayout()
        self.layout().addLayout(save_controls_row)

        self.acquisition_dir_on_checkbox = LabeledCheckbox(
            label_text='Save',
            default=self.manager._acquisition_dir_on,
            callback=self.callback_acquisition_dir_on)
        save_controls_row.addWidget(self.acquisition_dir_on_checkbox)

        self.acquisition_dir_button = QPushButton('Select Directory to Save To')
        self.acquisition_dir_button.setMinimumWidth(200)
        self.acquisition_dir_button.clicked.connect(self.callback_acquisition_dir)  # type: ignore
        save_controls_row.addWidget(self.acquisition_dir_button)

        self.acquisition_dir_textedit = QTextEdit(self.NO_DIRECTORY_SELECTED_TEXT)
        self.acquisition_dir_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.acquisition_dir_textedit.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.acquisition_dir_textedit.setFixedHeight(40)
        self.acquisition_dir_textedit.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.acquisition_dir_textedit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout().addWidget(self.acquisition_dir_textedit)

        self.update_save_highlight(self.acquisition_dir_on_checkbox.checkbox.isChecked())

    def callback_acquisition_on(self):
        is_enabled: bool = self.acquisition_on_checkbox.checkbox.isChecked()
        command = SetAcquisitionOnCommand(value=is_enabled)
        self.manager.send_ipc(command)

    def callback_acquisition_dir_on(self):
        should_save: bool = self.acquisition_dir_on_checkbox.checkbox.isChecked()
        self.update_save_highlight(should_save)
        command = SetAcquisitionDirOnCommand(value=should_save)
        self.manager.send_ipc(command)

    def callback_acquisition_mode(self):
        selected_mode: AcquisitionMode = self.acquisition_mode_combobox.currentText()
        command = SetAcquisitionModeCommand(mode=selected_mode)
        self.manager.send_ipc(command)

    def callback_acquisition_dir(self):
        settings = QSettings('MagScope', 'MagScope')
        last_directory = settings.value(
            'last acquisition_dir',
            os.path.expanduser("~"),
            type=str
        )
        selected_directory = QFileDialog.getExistingDirectory(
            None,
            'Select Folder',
            last_directory)

        if selected_directory:
            self.acquisition_dir_textedit.setText(selected_directory)
            settings.setValue('last acquisition_dir', QVariant(selected_directory))
        else:
            selected_directory = None
            self.acquisition_dir_textedit.setText(self.NO_DIRECTORY_SELECTED_TEXT)

        command = SetAcquisitionDirCommand(value=selected_directory)
        self.manager.send_ipc(command)

    def update_save_highlight(self, should_save: bool) -> None:
        self.set_highlighted(should_save)


class BeadSelectionPanel(ControlPanelBase):

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Bead Selection', collapsed_by_default=False)

        # Instructions
        note_text = textwrap.dedent(
            """
            <b>Add a bead:</b> Left-click on the video<br>
            <b>Remove a bead:</b> Right-click on the bead<br>
            <b>Lock beads:</b> Click the lock button to prevent accidentally moving/adding/removing beads.
            """
        ).strip()
        note = QLabel(note_text)
        note.setWordWrap(True)
        self.layout().addWidget(note)

        # ROI
        roi_row = QHBoxLayout()
        self.layout().addLayout(roi_row)
        roi_row.addWidget(QLabel('Current ROI:'))
        roi = self.manager.settings['ROI']
        self.roi_size_label = QLabel(f'{roi} x {roi} pixels')
        roi_row.addWidget(self.roi_size_label)
        roi_row.addStretch(1)

        # Row
        button_row = QHBoxLayout()
        self.layout().addLayout(button_row)

        # Lock/Unlock
        self.lock_button = QPushButton('🔓')
        self.lock_button.setCheckable(True)
        self.lock_button.clicked.connect(self.callback_lock)  # type: ignore
        button_row.addWidget(self.lock_button)

        # Remove All Beads
        self.clear_button = QPushButton('Remove All Beads')
        self.clear_button.setEnabled(True)
        self.clear_button.clicked.connect(self.manager.clear_beads)  # type: ignore
        button_row.addWidget(self.clear_button)

    def callback_lock(self):
        is_locked = self.lock_button.isChecked()
        self.lock_button.setText('🔒' if is_locked else '🔓')
        self.clear_button.setEnabled(not is_locked)
        self.set_highlighted(is_locked)
        self.manager.lock_beads(is_locked)


class CameraPanel(ControlPanelBase):

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Camera Settings', collapsed_by_default=True)

        self.layout().setSpacing(2)

        self._last_settings_update: datetime.datetime | None = None

        self.settings = {}
        for setting_name in self.manager.camera_type.settings:
            self.settings[setting_name] = LabeledLineEditWithValue(
                label_text=setting_name,
                widths=(0, 100, 50),
                callback=lambda n=setting_name: self.callback_set_camera_setting(n))
            self.layout().addWidget(self.settings[setting_name])

        refresh_row = QHBoxLayout()
        self.layout().addLayout(refresh_row)

        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.callback_refresh)  # noqa PyUnresolvedReferences
        refresh_row.addWidget(self.refresh_button)

        refresh_row.addStretch(1)

        self.last_update_label = QLabel(self._format_last_update_text())
        self.last_update_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        refresh_row.addWidget(self.last_update_label)

    def callback_refresh(self):
        for name in self.manager.camera_type.settings:
            command = GetCameraSettingCommand(name=name)
            self.manager.send_ipc(command)

    def callback_set_camera_setting(self, name):
        setting_value = self.settings[name].lineedit.text()
        if not setting_value:
            return
        self.settings[name].lineedit.setText('')
        self.settings[name].value_label.setText('')
        command = SetCameraSettingCommand(name=name, value=setting_value)
        self.manager.send_ipc(command)
        
    def update_camera_setting(self, name: str, value: str):
        self.settings[name].value_label.setText(value)
        self._last_settings_update = datetime.datetime.now()
        self.last_update_label.setText(self._format_last_update_text())

    def _format_last_update_text(self) -> str:
        if self._last_settings_update is None:
            return 'Last updated: not yet'
        return f"Last updated: {self._last_settings_update.strftime('%Y-%m-%d %H:%M:%S')}"


class HistogramPanel(ControlPanelBase):

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Histogram', collapsed_by_default=True)

        self.update_interval: float = 1  # seconds
        self._update_last_time: float = 0

        # ===== First Row ===== #
        controls_row = QHBoxLayout()
        self.layout().addLayout(controls_row)

        self.enable_checkbox = LabeledCheckbox(
            label_text='Enabled',
            callback=self.enabled_callback,
            widths=(50, 0),
            default=False)
        controls_row.addWidget(self.enable_checkbox)

        # Keep enabled state synced with collapse/expand so highlighting matches behavior
        self.groupbox.toggle_button.toggled.connect(self._groupbox_toggled)

        self.only_beads_checkbox = LabeledCheckbox(
            label_text='Only Bead ROIs', default=False)
        controls_row.addWidget(self.only_beads_checkbox)

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

    def enabled_callback(self, enabled: bool) -> None:
        effective_enabled = enabled and not self.groupbox.collapsed
        self._apply_enabled_state(effective_enabled)

    def _groupbox_toggled(self, expanded: bool) -> None:
        enabled = expanded and self.enable_checkbox.checkbox.isChecked()
        self._apply_enabled_state(enabled)

    def _apply_enabled_state(self, enabled: bool) -> None:
        self.set_highlighted(enabled)
        self.clear()

    def update_plot(self, data):
        if not self.enable_checkbox.checkbox.isChecked() or self.groupbox.collapsed:
            return

        current_time = time.time()
        if current_time - self._update_last_time < self.update_interval:
            return
        self._update_last_time = current_time

        image_dtype = self.manager.camera_type.dtype
        max_intensity = 2 ** self.manager.camera_type.bits
        image_shape = self.manager.video_buffer.image_shape
        image = np.frombuffer(data, image_dtype).reshape(image_shape)

        if self.only_beads_checkbox.checkbox.isChecked():
            bead_rois = self.manager.bead_rois
            if len(bead_rois) > 0:
                image = crop_stack_to_rois(
                    np.swapaxes(image, 0, 1)[:, :, None], list(bead_rois.values()))
            else:
                self.clear()
                return

        counts, _ = np.histogram(image, bins=256, range=(0, max_intensity))
        # fast safe log to prevent log(0)
        counts = np.log(counts + 1)

        for count, rect in zip(counts, self.bars.patches):
            rect.set_height(count)

        max_count = counts.max() if len(counts) > 0 else 1
        self.axes.set_ylim(0, max_count * 1.1)

        self.canvas.draw()

    def clear(self):
        for rect in self.bars.patches:
            rect.set_height(0)
        self.canvas.draw()


class PlotSettingsPanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Plot Settings', collapsed_by_default=True)

        # Selected Bead
        self.selected_bead = LabeledLineEdit(
            label_text='Selected Bead (red)',
            default='0',
            callback=self.selected_bead_callback,
        )
        self.layout().addWidget(self.selected_bead)

        # Selected Reference Bead
        self.reference_bead = LabeledLineEdit(
            label_text='Reference Bead (green)',
            callback=self.reference_bead_callback,
        )
        self.layout().addWidget(self.reference_bead)

        # =============== Limits ===============
        self.limits: dict[str, tuple[QLineEdit, QLineEdit]] = {}

        # Limits Grid
        self.grid_layout = QGridLayout()
        self.layout().addLayout(self.grid_layout)

        # First row of labels
        row_index = 0
        limit_label_font = QFont()
        limit_label_font.setBold(True)
        limit_label = QLabel('Limits')
        limit_label.setFont(limit_label_font)
        self.grid_layout.addWidget(limit_label, row_index, 0)
        self.grid_layout.addWidget(QLabel('Min'), row_index, 1)
        self.grid_layout.addWidget(QLabel('Max'), row_index, 2)

        # One row for each y-axis
        for _, plot in enumerate(self.manager.plot_worker.plots):
            row_index += 1
            ylabel = plot.ylabel
            self.limits[ylabel] = (QLineEdit(), QLineEdit())
            self.limits[ylabel][0].textChanged.connect(self.limits_callback)
            self.limits[ylabel][1].textChanged.connect(self.limits_callback)
            self.limits[ylabel][0].setPlaceholderText('auto')
            self.limits[ylabel][1].setPlaceholderText('auto')
            self.grid_layout.addWidget(QLabel(ylabel), row_index, 0)
            self.grid_layout.addWidget(self.limits[ylabel][0], row_index, 1)
            self.grid_layout.addWidget(self.limits[ylabel][1], row_index, 2)

        # Last row for "Time"
        row_index += 1
        self.limits['Time'] = (QLineEdit(), QLineEdit())
        self.limits['Time'][0].textChanged.connect(self.limits_callback)
        self.limits['Time'][1].textChanged.connect(self.limits_callback)
        self.limits['Time'][0].setPlaceholderText('auto')
        self.limits['Time'][1].setPlaceholderText('auto')
        self.grid_layout.addWidget(QLabel('Time (H:M:S)'), row_index, 0)
        self.grid_layout.addWidget(self.limits['Time'][0], row_index, 1)
        self.grid_layout.addWidget(self.limits['Time'][1], row_index, 2)

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
        try:
            bead = int(value)
        except (TypeError, ValueError):
            bead = -1
        self.manager.plot_worker.selected_bead_signal.emit(bead)
        self.manager.set_selected_bead(bead)

    def reference_bead_callback(self, value):
        value = self.reference_bead.lineedit.text()
        try:
            bead = int(value)
        except (TypeError, ValueError):
            bead = -1
        self.manager.plot_worker.reference_bead_signal.emit(bead)
        self.manager.set_reference_bead(bead)

    def limits_callback(self, _):
        limits_payload = {}
        today = datetime.date.today()
        for axis_label, limit in self.limits.items():
            raw_values = [limit[0].text(), limit[1].text()]
            parsed_limits: list[float | None] = []
            for raw_value in raw_values:
                if axis_label == 'Time':
                    try:
                        time_parts = raw_value.replace('.', ':').split(':')
                        parsed_value = datetime.datetime.combine(
                            today,
                            datetime.time(*map(int, time_parts)),
                        ).timestamp()
                    except (TypeError, ValueError):
                        parsed_value = None
                else:
                    try:
                        parsed_value = float(raw_value)
                    except (TypeError, ValueError):
                        parsed_value = None
                parsed_limits.append(parsed_value)
            limits_payload[axis_label] = tuple(parsed_limits)
        self.manager.plot_worker.limits_signal.emit(limits_payload)

    def beads_in_view_on_callback(self):
        value = self.beads_in_view_on.checkbox.isChecked()
        self.manager.beads_in_view_on = value

    def beads_in_view_count_callback(self):
        value = self.beads_in_view_count.lineedit.text()
        try:
            count = int(value)
        except ValueError:
            count = None
        self.manager.beads_in_view_count = count

    def beads_in_view_marker_size_callback(self):
        value = self.beads_in_view_marker_size.lineedit.text()
        try:
            size = int(value)
        except ValueError:
            size = 100
        self.manager.beads_in_view_marker_size = size


class ProfilePanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Radial Profile Monitor', collapsed_by_default=True)

        # Enable
        self.enable = LabeledCheckbox(
            label_text='Enabled',
            callback=self.enabled_callback,
        )
        self.layout().addWidget(self.enable)
        self.groupbox.toggle_button.toggled.connect(self._groupbox_toggled)

        # Selected bead
        selected_bead_row = QHBoxLayout()
        self.layout().addLayout(selected_bead_row)
        selected_bead_row.addWidget(QLabel('Selected bead:'))
        self.selected_bead_label = QLabel('')
        selected_bead_row.addWidget(self.selected_bead_label)

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

    def enabled_callback(self, enabled: bool) -> None:
        effective_enabled = enabled and not self.groupbox.collapsed
        self._apply_enabled_state(effective_enabled)

    def _groupbox_toggled(self, expanded: bool) -> None:
        enabled = expanded and self.enable.checkbox.isChecked()
        self._apply_enabled_state(enabled)

    def _apply_enabled_state(self, enabled: bool) -> None:
        self.set_highlighted(enabled)
        self.manager.set_live_profile_monitor_enabled(enabled)
        self.clear()

    def update_plot(self):
        if not self.enable.checkbox.isChecked() or self.groupbox.collapsed:
            return

        selected_bead = self.manager.selected_bead
        if selected_bead == -1:
            self.selected_bead_label.setText('')
        else:
            self.selected_bead_label.setText(str(selected_bead))

        if not self.manager.shared_values.live_profile_enabled.value:
            self.clear()
            return

        buffer_data = self.manager.live_profile_buffer.peak_unsorted()
        latest_entry = buffer_data[0]
        profile_length = latest_entry[2] if np.isfinite(latest_entry[2]) else 0
        bead_id = int(latest_entry[1]) if np.isfinite(latest_entry[1]) else -1

        if selected_bead != bead_id or profile_length <= 0:
            self.clear()
            return

        cleaned_profile = latest_entry[3:3 + int(profile_length)]
        radial_distances = np.arange(profile_length)
        radial_distances = radial_distances[np.isfinite(cleaned_profile)]
        cleaned_profile = cleaned_profile[np.isfinite(cleaned_profile)]

        self.line.set_xdata(radial_distances)
        self.line.set_ydata(cleaned_profile)

        if len(cleaned_profile) > 0:
            self.axes.set_xlim(0, max(radial_distances))
            self.axes.set_ylim(0, max(cleaned_profile))

        self.canvas.draw()

    def clear(self):
        self.selected_bead_label.setText('')
        self.line.set_xdata([])
        self.line.set_ydata([])
        self.canvas.draw()


class ScriptPanel(ControlPanelBase):
    NO_SCRIPT_SELECTED_TEXT = 'No script loaded'

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Scripting', collapsed_by_default=True)

        self.status_prefix = 'Status'
        self.status_label = QLabel('Status: Empty')
        self.layout().addWidget(self.status_label)

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
        self.load_button.clicked.connect(self.callback_load)  # type: ignore
        self.start_button.clicked.connect(self.callback_start)  # type: ignore
        self.pause_button.clicked.connect(self.callback_pause)  # type: ignore

        # Filepath
        self.filepath_textedit = QTextEdit(self.NO_SCRIPT_SELECTED_TEXT)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filepath_textedit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filepath_textedit.setFixedHeight(40)
        self.filepath_textedit.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.filepath_textedit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout().addWidget(self.filepath_textedit)

        self.update_status(ScriptStatus.EMPTY)

    def update_status(self, status: ScriptStatus):
        self.status_label.setText(f'{self.status_prefix}: {status}')
        if status == ScriptStatus.PAUSED:
            self.pause_button.setText('Resume')
        else:
            self.pause_button.setText('Pause')

        self.start_button.setEnabled(status in (ScriptStatus.LOADED, ScriptStatus.FINISHED))
        self.pause_button.setEnabled(status in (ScriptStatus.RUNNING, ScriptStatus.PAUSED))

        if status == ScriptStatus.EMPTY:
            self.filepath_textedit.setText(self.NO_SCRIPT_SELECTED_TEXT)
            self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        elif status == ScriptStatus.ERROR:
            self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def callback_load(self):
        settings = QSettings('MagScope', 'MagScope')
        last_script_path = settings.value(
            'last script filepath',
            os.path.expanduser("~"),
            type=str
        )
        script_path, _ = QFileDialog.getOpenFileName(None,
                                                     'Select Script File',
                                                     last_script_path,
                                                     'Script (*.py)')

        command = LoadScriptCommand(path=script_path)
        self.manager.send_ipc(command)

        if not script_path:  # user selected cancel
            script_path = self.NO_SCRIPT_SELECTED_TEXT
        else:
            settings.setValue('last script filepath', QVariant(script_path))
        self.filepath_textedit.setText(script_path)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def callback_start(self):
        command = StartScriptCommand()
        self.manager.send_ipc(command)

    def callback_pause(self):
        if self.pause_button.text() == 'Pause':
            command = PauseScriptCommand()
            self.manager.send_ipc(command)
        else:
            command = ResumeScriptCommand()
            self.manager.send_ipc(command)


class StatusPanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Status', collapsed_by_default=False)

        self.layout().setSpacing(0)
        self.dot_count = 0

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
        self.dot_count = (self.dot_count + 1) % 4
        dot_text = '.' * self.dot_count
        self.display_rate_status.setText(f'Display Rate: {text} {dot_text}')

    def update_video_processors_status(self, status_text: str):
        self.video_processors_status.setText(f'Video Processors: {status_text}')

    def update_video_buffer_status(self, status_text: str):
        self.video_buffer_status.setText(f'Video Buffer: {status_text}')
        try:
            percent_full = int(status_text.split('%')[0])
        except (ValueError, IndexError):
            percent_full = 0
        self.video_buffer_status_bar.setValue(percent_full)

    def _update_video_buffer_size_label(self) -> None:
        video_buffer = getattr(self.manager, 'video_buffer', None)
        if video_buffer is None or getattr(video_buffer, 'buffer_size', None) is None:
            self.video_buffer_size_status.setText('Video Buffer Size: Unknown')
            return

        size_mb = video_buffer.buffer_size / 1e6
        self.video_buffer_size_status.setText(f'Video Buffer Size: {size_mb:.1f} MB')

    def update_video_buffer_purge(self, timestamp: float):
        timestamp_text = time.strftime("%I:%M:%S %p", time.localtime(timestamp))
        self.video_buffer_purge_label.setText(f'Video Buffer Purged at: {timestamp_text}')


class XYLockPanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='XY-Lock', collapsed_by_default=True)

        # Note
        note_text = textwrap.dedent(
            """
            Periodically moves the bead-boxes to center the bead.
            """
        ).strip()
        note = QLabel(note_text)
        note.setWordWrap(True)
        self.layout().addWidget(note)

        controls_row = QHBoxLayout()
        self.layout().addLayout(controls_row)

        # Enabled
        self.enabled = LabeledCheckbox(
            label_text='Enabled',
            callback=self.enabled_callback,
        )
        controls_row.addWidget(self.enabled)

        # Once
        once = QPushButton('Once')
        once.clicked.connect(self.once_callback)
        controls_row.addWidget(once)

        # Interval
        default_interval = self.manager.settings['xy-lock default interval']
        self.interval = LabeledLineEditWithValue(
            label_text='Interval (sec)',
            default=f'{default_interval} sec',
            callback=self.interval_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.interval)

        # Max
        default_max = self.manager.settings['xy-lock default max']
        self.max = LabeledLineEditWithValue(
            label_text='Max (pixels)',
            default=f'{default_max} pixels',
            callback=self.max_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.max)

        # Window
        default_window = self.manager.settings.get('xy-lock default window', '')
        self.window = LabeledLineEditWithValue(
            label_text='Window',
            default=f'{default_window} window',
            callback=self.window_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.window)

    def enabled_callback(self):
        is_enabled = self.enabled.checkbox.isChecked()

        self.set_highlighted(is_enabled)

        # Send value
        command = SetXYLockOnCommand(value=is_enabled)
        self.manager.send_ipc(command)

    def once_callback(self):
        command = ExecuteXYLockCommand()
        self.manager.send_ipc(command)

    def interval_callback(self):
        # Get value
        value = self.interval.lineedit.text()
        self.interval.lineedit.setText('')

        # Check value
        try:
            interval_seconds = float(value)
        except ValueError:
            return
        if interval_seconds < 0:
            return

        # Send value
        command = SetXYLockIntervalCommand(value=interval_seconds)
        self.manager.send_ipc(command)

    def max_callback(self):
        # Get value
        value = self.max.lineedit.text()
        self.max.lineedit.setText('')

        # Check value
        try:
            max_distance = float(value)
        except ValueError:
            return
        if max_distance <= 1:
            return

        # Send value
        command = SetXYLockMaxCommand(value=max_distance)
        self.manager.send_ipc(command)

    def window_callback(self):
        # Get value
        value = self.window.lineedit.text()
        self.window.lineedit.setText('')

        # Check value
        try:
            window_size = int(value)
        except ValueError:
            return
        if window_size <= 0:
            return

        # Send value
        command = SetXYLockWindowCommand(value=window_size)
        self.manager.send_ipc(command)

    def update_enabled(self, value: bool):
        # Set checkbox
        self.enabled.checkbox.blockSignals(True)
        self.enabled.checkbox.setChecked(value)
        self.enabled.checkbox.blockSignals(False)

        self.set_highlighted(value)

    def update_interval(self, value: float):
        if value is None:
            value = ''
        self.interval.value_label.setText(f'{value} sec')

    def update_max(self, value: float):
        if value is None:
            value = ''
        self.max.value_label.setText(f'{value} pixels')

    def update_window(self, value: int):
        if value is None:
            value = ''
        self.window.value_label.setText(f'{value} window')


class ZLockPanel(ControlPanelBase):
    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Z-Lock', collapsed_by_default=True)

        # Note
        note_text = textwrap.dedent(
            """
            When enabled the Z-Lock overrides the "Z motor" target and adjusts the motor
            target to maintain the chosen bead at a fixed Z value. Adjustments run on a
            timer using the configured interval.
            """
        ).replace('\n', ' ').strip()
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
        default_interval = self.manager.settings['z-lock default interval']
        self.interval = LabeledLineEditWithValue(
            label_text='Interval (sec)',
            default=f'{default_interval} sec',
            callback=self.interval_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.interval)

        # Max
        default_max = self.manager.settings['z-lock default max']
        self.max = LabeledLineEditWithValue(
            label_text='Max (nm)',
            default=f'{default_max} nm',
            callback=self.max_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.max)

    def enabled_callback(self):
        is_enabled = self.enabled.checkbox.isChecked()

        self.set_highlighted(is_enabled)

        # Send value
        command = SetZLockOnCommand(value=is_enabled)
        self.manager.send_ipc(command)

    def bead_callback(self):
        # Get value
        value = self.bead.lineedit.text()
        self.bead.lineedit.setText('')

        # Check value
        try:
            bead_index = int(value)
        except ValueError:
            return
        if bead_index < 0:
            return

        # Send value
        command = SetZLockBeadCommand(value=bead_index)
        self.manager.send_ipc(command)

    def target_callback(self):
        # Get value
        value = self.target.lineedit.text()
        self.target.lineedit.setText('')

        # Check value
        try:
            target_nm = float(value)
        except ValueError:
            return

        # Send value
        command = SetZLockTargetCommand(value=target_nm)
        self.manager.send_ipc(command)

    def interval_callback(self):
        # Get value
        value = self.interval.lineedit.text()
        self.interval.lineedit.setText('')

        # Check value
        try:
            interval_seconds = float(value)
        except ValueError:
            return
        if interval_seconds < 0:
            return

        # Send value
        command = SetZLockIntervalCommand(value=interval_seconds)
        self.manager.send_ipc(command)

    def max_callback(self):
        # Get value
        value = self.max.lineedit.text()
        self.max.lineedit.setText('')

        # Check value
        try:
            max_nm = float(value)
        except ValueError:
            return
        if max_nm <= 1:
            return

        # Send value
        command = SetZLockMaxCommand(value=max_nm)
        self.manager.send_ipc(command)

    def update_enabled(self, value: bool):
        # Set checkbox
        self.enabled.checkbox.blockSignals(True)
        self.enabled.checkbox.setChecked(value)
        self.enabled.checkbox.blockSignals(False)

        self.set_highlighted(value)

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
        super().__init__(manager=manager, title='Z-LUT Generation', collapsed_by_default=True)

        # ROI
        roi_row = QHBoxLayout()
        self.layout().addLayout(roi_row)
        roi_row.addWidget(QLabel('Current ROI:'))
        roi = self.manager.settings['ROI']
        self.roi_size_label = QLabel(f'{roi} x {roi} pixels')
        roi_row.addWidget(self.roi_size_label)
        roi_row.addStretch(1)

        # Start
        self.start_input = LabeledLineEdit(label_text='Start (nm):')
        self.layout().addWidget(self.start_input)

        # Step
        self.step_input = LabeledLineEdit(label_text='Step (nm):')
        self.layout().addWidget(self.step_input)

        # Stop
        self.stop_input = LabeledLineEdit(label_text='Stop (nm):')
        self.layout().addWidget(self.stop_input)

        # Generate button
        button = QPushButton('Generate')
        button.clicked.connect(self.generate_callback)
        self.layout().addWidget(button)

    def generate_callback(self):
        # Start
        start_text = self.start_input.lineedit.text()
        try:
            start_nm = float(start_text)
        except ValueError:
            return

        # Step
        step_text = self.step_input.lineedit.text()
        try:
            step_nm = float(step_text)
        except ValueError:
            return

        # Stop
        stop_text = self.stop_input.lineedit.text()
        try:
            stop_nm = float(stop_text)
        except ValueError:
            return

        # Output file name
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H-%M-%S')
        roi = self.manager.settings['ROI']
        filename = f'Z-LUT {timestamp} {roi} {start_nm:.0f} {step_nm:.0f} {stop_nm:.0f}.txt'

        raise NotImplementedError


class ZLUTPanel(ControlPanelBase):
    zlut_file_selected = pyqtSignal(str)
    zlut_clear_requested = pyqtSignal()

    NO_ZLUT_SELECTED_TEXT = 'No Z-LUT file selected'

    def __init__(self, manager: 'WindowManager'):
        super().__init__(manager=manager, title='Z-LUT', collapsed_by_default=True)

        # Controls row
        controls_row = QHBoxLayout()
        self.layout().addLayout(controls_row)

        self.select_button = QPushButton('Select Z-LUT File')
        self.select_button.clicked.connect(self._select_zlut_file)  # type: ignore
        controls_row.addWidget(self.select_button)

        self.clear_button = QPushButton('Clear Z-LUT')
        self.clear_button.clicked.connect(self._clear_zlut)  # type: ignore
        controls_row.addWidget(self.clear_button)

        # Current filepath display
        self.filepath_textedit = QTextEdit(self.NO_ZLUT_SELECTED_TEXT)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filepath_textedit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.filepath_textedit.setFixedHeight(40)
        self.filepath_textedit.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.filepath_textedit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.layout().addWidget(self.filepath_textedit)

        # Metadata
        self._metadata_layout = QVBoxLayout()
        self.layout().addLayout(self._metadata_layout)

        self.min_value = self._add_metadata_row('Min (nm):')
        self.max_value = self._add_metadata_row('Max (nm):')
        self.step_value = self._add_metadata_row('Step (nm):')
        self.profile_length_value = self._add_metadata_row('Profile Length:')

    def _add_metadata_row(self, label_text: str) -> QLabel:
        row = QHBoxLayout()
        label = QLabel(label_text)
        value = QLabel('')
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(value, alignment=Qt.AlignmentFlag.AlignRight)
        self._metadata_layout.addLayout(row)
        return value

    def _select_zlut_file(self):
        settings = QSettings('MagScope', 'MagScope')
        last_value = settings.value(
            'last zlut directory',
            os.path.expanduser("~"),
            type=str
        )
        path, _ = QFileDialog.getOpenFileName(None,
                                              'Select Z-LUT File',
                                              last_value,
                                              'Text Files (*.txt)')
        if not path:
            return

        directory = os.path.dirname(path) or last_value
        settings.setValue('last zlut directory', QVariant(directory))

        self.filepath_textedit.setText(path)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clear_metadata()
        self.zlut_file_selected.emit(path)

    def _clear_zlut(self):
        self.set_filepath(None)
        self.zlut_clear_requested.emit()

    def set_filepath(self, path: str | None):
        if not path:
            self.filepath_textedit.setText(self.NO_ZLUT_SELECTED_TEXT)
            self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.clear_metadata()
            return

        self.filepath_textedit.setText(path)
        self.filepath_textedit.setAlignment(Qt.AlignmentFlag.AlignCenter)

        settings = QSettings('MagScope', 'MagScope')
        settings.setValue('last zlut directory', QVariant(os.path.dirname(path)))

    def update_metadata(self,
                        z_min: float | None = None,
                        z_max: float | None = None,
                        step_size: float | None = None,
                        profile_length: int | None = None):
        self.min_value.setText(self._format_number(z_min, suffix=' nm'))
        self.max_value.setText(self._format_number(z_max, suffix=' nm'))
        self.step_value.setText(self._format_number(step_size, suffix=' nm'))
        self.profile_length_value.setText('' if profile_length is None else f'{profile_length}')

    def clear_metadata(self):
        self.update_metadata(None, None, None, None)

    @staticmethod
    def _format_number(value: float | int | None, suffix: str = '') -> str:
        if value is None:
            return ''
        if isinstance(value, float):
            return f'{int(value):d}{suffix}'
        return f'{value}{suffix}'
