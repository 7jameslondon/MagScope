from __future__ import annotations

import copy
import datetime
import importlib.util
import math
import os
import sys
import textwrap
import time
from typing import TYPE_CHECKING, Any

import matplotlib
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np
from PyQt6.QtCore import QPointF, QSettings, QSize, QTimer, QUrl, Qt, QVariant, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPalette,
    QPainter,
    QPixmap,
    QPolygonF,
    QTextOption,
)
from PyQt6.QtWidgets import (
    QBoxLayout,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

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
    SetZLockWindowCommand,
    StartScriptCommand,
    UpdateScriptStepCommand,
    UpdateTrackingOptionsCommand,
    UpdateSettingsCommand,
)
from magscope.scripting import ScriptStatus
from magscope.settings import (
    DEFAULT_GUI_ACCENT_COLOR,
    GUI_ACCENT_COLOR_SETTING,
    MagScopeSettings,
    default_tracking_options,
    export_preferences_bundle,
    import_preferences_bundle,
    save_tracking_options_to_qsettings,
    tracking_options_from_mapping,
    tracking_options_from_qsettings,
)
from magscope.ui.search import (
    PanelControlTarget,
    PreferencesSettingTarget,
    PreferencesWidgetTarget,
    SearchTarget,
)
from magscope.ui.theme import PANEL_BACKGROUND_COLOR, get_accent_color
from magscope.ui.widgets import (
    CollapsibleGroupBox,
    FlashLabel,
    LabeledCheckbox,
    LabeledLineEdit,
    LabeledLineEditWithValue,
)
from magscope.utils import AcquisitionMode, crop_stack_to_rois

# Import only for the type check to avoid circular import
if TYPE_CHECKING:
    from magscope.ui.ui import UIManager


def _panel_control_target(
    label: str,
    panel_id: str,
    widget_attr: str,
    *,
    context: str,
    aliases: tuple[str, ...] = (),
    description: str = '',
    keywords: tuple[str, ...] = (),
) -> PanelControlTarget:
    return PanelControlTarget(
        label=label,
        aliases=aliases,
        context=context,
        description=description,
        keywords=keywords,
        panel_id=panel_id,
        widget_path=(widget_attr,),
    )


def _preference_widget_targets(
    definitions: tuple[tuple[str, str, tuple[str, ...]], ...],
    *,
    tab_name: str,
    context: str,
) -> list[SearchTarget]:
    return [
        PreferencesWidgetTarget(
            label=label,
            aliases=aliases,
            context=context,
            description=f'Shows the {label} control in {context}.',
            keywords=(widget_attr,),
            tab_name=tab_name,
            widget_attr=widget_attr,
        )
        for widget_attr, label, aliases in definitions
    ]


class ControlPanelBase(QWidget):
    def __init__(
        self,
        manager: 'UIManager',
        title: str,
        collapsed_by_default: bool = False,
        collapsible: bool = False,
    ):
        super().__init__()
        self.manager: UIManager = manager
        self.groupbox: CollapsibleGroupBox | None = None
        self._content_layout: QBoxLayout | None = None

        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        super().setLayout(outer_layout)

        content_layout = QVBoxLayout()
        if title or collapsible:
            self.groupbox = CollapsibleGroupBox(
                title=title,
                collapsed=collapsed_by_default,
                collapsible=collapsible,
            )
            outer_layout.addWidget(self.groupbox)
        else:
            content_layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(content_layout)

    def set_title(self, text: str) -> None:
        if self.groupbox is not None:
            self.groupbox.setTitle(text)

    def setLayout(self, layout: QBoxLayout) -> None:
        if self.groupbox is None:
            self._content_layout = layout
            super().layout().addLayout(layout)
            return
        self.groupbox.setContentLayout(layout)
        self._content_layout = self.groupbox.content_area.layout()

    def layout(self) -> QBoxLayout:
        if self._content_layout is None:
            raise RuntimeError('Control panel layout has not been initialized')
        return self._content_layout

    def set_highlighted(self, enabled: bool) -> None:
        if self.groupbox is None:
            return
        if enabled:
            self.groupbox.set_highlight_border(get_accent_color())
        else:
            self.groupbox.set_highlight_border(None)


class MatplotlibCleanupMixin:
    def _init_matplotlib_cleanup(self) -> None:
        self._matplotlib_disposed = False
        self.destroyed.connect(self._dispose_matplotlib)  # type: ignore[arg-type]

    def _dispose_matplotlib(self, *_args: object) -> None:
        if getattr(self, '_matplotlib_disposed', False):
            return
        self._matplotlib_disposed = True

        canvas = getattr(self, 'canvas', None)
        figure = getattr(self, 'figure', None)

        if canvas is not None:
            try:
                canvas.hide()
            except RuntimeError:
                pass
            try:
                canvas.setParent(None)
            except RuntimeError:
                pass

        if figure is not None:
            try:
                figure.clear()
            except Exception:
                pass

        if canvas is not None:
            try:
                canvas.close()
            except RuntimeError:
                pass
            try:
                canvas.deleteLater()
            except RuntimeError:
                pass

        if hasattr(self, 'axes'):
            self.axes = None
        if hasattr(self, 'figure'):
            self.figure = None
        if hasattr(self, 'canvas'):
            self.canvas = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._dispose_matplotlib()
        super().closeEvent(event)


class ResponsivePlotCanvas(FigureCanvas):
    """Figure canvas that grows taller when constrained to a narrow panel."""

    def __init__(
        self,
        figure: Figure,
        *,
        minimum_height: int = 210,
        maximum_height: int | None = 235,
        height_for_width: float = 0.72,
    ):
        super().__init__(figure)
        self._minimum_height = minimum_height
        self._maximum_height = maximum_height
        self._height_for_width = height_for_width
        self._preferred_height = minimum_height
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._apply_preferred_height(minimum_height)

    def _apply_preferred_height(self, height: int) -> None:
        self._preferred_height = height
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.updateGeometry()

    def _update_preferred_height(self, width: int) -> None:
        target_height = max(self._minimum_height, int(width * self._height_for_width))
        if self._maximum_height is not None:
            target_height = min(target_height, self._maximum_height)
        if target_height == self._preferred_height:
            return
        self._apply_preferred_height(target_height)

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._update_preferred_height(event.size().width())

    def sizeHint(self) -> QSize:  # type: ignore[override]
        hint = super().sizeHint()
        width = self.width() if self.width() > 0 else hint.width()
        return QSize(width, self._preferred_height)


class HelpPanel(QFrame):
    """Clickable panel that links to the MagScope documentation."""

    HELP_URL = QUrl("https://magscope.readthedocs.io")

    def __init__(self, manager: 'UIManager'):
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


class MagScopeSettingsPanel(QWidget):
    """Allow importing, exporting, and editing MagScope configuration values."""

    _SETTING_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("Imaging", ("ROI", "magnification")),
        (
            "Data Buffers",
            (
                "tracks max datapoints",
                "video buffer n images",
                "video buffer n stacks",
                "video processors n",
            ),
        ),
        (
            "XY Lock Defaults",
            ("xy-lock default interval", "xy-lock default max", "xy-lock default window"),
        ),
        (
            "Z Lock Defaults",
            ("z-lock default interval", "z-lock default max", "z-lock default window"),
        ),
    )

    def __init__(self, manager: "UIManager", *, collapsible: bool = True):
        super().__init__()
        self.manager = manager
        self._current_settings = manager.settings.clone()
        self._setting_inputs: dict[str, QLineEdit] = {}
        self._setting_value_labels: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        title = QLabel("Core Settings")
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        description = QLabel(
            "Adjust core MagScope settings. Changes are applied when a field "
            "loses focus or Enter is pressed."
        )
        description.setWordWrap(True)
        description.setObjectName("preferencesDescription")
        layout.addWidget(description)

        for group_title, keys in self._SETTING_GROUPS:
            group = self._build_setting_group(group_title, keys)
            layout.addWidget(group)

        layout.addStretch(1)

    @staticmethod
    def search_targets() -> list[SearchTarget]:
        targets: list[SearchTarget] = []
        for key in MagScopeSettings.magscope_panel_keys():
            spec = MagScopeSettings.spec_for(key)
            if key == "ROI":
                targets.append(
                    PreferencesSettingTarget(
                        label="ROI Size",
                        aliases=("ROI", "ROI size", "ROI (pixels)", "bead ROI", "region of interest"),
                        context="Preferences > MagScope",
                        setting_key="ROI",
                    )
                )
            else:
                targets.append(
                    PreferencesSettingTarget(
                        label=spec.label,
                        aliases=(key,),
                        context="Preferences > MagScope",
                        setting_key=key,
                    )
                )
        return targets

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Settings", message)

    def _push_settings(self, settings: MagScopeSettings) -> None:
        self._current_settings = settings.clone()
        self.manager.settings = settings.clone()
        apply_accent_color = getattr(self.manager, "_apply_accent_color", None)
        if callable(apply_accent_color):
            apply_accent_color(settings[GUI_ACCENT_COLOR_SETTING])
        command = UpdateSettingsCommand(settings=settings.clone())
        self.manager.send_ipc(command)
        self._refresh_fields()

    def _refresh_fields(self) -> None:
        for key, lineedit in self._setting_inputs.items():
            value = self._current_settings[key]
            lineedit.setText(str(value))
            if key in self._setting_value_labels:
                self._setting_value_labels[key].setText(f"Saved: {value}")

    def _apply_setting_from_input(self, key: str) -> None:
        lineedit = self._setting_inputs.get(key)
        if lineedit is None:
            return
        text = lineedit.text().strip()
        updated = self._current_settings.clone()
        try:
            updated[key] = text
        except (KeyError, ValueError) as exc:
            self._show_error(str(exc))
            lineedit.setText(str(self._current_settings[key]))
            return
        if updated[key] == self._current_settings[key]:
            lineedit.setText(str(updated[key]))
            return
        self._push_settings(updated)

    def reset_defaults(self) -> None:
        defaults = MagScopeSettings()
        defaults[GUI_ACCENT_COLOR_SETTING] = self._current_settings[GUI_ACCENT_COLOR_SETTING]
        self._push_settings(defaults)

    def _build_setting_group(self, title: str, keys: tuple[str, ...]) -> QGroupBox:
        group = QGroupBox(title, self)
        group.setFlat(True)

        grid = QGridLayout(group)
        grid.setContentsMargins(16, 20, 16, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(5)

        grid.addWidget(self._column_header("Setting"), 0, 0)
        grid.addWidget(self._column_header("Value"), 0, 1)
        saved_header = self._column_header("Saved")
        saved_header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(saved_header, 0, 2)

        for row, key in enumerate(keys, start=1):
            spec = MagScopeSettings.spec_for(key)

            label = QLabel(spec.label)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setFixedWidth(155)
            grid.addWidget(label, row, 0)

            lineedit = QLineEdit(str(self._current_settings[key]))
            lineedit.setFixedWidth(120)
            lineedit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lineedit.editingFinished.connect(  # type: ignore[arg-type]
                lambda k=key: self._apply_setting_from_input(k)
            )
            grid.addWidget(lineedit, row, 1)
            self._setting_inputs[key] = lineedit

            saved_label = QLabel(f"Saved: {self._current_settings[key]}")
            saved_label.setObjectName("preferencesSavedLabel")
            grid.addWidget(saved_label, row, 2)
            self._setting_value_labels[key] = saved_label

        grid.setColumnStretch(0, 0)
        grid.setColumnStretch(1, 0)
        grid.setColumnStretch(2, 1)

        return group

    @staticmethod
    def _column_header(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("preferencesColumnHeader")
        return label


class AcquisitionPanel(ControlPanelBase):
    NO_DIRECTORY_SELECTED_TEXT = 'No save folder selected'

    def __init__(self, manager: 'UIManager'):
        super().__init__(manager=manager, title='Recording and Saving', collapsed_by_default=True)
        self.layout().setSpacing(4)
        controls_grid = QGridLayout()
        controls_grid.setContentsMargins(0, 0, 0, 0)
        controls_grid.setHorizontalSpacing(6)
        controls_grid.setVerticalSpacing(4)
        self.layout().addLayout(controls_grid)

        self.acquisition_on_checkbox = LabeledCheckbox(
            label_text='Acquire',
            default=self.manager._acquisition_on,
            callback=self.callback_acquisition_on)
        self.acquisition_on_checkbox.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        controls_grid.addWidget(self.acquisition_on_checkbox, 0, 0)

        mode_selection_label = QLabel('Data:')
        controls_grid.addWidget(mode_selection_label, 0, 1)
        self.acquisition_mode_combobox = QComboBox()
        controls_grid.addWidget(self.acquisition_mode_combobox, 0, 2, 1, 2)
        acquisition_modes = [
            AcquisitionMode.TRACK,
            AcquisitionMode.TRACK_AND_VIDEO_ROIS,
            AcquisitionMode.TRACK_AND_VIDEO_FULL,
            AcquisitionMode.VIDEO_ROIS,
            AcquisitionMode.VIDEO_FULL,
        ]
        for mode in acquisition_modes:
            self.acquisition_mode_combobox.addItem(mode)
        self.acquisition_mode_combobox.setCurrentText(self.manager._acquisition_mode)
        self.acquisition_mode_combobox.currentIndexChanged.connect(
            self.callback_acquisition_mode)  # type: ignore

        self.acquisition_dir_on_checkbox = LabeledCheckbox(
            label_text='Saving',
            default=self.manager._acquisition_dir_on,
            callback=self.callback_acquisition_dir_on)
        self.acquisition_dir_on_checkbox.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        controls_grid.addWidget(self.acquisition_dir_on_checkbox, 1, 0)

        directory_label = QLabel('Folder:')
        controls_grid.addWidget(directory_label, 1, 1)

        self.acquisition_dir_textedit = QLineEdit()
        self.acquisition_dir_textedit.setReadOnly(True)
        self.acquisition_dir_textedit.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        self.acquisition_dir_textedit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        controls_grid.addWidget(self.acquisition_dir_textedit, 1, 2)

        self.acquisition_dir_button = QPushButton('Browse...')
        self.acquisition_dir_button.clicked.connect(self.callback_acquisition_dir)  # type: ignore
        controls_grid.addWidget(self.acquisition_dir_button, 1, 3)
        controls_grid.setColumnStretch(2, 1)

        self.set_acquisition_dir_text(self.manager._acquisition_dir)

        self.update_save_highlight(self.acquisition_dir_on_checkbox.checkbox.isChecked())

    def set_acquisition_dir_text(self, path: str | None) -> None:
        display_text = path or self.NO_DIRECTORY_SELECTED_TEXT
        self.acquisition_dir_textedit.setText(display_text)
        self.acquisition_dir_textedit.setToolTip(path or '')
        self.acquisition_dir_textedit.setCursorPosition(0)

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
            self.set_acquisition_dir_text(selected_directory)
            settings.setValue('last acquisition_dir', QVariant(selected_directory))
        else:
            selected_directory = None
            self.set_acquisition_dir_text(None)

        command = SetAcquisitionDirCommand(value=selected_directory)
        self.manager.send_ipc(command)

    def update_save_highlight(self, should_save: bool) -> None:
        self.set_highlighted(should_save)

    def search_targets(self) -> list[SearchTarget]:
        return [
            _panel_control_target(
                'Acquire',
                'AcquisitionPanel',
                'acquisition_on_checkbox',
                context='Recording and Saving',
                aliases=('acquisition on', 'start acquisition', 'record'),
            ),
            _panel_control_target(
                'Data Mode',
                'AcquisitionPanel',
                'acquisition_mode_combobox',
                context='Recording and Saving',
                aliases=('acquisition mode', 'mode', 'recording mode', 'save mode'),
            ),
            _panel_control_target(
                'Save Recording',
                'AcquisitionPanel',
                'acquisition_dir_on_checkbox',
                context='Recording and Saving',
                aliases=('save', 'save data', 'save acquisition'),
            ),
            _panel_control_target(
                'Save Folder',
                'AcquisitionPanel',
                'acquisition_dir_button',
                context='Recording and Saving',
                aliases=('save directory', 'output folder', 'acquisition folder'),
            ),
        ]


class BeadSelectionPanel(ControlPanelBase):

    def __init__(self, manager: 'UIManager'):
        super().__init__(manager=manager, title='Bead Selection', collapsed_by_default=False)

        # Instructions
        note_text = textwrap.dedent(
            """
            <b>Add a bead:</b> Left-click on the video<br>
            <b>Activate a bead:</b> Left-click on the bead ROI<br>
            <b>Move a bead:</b> Drag the active bead ROI<br>
            <b>Remove a bead:</b> Right-click on the bead
            """
        ).strip()
        note = QLabel(note_text)
        note.setWordWrap(True)
        self.layout().addWidget(note)

        next_id_row = QHBoxLayout()
        self.layout().addLayout(next_id_row)

        self.next_bead_id_label = QLabel()
        next_id_row.addWidget(self.next_bead_id_label)

        next_id_row.addStretch(1)

        self.reset_id_button = QPushButton('Reassign IDs')
        self.reset_id_button.clicked.connect(self.manager.reset_bead_ids)  # type: ignore
        next_id_row.addWidget(self.reset_id_button)

        self.update_next_bead_id_label(self.manager.bead_next_id)

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

        # Remove All Beads
        self.clear_button = QPushButton('Remove All Beads')
        self.clear_button.setEnabled(True)
        self.clear_button.clicked.connect(self.manager.clear_beads)  # type: ignore
        button_row.addWidget(self.clear_button)

    def search_targets(self) -> list[SearchTarget]:
        return [
            PanelControlTarget(
                label='Remove All Beads',
                aliases=('clear beads', 'delete beads'),
                context='Bead Selection',
                panel_id='BeadSelectionPanel',
                widget_path=('clear_button',),
            ),
            PanelControlTarget(
                label='Reassign IDs',
                aliases=('reset bead ids', 'renumber beads'),
                context='Bead Selection',
                panel_id='BeadSelectionPanel',
                widget_path=('reset_id_button',),
            ),
        ]

    def update_next_bead_id_label(self, next_bead_id: int) -> None:
        self.next_bead_id_label.setText(f"Next Bead ID: {next_bead_id}")

class CameraPanel(ControlPanelBase):

    def __init__(self, manager: 'UIManager'):
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


class HistogramPanel(MatplotlibCleanupMixin, ControlPanelBase):

    def __init__(self, manager: 'UIManager'):
        super().__init__(manager=manager, title='Histogram', collapsed_by_default=True)

        self.update_interval: float = 1  # seconds
        self._update_last_time: float = 0

        # ===== First Row ===== #
        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(8)
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
            label_text='Only ROIs', default=False)
        controls_row.addWidget(self.only_beads_checkbox)
        controls_row.addStretch(1)

        # ===== Plot ===== #
        self.n_bins = 256
        self.figure = Figure(dpi=100, facecolor=PANEL_BACKGROUND_COLOR, constrained_layout=True)
        self.figure.set_constrained_layout_pads(w_pad=0.02, h_pad=0.02, hspace=0.0, wspace=0.0)
        self.canvas = ResponsivePlotCanvas(
            self.figure,
            minimum_height=102,
            maximum_height=123,
            height_for_width=0.32,
        )
        self.axes = self.figure.subplots(nrows=1, ncols=1)

        _, _, self.bars = self.axes.hist(
            [],
            bins=self.n_bins,
            edgecolor=None,
            facecolor=get_accent_color(),
        )

        self.axes.set_facecolor(PANEL_BACKGROUND_COLOR)
        self.axes.set_xlabel('Intensity')
        self.axes.set_ylabel('Count')
        plot_font_size = self.font().pointSizeF()
        if plot_font_size <= 0:
            plot_font_size = float(self.font().pointSize() or 9)
        plot_font_size = max(6.0, plot_font_size - 1.5)
        self.axes.xaxis.label.set_size(plot_font_size)
        self.axes.yaxis.label.set_size(plot_font_size)
        self.axes.tick_params(axis='both', labelsize=plot_font_size)
        self.axes.set_yticks([])
        self.axes.set_xticks([])
        self.axes.spines['left'].set_visible(True)
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)
        self.axes.set_xlim(0, 1)

        self.layout().addWidget(self.canvas)
        self._init_matplotlib_cleanup()

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
            _, bead_rois = self.manager.get_cached_bead_rois()
            if len(bead_rois) > 0:
                image = crop_stack_to_rois(
                    np.swapaxes(image, 0, 1)[:, :, None], bead_rois)
            else:
                self.clear()
                return

        counts, _ = np.histogram(image, bins=256, range=(0, max_intensity))
        # fast safe log to prevent log(0)
        counts = np.log(counts + 1)

        for count, rect in zip(counts, self.bars.patches):
            rect.set_height(count)
            rect.set_facecolor(get_accent_color())

        max_count = counts.max() if len(counts) > 0 else 1
        self.axes.set_ylim(0, max_count * 1.1)

        self.canvas.draw()

    def clear(self):
        for rect in self.bars.patches:
            rect.set_height(0)
        self.canvas.draw()


class PlotSettingsPanel(ControlPanelBase):
    def __init__(self, manager: 'UIManager'):
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

        # Time axis mode selector sits between Z and Time rows
        row_index += 1
        self.grid_layout.addWidget(QLabel('Time axis mode'), row_index, 0)
        self.time_mode = QComboBox()
        self.time_mode.addItems(['Absolute', 'Relative'])
        self.time_mode.currentTextChanged.connect(self.time_mode_callback)
        self.grid_layout.addWidget(self.time_mode, row_index, 1, 1, 2)

        # Last row for "Time"
        row_index += 1
        self.time_label = QLabel('Time (H:M:S)')
        self.grid_layout.addWidget(self.time_label, row_index, 0)

        # Absolute time inputs (min/max)
        time_absolute_widget = QWidget()
        time_absolute_layout = QHBoxLayout()
        time_absolute_layout.setContentsMargins(0, 0, 0, 0)
        time_absolute_layout.setSpacing(4)
        time_absolute_widget.setLayout(time_absolute_layout)

        self.time_limits_absolute = (QLineEdit(), QLineEdit())
        self.time_limits_absolute[0].setPlaceholderText('auto')
        self.time_limits_absolute[1].setPlaceholderText('auto')
        self.time_limits_absolute[0].textChanged.connect(self.limits_callback)
        self.time_limits_absolute[1].textChanged.connect(self.limits_callback)
        time_absolute_layout.addWidget(self.time_limits_absolute[0])
        time_absolute_layout.addWidget(self.time_limits_absolute[1])

        # Relative time input (single window box)
        time_relative_widget = QWidget()
        time_relative_layout = QHBoxLayout()
        time_relative_layout.setContentsMargins(0, 0, 0, 0)
        time_relative_layout.setSpacing(4)
        time_relative_widget.setLayout(time_relative_layout)

        self.time_relative_window = QLineEdit('00:05:00')
        self.time_relative_window.textChanged.connect(self.relative_time_window_callback)
        time_relative_layout.addWidget(self.time_relative_window)

        self.time_inputs_stack = QStackedLayout()
        self.time_inputs_stack.addWidget(time_absolute_widget)
        self.time_inputs_stack.addWidget(time_relative_widget)
        time_inputs_container = QWidget()
        time_inputs_container.setLayout(self.time_inputs_stack)

        self.grid_layout.addWidget(time_inputs_container, row_index, 1, 1, 2)
        self.limits['Time'] = self.time_limits_absolute

        def _triangle_icon(direction: Qt.ArrowType) -> QIcon:
            side = 9.0
            height = math.sqrt(3) / 2 * side
            size = int(math.ceil(max(side, height))) + 4
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)

            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(size / 2, size / 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.palette().color(QPalette.ColorRole.WindowText))

            if direction == Qt.ArrowType.DownArrow:
                points = [
                    QPointF(0, height),
                    QPointF(-side / 2, 0),
                    QPointF(side / 2, 0),
                ]
            else:
                points = [
                    QPointF(height, 0),
                    QPointF(0, -side / 2),
                    QPointF(0, side / 2),
                ]

            painter.drawPolygon(QPolygonF(points))
            painter.end()

            return QIcon(pixmap)

        right_triangle_icon = _triangle_icon(Qt.ArrowType.RightArrow)
        down_triangle_icon = _triangle_icon(Qt.ArrowType.DownArrow)
        icon_size = (
            right_triangle_icon.availableSizes()[0]
            if right_triangle_icon.availableSizes()
            else QSize(12, 12)
        )

        bead_options_toggle = QToolButton()
        bead_options_toggle.setText('Advanced Options: Display bead centers')
        bead_options_toggle.setCheckable(True)
        bead_options_toggle.setChecked(False)
        bead_options_toggle.setIcon(right_triangle_icon)
        bead_options_toggle.setIconSize(icon_size)
        bead_options_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        subtitle_font = bead_options_toggle.font()
        subtitle_font.setPointSize(subtitle_font.pointSize() - 1)
        subtitle_font.setBold(False)
        bead_options_toggle.setFont(subtitle_font)
        bead_options_toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.layout().addWidget(bead_options_toggle)

        bead_view_container = QWidget()
        bead_view_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        bead_view_layout = QVBoxLayout()
        bead_view_layout.setContentsMargins(0, 0, 0, 0)
        bead_view_layout.setSpacing(4)
        bead_view_container.setLayout(bead_view_layout)

        # Show beads on view
        self.beads_in_view_on = LabeledCheckbox(
            label_text='Show beads on video? (slow)',
            default=False,
            callback=self.beads_in_view_on_callback,
        )
        bead_view_layout.addWidget(self.beads_in_view_on)

        # Number of timepoints to show
        self.beads_in_view_count = LabeledLineEdit(
            label_text='Number of timepoints to show',
            default='1',
            callback=self.beads_in_view_count_callback,
        )
        bead_view_layout.addWidget(self.beads_in_view_count)

        # Marker size
        self.beads_in_view_marker_size = LabeledLineEdit(
            label_text='Marker size',
            default='20',
            callback=self.beads_in_view_marker_size_callback,
        )
        bead_view_layout.addWidget(self.beads_in_view_marker_size)
        bead_view_container.setVisible(False)

        def _toggle_bead_overlay_options(checked: bool) -> None:
            bead_options_toggle.setIcon(
                down_triangle_icon if checked else right_triangle_icon)
            bead_view_container.setVisible(checked)
            self.groupbox.layout().activate()

        bead_options_toggle.toggled.connect(_toggle_bead_overlay_options)
        self.layout().addWidget(bead_view_container)

    def search_targets(self) -> list[SearchTarget]:
        return [
            _panel_control_target(
                'Selected Bead',
                'PlotSettingsPanel',
                'selected_bead',
                context='Plot Settings',
                aliases=('active bead', 'red bead'),
            ),
            _panel_control_target(
                'Reference Bead',
                'PlotSettingsPanel',
                'reference_bead',
                context='Plot Settings',
                aliases=('green bead', 'subtract bead'),
            ),
            _panel_control_target(
                'Time axis mode',
                'PlotSettingsPanel',
                'time_mode',
                context='Plot Settings',
                aliases=('time mode', 'absolute time', 'relative time'),
            ),
            _panel_control_target(
                'Relative Time',
                'PlotSettingsPanel',
                'time_relative_window',
                context='Plot Settings',
                aliases=('relative time window', 'time window'),
            ),
            _panel_control_target(
                'Show beads on video',
                'PlotSettingsPanel',
                'beads_in_view_on',
                context='Plot Settings',
                aliases=('show bead centers', 'bead overlay', 'plot beads on video'),
            ),
            _panel_control_target(
                'Number of timepoints to show',
                'PlotSettingsPanel',
                'beads_in_view_count',
                context='Plot Settings',
                aliases=('bead overlay history', 'timepoints'),
            ),
            _panel_control_target(
                'Marker size',
                'PlotSettingsPanel',
                'beads_in_view_marker_size',
                context='Plot Settings',
                aliases=('bead marker size', 'crosshair size'),
            ),
        ]

    def selected_bead_callback(self, value):
        try:
            bead = int(value)
        except (TypeError, ValueError):
            bead = -1
        self.manager.set_selected_bead(bead)

    def reference_bead_callback(self, value):
        value = self.reference_bead.lineedit.text()
        try:
            bead = int(value)
        except (TypeError, ValueError):
            bead = -1
        self.manager.set_reference_bead(None if bead < 0 else bead)

    def time_mode_callback(self, value: str):
        mode = value.lower()
        is_relative = mode == 'relative'
        self.time_inputs_stack.setCurrentIndex(1 if is_relative else 0)
        self.time_label.setText('Relative Time (H:M:S)' if is_relative else 'Time (H:M:S)')
        self.manager.plot_worker.time_mode_signal.emit(mode)
        if is_relative:
            self.relative_time_window_callback(self.time_relative_window.text())
        else:
            self.limits_callback(None)

    def relative_time_window_callback(self, _value):
        text = self.time_relative_window.text()
        window_seconds: float | None
        try:
            time_parts = text.replace('.', ':').split(':')
            if len(time_parts) == 1:
                hours, minutes, seconds = int(time_parts[0]), 0, 0
            elif len(time_parts) == 2:
                hours, minutes = map(int, time_parts)
                seconds = 0
            elif len(time_parts) == 3:
                hours, minutes, seconds = map(int, time_parts)
            else:
                raise ValueError
            window_seconds = hours * 3600 + minutes * 60 + seconds
            if window_seconds <= 0:
                window_seconds = None
        except (TypeError, ValueError):
            window_seconds = None
        self.manager.plot_worker.relative_window_signal.emit(window_seconds)

    def limits_callback(self, _):
        limits_payload = {}
        today = datetime.date.today()
        for axis_label, limit in self.limits.items():
            raw_values = [limit[0].text(), limit[1].text()]
            parsed_limits: list[float | None] = []
            for raw_value in raw_values:
                if axis_label == 'Time':
                    if self.time_mode.currentText().lower() == 'relative':
                        parsed_value = None
                    else:
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


def has_tweezepy_support() -> bool:
    return importlib.util.find_spec('tweezepy') is not None


def load_tweezepy_avar() -> tuple[callable | None, str | None]:
    cached_allanvar = sys.modules.get('tweezepy.allanvar')
    if cached_allanvar is not None:
        avar = getattr(cached_allanvar, 'avar', None)
        if avar is not None:
            return avar, None

    try:
        package_spec = importlib.util.find_spec('tweezepy')
    except (ImportError, ValueError) as exc:
        return None, str(exc).strip() or repr(exc)
    if package_spec is None or package_spec.origin is None:
        return None, 'tweezepy package not found'

    package_dir = os.path.dirname(package_spec.origin)
    allanvar_path = os.path.join(package_dir, 'allanvar.py')
    module_name = 'magscope_optional_tweezepy_allanvar'

    try:
        module_spec = importlib.util.spec_from_file_location(module_name, allanvar_path)
        if module_spec is None or module_spec.loader is None:
            return None, 'could not load tweezepy allanvar module'

        allanvar = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(allanvar)
    except Exception as exc:
        return None, str(exc).strip() or repr(exc)

    avar = getattr(allanvar, 'avar', None)
    if avar is None:
        return None, 'tweezepy.allanvar.avar is unavailable'
    return avar, None


class AllanDeviationPanel(MatplotlibCleanupMixin, ControlPanelBase):
    _SETTINGS_GROUP = 'AllanDeviationPanel'

    def __init__(self, manager: 'UIManager'):
        super().__init__(manager=manager, title='Allan Deviation', collapsed_by_default=True)

        refresh_row = QHBoxLayout()
        self.layout().addLayout(refresh_row)

        refresh_row.addStretch(1)
        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self.refresh_plot)  # type: ignore
        refresh_row.addWidget(self.refresh_button)
        refresh_row.addStretch(1)

        history_row = QHBoxLayout()
        self.layout().addLayout(history_row)

        history_row.addWidget(QLabel('History window'))
        self.history_window = QLineEdit(self._load_setting('history_window', '05:00'))
        self.history_window.setPlaceholderText('SS, MM:SS, or HH:MM:SS')
        self.history_window.setToolTip('Accepted formats: SS, MM:SS, or HH:MM:SS')
        history_row.addWidget(self.history_window)

        self.history_window_hint = QLabel('Format: SS, MM:SS, or HH:MM:SS')
        self.history_window_hint.setStyleSheet('color: #aaaaaa;')
        self.layout().addWidget(self.history_window_hint)

        taus_row = QHBoxLayout()
        self.layout().addLayout(taus_row)

        taus_row.addWidget(QLabel('Taus'))
        self.taus_mode = QComboBox()
        self.taus_mode.addItems(['Octave', 'Decade'])
        self.taus_mode.setCurrentText(self._load_setting('taus_mode', 'Octave'))
        taus_row.addWidget(self.taus_mode)

        self.figure = Figure(dpi=100, facecolor=PANEL_BACKGROUND_COLOR, constrained_layout=True)
        self.canvas = ResponsivePlotCanvas(
            self.figure,
            minimum_height=210,
            maximum_height=235,
            height_for_width=0.72,
        )
        self.axes = self.figure.subplots(nrows=1, ncols=1)
        self.layout().addWidget(self.canvas)

        self.status_label = QLabel('Click Refresh to compute Allan deviation')
        self.status_label.setWordWrap(True)
        self.layout().addWidget(self.status_label)

        self._configure_axes()
        self._init_matplotlib_cleanup()
        self.history_window.editingFinished.connect(self._persist_controls)  # type: ignore
        self.taus_mode.currentTextChanged.connect(lambda _value: self._persist_controls())

    def _settings(self) -> QSettings:
        return QSettings('MagScope', 'MagScope')

    def _setting_key(self, name: str) -> str:
        return f'{self._SETTINGS_GROUP}/{name}'

    def _load_setting(self, name: str, default: str) -> str:
        return self._settings().value(self._setting_key(name), default, type=str)

    def _persist_controls(self) -> None:
        settings = self._settings()
        settings.setValue(self._setting_key('history_window'), self.history_window.text().strip())
        settings.setValue(self._setting_key('taus_mode'), self.taus_mode.currentText())

    def _configure_axes(self) -> None:
        self.axes.clear()
        self.axes.set_facecolor(PANEL_BACKGROUND_COLOR)
        self.axes.set_xlabel('Tau (s)')
        self.axes.set_ylabel('Allan deviation (nm)')
        self.axes.set_xscale('log')
        self.axes.set_yscale('log')
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)

    def refresh_plot(self) -> None:
        self._persist_controls()

        avar, import_error = load_tweezepy_avar()
        if avar is None:
            if not has_tweezepy_support():
                self.clear('Tweezepy is not installed.')
                return
            self.clear(f'Tweezepy import failed: {import_error}')
            return

        window_seconds = self._parse_window_seconds(self.history_window.text())
        if window_seconds is None or window_seconds <= 0:
            self.clear('Enter a positive history window like 30, 05:00, or 01:00:00.')
            return

        tracks = self.manager.tracks_buffer.peak_unsorted()
        if tracks is None or not hasattr(tracks, 'size') or tracks.size == 0:
            self.clear('No track data available yet.')
            return

        tracks = np.asarray(tracks, dtype=np.float64)
        tracks = tracks[np.argsort(tracks[:, 0], kind='stable')]
        selected_bead = self.manager.selected_bead
        reference_bead = self.manager.reference_bead
        taus_mode = self.taus_mode.currentText().lower()

        self._configure_axes()
        plotted_axes: list[str] = []
        skipped_axes: list[str] = []
        for axis_name, color in (('X', 'r'), ('Y', 'lime'), ('Z', 'cyan')):
            timestamps, values = self._extract_axis_series(
                tracks,
                axis_name=axis_name,
                selected_bead=selected_bead,
                reference_bead=reference_bead,
            )
            if timestamps.size < 4 or values.size < 4:
                skipped_axes.append(f'{axis_name}: insufficient aligned track samples')
                continue

            windowed_timestamps, windowed_values = self._apply_history_window(
                timestamps,
                values,
                window_seconds,
            )
            if windowed_timestamps.size < 4 or windowed_values.size < 4:
                skipped_axes.append(f'{axis_name}: insufficient recent track samples')
                continue

            sampling_rate = self._estimate_sampling_rate(windowed_timestamps)
            if sampling_rate is None:
                skipped_axes.append(f'{axis_name}: invalid sampling rate')
                continue

            try:
                taus, _edfs, variances = avar(
                    windowed_values,
                    rate=sampling_rate,
                    taus=taus_mode,
                    overlapping=True,
                )
            except Exception as exc:
                skipped_axes.append(f'{axis_name}: Allan deviation calculation failed ({exc})')
                continue

            taus = np.asarray(taus, dtype=np.float64)
            deviations = np.sqrt(np.asarray(variances, dtype=np.float64))
            finite = np.isfinite(taus) & np.isfinite(deviations) & (taus > 0) & (deviations > 0)
            taus = taus[finite]
            deviations = deviations[finite]
            if taus.size == 0 or deviations.size == 0:
                skipped_axes.append(f'{axis_name}: no finite Allan deviation values')
                continue

            self.axes.plot(taus, deviations, color=color, label=axis_name)
            plotted_axes.append(axis_name)

        if not plotted_axes:
            self.canvas.draw()
            self.status_label.setText(
                'Could not plot Allan deviation. ' + ' '.join(f'Skipped {reason}.' for reason in skipped_axes)
            )
            return

        self.axes.legend(
            loc='upper right',
            frameon=False,
        )
        self.canvas.draw()

        if reference_bead is None:
            source_text = f'selected bead {selected_bead}'
        else:
            source_text = f'selected bead {selected_bead} minus reference bead {reference_bead}'
        status_message = f'Refreshed Allan deviation for {", ".join(plotted_axes)} using {source_text}.'
        if skipped_axes:
            status_message += ' ' + ' '.join(f'Skipped {reason}.' for reason in skipped_axes)
        self.status_label.setText(status_message)

    def clear(self, message: str = 'Click Refresh to compute Allan deviation') -> None:
        self._configure_axes()
        self.canvas.draw()
        self.status_label.setText(message)

    @staticmethod
    def _parse_window_seconds(value: str) -> float | None:
        text = value.strip()
        if not text:
            return None
        try:
            if ':' not in text:
                return float(text)
            parts = [float(part) for part in text.split(':')]
        except ValueError:
            return None
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return hours * 3600 + minutes * 60 + seconds
        if len(parts) == 2:
            minutes, seconds = parts
            return minutes * 60 + seconds
        if len(parts) == 1:
            return parts[0]
        return None

    @staticmethod
    def _estimate_sampling_rate(timestamps: np.ndarray) -> float | None:
        diffs = np.diff(np.asarray(timestamps, dtype=np.float64))
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        if diffs.size == 0:
            return None
        median_diff = float(np.median(diffs))
        if median_diff <= 0:
            return None
        return 1.0 / median_diff

    @staticmethod
    def _apply_history_window(
        timestamps: np.ndarray,
        values: np.ndarray,
        window_seconds: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        if timestamps.size == 0:
            return timestamps, values
        cutoff = float(np.max(timestamps)) - float(window_seconds)
        keep = timestamps >= cutoff
        return timestamps[keep], values[keep]

    @staticmethod
    def _extract_axis_series(
        tracks: np.ndarray,
        *,
        axis_name: str,
        selected_bead: int | None,
        reference_bead: int | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        axis_index = ['X', 'Y', 'Z'].index(axis_name) + 1
        finite_rows = np.isfinite(tracks[:, [0, axis_index, 4]]).all(axis=1)
        tracks = tracks[finite_rows]
        if tracks.size == 0 or selected_bead is None or selected_bead < 0:
            return np.asarray([]), np.asarray([])

        timestamps = tracks[:, 0]
        bead_ids = tracks[:, 4]
        values = tracks[:, axis_index]

        selected_mask = bead_ids == selected_bead
        timestamps_selected = timestamps[selected_mask]
        values_selected = values[selected_mask]
        if reference_bead is None:
            return timestamps_selected, values_selected

        reference_mask = bead_ids == reference_bead
        timestamps_reference = timestamps[reference_mask]
        values_reference = values[reference_mask]
        if timestamps_selected.size == 0 or timestamps_reference.size == 0:
            return np.asarray([]), np.asarray([])

        aligned_timestamps, index_selected, index_reference = np.intersect1d(
            timestamps_selected,
            timestamps_reference,
            assume_unique=False,
            return_indices=True,
        )
        aligned_values = values_selected[index_selected] - values_reference[index_reference]
        if axis_name == 'Z':
            aligned_values *= -1
        return aligned_timestamps, aligned_values


class ProfilePanel(MatplotlibCleanupMixin, ControlPanelBase):
    def __init__(self, manager: 'UIManager'):
        super().__init__(manager=manager, title='Radial Profile Monitor', collapsed_by_default=True)

        # Controls
        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(8)
        self.layout().addLayout(controls_row)

        self.enable = LabeledCheckbox(
            label_text='Enabled',
            callback=self.enabled_callback,
        )
        controls_row.addWidget(self.enable)
        self.groupbox.toggle_button.toggled.connect(self._groupbox_toggled)

        controls_row.addWidget(QLabel('Bead:'))
        self.selected_bead_label = QLabel('')
        self.selected_bead_label.setMinimumWidth(24)
        controls_row.addWidget(self.selected_bead_label)

        controls_row.addWidget(QLabel('Length:'))
        self.profile_length_label = QLabel('')
        self.profile_length_label.setMinimumWidth(36)
        controls_row.addWidget(self.profile_length_label)
        controls_row.addStretch(1)

        # Figure
        self.figure = Figure(dpi=100, facecolor=PANEL_BACKGROUND_COLOR, constrained_layout=True)
        self.figure.set_constrained_layout_pads(w_pad=0.02, h_pad=0.02, hspace=0.0, wspace=0.0)
        self.canvas = ResponsivePlotCanvas(
            self.figure,
            minimum_height=102,
            maximum_height=123,
            height_for_width=0.32,
        )
        self.layout().addWidget(self.canvas)

        # Plot
        self.axes = self.figure.subplots(nrows=1, ncols=1)
        self.axes.set_facecolor(PANEL_BACKGROUND_COLOR)
        self.axes.set_xlabel('Radius (pixels)')
        self.axes.set_ylabel('Intensity')
        plot_font_size = self.font().pointSizeF()
        if plot_font_size <= 0:
            plot_font_size = float(self.font().pointSize() or 9)
        plot_font_size = max(6.0, plot_font_size - 1.5)
        self.axes.xaxis.label.set_size(plot_font_size)
        self.axes.yaxis.label.set_size(plot_font_size)
        self.axes.tick_params(axis='both', labelsize=plot_font_size)
        self.axes.spines['top'].set_visible(False)
        self.axes.spines['right'].set_visible(False)
        self.axes.spines['left'].set_visible(True)
        self.axes.set_yticks([])
        self.line, = self.axes.plot([], [], color=get_accent_color(), linewidth=1.0)
        self._init_matplotlib_cleanup()

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

        self.profile_length_label.setText(str(int(profile_length)))

        cleaned_profile = latest_entry[3:3 + int(profile_length)]
        radial_distances = np.arange(profile_length)
        radial_distances = radial_distances[np.isfinite(cleaned_profile)]
        cleaned_profile = cleaned_profile[np.isfinite(cleaned_profile)]

        self.line.set_xdata(radial_distances)
        self.line.set_ydata(cleaned_profile)
        self.line.set_color(get_accent_color())

        if len(cleaned_profile) > 0:
            self.axes.set_xlim(0, max(radial_distances))
            self.axes.set_ylim(0, max(cleaned_profile))

        self.canvas.draw()

    def clear(self):
        self.selected_bead_label.setText('')
        self.profile_length_label.setText('')
        self.line.set_xdata([])
        self.line.set_ydata([])
        self.canvas.draw()


class TrackingOptionsPanel(ControlPanelBase):
    def __init__(self, manager: 'UIManager', *, collapsible: bool = True):
        super().__init__(
            manager=manager,
            title='Tracking Options' if collapsible else '',
            collapsed_by_default=True,
            collapsible=collapsible,
        )
        self._current_options: dict[str, Any] = tracking_options_from_qsettings()
        self._updating_fields = False

        note = QLabel(
            textwrap.dedent(
                """
                <a href="https://magtrack.readthedocs.io/en/stable/api/magtrack/core/index.html#magtrack.core.stack_to_xyzp_advanced">Advanced Tracking Options Guide</a>
                <br>Configure the arguments forwarded to MagTrack's
                stack_to_xyzp_advanced pipeline. Changes are applied when a field loses focus
                or Enter is pressed. Defaults reflect MagTrack's standard parameters.
                """
            ).strip()
        )
        note.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        note.setOpenExternalLinks(True)
        note.setWordWrap(True)
        self.layout().addWidget(note)

        background_row = QHBoxLayout()
        background_row.addWidget(QLabel('Center-of-mass background:'))
        self.background_combo = QComboBox()
        self.background_combo.addItems(['none', 'mean', 'median'])
        self.background_combo.setCurrentText(self._current_options['center_of_mass']['background'])
        self.background_combo.currentTextChanged.connect(lambda _value: self._apply_options_from_inputs())
        background_row.addWidget(self.background_combo)
        background_row.addStretch(1)
        self.layout().addLayout(background_row)

        self.iterations = LabeledLineEditWithValue(
            label_text='Auto-conv iterations',
            default=str(self._current_options['n auto_conv_multiline_sub_pixel']),
            widths=(150, 60, 0),
        )
        self.layout().addWidget(self.iterations)

        self.line_ratio = LabeledLineEditWithValue(
            label_text='Line ratio',
            default=str(self._current_options['auto_conv_multiline_sub_pixel']['line_ratio']),
            widths=(150, 60, 0),
        )
        self.layout().addWidget(self.line_ratio)

        self.n_local = LabeledLineEditWithValue(
            label_text='n_local (auto-conv)',
            default=str(self._current_options['auto_conv_multiline_sub_pixel']['n_local']),
            widths=(150, 60, 0),
        )
        self.layout().addWidget(self.n_local)

        self.use_fft = LabeledCheckbox(
            label_text='Use FFT profile',
            callback=self._use_fft_changed,
        )
        self.layout().addWidget(self.use_fft)

        self.fft_oversample = LabeledLineEditWithValue(
            label_text='FFT oversample',
            default=str(self._current_options['fft_profile']['oversample']),
            widths=(150, 60, 0),
        )
        self.layout().addWidget(self.fft_oversample)

        self.fft_rmin = LabeledLineEditWithValue(
            label_text='FFT rmin',
            default=str(self._current_options['fft_profile']['rmin']),
            widths=(150, 60, 0),
        )
        self.layout().addWidget(self.fft_rmin)

        self.fft_rmax = LabeledLineEditWithValue(
            label_text='FFT rmax',
            default=str(self._current_options['fft_profile']['rmax']),
            widths=(150, 60, 0),
        )
        self.layout().addWidget(self.fft_rmax)

        self.fft_gaus_factor = LabeledLineEditWithValue(
            label_text='FFT gaus_factor',
            default=str(self._current_options['fft_profile']['gaus_factor']),
            widths=(150, 60, 0),
        )
        self.layout().addWidget(self.fft_gaus_factor)

        self.radial_oversample = LabeledLineEditWithValue(
            label_text='Radial oversample',
            default=str(self._current_options['radial_profile']['oversample']),
            widths=(150, 60, 0),
        )
        self.layout().addWidget(self.radial_oversample)

        self.lookup_n_local = LabeledLineEditWithValue(
            label_text='lookup_z n_local',
            default=str(self._current_options['lookup_z']['n_local']),
            widths=(150, 60, 0),
        )
        self.layout().addWidget(self.lookup_n_local)

        for widget in self._option_line_edits():
            widget.lineedit.editingFinished.connect(self._apply_options_from_inputs)  # type: ignore[arg-type]

        self._update_value_labels()
        self._populate_inputs_from_options()
        self._sync_fft_enabled_state()

    @staticmethod
    def search_targets() -> list[SearchTarget]:
        return _preference_widget_targets(
            (
                ('use_fft', 'Use FFT profile', ('FFT profile', 'enable FFT profile')),
                ('fft_oversample', 'FFT oversample', ('FFT oversampling',)),
                ('fft_rmin', 'FFT rmin', ('FFT Rmin', 'rmin', 'FFT r min', 'minimum FFT radius')),
                ('fft_rmax', 'FFT rmax', ('FFT Rmax', 'rmax', 'FFT r max', 'maximum FFT radius')),
                ('fft_gaus_factor', 'FFT gaus_factor', ('FFT gaussian factor', 'FFT gaus factor')),
                ('radial_oversample', 'Radial oversample', ('radial oversampling',)),
                ('lookup_n_local', 'lookup_z n_local', ('lookup z n local', 'z lookup n local')),
                ('iterations', 'Auto-conv iterations', ('auto conv iterations', 'auto convolution iterations')),
                ('line_ratio', 'Line ratio', ('tracking line ratio',)),
                ('n_local', 'n_local (auto-conv)', ('n local auto conv', 'auto conv n local')),
            ),
            tab_name='Tracking',
            context='Preferences > Tracking',
        )

    def _option_line_edits(self) -> tuple[LabeledLineEditWithValue, ...]:
        return (
            self.iterations,
            self.line_ratio,
            self.n_local,
            self.fft_oversample,
            self.fft_rmin,
            self.fft_rmax,
            self.fft_gaus_factor,
            self.radial_oversample,
            self.lookup_n_local,
        )

    def _update_value_labels(self) -> None:
        self.iterations.value_label.setText(str(self._current_options['n auto_conv_multiline_sub_pixel']))
        self.line_ratio.value_label.setText(str(self._current_options['auto_conv_multiline_sub_pixel']['line_ratio']))
        self.n_local.value_label.setText(str(self._current_options['auto_conv_multiline_sub_pixel']['n_local']))
        self.radial_oversample.value_label.setText(str(self._current_options['radial_profile']['oversample']))
        self.lookup_n_local.value_label.setText(str(self._current_options['lookup_z']['n_local']))

        fft_settings = self._current_options['fft_profile']
        self.fft_oversample.value_label.setText(str(fft_settings['oversample']))
        self.fft_rmin.value_label.setText(str(fft_settings['rmin']))
        self.fft_rmax.value_label.setText(str(fft_settings['rmax']))
        self.fft_gaus_factor.value_label.setText(str(fft_settings['gaus_factor']))

        self.use_fft.checkbox.blockSignals(True)
        self.use_fft.checkbox.setChecked(bool(self._current_options['use fft_profile']))
        self.use_fft.checkbox.blockSignals(False)

    def _sync_fft_enabled_state(self) -> None:
        use_fft = self.use_fft.checkbox.isChecked()
        for widget in (self.fft_oversample, self.fft_rmin, self.fft_rmax, self.fft_gaus_factor):
            widget.setEnabled(use_fft)
        self.radial_oversample.setEnabled(not use_fft)

    def _use_fft_changed(self, value: bool) -> None:
        if self._updating_fields:
            return
        self._apply_options_from_inputs()

    def _set_options(
        self,
        options: dict[str, Any],
        message: str | None = None,
        *,
        populate_inputs: bool = False,
    ) -> None:
        self._current_options = tracking_options_from_mapping(options)
        self._updating_fields = True
        try:
            self.background_combo.blockSignals(True)
            self.background_combo.setCurrentText(self._current_options['center_of_mass']['background'])
            self.background_combo.blockSignals(False)
            self._update_value_labels()
            self._populate_inputs_from_options()
            self._sync_fft_enabled_state()
        finally:
            self.background_combo.blockSignals(False)
            self._updating_fields = False
        save_tracking_options_to_qsettings(self._current_options)
        self.manager.send_ipc(UpdateTrackingOptionsCommand(value=copy.deepcopy(self._current_options)))

    def _populate_inputs_from_options(self) -> None:
        self.iterations.lineedit.setText(str(self._current_options['n auto_conv_multiline_sub_pixel']))
        self.line_ratio.lineedit.setText(str(self._current_options['auto_conv_multiline_sub_pixel']['line_ratio']))
        self.n_local.lineedit.setText(str(self._current_options['auto_conv_multiline_sub_pixel']['n_local']))
        self.fft_oversample.lineedit.setText(str(self._current_options['fft_profile']['oversample']))
        self.fft_rmin.lineedit.setText(str(self._current_options['fft_profile']['rmin']))
        self.fft_rmax.lineedit.setText(str(self._current_options['fft_profile']['rmax']))
        self.fft_gaus_factor.lineedit.setText(str(self._current_options['fft_profile']['gaus_factor']))
        self.radial_oversample.lineedit.setText(str(self._current_options['radial_profile']['oversample']))
        self.lookup_n_local.lineedit.setText(str(self._current_options['lookup_z']['n_local']))

    def _load_options_from_mapping(self, loaded: Any) -> dict[str, Any]:
        return tracking_options_from_mapping(loaded)

    def _options_from_inputs(self) -> dict[str, Any]:
        return {
            'center_of_mass': {'background': self.background_combo.currentText()},
            'n auto_conv_multiline_sub_pixel': self.iterations.lineedit.text().strip(),
            'auto_conv_multiline_sub_pixel': {
                'line_ratio': self.line_ratio.lineedit.text().strip(),
                'n_local': self.n_local.lineedit.text().strip(),
            },
            'use fft_profile': self.use_fft.checkbox.isChecked(),
            'fft_profile': {
                'oversample': self.fft_oversample.lineedit.text().strip(),
                'rmin': self.fft_rmin.lineedit.text().strip(),
                'rmax': self.fft_rmax.lineedit.text().strip(),
                'gaus_factor': self.fft_gaus_factor.lineedit.text().strip(),
            },
            'radial_profile': {'oversample': self.radial_oversample.lineedit.text().strip()},
            'lookup_z': {'n_local': self.lookup_n_local.lineedit.text().strip()},
        }

    def _apply_options_from_inputs(self) -> None:
        if self._updating_fields:
            return
        try:
            options = tracking_options_from_mapping(self._options_from_inputs())
        except ValueError as exc:
            QMessageBox.critical(self, 'Tracking options', str(exc))
            self._updating_fields = True
            try:
                self._populate_inputs_from_options()
                self.background_combo.setCurrentText(self._current_options['center_of_mass']['background'])
                self._update_value_labels()
                self._sync_fft_enabled_state()
            finally:
                self._updating_fields = False
            return
        if options == self._current_options:
            self._populate_inputs_from_options()
            self._sync_fft_enabled_state()
            return
        self._set_options(options)

    def reset_defaults(self) -> None:
        self._set_options(default_tracking_options(), 'Defaults restored', populate_inputs=True)


class PreferencesDialog(QDialog):
    """Modal dialog for global MagScope preferences."""

    _SIDEBAR_SECTIONS: tuple[tuple[str, str], ...] = (
        ('tune', 'MagScope'),
        ('ads_click', 'Tracking'),
        ('palette', 'Appearance/Layout'),
    )

    def __init__(self, manager: 'UIManager'):
        super().__init__(manager.windows[0] if getattr(manager, 'windows', None) else None)
        self.manager = manager
        self.setWindowTitle('Preferences')
        self.setModal(True)
        self.resize(880, 700)

        # --- dark theme ---
        accent = self.manager.settings[GUI_ACCENT_COLOR_SETTING]
        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: #111111;
            }}
            #preferencesHeader {{
                background-color: #161616;
            }}
            #preferencesHeader QLabel {{
                color: #bbbbbb;
                background: transparent;
            }}
            #preferencesHeader QPushButton {{
                background-color: #242424;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 4px 12px;
                color: #cccccc;
            }}
            #preferencesHeader QPushButton:hover {{
                background-color: #333333;
            }}
            #preferencesSeparator {{
                background-color: #2a2a2a;
                max-height: 1px;
            }}
            QListWidget {{
                background-color: #1b1b1b;
                border: none;
                border-right: 1px solid #2a2a2a;
                padding: 8px 0px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 7px 16px;
                margin: 0px;
                border-radius: 0px;
                border-left: 3px solid transparent;
                color: #cccccc;
            }}
            QListWidget::item:selected {{
                background-color: #1e2a3a;
                border-left: 3px solid {accent};
                color: #e0e0e0;
            }}
            QListWidget::item:hover:!selected {{
                background-color: #222222;
                border-left: 3px solid #333333;
            }}
            QStackedWidget {{
                background-color: #111111;
            }}
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QGroupBox {{
                background-color: #181818;
                border: 1px solid #2a2a2a;
                border-radius: 4px;
                margin-top: 14px;
                padding-top: 16px;
                font-weight: bold;
                color: #aaaaaa;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
            }}
            QLineEdit {{
                background-color: #242424;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 3px 6px;
                color: #e0e0e0;
                selection-background-color: {accent};
            }}
            QLineEdit:focus {{
                border: 1px solid {accent};
            }}
            QComboBox {{
                background-color: #242424;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 3px 6px;
                color: #e0e0e0;
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: #242424;
                border: 1px solid #3a3a3a;
                color: #e0e0e0;
                selection-background-color: #1e2a3a;
            }}
            QLabel {{
                color: #bbbbbb;
                background: transparent;
            }}
            #preferencesColumnHeader {{
                color: #777777;
                font-size: 11px;
            }}
            #preferencesSavedLabel {{
                color: #777777;
                font-size: 11px;
                padding-left: 4px;
            }}
            #preferencesDescription {{
                color: #888888;
            }}
            #preferencesFooter {{
                background-color: #161616;
            }}
            #preferencesFooter QPushButton {{
                background-color: #242424;
                border: 1px solid #3a3a3a;
                border-radius: 3px;
                padding: 5px 16px;
                color: #cccccc;
            }}
            #preferencesFooter QPushButton:hover {{
                background-color: #333333;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- header bar ---
        header = QWidget(self)
        header.setObjectName("preferencesHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 8, 20, 8)
        header_layout.setSpacing(8)
        header_layout.addWidget(QLabel("Preferences file:"))

        self.load_preferences_button = QPushButton("Load Preferences...")
        self.load_preferences_button.clicked.connect(self._on_load_preferences_clicked)  # type: ignore[arg-type]
        header_layout.addWidget(self.load_preferences_button)

        self.save_preferences_button = QPushButton("Save Preferences...")
        self.save_preferences_button.clicked.connect(self._on_save_preferences_clicked)  # type: ignore[arg-type]
        header_layout.addWidget(self.save_preferences_button)

        self.reset_all_preferences_button = QPushButton("Reset All Preferences")
        self.reset_all_preferences_button.clicked.connect(self._on_reset_all_preferences_clicked)  # type: ignore[arg-type]
        header_layout.addWidget(self.reset_all_preferences_button)

        self.preferences_file_status = FlashLabel()
        self.preferences_file_status.setText(
            "Save or load MagScope, tracking, appearance, and layout preferences together."
        )
        header_layout.addWidget(self.preferences_file_status)

        header_layout.addStretch(1)
        layout.addWidget(header)

        # --- separator ---
        separator = QFrame(self)
        separator.setObjectName("preferencesSeparator")
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)

        # --- content: sidebar + stacked pages ---
        content_row = QHBoxLayout()
        content_row.setSpacing(0)

        self.settings_panel = MagScopeSettingsPanel(manager, collapsible=False)
        self.settings_scroll = self._scrollable_tab(self.settings_panel)

        self.tracking_options_panel = TrackingOptionsPanel(manager, collapsible=False)
        self.tracking_scroll = self._scrollable_tab(self.tracking_options_panel)

        self.appearance_layout_tab = self._create_appearance_layout_tab()

        self.stack = QStackedWidget(self)
        self.stack.addWidget(self.settings_scroll)
        self.stack.addWidget(self.tracking_scroll)
        self.stack.addWidget(self.appearance_layout_tab)

        self.sidebar = QListWidget(self)
        self.sidebar.setFixedWidth(210)
        self.sidebar.setSpacing(0)
        self.sidebar.currentRowChanged.connect(self._on_sidebar_selection)  # type: ignore[arg-type]

        icon_font = type(self.manager)._material_symbols_font(point_size=14)
        for icon_name, label in self._SIDEBAR_SECTIONS:
            icon = self._make_material_symbol_icon(icon_font, icon_name, "#888888", 16)
            item = QListWidgetItem(icon, label)
            self.sidebar.addItem(item)

        content_row.addWidget(self.sidebar)
        content_row.addWidget(self.stack, 1)

        layout.addLayout(content_row, 1)

        # --- footer ---
        footer = QWidget(self)
        footer.setObjectName("preferencesFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 6, 20, 6)
        footer_layout.addStretch(1)
        self.reset_section_button = QPushButton("Reset Current Section")
        self.reset_section_button.clicked.connect(self._on_reset_current_section)  # type: ignore[arg-type]
        footer_layout.addWidget(self.reset_section_button)
        layout.addWidget(footer)

        self.sidebar.setCurrentRow(0)

    def _on_load_preferences_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Load preferences',
            '',
            'YAML Files (*.yaml);;All Files (*)',
        )
        if not path:
            return

        try:
            bundle = import_preferences_bundle(path)
            validate_layout = getattr(self.manager, 'validate_appearance_layout_preferences', None)
            if callable(validate_layout):
                validate_layout(bundle['appearance_layout'])
            import_layout = getattr(self.manager, 'import_appearance_layout_preferences', None)
            if callable(import_layout):
                import_layout(bundle['appearance_layout'])
            self.settings_panel._push_settings(bundle['magscope'])
            accent_color = self.manager.settings[GUI_ACCENT_COLOR_SETTING]
            self.accent_color_input.setText(accent_color)
            self._update_accent_color_swatch(accent_color)
            self.tracking_options_panel._set_options(
                bundle['tracking'],
                f'Loaded preferences from {os.path.basename(path)}',
                populate_inputs=True,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, 'Preferences', str(exc))
            return

        self.preferences_file_status.setText(f'Loaded preferences from {os.path.basename(path)}')

    def _on_save_preferences_clicked(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            'Save preferences',
            'magscope-preferences.yaml',
            'YAML Files (*.yaml);;All Files (*)',
        )
        if not path:
            return

        export_layout = getattr(self.manager, 'export_appearance_layout_preferences', None)
        appearance_layout = export_layout() if callable(export_layout) else {}
        try:
            export_preferences_bundle(
                path,
                magscope_settings=self.manager.settings.clone(),
                tracking_options=self.tracking_options_panel._current_options,
                appearance_layout=appearance_layout,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, 'Preferences', str(exc))
            return

        self.preferences_file_status.setText(f'Saved preferences to {os.path.basename(path)}')

    def _on_reset_all_preferences_clicked(self) -> None:
        confirmation = QMessageBox.question(
            self,
            'Reset Preferences',
            'Reset all MagScope, tracking, appearance, and layout preferences to defaults?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        self.settings_panel._push_settings(MagScopeSettings())
        self.tracking_options_panel.reset_defaults()
        self._reset_appearance_layout(reset_accent=False)
        accent_color = self.manager.settings[GUI_ACCENT_COLOR_SETTING]
        self.accent_color_input.setText(accent_color)
        self._update_accent_color_swatch(accent_color)
        self.preferences_file_status.setText('All preferences reset to defaults')

    def _create_appearance_layout_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(14)

        title = QLabel("Appearance & Layout")
        font = title.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        description = QLabel(
            "Customize GUI accent color, viewer layout, and collapsed panel states. "
            "Layout preferences are saved per-session."
        )
        description.setWordWrap(True)
        description.setObjectName("preferencesDescription")
        layout.addWidget(description)

        accent_group = QGroupBox("Accent", tab)
        accent_group.setFlat(True)
        accent_inner = QHBoxLayout(accent_group)
        accent_inner.setContentsMargins(16, 20, 16, 16)
        accent_inner.setSpacing(10)

        accent_label = QLabel("Accent color", accent_group)
        accent_label.setFixedWidth(140)
        accent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        accent_inner.addWidget(accent_label)

        self.accent_color_input = QLineEdit(
            self.manager.settings[GUI_ACCENT_COLOR_SETTING],
            accent_group,
        )
        self.accent_color_input.setObjectName("AccentColorInput")
        self.accent_color_input.setFixedWidth(120)
        self.accent_color_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.accent_color_input.setToolTip(
            f"Use #RRGGBB hex format, for example {DEFAULT_GUI_ACCENT_COLOR}."
        )
        self.accent_color_input.editingFinished.connect(  # type: ignore[arg-type]
            self._apply_accent_color_setting
        )
        accent_inner.addWidget(self.accent_color_input)

        self.accent_color_swatch = QPushButton("", accent_group)
        self.accent_color_swatch.setObjectName("AccentColorSwatch")
        self.accent_color_swatch.setFixedSize(28, 28)
        self.accent_color_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.accent_color_swatch.setToolTip("Choose accent color")
        self.accent_color_swatch.clicked.connect(  # type: ignore[arg-type]
            self._choose_accent_color
        )
        accent_inner.addWidget(self.accent_color_swatch)

        self.choose_accent_color_button = QPushButton("Choose\xa0...", accent_group)
        self.choose_accent_color_button.clicked.connect(  # type: ignore[arg-type]
            self._choose_accent_color
        )
        accent_inner.addWidget(self.choose_accent_color_button)
        accent_inner.addStretch(1)

        layout.addWidget(accent_group)
        self._update_accent_color_swatch(self.manager.settings[GUI_ACCENT_COLOR_SETTING])

        self.appearance_status_label = FlashLabel()
        self.appearance_status_label.setText("")
        layout.addWidget(self.appearance_status_label)

        layout.addStretch(1)
        return tab

    def _choose_accent_color(self) -> None:
        current_color = self.manager.settings[GUI_ACCENT_COLOR_SETTING]
        color = QColorDialog.getColor(
            QColor(current_color),
            self,
            'Choose accent color',
        )
        if color.isValid():
            self.accent_color_input.setText(color.name())
            self._apply_accent_color_setting()

    def _update_accent_color_swatch(self, color: str) -> None:
        self.accent_color_swatch.setStyleSheet(
            f"""
            #AccentColorSwatch {{
                background-color: {color};
                border: 1px solid palette(mid);
                border-radius: 3px;
            }}
            #AccentColorSwatch:hover {{
                border: 1px solid palette(light);
            }}
            """
        )

    def _apply_accent_color_setting(self) -> None:
        settings = self.manager.settings.clone()
        try:
            settings[GUI_ACCENT_COLOR_SETTING] = self.accent_color_input.text()
        except ValueError as exc:
            QMessageBox.critical(self, 'Accent Color', str(exc))
            current_color = self.manager.settings[GUI_ACCENT_COLOR_SETTING]
            self.accent_color_input.setText(current_color)
            self._update_accent_color_swatch(current_color)
            return

        accent_color = settings[GUI_ACCENT_COLOR_SETTING]
        if accent_color == self.manager.settings[GUI_ACCENT_COLOR_SETTING]:
            self.accent_color_input.setText(accent_color)
            self._update_accent_color_swatch(accent_color)
            return

        self.manager.settings = settings.clone()
        self.settings_panel._current_settings = settings.clone()
        apply_accent_color = getattr(self.manager, '_apply_accent_color', None)
        if callable(apply_accent_color):
            apply_accent_color(accent_color)
        self.manager.send_ipc(UpdateSettingsCommand(settings=settings.clone()))
        self.accent_color_input.setText(accent_color)
        self._update_accent_color_swatch(accent_color)
        self.settings_panel._refresh_fields()
        self.appearance_status_label.setText('Accent color updated')

    def _scrollable_tab(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    @staticmethod
    def _make_material_symbol_icon(
        font: QFont,
        text: str,
        color: str = "#888888",
        size: int = 16,
    ) -> QIcon:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return QIcon(pixmap)

    def _on_sidebar_selection(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)

    def _on_reset_current_section(self) -> None:
        index = self.stack.currentIndex()
        section_labels = [label for _, label in self._SIDEBAR_SECTIONS]
        section_name = section_labels[index] if 0 <= index < len(section_labels) else "this section"

        confirmation = QMessageBox.question(
            self,
            f"Reset {section_name}",
            f"Reset {section_name} preferences to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        if index == 0:
            self.settings_panel.reset_defaults()
        elif index == 1:
            self.tracking_options_panel.reset_defaults()
        elif index == 2:
            self._reset_appearance_layout(reset_accent=True)

    def _stack_index_for_scroll(self, scroll: QScrollArea) -> int:
        if scroll is self.settings_scroll:
            return 0
        if scroll is self.tracking_scroll:
            return 1
        if scroll is self.appearance_layout_tab:
            return 2
        return -1

    def reveal_setting(self, setting_key: str) -> None:
        self.reveal_magscope_setting(setting_key)

    def reveal_magscope_setting(self, setting_key: str) -> None:
        widget = self.settings_panel._setting_inputs.get(setting_key)
        if widget is None:
            return

        self._reveal_widget(self.settings_scroll, widget)

    def reveal_tracking_option(self, widget_attr: str) -> None:
        self.reveal_widget('Tracking', widget_attr)

    def reveal_widget(self, tab_name: str, widget_attr: str) -> None:
        if tab_name == 'Tracking':
            scroll = self.tracking_scroll
            panel = self.tracking_options_panel
        elif tab_name == 'MagScope':
            scroll = self.settings_scroll
            panel = self.settings_panel
        else:
            return

        widget = getattr(panel, widget_attr, None)
        if not isinstance(widget, QWidget):
            return

        self._reveal_widget(scroll, widget)

    def _reveal_widget(self, scroll: QScrollArea, widget: QWidget) -> None:
        stack_idx = self._stack_index_for_scroll(scroll)
        if stack_idx >= 0:
            self.sidebar.setCurrentRow(stack_idx)
        self.show()
        self.raise_()
        self.activateWindow()
        scroll.ensureWidgetVisible(widget)
        QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(widget))

        highlight = getattr(self.manager, '_highlight_search_widget', None)
        if callable(highlight):
            highlight(widget)
        if isinstance(widget, QLineEdit):
            widget.setFocus()
            widget.selectAll()
        else:
            lineedit = getattr(widget, 'lineedit', None)
            if isinstance(lineedit, QLineEdit):
                lineedit.setFocus()
                lineedit.selectAll()

    def _on_reset_appearance_tab_clicked(self) -> None:
        confirmation = QMessageBox.question(
            self,
            'Reset Appearance/Layout',
            'Reset appearance and layout preferences to defaults?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation == QMessageBox.StandardButton.Yes:
            self._reset_appearance_layout(reset_accent=True)

    def _reset_appearance_layout(self, *, reset_accent: bool) -> None:
        if reset_accent:
            settings = self.manager.settings.clone()
            settings[GUI_ACCENT_COLOR_SETTING] = DEFAULT_GUI_ACCENT_COLOR
            self.manager.settings = settings.clone()
            self.settings_panel._current_settings = settings.clone()
            apply_accent_color = getattr(self.manager, '_apply_accent_color', None)
            if callable(apply_accent_color):
                apply_accent_color(DEFAULT_GUI_ACCENT_COLOR)
            self.manager.send_ipc(UpdateSettingsCommand(settings=settings.clone()))
            self.accent_color_input.setText(DEFAULT_GUI_ACCENT_COLOR)
            self._update_accent_color_swatch(DEFAULT_GUI_ACCENT_COLOR)
            self.settings_panel._refresh_fields()

        reset_layout = getattr(self.manager, 'reset_appearance_layout_preferences', None)
        if callable(reset_layout):
            reset_layout()
        self.appearance_status_label.setText('Appearance/Layout reset to defaults')


class ScriptPanel(ControlPanelBase):
    NO_SCRIPT_SELECTED_TEXT = 'No script loaded'

    def __init__(self, manager: 'UIManager'):
        super().__init__(manager=manager, title='Scripting', collapsed_by_default=True)

        self.status_prefix = 'Status'
        self.status_label = QLabel('Status: Empty')
        self.layout().addWidget(self.status_label)

        self.step_position_label = QLabel()
        self.step_position_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.layout().addWidget(self.step_position_label)

        self.step_description_label = QLabel()
        self.step_description_label.setWordWrap(True)
        self.step_description_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.layout().addWidget(self.step_description_label)

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
        self.update_step(None, 0, None)

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

    def update_step(self, current_step: int | None, total_steps: int, description: str | None):
        total_steps = max(total_steps, 0)
        current_text = '-' if current_step is None else str(current_step)
        total_text = '-' if total_steps == 0 else str(total_steps)
        position_text = f'{current_text}/{total_text}'

        self.step_position_label.setText(f'Step: {position_text}')

        if description:
            self.step_description_label.setText(description)
            self.step_description_label.setVisible(True)
        else:
            self.step_description_label.clear()
            self.step_description_label.setVisible(False)

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
    def __init__(self, manager: 'UIManager'):
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
    def __init__(self, manager: 'UIManager'):
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
        self.once_button = QPushButton('Once')
        self.once_button.clicked.connect(self.once_callback)
        controls_row.addWidget(self.once_button)

        # Interval
        default_interval = self.manager.settings['xy-lock default interval']
        self.interval = LabeledLineEditWithValue(
            label_text='Interval (sec)',
            default=f'{default_interval} sec',
            callback=self.interval_callback,
            widths=(105, 100, 0),
        )
        self.layout().addWidget(self.interval)

        # Max
        default_max = self.manager.settings['xy-lock default max']
        self.max = LabeledLineEditWithValue(
            label_text='Max (pixels)',
            default=f'{default_max} pixels',
            callback=self.max_callback,
            widths=(105, 100, 0),
        )
        self.layout().addWidget(self.max)

        # Averaging Window
        default_window = self.manager.settings.get('xy-lock default window', '')
        self.window = LabeledLineEditWithValue(
            label_text='Averaging Window',
            default=f'{default_window} frames',
            callback=self.window_callback,
            widths=(105, 100, 0),
        )
        self.layout().addWidget(self.window)

    def search_targets(self) -> list[SearchTarget]:
        return [
            _panel_control_target('XY-Lock Enabled', 'XYLockPanel', 'enabled', context='XY-Lock', aliases=('enable xy lock', 'xy lock on')),
            _panel_control_target('XY-Lock Once', 'XYLockPanel', 'once_button', context='XY-Lock', aliases=('run xy lock once', 'center beads once')),
            _panel_control_target('XY-Lock Interval', 'XYLockPanel', 'interval', context='XY-Lock', aliases=('xy lock frequency',)),
            _panel_control_target('XY-Lock Max', 'XYLockPanel', 'max', context='XY-Lock', aliases=('xy lock maximum', 'xy lock max pixels')),
            _panel_control_target('XY-Lock Averaging Window', 'XYLockPanel', 'window', context='XY-Lock', aliases=('xy lock window', 'xy lock averaging')),
        ]

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
        stripped_value = value.strip()
        try:
            interval_seconds = float(stripped_value)
        except ValueError:
            return
        if interval_seconds <= 0 or (interval_seconds == 0 and stripped_value.startswith('-')):
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
        if max_distance < 1:
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
        self.window.value_label.setText(f'{value} frames')


class ZLockPanel(ControlPanelBase):
    def __init__(self, manager: 'UIManager'):
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
            default='Not set',
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

        # Averaging Window
        default_window = self.manager.settings.get('z-lock default window', '')
        self.window = LabeledLineEditWithValue(
            label_text='Averaging Window',
            default=f'{default_window} frames',
            callback=self.window_callback,
            widths=(75, 100, 0),
        )
        self.layout().addWidget(self.window)

    def search_targets(self) -> list[SearchTarget]:
        return [
            _panel_control_target('Z-Lock Enabled', 'ZLockPanel', 'enabled', context='Z-Lock', aliases=('enable z lock', 'z lock on')),
            _panel_control_target('Z-Lock Bead', 'ZLockPanel', 'bead', context='Z-Lock', aliases=('focus bead', 'z lock bead roi')),
            _panel_control_target('Z-Lock Target', 'ZLockPanel', 'target', context='Z-Lock', aliases=('z target', 'focus target')),
            _panel_control_target('Z-Lock Interval', 'ZLockPanel', 'interval', context='Z-Lock', aliases=('z lock frequency',)),
            _panel_control_target('Z-Lock Max', 'ZLockPanel', 'max', context='Z-Lock', aliases=('z lock maximum', 'z lock max nm')),
            _panel_control_target('Z-Lock Averaging Window', 'ZLockPanel', 'window', context='Z-Lock', aliases=('z lock window', 'z lock averaging')),
        ]

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

        self.update_bead(bead_index)

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

        self.update_target(target_nm)

        # Send value
        command = SetZLockTargetCommand(value=target_nm)
        self.manager.send_ipc(command)

    def interval_callback(self):
        # Get value
        value = self.interval.lineedit.text()
        self.interval.lineedit.setText('')

        # Check value
        stripped_value = value.strip()
        try:
            interval_seconds = float(stripped_value)
        except ValueError:
            return
        if interval_seconds <= 0 or (interval_seconds == 0 and stripped_value.startswith('-')):
            return

        self.update_interval(interval_seconds)

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

        self.update_max(max_nm)

        # Send value
        command = SetZLockMaxCommand(value=max_nm)
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

        self.update_window(window_size)

        # Send value
        command = SetZLockWindowCommand(value=window_size)
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
            self.target.value_label.setText('Not set')
            return
        self.target.value_label.setText(f'{value} nm')

    def update_interval(self, value: float):
        if value is None:
            value = ''
        self.interval.value_label.setText(f'{value} sec')

    def update_max(self, value: float):
        if value is None:
            value = ''
        self.max.value_label.setText(f'{value} nm')

    def update_window(self, value: int):
        if value is None:
            value = ''
        self.window.value_label.setText(f'{value} frames')


class ZLUTGenerationPanel(ControlPanelBase):
    def __init__(self, manager: 'UIManager'):
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

        # Measurements per step
        self.measurements_input = LabeledLineEdit(label_text='Measurements per step:')
        self.measurements_input.lineedit.setText(str(self.manager.settings['video buffer n images']))
        self.layout().addWidget(self.measurements_input)

        # Generate button
        buttons_row = QHBoxLayout()
        self.layout().addLayout(buttons_row)

        self.generate_button = QPushButton('Generate')
        self.generate_button.clicked.connect(self.generate_callback)
        buttons_row.addWidget(self.generate_button)

    def search_targets(self) -> list[SearchTarget]:
        return [
            _panel_control_target('Z-LUT Start', 'ZLUTGenerationPanel', 'start_input', context='Z-LUT Generation', aliases=('start nm', 'z lut start')),
            _panel_control_target('Z-LUT Step', 'ZLUTGenerationPanel', 'step_input', context='Z-LUT Generation', aliases=('step nm', 'z lut step')),
            _panel_control_target('Z-LUT Stop', 'ZLUTGenerationPanel', 'stop_input', context='Z-LUT Generation', aliases=('stop nm', 'z lut stop')),
            _panel_control_target('Z-LUT Measurements per Step', 'ZLUTGenerationPanel', 'measurements_input', context='Z-LUT Generation', aliases=('measurements per step', 'captures per step')),
            _panel_control_target('Generate Z-LUT', 'ZLUTGenerationPanel', 'generate_button', context='Z-LUT Generation', aliases=('generate', 'start z lut generation')),
        ]

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

        measurements_text = self.measurements_input.lineedit.text()
        try:
            profiles_per_bead = int(measurements_text)
        except ValueError:
            return
        if profiles_per_bead <= 0:
            return

        self.manager.start_zlut_generation(
            start_nm=start_nm,
            step_nm=step_nm,
            stop_nm=stop_nm,
            profiles_per_bead=profiles_per_bead,
        )

    def update_state(
        self,
        status: str,
        detail: str | None = None,
        *,
        running: bool = False,
        can_cancel: bool = False,
        phase: str = 'idle',
    ) -> None:
        generation_blocked = running or phase in {'evaluating', 'waiting_focus_limits'}
        self.generate_button.setEnabled(not generation_blocked)

    def update_progress(
        self,
        current_step: int,
        total_steps: int,
        capture_count: int,
        capture_capacity: int,
        motor_z_value: float | None = None,
    ) -> None:
        _ = (current_step, total_steps, capture_count, capture_capacity, motor_z_value)


class ZLUTGenerationSetupDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        roi_size: int,
        default_measurements: int,
    ):
        super().__init__(parent)
        self.setWindowTitle('New Z-LUT')
        self.setModal(True)

        layout = QVBoxLayout(self)

        roi_row = QHBoxLayout()
        roi_row.addWidget(QLabel('Current ROI:'))
        roi_row.addWidget(QLabel(f'{roi_size} x {roi_size} pixels'))
        roi_row.addStretch(1)
        layout.addLayout(roi_row)

        self.start_input = LabeledLineEdit(label_text='Start (nm):')
        layout.addWidget(self.start_input)
        self.step_input = LabeledLineEdit(label_text='Step (nm):')
        layout.addWidget(self.step_input)
        self.stop_input = LabeledLineEdit(label_text='Stop (nm):')
        layout.addWidget(self.stop_input)
        self.measurements_input = LabeledLineEdit(label_text='Measurements per step:')
        self.measurements_input.lineedit.setText(str(default_measurements))
        layout.addWidget(self.measurements_input)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.clicked.connect(self.reject)  # type: ignore
        button_row.addWidget(self.cancel_button)
        self.generate_button = QPushButton('Generate')
        self.generate_button.clicked.connect(self._accept_if_valid)  # type: ignore
        button_row.addWidget(self.generate_button)
        layout.addLayout(button_row)

        self._values: tuple[float, float, float, int] | None = None

    @property
    def values(self) -> tuple[float, float, float, int] | None:
        return self._values

    def _accept_if_valid(self) -> None:
        try:
            start_nm = float(self.start_input.lineedit.text())
            step_nm = float(self.step_input.lineedit.text())
            stop_nm = float(self.stop_input.lineedit.text())
            profiles_per_bead = int(self.measurements_input.lineedit.text())
        except ValueError:
            QMessageBox.warning(self, 'Invalid Z-LUT settings', 'Enter numeric Z-LUT settings.')
            return
        if profiles_per_bead <= 0:
            QMessageBox.warning(
                self,
                'Invalid Z-LUT settings',
                'Measurements per step must be greater than zero.',
            )
            return

        self._values = (start_nm, step_nm, stop_nm, profiles_per_bead)
        self.accept()


class ZLUTSweepPreviewWidget(MatplotlibCleanupMixin, QWidget):
    _STATE_LABELS = {
        0: 'Absent',
        1: 'Creating',
        2: 'Ready',
        3: 'Capturing',
        4: 'Complete',
        5: 'Detaching',
        6: 'Failed',
        7: 'Destroyed',
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        layout = QVBoxLayout(self)

        self.summary_label = QLabel('Waiting for Z-LUT sweep data...')
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self._preview_cmap = matplotlib.colormaps['gray'].copy()

        self.figure = Figure(dpi=100, facecolor=PANEL_BACKGROUND_COLOR)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(280)
        layout.addWidget(self.canvas, 1)

        self.axes = self.figure.subplots(nrows=1, ncols=1)
        self.axes.set_facecolor(PANEL_BACKGROUND_COLOR)
        self.axes.set_xlabel('Capture Index')
        self.axes.set_ylabel('Profile Radius (px)')
        self._image = self.axes.imshow(
            np.zeros((1, 1), dtype=np.float64),
            cmap=self._preview_cmap,
            aspect='auto',
            interpolation='nearest',
            origin='lower',
        )
        self.axes.set_title('No sweep preview available')
        self.figure.tight_layout()
        self._init_matplotlib_cleanup()

    def clear(self, message: str = 'Waiting for Z-LUT sweep data...') -> None:
        self.summary_label.setText(message)
        self._image.set_data(np.zeros((1, 1), dtype=np.float64))
        self._image.set_extent((-0.5, 0.5, -0.5, 0.5))
        self._image.set_clim(0.0, 1.0)
        self.axes.set_title('No sweep preview available')
        self.axes.set_xlabel('Capture Index')
        self.axes.set_ylabel('Profile Radius (px)')
        self.axes.set_xlim(-0.5, 0.5)
        self.axes.set_ylim(-0.5, 0.5)
        self.canvas.draw()

    def update_preview(
        self,
        *,
        state: int,
        count: int,
        capacity: int,
        n_steps: int,
        n_beads: int,
        profiles_per_bead: int,
        profile_length: int,
        preview_image: np.ndarray | None,
        selected_bead_id: int | None,
        mode: str,
        motor_z_min: float | None,
        motor_z_max: float | None,
        expected_capture_count: int | None = None,
        x_axis_label: str = 'Z Position (nm)',
        x_axis_min: float | None = None,
        x_axis_max: float | None = None,
        image_x_min: float | None = None,
        image_x_max: float | None = None,
    ) -> None:
        state_text = self._STATE_LABELS.get(int(state), str(state))
        summary_parts = [
            f'State: {state_text}',
            f'Captures: {count} / {capacity}',
            f'Steps: {n_steps}',
            f'Beads: {n_beads}',
            f'Profiles/bead: {profiles_per_bead}',
            f'Profile length: {profile_length}',
        ]
        if selected_bead_id is not None:
            summary_parts.append(f'Preview bead: {selected_bead_id}')
        if motor_z_min is not None and motor_z_max is not None:
            summary_parts.append(f'Observed Z: {motor_z_min:.1f} to {motor_z_max:.1f} nm')
        self.summary_label.setText(' | '.join(summary_parts))

        if preview_image is None or preview_image.size == 0:
            self._image.set_data(np.zeros((1, 1), dtype=np.float64))
            self._image.set_extent((-0.5, 0.5, -0.5, 0.5))
            self._image.set_clim(0.0, 1.0)
            self.axes.set_title('No sweep preview available')
            self.axes.set_xlabel(x_axis_label)
            self.axes.set_ylabel('Profile Radius (px)')
            self.axes.set_xlim(-0.5, 0.5)
            self.axes.set_ylim(-0.5, 0.5)
            self.canvas.draw()
            return

        finite = np.asarray(preview_image, dtype=np.float64)
        finite_mask = np.isfinite(finite)
        if not np.any(finite_mask):
            self._image.set_data(np.zeros((1, 1), dtype=np.float64))
            self._image.set_extent((-0.5, 0.5, -0.5, 0.5))
            self._image.set_clim(0.0, 1.0)
            self.axes.set_title('No finite sweep data available')
            self.axes.set_xlabel(x_axis_label)
            self.axes.set_ylabel('Profile Radius (px)')
            self.axes.set_xlim(-0.5, 0.5)
            self.axes.set_ylim(-0.5, 0.5)
            self.canvas.draw()
            return

        finite_values = finite[finite_mask]
        vmin = float(np.min(finite_values))
        vmax = float(np.max(finite_values))
        if np.isclose(vmin, vmax):
            vmax = vmin + 1.0

        extent_x_min = -0.5
        extent_x_max = finite.shape[1] - 0.5
        if image_x_min is not None and image_x_max is not None:
            extent_x_min = float(image_x_min)
            extent_x_max = float(image_x_max)
        self._image.set_data(np.ma.masked_invalid(finite))
        self._image.set_extent((extent_x_min, extent_x_max, -0.5, finite.shape[0] - 0.5))
        self._image.set_clim(vmin, vmax)
        self.axes.set_title(f'{mode} preview')
        self.axes.set_xlabel(x_axis_label)
        self.axes.set_ylabel('Profile Radius (px)')
        if x_axis_min is not None and x_axis_max is not None:
            self.axes.set_xlim(float(x_axis_min), float(x_axis_max))
        elif mode == 'Raw sweep' and expected_capture_count is not None and expected_capture_count > 0:
            self.axes.set_xlim(-0.5, expected_capture_count - 0.5)
        else:
            self.axes.set_xlim(-0.5, finite.shape[1] - 0.5)
        self.axes.set_ylim(-0.5, finite.shape[0] - 0.5)
        self.canvas.draw()


class ZLUTGenerationDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle('Z-LUT Generation')
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(900, 700)

        self._running = False
        self._evaluation_active = False
        self._startup_pending = False
        self._close_when_canceled = False
        self._selected_bead_id: int | None = None

        layout = QVBoxLayout(self)

        self.status_label = QLabel('Preparing Z-LUT generation...')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.detail_label = QLabel('')
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        self.progress_label = QLabel('0 / 0 steps')
        layout.addWidget(self.progress_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.preview_widget = ZLUTSweepPreviewWidget(self)
        layout.addWidget(self.preview_widget, 1)

        evaluation_row = QHBoxLayout()
        evaluation_row.addWidget(QLabel('Bead:'))
        self.bead_selector = QComboBox()
        self.bead_selector.currentIndexChanged.connect(self._handle_bead_selection_changed)
        self.bead_selector.setEnabled(False)
        evaluation_row.addWidget(self.bead_selector)
        evaluation_row.addStretch(1)
        layout.addLayout(evaluation_row)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.clicked.connect(self._handle_cancel_clicked)
        button_row.addWidget(self.cancel_button)
        self.save_button = QPushButton('Save')
        self.save_button.clicked.connect(self._handle_save_clicked)
        self.save_button.setEnabled(False)
        button_row.addWidget(self.save_button)
        self.save_and_load_button = QPushButton('Save and Load')
        self.save_and_load_button.clicked.connect(self._handle_save_and_load_clicked)
        self.save_and_load_button.setEnabled(False)
        button_row.addWidget(self.save_and_load_button)
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self._handle_close_clicked)
        self.close_button.setEnabled(False)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self._cancel_callback = None
        self._close_callback = None
        self._save_callback = None
        self._save_and_load_callback = None
        self._select_bead_callback = None

    def set_cancel_callback(self, callback) -> None:
        self._cancel_callback = callback

    def set_save_callback(self, callback) -> None:
        self._save_callback = callback

    def set_save_and_load_callback(self, callback) -> None:
        self._save_and_load_callback = callback

    def set_close_callback(self, callback) -> None:
        self._close_callback = callback

    def set_select_bead_callback(self, callback) -> None:
        self._select_bead_callback = callback

    def _handle_cancel_clicked(self) -> None:
        if self._cancel_callback is not None:
            self._close_when_canceled = True
            self.cancel_button.setEnabled(False)
            self._cancel_callback()

    def _handle_save_clicked(self) -> None:
        if self._save_callback is not None and self._selected_bead_id is not None:
            self._save_callback(self._selected_bead_id)

    def _handle_save_and_load_clicked(self) -> None:
        if self._save_and_load_callback is not None and self._selected_bead_id is not None:
            self._save_and_load_callback(self._selected_bead_id)

    def _handle_close_clicked(self) -> None:
        if self._running or self._startup_pending:
            return
        if self._evaluation_active and self._close_callback is not None:
            self._close_callback()
            self._evaluation_active = False
        self.close()

    def mark_starting(self) -> None:
        self._running = True
        self._startup_pending = True
        self._evaluation_active = False
        self.status_label.setText('Preparing Z-LUT generation...')
        self.detail_label.setText('Submitting the sweep request and waiting for the first status update.')
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText('Cancel')
        self.close_button.setEnabled(False)
        self.close_button.setText('Close')

    def _handle_bead_selection_changed(self, index: int) -> None:
        if index < 0:
            return
        bead_id = self.bead_selector.itemData(index)
        if bead_id is None:
            return
        self._selected_bead_id = int(bead_id)
        if self._select_bead_callback is not None:
            self._select_bead_callback(self._selected_bead_id)

    def update_state(
        self,
        status: str,
        detail: str | None = None,
        *,
        running: bool = False,
        can_cancel: bool = False,
        phase: str = 'idle',
    ) -> None:
        self._startup_pending = False
        self._running = running
        self._evaluation_active = phase == 'evaluating'
        self.status_label.setText(status)
        self.detail_label.setText(detail or '')
        self.cancel_button.setVisible(running or can_cancel)
        self.cancel_button.setEnabled(can_cancel)
        self.cancel_button.setText('Cancel')
        save_enabled = self._evaluation_active and self._selected_bead_id is not None
        self.save_button.setEnabled(save_enabled)
        self.save_and_load_button.setEnabled(save_enabled)
        self.bead_selector.setEnabled(self.bead_selector.count() > 0)
        self.close_button.setEnabled(not running)
        self.close_button.setText('Cancel' if self._evaluation_active else 'Close')
        if self._close_when_canceled and not running and phase == 'idle':
            self._close_when_canceled = False
            self.close()

    def update_progress(
        self,
        current_step: int,
        total_steps: int,
        capture_count: int,
        capture_capacity: int,
        motor_z_value: float | None = None,
    ) -> None:
        progress_total = max(total_steps, 1)
        progress_value = min(max(current_step, 0), progress_total)
        self.progress_bar.setRange(0, progress_total)
        self.progress_bar.setValue(progress_value)

        progress_text = f'{current_step} / {total_steps} steps'
        if capture_capacity > 0:
            progress_text += f' | {capture_count} / {capture_capacity} captures'
        if motor_z_value is not None:
            progress_text += f' | Z = {motor_z_value:.1f} nm'
        self.progress_label.setText(progress_text)

    def update_evaluation(self, *, active: bool, bead_ids: list[int], selected_bead_id: int | None) -> None:
        self._evaluation_active = active
        self.bead_selector.blockSignals(True)
        self.bead_selector.clear()
        for bead_id in bead_ids:
            self.bead_selector.addItem(str(bead_id), bead_id)
        if selected_bead_id is not None:
            index = self.bead_selector.findData(selected_bead_id)
            if index >= 0:
                self.bead_selector.setCurrentIndex(index)
                self._selected_bead_id = int(selected_bead_id)
            else:
                self._selected_bead_id = None
        else:
            self._selected_bead_id = None
        self.bead_selector.blockSignals(False)
        self.bead_selector.setEnabled(self.bead_selector.count() > 0)
        save_enabled = active and self._selected_bead_id is not None
        self.save_button.setEnabled(save_enabled)
        self.save_and_load_button.setEnabled(save_enabled)
        self.cancel_button.setVisible(self._running)
        self.cancel_button.setEnabled(self._running)
        self.cancel_button.setText('Cancel')
        self.close_button.setText('Cancel' if active else 'Close')

    def force_close(self) -> None:
        self._running = False
        self._startup_pending = False
        self._close_when_canceled = False
        self._evaluation_active = False
        self.close()

    def closeEvent(self, event) -> None:
        if self._running or self._startup_pending:
            event.ignore()
            return
        if self._evaluation_active and self._close_callback is not None:
            self._close_callback()
            self._evaluation_active = False
        super().closeEvent(event)


class CurrentZLUTDialog(MatplotlibCleanupMixin, QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle('Current Z-LUT')
        self.resize(720, 560)
        layout = QVBoxLayout(self)

        self.preview_status_label = QLabel('No Z-LUT loaded')
        self.preview_status_label.setWordWrap(True)
        layout.addWidget(self.preview_status_label)

        self.figure = Figure(dpi=100, facecolor=PANEL_BACKGROUND_COLOR)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(280)
        layout.addWidget(self.canvas, 1)
        self.axes = self.figure.subplots(nrows=1, ncols=1)
        self.axes.set_facecolor(PANEL_BACKGROUND_COLOR)
        self._image = self.axes.imshow(
            np.zeros((1, 1), dtype=np.float64),
            cmap=matplotlib.colormaps['gray'].copy(),
            aspect='auto',
            interpolation='nearest',
            origin='lower',
        )
        self._clear_preview('No Z-LUT loaded')
        self._init_matplotlib_cleanup()

        metadata_layout = QVBoxLayout()
        layout.addLayout(metadata_layout)
        self.min_value = self._add_metadata_row(metadata_layout, 'Min (nm):')
        self.max_value = self._add_metadata_row(metadata_layout, 'Max (nm):')
        self.step_value = self._add_metadata_row(metadata_layout, 'Step (nm):')
        self.profile_length_value = self._add_metadata_row(metadata_layout, 'Profile Length:')

        self.filepath_label = QLabel('File: No Z-LUT loaded')
        self.filepath_label.setWordWrap(True)
        self.filepath_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.filepath_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_button = QPushButton('Close')
        close_button.clicked.connect(self.close)  # type: ignore
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def _add_metadata_row(self, layout: QVBoxLayout, label_text: str) -> QLabel:
        row = QHBoxLayout()
        label = QLabel(label_text)
        value = QLabel('')
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(label)
        row.addStretch(1)
        row.addWidget(value, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(row)
        return value

    def update_zlut(
        self,
        filepath: str | None,
        *,
        z_min: float | None = None,
        z_max: float | None = None,
        step_size: float | None = None,
        profile_length: int | None = None,
    ) -> None:
        self.filepath_label.setText(f'File: {filepath}' if filepath else 'File: No Z-LUT loaded')
        self.min_value.setText(self._format_number(z_min, suffix=' nm'))
        self.max_value.setText(self._format_number(z_max, suffix=' nm'))
        self.step_value.setText(self._format_number(step_size, suffix=' nm'))
        self.profile_length_value.setText('' if profile_length is None else f'{profile_length}')
        self._update_preview(filepath)

    def _clear_preview(self, message: str) -> None:
        self.preview_status_label.setText(message)
        self._image.set_data(np.zeros((1, 1), dtype=np.float64))
        self._image.set_extent((-0.5, 0.5, -0.5, 0.5))
        self._image.set_clim(0.0, 1.0)
        self.axes.set_title('No Z-LUT preview available')
        self.axes.set_xlabel('Z Position (nm)')
        self.axes.set_ylabel('Profile Radius (px)')
        self.axes.set_xlim(-0.5, 0.5)
        self.axes.set_ylim(-0.5, 0.5)
        self.canvas.draw()

    def _update_preview(self, filepath: str | None) -> None:
        if not filepath:
            self._clear_preview('No Z-LUT loaded')
            return
        try:
            zlut_array = np.loadtxt(filepath)
            if zlut_array.ndim != 2 or zlut_array.shape[0] < 2 or zlut_array.shape[1] < 2:
                raise ValueError('Z-LUT must be a 2D array with z references and profile rows.')
        except Exception as exc:
            reason = str(exc).strip() or repr(exc)
            self._clear_preview(f'Could not load Z-LUT preview: {reason}')
            return

        z_references = np.asarray(zlut_array[0, :], dtype=np.float64)
        profiles = np.asarray(zlut_array[1:, :], dtype=np.float64)
        finite_z_references = z_references[np.isfinite(z_references)]
        if finite_z_references.size == 0:
            self._clear_preview('No finite Z-LUT reference positions available')
            return
        finite_mask = np.isfinite(profiles)
        if not np.any(finite_mask):
            self._clear_preview('No finite Z-LUT profile values available')
            return

        finite_values = profiles[finite_mask]
        vmin = float(np.min(finite_values))
        vmax = float(np.max(finite_values))
        if np.isclose(vmin, vmax):
            vmax = vmin + 1.0

        x_min = float(np.min(finite_z_references))
        x_max = float(np.max(finite_z_references))
        if np.isclose(x_min, x_max):
            x_min -= 0.5
            x_max += 0.5
        self.preview_status_label.setText('')
        self._image.set_data(np.ma.masked_invalid(profiles))
        self._image.set_extent((x_min, x_max, -0.5, profiles.shape[0] - 0.5))
        self._image.set_clim(vmin, vmax)
        self.axes.set_title('Current Z-LUT')
        self.axes.set_xlabel('Z Position (nm)')
        self.axes.set_ylabel('Profile Radius (px)')
        self.axes.set_xlim(x_min, x_max)
        self.axes.set_ylim(-0.5, profiles.shape[0] - 0.5)
        self.canvas.draw()

    @staticmethod
    def _format_number(value: float | int | None, suffix: str = '') -> str:
        if value is None:
            return ''
        if isinstance(value, float):
            return f'{int(value):d}{suffix}'
        return f'{value}{suffix}'


class ZLUTPanel(ControlPanelBase):
    zlut_file_selected = pyqtSignal(str)
    zlut_clear_requested = pyqtSignal()

    NO_ZLUT_SELECTED_TEXT = 'No Z-LUT file selected'

    def __init__(self, manager: 'UIManager'):
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

    def search_targets(self) -> list[SearchTarget]:
        return [
            _panel_control_target('Select Z-LUT File', 'ZLUTPanel', 'select_button', context='Z-LUT', aliases=('load z lut', 'choose z-lut file')),
            _panel_control_target('Clear Z-LUT', 'ZLUTPanel', 'clear_button', context='Z-LUT', aliases=('remove z lut', 'reset z lut')),
        ]

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
