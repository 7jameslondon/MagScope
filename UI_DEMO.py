from __future__ import annotations

from collections import OrderedDict
import math
import sys
from pathlib import Path
from time import time
from types import SimpleNamespace

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from magscope.settings import MagScopeSettings
from magscope.scripting import ScriptStatus
from magscope.ui import controls as controls_module
from magscope.ui import panel_layout as panel_layout_module
from magscope.ui import ui as ui_module
from magscope.ui import video_viewer as video_viewer_module
from magscope.ui import widgets as widgets_module
from magscope.ui.plots import PlotWorker


class DemoStateStore:
    def value(self, _key: str, default=None, type=None):  # noqa: A002
        if type is None or default is None:
            return default
        if type is bool:
            return bool(default)
        if type is list:
            return list(default) if isinstance(default, (list, tuple)) else default
        try:
            return type(default)
        except (TypeError, ValueError):
            return default

    def setValue(self, _key: str, _value) -> None:  # noqa: N802
        return

    def beginGroup(self, _group: str) -> None:  # noqa: N802
        return

    def endGroup(self) -> None:  # noqa: N802
        return

    def remove(self, _key: str) -> None:
        return

    def childKeys(self) -> list[str]:  # noqa: N802
        return []


def _install_demo_no_persistence_hooks() -> None:
    controls_module.QSettings = lambda *args, **kwargs: DemoStateStore()
    ui_module.QSettings = lambda *args, **kwargs: None

    def collapsible_init(self, title="", collapsed=False):
        widgets_module.QGroupBox.__init__(self)

        self.title = title
        self.default_collapsed = collapsed
        self._settings_key = f"{self.title}_Group Box Collapsed"

        self.toggle_button = QPushButton(self._get_toggle_text(title, not collapsed))
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(not collapsed)
        self.toggle_button.setStyleSheet(
            """
                text-align: left;
                padding: 0px;
                border: none;
                font-weight: bold;
                font-size: 14px;
            """
        )
        self.toggle_button.toggled.connect(self.toggle)  # type: ignore[arg-type]

        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 2)
        title_widget = QWidget()
        title_layout = QHBoxLayout(title_widget)
        title_layout.setContentsMargins(4, 4, 4, 4)
        title_layout.setSpacing(6)
        self.toggle_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title_layout.addWidget(self.toggle_button)

        self.drag_handle = QLabel("᎒᎒᎒")
        self.drag_handle.setObjectName("PanelDragHandle")
        self.drag_handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drag_handle.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_handle.setToolTip("Drag to reposition panel")
        self.drag_handle.setFixedWidth(20)
        self.drag_handle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.drag_handle.setStyleSheet("font-size: 16px;")
        title_layout.addWidget(self.drag_handle)
        self.setTitle("")
        self.layout().addWidget(title_widget)
        self.layout().setSpacing(0)

        self.content_area = QWidget()
        self.content_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.layout().addWidget(self.content_area)

        self.animation = widgets_module.QPropertyAnimation(self.content_area, b"maximumHeight")
        self.animation.setDuration(300)
        self.animation.setEasingCurve(widgets_module.QEasingCurve.Type.InOutQuad)
        self.animation.finished.connect(self._animation_finished)

        self.collapsed = collapsed
        if collapsed:
            self.content_area.setMaximumHeight(0)
        else:
            self.content_area.setMaximumHeight(16777215)

    def collapsible_apply(self, collapsed: bool, *, animate: bool, persist: bool) -> None:
        _ = persist
        expanded = not collapsed
        self.collapsed = collapsed
        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.blockSignals(False)
        self.toggle_button.setText(self._get_toggle_text(self.title, expanded))

        if animate:
            if expanded:
                self.animation.setStartValue(0)
                self.animation.setEndValue(self.content_area.sizeHint().height())
            else:
                self.animation.setStartValue(self.content_area.height())
                self.animation.setEndValue(0)
            self.animation.start()
        else:
            self.animation.stop()
            self.content_area.setMaximumHeight(0 if collapsed else 16777215)

    def panel_layout_load(self):
        return OrderedDict()

    def panel_layout_store(self):
        return

    def panel_layout_remove_column(self, name: str) -> None:
        column = self.columns.get(name)
        if column is None:
            return
        if column.panels():
            raise ValueError(f"Column '{name}' is not empty")
        column.set_manager(None)
        del self.columns[name]

    def controls_reset(self) -> None:
        for panel in self.panels.values():
            groupbox = getattr(panel, "groupbox", None)
            if isinstance(groupbox, widgets_module.CollapsibleGroupBox):
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
            self.layout_manager.columns[column_name].add_panel(wrapper)

        self.layout_manager.layout_changed()

    widgets_module.CollapsibleGroupBox.__init__ = collapsible_init
    widgets_module.CollapsibleGroupBox._apply_collapsed_state = collapsible_apply
    panel_layout_module.PanelLayoutManager._load_layout = panel_layout_load
    panel_layout_module.PanelLayoutManager.stored_layout = panel_layout_load
    panel_layout_module.PanelLayoutManager.save_layout = panel_layout_store
    panel_layout_module.PanelLayoutManager.remove_column = panel_layout_remove_column
    ui_module.Controls.reset_to_defaults = controls_reset


_install_demo_no_persistence_hooks()


class DemoPlotsLabel(widgets_module.ResizableLabel):
    def __init__(self) -> None:
        super().__init__()
        self.setScaledContents(True)
        self.resized.connect(self._render_plot_pixmap)
        self._render_plot_pixmap(800, 320)

    def _render_plot_pixmap(self, width: int, height: int) -> None:
        width = max(300, width)
        height = max(180, height)
        figure = Figure(
            figsize=(width / 100, height / 100),
            dpi=100,
            facecolor="#1e1e1e",
        )
        canvas = FigureCanvasAgg(figure)
        axes = figure.subplots(nrows=3, ncols=1, sharex=True, sharey=False)
        figure.tight_layout()
        figure.subplots_adjust(hspace=0.08)

        x = np.linspace(0, 300, 400)
        traces = [
            90 * np.sin(x / 70.0) + 10 * np.cos(x / 17.0),
            55 * np.cos(x / 50.0 + 0.7),
            110 * np.sin(x / 90.0 + 1.2) - 20 * np.cos(x / 22.0),
        ]
        labels = ["X (nm)", "Y (nm)", "Z (nm)"]

        for axis, values, ylabel in zip(axes, traces, labels, strict=False):
            axis.set_facecolor("#1e1e1e")
            axis.margins(x=0)
            axis.plot(x, values, "r")
            axis.set_ylabel(ylabel)

        axes[-1].set_xlabel("Time (h:m:s)")

        canvas.draw()
        buffer = np.asarray(canvas.buffer_rgba(), dtype=np.uint8)
        image = QImage(buffer.data, width, height, QImage.Format.Format_RGBA8888)
        self.setPixmap(QPixmap.fromImage(image.copy()))


class DemoGripSplitter(widgets_module.GripSplitter):
    """Grip splitter variant with persistence disabled for the demo."""

    def __init__(self, orientation, name=None, parent=None):
        super().__init__(orientation=orientation, name=name, parent=parent)
        self.setting_name = None

    def showEvent(self, event):
        return super(widgets_module.GripSplitter, self).showEvent(event)

    def handle_released(self):
        return


def make_demo_video_pixmap(width: int = 640, height: int = 512) -> QPixmap:
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    background = 35.0 + 30.0 * np.sin(xx * math.pi * 2.2) + 18.0 * np.cos(yy * math.pi * 3.4)

    for center_x, center_y, radius, amplitude in (
        (0.22, 0.28, 0.05, 120.0),
        (0.48, 0.62, 0.07, 110.0),
        (0.74, 0.38, 0.06, 135.0),
    ):
        dist_sq = (xx - center_x) ** 2 + (yy - center_y) ** 2
        background += amplitude * np.exp(-dist_sq / (2 * radius * radius))

    image_array = np.clip(background, 0, 255).astype(np.uint8)
    image = QImage(image_array.data, width, height, width, QImage.Format.Format_Grayscale8)
    return QPixmap.fromImage(image.copy())


class DemoManager:
    def __init__(self) -> None:
        self._acquisition_dir_on = True
        self._acquisition_mode = "FULL_VIDEO"
        self._acquisition_on = True
        self._bead_next_id = 3
        self.beads_in_view_count = 5
        self.beads_in_view_marker_size = 20
        self.beads_in_view_on = False
        self.controls = None
        self.controls_to_add: list[tuple[object, object]] = []
        self.camera_type = SimpleNamespace(
            settings=["Exposure", "Gain", "Frame Rate"],
            dtype=np.uint16,
            bits=12,
            nm_per_px=100,
        )
        self.live_profile_buffer = None
        self.live_profile_enabled = False
        self.plot_worker = PlotWorker()
        self.reference_bead = 1
        self.selected_bead = 0
        self.settings = MagScopeSettings(
            {
                "ROI": 80,
                "video buffer n images": 20,
                "xy-lock default interval": 0.5,
                "xy-lock default max": 5,
                "xy-lock default window": 10,
                "z-lock default interval": 0.5,
                "z-lock default max": 200,
            }
        )
        self.shared_values = SimpleNamespace(live_profile_enabled=SimpleNamespace(value=False))
        self.video_buffer = SimpleNamespace(buffer_size=512_000_000, image_shape=(512, 640), dtype=np.uint16)
        self._ipc_commands: list[object] = []

    @property
    def bead_next_id(self) -> int:
        return self._bead_next_id

    def send_ipc(self, command) -> None:
        self._ipc_commands.append(command)

    def reset_bead_ids(self) -> None:
        self._bead_next_id = 0
        if self.controls is not None:
            self.controls.bead_selection_panel.update_next_bead_id_label(self._bead_next_id)

    def clear_beads(self) -> None:
        self._bead_next_id = 0
        if self.controls is not None:
            self.controls.bead_selection_panel.update_next_bead_id_label(self._bead_next_id)

    def start_auto_bead_selection(self) -> None:
        return

    def _update_auto_bead_selection_button_state(self) -> None:
        if self.controls is not None:
            self.controls.bead_selection_panel.auto_select_button.setEnabled(True)

    def request_zlut_file(self, _path: str) -> None:
        return

    def clear_zlut(self) -> None:
        return

    def start_zlut_generation(
        self,
        *,
        start_nm: float,
        step_nm: float,
        stop_nm: float,
        profiles_per_bead: int,
    ) -> None:
        _ = (start_nm, step_nm, stop_nm, profiles_per_bead)

    def set_selected_bead(self, bead: int) -> None:
        self.selected_bead = bead
        self.plot_worker.selected_bead_signal.emit(bead)

    def set_reference_bead(self, bead: int | None) -> None:
        self.reference_bead = bead
        self.plot_worker.reference_bead_signal.emit(-1 if bead is None else bead)

    def set_live_profile_monitor_enabled(self, enabled: bool) -> None:
        self.live_profile_enabled = enabled
        self.shared_values.live_profile_enabled.value = enabled


class DemoWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.manager = DemoManager()
        self.controls = ui_module.Controls(self.manager)
        self.manager.controls = self.controls
        self.controls.reset_to_defaults()

        self.plots_widget = DemoPlotsLabel()
        self.video_viewer = video_viewer_module.VideoViewer()

        self._populate_demo_state()
        self._build_layout()

        self.setWindowTitle("MagScope")
        self.setMinimumWidth(300)
        self.setMinimumHeight(300)

    def _build_layout(self) -> None:
        central_widget = QWidget()
        central_layout = QVBoxLayout()
        central_widget.setLayout(central_layout)

        lr_splitter = DemoGripSplitter(
            name="One Window Left-Right Splitter",
            orientation=Qt.Orientation.Horizontal,
        )
        central_layout.addWidget(lr_splitter)

        left_widget = QWidget()
        left_widget.setMinimumWidth(150)
        left_layout = QHBoxLayout()
        left_widget.setLayout(left_layout)
        left_layout.addWidget(self.controls)
        lr_splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_widget.setMinimumWidth(150)
        right_layout = QHBoxLayout()
        right_widget.setLayout(right_layout)
        lr_splitter.addWidget(right_widget)

        ud_splitter = DemoGripSplitter(
            name="One Window Top-Bottom Splitter",
            orientation=Qt.Orientation.Vertical,
        )
        right_layout.addWidget(ud_splitter)

        right_top_widget = QWidget()
        right_top_widget.setMinimumHeight(150)
        right_top_layout = QHBoxLayout()
        right_top_widget.setLayout(right_top_layout)
        right_top_layout.addWidget(self.plots_widget)
        ud_splitter.addWidget(right_top_widget)

        right_bottom_widget = QWidget()
        right_bottom_widget.setMinimumHeight(150)
        right_bottom_layout = QHBoxLayout()
        right_bottom_widget.setLayout(right_bottom_layout)
        right_bottom_layout.addWidget(self.video_viewer)
        ud_splitter.addWidget(right_bottom_widget)

        self.setCentralWidget(central_widget)
        lr_splitter.setSizes([620, 980])
        ud_splitter.setSizes([340, 620])

    def _populate_demo_state(self) -> None:
        self.controls.status_panel.update_display_rate("18 updates/sec")
        self.controls.status_panel.update_video_processors_status("3/4 busy")
        self.controls.status_panel.update_video_buffer_status("25% full, 20 max images")
        self.controls.status_panel.update_video_buffer_purge(time())

        self.controls.bead_selection_panel.update_next_bead_id_label(self.manager.bead_next_id)
        self.controls.camera_panel.update_camera_setting("Exposure", "5000")
        self.controls.camera_panel.update_camera_setting("Gain", "1.2")
        self.controls.camera_panel.update_camera_setting("Frame Rate", "20")

        self.controls.acquisition_panel.acquisition_dir_textedit.setText(r"C:\Data\Experiments\demo-run")
        self.controls.plot_settings_panel.selected_bead.lineedit.setText("0")
        self.controls.plot_settings_panel.reference_bead.lineedit.setText("1")
        self.controls.plot_settings_panel.time_mode.setCurrentText("Absolute")
        self.controls.plot_settings_panel.time_limits_absolute[0].setText("00:00:00")
        self.controls.plot_settings_panel.time_limits_absolute[1].setText("00:05:00")

        self.controls.zlut_panel.set_filepath(r"C:\Data\zlut\example_zlut.txt")
        self.controls.zlut_panel.update_metadata(
            z_min=-1000,
            z_max=1000,
            step_size=100,
            profile_length=64,
        )

        self.controls.script_panel.update_status(ScriptStatus.LOADED)
        self.controls.script_panel.update_step(2, 5, "Move to target and capture sweep")
        self.controls.script_panel.filepath_textedit.setText(r"C:\Scripts\example_script.py")

        self.controls.xy_lock_panel.update_enabled(True)
        self.controls.xy_lock_panel.update_interval(0.5)
        self.controls.xy_lock_panel.update_max(5)
        self.controls.xy_lock_panel.update_window(10)

        self.controls.z_lock_panel.update_enabled(False)
        self.controls.z_lock_panel.update_bead(0)
        self.controls.z_lock_panel.update_target(0.0)
        self.controls.z_lock_panel.update_interval(0.5)
        self.controls.z_lock_panel.update_max(200)

        self.controls.z_lut_generation_panel.start_input.lineedit.setText("-1000")
        self.controls.z_lut_generation_panel.step_input.lineedit.setText("100")
        self.controls.z_lut_generation_panel.stop_input.lineedit.setText("1000")

        self.video_viewer.set_pixmap(make_demo_video_pixmap())
        self.video_viewer.set_bead_overlay(
            {
                0: (90, 170, 90, 170),
                1: (260, 340, 280, 360),
                2: (430, 510, 150, 230),
            },
            active_bead_id=1,
            selected_bead_id=0,
            reference_bead_id=2,
        )
        self.video_viewer.plot(
            np.asarray([130, 300, 470]),
            np.asarray([130, 320, 190]),
            20,
        )
        self.video_viewer.coordinatesChanged.emit(QPoint(312, 244))


def main() -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    try:
        QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except AttributeError:
        pass

    window = DemoWindow()
    window.showMaximized()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
