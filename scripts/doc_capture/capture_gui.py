"""Render deterministic MagScope GUI screenshots for documentation assets."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import io
from multiprocessing import Lock
from multiprocessing.shared_memory import SharedMemory
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from typing import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
VISIBLE_CAPTURE_REQUESTED = os.environ.get("MAGSCOPE_DOC_CAPTURE_VISIBLE") == "1" or "--visible" in sys.argv

if not VISIBLE_CAPTURE_REQUESTED:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CUPY_CACHE_DIR", str(REPO_ROOT / ".codex-tmp" / "cupy_cache"))

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRect, QSettings, Qt
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPalette,
    QPixmap,
)
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from examples.focus.simulated_focus_motor import (
    FOCUS_MOTOR_BUFFER_NAME,
    FocusMotorControls,
    FocusMotorPlot,
    SimulatedFocusMotor,
)
from magscope.app_icon import load_app_icon
from magscope.auto_bead_selection import AutoBeadCandidate
from magscope.camera import DummyCameraBeads
from magscope.datatypes import MatrixBuffer
from magscope.settings import GUI_ACCENT_COLOR_SETTING, MagScopeSettings
from magscope.ui.auto_bead_selection_dialog import AutoBeadSelectionDialog
from magscope.ui.controls import ZLUTGenerationDialog, ZLUTGenerationSetupDialog
from magscope.ui.plots import PlotWorker, TracksTimeSeriesPlot
from magscope.ui.theme import APP_BACKGROUND_COLOR, PANEL_BACKGROUND_COLOR
from magscope.ui.ui import Controls, UIManager
from magscope.ui.video_viewer import VideoViewer
from magscope.ui.widgets import ResizableLabel
from scripts.doc_capture.assemble_gif import (
    CursorOverlayFrame,
    CursorOverlayOptions,
    DEFAULT_DURATION_MS,
    GifAssemblyError,
    GifAssemblyOptions,
    assemble_gif_from_frames,
    write_cursor_overlay_frames,
)


DEFAULT_OUTPUT_DIR = Path("assets/doc_capture")
SCREENSHOT_DIR = "screenshots"
GIF_DIR = "gifs"
FRAME_DIR = "gif_frames"
WINDOW_SIZE = (1400, 900)
DOCUMENTATION_FONT_FAMILY = "Segoe UI"
DOCUMENTATION_FONT_SIZE = 9
CURSOR_WORKFLOW_DURATION_MS = 100
MATERIAL_ICON_TEXTS = {
    "close",
    "construction",
    "crop_square",
    "dashboard",
    "filter_none",
    "help",
    "minimize",
    "open_in_new",
    "push_pin",
    "search",
    "settings",
}


@dataclass(frozen=True)
class CaptureResult:
    label: str
    path: Path


class _FrameSink:
    def __init__(self) -> None:
        self.image_bytes: bytes | None = None
        self.timestamp: float | None = None

    def write_image_and_timestamp(self, image_bytes: bytes, timestamp: float) -> None:
        self.image_bytes = bytes(image_bytes)
        self.timestamp = float(timestamp)


class _PreviewVideoBuffer:
    def __init__(self, frame: np.ndarray) -> None:
        self._frame = np.asarray(frame)
        self._index = 1
        self.buffer_size = self._frame.nbytes * 40
        self.dtype = self._frame.dtype.type
        self.image_shape = self._frame.shape
        self.n_total_images = 200

    def peak_image(self) -> tuple[int, bytes]:
        return self._index, self._frame.tobytes()

    def get_level(self) -> float:
        return 0.12


class _StaticTracksBuffer:
    def __init__(self, tracks: np.ndarray) -> None:
        self._tracks = np.asarray(tracks, dtype=np.float64)

    def peak_unsorted(self) -> np.ndarray:
        return self._tracks

    def set_tracks(self, tracks: np.ndarray) -> None:
        self._tracks = np.asarray(tracks, dtype=np.float64)


@dataclass
class _DocumentationFocusPlotParent:
    locks: dict[str, object]
    limits: dict[str, tuple[float, float]]
    time_mode: str = "absolute"
    relative_window_seconds: float | None = None


def _make_demo_focus_motor_rows(n_points: int = 140) -> np.ndarray:
    timestamps = 1_700_000_000.0 + np.linspace(0.0, 42.0, n_points)
    target = np.zeros(n_points, dtype=np.float64)
    target[n_points // 4:] = 500.0
    target[n_points // 2:] = -250.0
    target[(3 * n_points) // 4:] = 350.0

    position = np.zeros(n_points, dtype=np.float64)
    for index in range(1, n_points):
        position[index] = position[index - 1] + (target[index] - position[index - 1]) * 0.085

    is_at_target = np.isclose(position, target, atol=12.0).astype(np.float64)
    return np.column_stack((timestamps, position, target, is_at_target)).astype(np.float64)


def _close_matrix_buffer(buffer: MatrixBuffer | None) -> None:
    if buffer is None:
        return
    for attr in ("_shm", "_idx_shm", "_shm_info"):
        shared_memory = getattr(buffer, attr, None)
        if shared_memory is not None:
            try:
                shared_memory.close()
            except FileNotFoundError:
                pass


def _unlink_shared_memory_segments(name: str) -> None:
    for segment_name in (name, f"{name} Index", f"{name} Info"):
        try:
            segment = SharedMemory(name=segment_name)
        except FileNotFoundError:
            continue
        try:
            segment.unlink()
        finally:
            segment.close()


@contextmanager
def _simulated_focus_motor_buffer() -> Iterator[tuple[MatrixBuffer, dict[str, object]]]:
    locks = {FOCUS_MOTOR_BUFFER_NAME: Lock()}
    buffer = MatrixBuffer(
        create=True,
        locks=locks,
        name=FOCUS_MOTOR_BUFFER_NAME,
        shape=(180, 4),
    )
    buffer.write(_make_demo_focus_motor_rows())
    try:
        yield buffer, locks
    finally:
        _close_matrix_buffer(buffer)
        _unlink_shared_memory_segments(FOCUS_MOTOR_BUFFER_NAME)


class _DocumentationTracksPlot(TracksTimeSeriesPlot):
    def __init__(self, axis_name: str, buffer: _StaticTracksBuffer) -> None:
        super().__init__(axis_name)
        self._documentation_buffer = buffer

    def setup(self) -> None:
        self.buffer = self._documentation_buffer
        self.axes.set_ylabel(self.ylabel)
        self.line, = self.axes.plot([], [], 'r')


def _repo_root() -> Path:
    return REPO_ROOT


def _resolve_output_dir(raw_output: str) -> Path:
    output_dir = Path(raw_output)
    if not output_dir.is_absolute():
        output_dir = _repo_root() / output_dir
    return output_dir


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture deterministic MagScope GUI assets for documentation.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Output directory for generated assets. Defaults to assets/doc_capture.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the capture scene and list outputs without writing files.",
    )
    parser.add_argument(
        "--settings-dir",
        default=None,
        help="Optional temporary QSettings directory. Defaults to an isolated temp directory.",
    )
    parser.add_argument(
        "--skip-gif",
        action="store_true",
        help="Write PNG frame sequences but skip optional GIF encoding.",
    )
    parser.add_argument(
        "--visible",
        action="store_true",
        default=os.environ.get("MAGSCOPE_DOC_CAPTURE_VISIBLE") == "1",
        help=(
            "Show the real Qt window and capture native widget renders. This gives "
            "higher-fidelity screenshots than Qt offscreen rendering."
        ),
    )
    return parser.parse_args(argv)


@contextmanager
def _isolated_qsettings(settings_dir: Path) -> Iterator[None]:
    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_dir))
    QSettings("MagScope", "MagScope").clear()
    try:
        yield
    finally:
        QSettings("MagScope", "MagScope").clear()
        QSettings.setDefaultFormat(previous_format)


def _clear_ui_manager_singleton() -> None:
    type(UIManager)._instances.pop(UIManager, None)


def _process_events(app: QApplication, passes: int = 4, delay: float = 0.02) -> None:
    for _ in range(passes):
        app.processEvents()
        time.sleep(delay)


def _configure_app(app: QApplication) -> None:
    app.setFont(QFont(DOCUMENTATION_FONT_FAMILY, DOCUMENTATION_FONT_SIZE))
    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    QGuiApplication.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(APP_BACKGROUND_COLOR))
    palette.setColor(QPalette.ColorRole.Base, QColor(PANEL_BACKGROUND_COLOR))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#242424"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#d0d0d0"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#d0d0d0"))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#888888"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#78c7ff"))
    app.setPalette(palette)


def _make_settings() -> MagScopeSettings:
    settings = MagScopeSettings()
    settings[GUI_ACCENT_COLOR_SETTING] = "#78c7ff"
    return settings


def _make_simulated_frame() -> tuple[DummyCameraBeads, np.ndarray]:
    camera = DummyCameraBeads()
    sink = _FrameSink()
    camera.connect(sink)
    camera.fetch()
    if sink.image_bytes is None:
        frame = np.zeros((camera.height, camera.width), dtype=camera.dtype)
    else:
        frame = np.frombuffer(sink.image_bytes, dtype=camera.dtype).reshape(
            camera.height,
            camera.width,
        )
    return camera, np.array(frame, copy=True)


def _rois_from_camera(
    camera: DummyCameraBeads,
    roi_size: int,
    limit: int = 6,
) -> dict[int, tuple[int, int, int, int]]:
    centers = []
    if getattr(camera, "_centers_fixed", None) is not None and camera._centers_fixed.size:
        centers.extend(camera._centers_fixed.tolist())
    if getattr(camera, "_centers_teth", None) is not None and camera._centers_teth.size:
        centers.extend(camera._centers_teth.tolist())

    half_roi = roi_size // 2
    rois: dict[int, tuple[int, int, int, int]] = {}
    for bead_id, (cx, cy) in enumerate(centers[:limit]):
        x0 = max(0, int(round(cx)) - half_roi)
        y0 = max(0, int(round(cy)) - half_roi)
        x1 = min(camera.width, x0 + roi_size)
        y1 = min(camera.height, y0 + roi_size)
        rois[bead_id] = (x0, x1, y0, y1)
    return rois


def _pixmap_from_frame(frame: np.ndarray) -> QPixmap:
    frame = np.ascontiguousarray(frame)
    height, width = frame.shape
    qimage = QImage(
        frame.data,
        width,
        height,
        int(frame.strides[0]),
        QImage.Format.Format_Grayscale8,
    ).copy()
    return QPixmap.fromImage(qimage)


def _make_initial_tracks_buffer(rows: int = 256) -> _StaticTracksBuffer:
    return _StaticTracksBuffer(np.full((rows, 7), np.nan, dtype=np.float64))


def _make_demo_tracks(n_points: int = 180) -> np.ndarray:
    timestamps = 1_700_000_000.0 + np.arange(n_points, dtype=np.float64)
    phase = np.linspace(0.0, 4.0 * np.pi, n_points)
    x_values = 55.0 * np.sin(phase)
    y_values = 34.0 * np.cos(phase * 0.65)
    z_values = 90.0 + 24.0 * np.sin(phase * 0.45)

    selected_tracks = np.zeros((n_points, 7), dtype=np.float64)
    selected_tracks[:, 0] = timestamps
    selected_tracks[:, 1] = x_values + 10.0
    selected_tracks[:, 2] = y_values - 5.0
    selected_tracks[:, 3] = 10.0 - z_values
    selected_tracks[:, 4] = 0.0

    reference_tracks = np.zeros((n_points, 7), dtype=np.float64)
    reference_tracks[:, 0] = timestamps
    reference_tracks[:, 1] = 10.0
    reference_tracks[:, 2] = -5.0
    reference_tracks[:, 3] = 10.0
    reference_tracks[:, 4] = 1.0

    tracks = np.empty((n_points * 2, 7), dtype=np.float64)
    tracks[0::2] = selected_tracks
    tracks[1::2] = reference_tracks
    return tracks


def _make_plot_worker() -> PlotWorker:
    tracks_buffer = _make_initial_tracks_buffer()
    worker = PlotWorker()
    worker.plots = [
        _DocumentationTracksPlot("X", tracks_buffer),
        _DocumentationTracksPlot("Y", tracks_buffer),
        _DocumentationTracksPlot("Z", tracks_buffer),
    ]
    worker.set_locks({})
    worker.setup()
    worker.selected_bead = 0
    worker.reference_bead = None
    return worker


def _build_live_plots_widget() -> ResizableLabel:
    label = ResizableLabel(ignore_pixmap_size_hint=True)
    label.setScaledContents(False)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    label.setMinimumSize(1, 1)
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
    label.setStyleSheet(f"background-color: {PANEL_BACKGROUND_COLOR};")
    return label


def _set_plot_tracks(manager: UIManager, tracks: np.ndarray) -> None:
    if manager.plot_worker is None:
        return
    seen_buffers: set[int] = set()
    for plot in manager.plot_worker.plots:
        buffer = getattr(plot, "buffer", None)
        if isinstance(buffer, _StaticTracksBuffer) and id(buffer) not in seen_buffers:
            buffer.set_tracks(tracks)
            seen_buffers.add(id(buffer))


def _set_line_edit_text(lineedit: object, text: str) -> None:
    was_blocked = lineedit.blockSignals(True)
    try:
        lineedit.setText(text)
    finally:
        lineedit.blockSignals(was_blocked)


def _render_live_plots(manager: UIManager, app: QApplication) -> None:
    if manager.plot_worker is None or manager.plots_widget is None:
        return

    width = max(1, manager.plots_widget.width())
    height = max(1, manager.plots_widget.height())
    device_pixel_ratio = manager.plots_widget.devicePixelRatioF()
    manager.plot_worker._update_figure_size(width, height, device_pixel_ratio)
    for _ in range(2):
        manager.plot_worker._update_last_time = time.time()
        manager.plot_worker.do_main_loop()
        _process_events(app)


def _set_documentation_beads(
    manager: UIManager,
    rois: dict[int, tuple[int, int, int, int]],
    *,
    selected_bead_id: int | None = None,
    reference_bead_id: int | None = None,
    active_bead_id: int | None = None,
) -> None:
    manager._bead_rois = dict(rois)
    manager._bead_next_id = max(rois, default=-1) + 1
    manager.selected_bead = -1 if selected_bead_id is None else int(selected_bead_id)
    manager.reference_bead = None if reference_bead_id is None else int(reference_bead_id)
    if manager.plot_worker is not None:
        manager.plot_worker.selected_bead = manager.selected_bead
        manager.plot_worker.reference_bead = manager.reference_bead
    if manager.controls is not None:
        panel = getattr(manager.controls, "plot_settings_panel", None)
        if panel is not None:
            selected_text = "" if selected_bead_id is None else str(selected_bead_id)
            reference_text = "" if reference_bead_id is None else str(reference_bead_id)
            _set_line_edit_text(panel.selected_bead.lineedit, selected_text)
            _set_line_edit_text(panel.reference_bead.lineedit, reference_text)
    manager._set_active_bead(active_bead_id)
    manager._update_next_bead_id_label()
    manager._refresh_bead_overlay()


def _is_icon_tool_button(widget: QWidget) -> bool:
    if not isinstance(widget, QToolButton):
        return False
    if widget.property("topBarCompact") is True:
        return True
    return widget.text() in MATERIAL_ICON_TEXTS


def _apply_documentation_font(root: QWidget) -> None:
    font = QFont(DOCUMENTATION_FONT_FAMILY, DOCUMENTATION_FONT_SIZE)
    widgets = [root, *root.findChildren(QWidget)]
    for widget in widgets:
        if _is_icon_tool_button(widget):
            continue
        widget.setFont(font)
        if isinstance(widget, QPushButton) and "text-align: left" in widget.styleSheet():
            style = widget.styleSheet()
            if "color:" not in style:
                widget.setStyleSheet(style + "\ncolor: #d0d0d0;")


def _make_manager(app: QApplication) -> tuple[UIManager, DummyCameraBeads]:
    _clear_ui_manager_singleton()
    manager = UIManager()
    manager.qt_app = app
    manager.settings = _make_settings()
    manager.camera_type = DummyCameraBeads
    manager.hardware_types = {}
    manager.send_ipc = lambda _command: None
    manager.plot_worker = _make_plot_worker()

    camera, frame = _make_simulated_frame()
    manager.video_buffer = _PreviewVideoBuffer(frame)
    manager.controls = Controls(manager)
    manager.video_viewer = VideoViewer()
    manager.video_viewer.sceneClicked.connect(manager.callback_view_clicked)
    manager.video_viewer.set_pixmap(_pixmap_from_frame(frame))
    manager.video_viewer.set_bead_overlay(
        _rois_from_camera(camera, manager.settings["ROI"]),
        active_bead_id=0,
        selected_bead_id=0,
        reference_bead_id=1,
    )
    manager.video_viewer.reset_view()
    manager.plots_widget = _build_live_plots_widget()
    manager.plot_worker.image_signal.connect(manager._set_plot_image)

    if manager.controls is not None:
        for setting_name in camera.settings:
            manager.controls.camera_panel.update_camera_setting(
                setting_name,
                camera.get_setting(setting_name),
            )
        manager.controls.status_panel.update_display_rate("25 updates/sec")
        manager.controls.status_panel.update_video_processors_status("0/3 busy")
        manager.controls.status_panel.update_video_buffer_status("12% full")

    return manager, camera


def _build_window(manager: UIManager) -> QMainWindow:
    manager.create_central_widgets()
    window = QMainWindow()
    window.setWindowTitle("MagScope")
    UIManager._configure_unified_top_bar_window(window)
    app_icon = load_app_icon()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.setMinimumSize(900, 650)
    window.resize(*WINDOW_SIZE)
    window.setCentralWidget(manager.central_widgets[0])
    manager.windows.append(window)
    manager._create_viewer_docks(window)
    manager._create_preferences_menu_action(window)
    manager._create_view_menu(window)
    manager._create_tools_menu(window)
    manager._create_zlut_menu(window)
    manager._create_help_menu_action(window)
    manager._create_search_menu_widget(window)
    _apply_documentation_font(window)
    manager._apply_default_viewer_layout()
    window.show()
    return window


def _set_panel_expanded(manager: UIManager, panel_id: str, expanded: bool) -> None:
    if manager.controls is None:
        return
    panel = manager.controls.panels.get(panel_id)
    groupbox = getattr(panel, "groupbox", None)
    if groupbox is not None:
        groupbox._apply_collapsed_state(
            collapsed=not expanded,
            animate=False,
            persist=False,
        )
    manager.controls.reveal_panel(panel_id)


def _capture_controls_tab(
    manager: UIManager,
    app: QApplication,
    panel_id: str,
    path: Path,
    dry_run: bool,
    visible_capture: bool,
) -> CaptureResult | None:
    if manager.controls is None:
        return None
    _set_panel_expanded(manager, panel_id, True)
    _process_events(app)
    return _save_widget(manager.controls, path, dry_run, visible_capture=visible_capture)


def _capture_panel(
    manager: UIManager,
    app: QApplication,
    panel_id: str,
    path: Path,
    dry_run: bool,
    visible_capture: bool,
    *,
    expanded: bool = True,
) -> CaptureResult | None:
    if manager.controls is None:
        return None
    panel = manager.controls.panels.get(panel_id)
    if panel is None:
        return None
    _set_panel_expanded(manager, panel_id, expanded)
    _process_events(app)
    return _save_widget(panel, path, dry_run, visible_capture=visible_capture)


def _composite_matplotlib_canvas_grabs(root: QWidget, base_pixmap: QPixmap) -> QPixmap:
    canvases = [
        canvas
        for canvas in root.findChildren(FigureCanvas)
        if canvas.isVisible() and canvas.width() > 0 and canvas.height() > 0
    ]
    if not canvases:
        return base_pixmap

    pixmap = QPixmap(base_pixmap)
    painter = QPainter(pixmap)
    try:
        for canvas in canvases:
            canvas_pixmap = _render_matplotlib_canvas_to_pixmap(canvas)
            if canvas_pixmap.isNull():
                continue
            painter.drawPixmap(canvas.mapTo(root, canvas.rect().topLeft()), canvas_pixmap)
    finally:
        painter.end()
    return pixmap


def _render_matplotlib_canvas_to_pixmap(canvas: FigureCanvas) -> QPixmap:
    width = max(1, canvas.width())
    height = max(1, canvas.height())
    dpi = float(canvas.figure.dpi)
    original_size = canvas.figure.get_size_inches()
    canvas.figure.set_size_inches(width / dpi, height / dpi, forward=False)
    canvas.figure.canvas.draw()

    buffer = io.BytesIO()
    try:
        canvas.figure.savefig(
            buffer,
            format="png",
            dpi=dpi,
            facecolor=canvas.figure.get_facecolor(),
            edgecolor="none",
        )
    finally:
        canvas.figure.set_size_inches(original_size, forward=False)

    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue())
    if pixmap.isNull() or (pixmap.width() == width and pixmap.height() == height):
        return pixmap
    return pixmap.scaled(
        width,
        height,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _save_widget(
    widget: QWidget,
    path: Path,
    dry_run: bool,
    *,
    visible_capture: bool = False,
) -> CaptureResult:
    if dry_run:
        return CaptureResult(widget.objectName() or widget.windowTitle() or "widget", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = widget.grab()
    if visible_capture:
        pixmap = _composite_matplotlib_canvas_grabs(widget, pixmap)
    if pixmap.isNull():
        raise RuntimeError(f"Could not capture {path}: capture backend returned a null pixmap")
    if not pixmap.save(str(path)):
        raise RuntimeError(f"Could not save screenshot to {path}")
    return CaptureResult(widget.objectName() or widget.windowTitle() or "widget", path)


def _widget_global_rect(widget: QWidget) -> QRect:
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def _save_composite_widget_capture(
    base_widget: QWidget,
    overlay_widgets: list[QWidget],
    path: Path,
    dry_run: bool,
    *,
    margin: int = 6,
) -> CaptureResult:
    if dry_run:
        return CaptureResult(base_widget.objectName() or base_widget.windowTitle() or "widget", path)

    visible_widgets = [
        widget
        for widget in [base_widget, *overlay_widgets]
        if widget.isVisible() and widget.width() > 0 and widget.height() > 0
    ]
    if not visible_widgets:
        raise RuntimeError(f"Could not capture {path}: no visible widgets to composite")

    capture_rect = _widget_global_rect(visible_widgets[0])
    for widget in visible_widgets[1:]:
        capture_rect = capture_rect.united(_widget_global_rect(widget))
    capture_rect = capture_rect.adjusted(-margin, -margin, margin, margin)

    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = QPixmap(capture_rect.size())
    pixmap.fill(QColor(APP_BACKGROUND_COLOR))

    painter = QPainter(pixmap)
    try:
        for widget in visible_widgets:
            widget_pixmap = widget.grab()
            if widget_pixmap.isNull():
                continue
            top_left = _widget_global_rect(widget).topLeft() - capture_rect.topLeft()
            painter.drawPixmap(top_left, widget_pixmap)
    finally:
        painter.end()

    if not pixmap.save(str(path)):
        raise RuntimeError(f"Could not save screenshot to {path}")
    return CaptureResult(base_widget.objectName() or base_widget.windowTitle() or "widget", path)


def _save_dialog(
    dialog: QWidget,
    app: QApplication,
    path: Path,
    dry_run: bool,
    *,
    visible_capture: bool,
) -> CaptureResult:
    _apply_documentation_font(dialog)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    _process_events(app)
    result = _save_widget(dialog, path, dry_run, visible_capture=visible_capture)
    dialog.close()
    dialog.deleteLater()
    _process_events(app)
    return result


def _write_optional_gif(
    frame_paths: list[Path],
    gif_path: Path,
    dry_run: bool,
    *,
    duration_ms: int = DEFAULT_DURATION_MS,
) -> Path | None:
    if dry_run:
        return gif_path

    if not frame_paths:
        return None
    try:
        return assemble_gif_from_frames(
            frame_paths,
            gif_path,
            options=GifAssemblyOptions(duration_ms=duration_ms),
        )
    except GifAssemblyError as exc:
        if "Pillow is required" in str(exc):
            return None
        raise


def _latest_documentation_frame(manager: UIManager) -> np.ndarray | None:
    video_buffer = manager.video_buffer
    if video_buffer is None:
        return None
    try:
        _, image_bytes = video_buffer.peak_image()
    except Exception:
        return None
    return np.frombuffer(image_bytes, dtype=video_buffer.dtype).copy().reshape(video_buffer.image_shape)


def _make_zlut_preview_image(profile_length: int = 72, n_steps: int = 17) -> np.ndarray:
    radius = np.linspace(0.0, 1.0, profile_length, dtype=np.float64)[:, np.newaxis]
    phase = np.linspace(0.0, 2.0 * np.pi, n_steps, dtype=np.float64)[np.newaxis, :]
    center = 0.45 + 0.12 * np.sin(phase)
    width = 0.035 + 0.01 * np.cos(phase * 0.5)
    bead_profile = np.exp(-np.square(radius - center) / width)
    background = 0.18 + 0.12 * radius + 0.08 * np.cos(phase)
    return bead_profile + background


def _capture_auto_bead_selection_assets(
    manager: UIManager,
    camera: DummyCameraBeads,
    app: QApplication,
    window: QMainWindow,
    screenshot_root: Path,
    dry_run: bool,
    visible_capture: bool,
) -> list[CaptureResult]:
    if manager.camera_dock is None:
        return []

    frame = _latest_documentation_frame(manager)
    if frame is None:
        return []

    rois = _rois_from_camera(camera, manager.settings["ROI"], limit=8)
    if len(rois) < 7:
        return []

    existing_rois = {0: rois[0]}
    seed_roi = rois[1]
    candidates = [
        AutoBeadCandidate(rois[bead_id], score)
        for bead_id, score in zip(range(2, 7), (0.986, 0.975, 0.968, 0.955, 0.910))
    ]

    dialog = AutoBeadSelectionDialog(
        parent=window,
        image=frame,
        roi_size=int(manager.settings["ROI"]),
        existing_rois=existing_rois,
        display_scale=manager._current_image_display_scale(),
    )
    dialog._seed_roi = seed_roi
    dialog._candidates = candidates
    dialog._configure_threshold_slider()
    dialog._set_search_ui_state(False)
    dialog._refresh_visible_candidates()
    dialog.video_viewer.reset_view()
    dialog_result = _save_dialog(
        dialog,
        app,
        screenshot_root / "workflows" / "auto-bead-selection-dialog.png",
        dry_run,
        visible_capture=visible_capture,
    )

    accepted_rois = dict(existing_rois)
    for bead_id, roi in enumerate(
        [seed_roi, *[candidate.roi for candidate in candidates[:4]]],
        start=1,
    ):
        accepted_rois[bead_id] = roi
    _set_documentation_beads(
        manager,
        accepted_rois,
        selected_bead_id=1,
        reference_bead_id=2,
        active_bead_id=1,
    )
    _process_events(app)
    accepted_result = _save_widget(
        manager.camera_dock,
        screenshot_root / "workflows" / "auto-bead-selection-accepted.png",
        dry_run,
        visible_capture=visible_capture,
    )
    return [dialog_result, accepted_result]


def _capture_save_folder_picker(
    app: QApplication,
    window: QMainWindow,
    screenshot_root: Path,
    dry_run: bool,
    visible_capture: bool,
) -> CaptureResult:
    dialog = QFileDialog(window, "Select Folder", str(_repo_root() / "assets"))
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
    dialog.setFileMode(QFileDialog.FileMode.Directory)
    dialog.setViewMode(QFileDialog.ViewMode.List)
    dialog.selectFile("doc_capture")
    dialog.resize(760, 520)
    return _save_dialog(
        dialog,
        app,
        screenshot_root / "workflows" / "save-folder-picker.png",
        dry_run,
        visible_capture=visible_capture,
    )


def _capture_zlut_dialog_assets(
    manager: UIManager,
    app: QApplication,
    window: QMainWindow,
    screenshot_root: Path,
    dry_run: bool,
    visible_capture: bool,
) -> list[CaptureResult]:
    setup_dialog = ZLUTGenerationSetupDialog(
        window,
        roi_size=int(manager.settings["ROI"]),
        default_measurements=120,
    )
    setup_dialog.start_input.lineedit.setText("-500")
    setup_dialog.step_input.lineedit.setText("50")
    setup_dialog.stop_input.lineedit.setText("500")
    setup_dialog.measurements_input.lineedit.setText("120")
    setup_dialog.resize(460, 260)
    setup_result = _save_dialog(
        setup_dialog,
        app,
        screenshot_root / "zlut" / "new-zlut-dialog.png",
        dry_run,
        visible_capture=visible_capture,
    )

    generation_dialog = ZLUTGenerationDialog(window)
    generation_dialog.update_progress(
        current_step=21,
        total_steps=21,
        capture_count=1260,
        capture_capacity=1260,
        motor_z_value=500.0,
    )
    generation_dialog.update_evaluation(active=True, bead_ids=[1, 2, 3, 4], selected_bead_id=2)
    generation_dialog.update_state(
        "Review the generated Z-LUT.",
        "Select a bead, then save the generated Z-LUT or save and load it for Z-locking.",
        running=False,
        can_cancel=False,
        phase="evaluating",
    )
    generation_dialog.preview_widget.update_preview(
        state=4,
        count=1260,
        capacity=1260,
        n_steps=21,
        n_beads=4,
        profiles_per_bead=15,
        profile_length=72,
        preview_image=_make_zlut_preview_image(),
        selected_bead_id=2,
        mode="Generated Z-LUT",
        motor_z_min=-500.0,
        motor_z_max=500.0,
        expected_capture_count=1260,
        x_axis_label="Z Position (nm)",
        x_axis_min=-500.0,
        x_axis_max=500.0,
        image_x_min=-500.0,
        image_x_max=500.0,
    )
    generation_result = _save_dialog(
        generation_dialog,
        app,
        screenshot_root / "zlut" / "zlut-generation-dialog.png",
        dry_run,
        visible_capture=visible_capture,
    )
    return [setup_result, generation_result]


def _save_focus_motor_plot(
    locks: dict[str, object],
    app: QApplication,
    path: Path,
    dry_run: bool,
    visible_capture: bool,
) -> CaptureResult:
    if dry_run:
        return CaptureResult("simulated focus motor plot", path)

    figure = Figure(
        figsize=(7.6, 2.8),
        dpi=100,
        facecolor=PANEL_BACKGROUND_COLOR,
        constrained_layout=True,
    )
    figure.set_constrained_layout_pads(w_pad=0.02, h_pad=0.02, hspace=0.02, wspace=0.0)
    canvas = FigureCanvas(figure)
    canvas.setFixedSize(760, 280)

    axes = figure.subplots()
    axes.set_facecolor(PANEL_BACKGROUND_COLOR)
    axes.margins(x=0)
    axes.set_xlabel("Time (h:m:s)")
    axes.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))

    plot = FocusMotorPlot()
    plot.set_parent(_DocumentationFocusPlotParent(locks=locks, limits={}))
    plot.set_axes(axes)
    plot.setup()
    try:
        plot.update()
        data = plot.buffer.peak_unsorted().copy()
        finite_rows = np.isfinite(data[:, 0])
        if np.any(finite_rows):
            rows = data[finite_rows]
            rows = rows[np.argsort(rows[:, 0])]
            timepoints = [datetime.fromtimestamp(timestamp) for timestamp in rows[:, 0]]
            y_values = np.concatenate([rows[:, 1], rows[:, 2]])
            y_values = y_values[np.isfinite(y_values)]
            axes.set_xlim(mdates.date2num(timepoints[0]), mdates.date2num(timepoints[-1]))
            if y_values.size:
                y_min = float(np.min(y_values))
                y_max = float(np.max(y_values))
                padding = max((y_max - y_min) * 0.08, 1.0)
                axes.set_ylim(y_min - padding, y_max + padding)
        canvas.draw()
        canvas.show()
        _process_events(app)
        return _save_widget(canvas, path, dry_run, visible_capture=visible_capture)
    finally:
        _close_matrix_buffer(getattr(plot, "buffer", None))
        figure.clear()
        canvas.close()
        canvas.deleteLater()
        _process_events(app, passes=2)


def _capture_simulated_focus_motor_assets(
    manager: UIManager,
    app: QApplication,
    screenshot_root: Path,
    dry_run: bool,
    visible_capture: bool,
) -> list[CaptureResult]:
    panel_path = screenshot_root / "zlut" / "simulated-focus-motor-panel.png"
    plot_path = screenshot_root / "zlut" / "simulated-focus-motor-plot.png"
    if dry_run:
        return [
            CaptureResult("simulated focus motor panel", panel_path),
            CaptureResult("simulated focus motor plot", plot_path),
        ]

    results: list[CaptureResult] = []
    original_locks = manager.locks
    original_hardware_types = dict(manager.hardware_types)
    panel: FocusMotorControls | None = None

    with _simulated_focus_motor_buffer() as (_buffer, locks):
        manager.locks = locks
        manager.hardware_types = {SimulatedFocusMotor.__name__: SimulatedFocusMotor}
        try:
            panel = FocusMotorControls(manager)
            panel.setObjectName("SimulatedFocusMotorDocumentationPanel")
            _apply_documentation_font(panel)
            panel.resize(360, panel.sizeHint().height())
            panel.show()
            panel._update_labels()
            _process_events(app)
            results.append(
                _save_widget(
                    panel,
                    panel_path,
                    dry_run,
                    visible_capture=visible_capture,
                )
            )
            results.append(
                _save_focus_motor_plot(
                    locks,
                    app,
                    plot_path,
                    dry_run,
                    visible_capture,
                )
            )
        finally:
            manager.locks = original_locks
            manager.hardware_types = original_hardware_types
            if panel is not None:
                timer = getattr(panel, "_timer", None)
                if timer is not None:
                    timer.stop()
                _close_matrix_buffer(getattr(panel, "_buffer", None))
                panel.close()
                panel.deleteLater()
                _process_events(app, passes=2)

    return results


def _capture_plot_workflow_assets(
    manager: UIManager,
    app: QApplication,
    window: QMainWindow,
    screenshot_root: Path,
    dry_run: bool,
    visible_capture: bool,
) -> list[CaptureResult]:
    results: list[CaptureResult] = []

    _set_plot_tracks(manager, _make_demo_tracks())
    _render_live_plots(manager, app)
    if manager.plots_dock is not None:
        results.append(
            _save_widget(
                manager.plots_dock,
                screenshot_root / "live-view" / "live-plots-with-data.png",
                dry_run,
                visible_capture=visible_capture,
            )
        )

    _set_panel_expanded(manager, "PlotSettingsPanel", True)
    _set_panel_expanded(manager, "HistogramPanel", True)
    _set_panel_expanded(manager, "ProfilePanel", True)
    _render_live_plots(manager, app)
    _process_events(app)
    results.append(
        _save_widget(
            window,
            screenshot_root / "workflows" / "analysis-tab-with-plots.png",
            dry_run,
            visible_capture=visible_capture,
        )
    )
    return results


def _roi_center(roi: tuple[int, int, int, int]) -> QPointF:
    x0, x1, y0, y1 = roi
    return QPointF((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def _viewer_point_for_scene(viewer: VideoViewer, scene_point: QPointF) -> QPoint:
    mapped_point = viewer.mapFromScene(scene_point)
    return mapped_point if isinstance(mapped_point, QPoint) else mapped_point.toPoint()


def _cursor_position_for_frame(
    capture_widget: QWidget,
    source_path: Path,
    viewer: VideoViewer,
    scene_point: QPointF,
) -> tuple[float, float]:
    viewport_point = _viewer_point_for_scene(viewer, scene_point)
    capture_point = viewer.viewport().mapTo(capture_widget, viewport_point)
    x = float(capture_point.x())
    y = float(capture_point.y())

    if source_path.exists() and capture_widget.width() > 0 and capture_widget.height() > 0:
        image = QImage(str(source_path))
        if not image.isNull():
            x *= image.width() / capture_widget.width()
            y *= image.height() / capture_widget.height()
    return x, y


def _append_cursor_frame(
    specs: list[CursorOverlayFrame],
    *,
    capture_widget: QWidget,
    source_path: Path,
    output_dir: Path,
    viewer: VideoViewer,
    scene_point: QPointF,
    state: str = "default",
) -> None:
    specs.append(
        CursorOverlayFrame(
            source_path=source_path,
            output_path=output_dir / f"frame-{len(specs) + 1:02d}.png",
            position=_cursor_position_for_frame(capture_widget, source_path, viewer, scene_point),
            state=state,
        )
    )


def _append_cursor_move(
    specs: list[CursorOverlayFrame],
    *,
    capture_widget: QWidget,
    source_path: Path,
    output_dir: Path,
    viewer: VideoViewer,
    start: QPointF,
    end: QPointF,
    steps: int,
    state: str = "default",
) -> None:
    if steps <= 0:
        return
    for step in range(1, steps + 1):
        progress = step / steps
        scene_point = QPointF(
            start.x() + (end.x() - start.x()) * progress,
            start.y() + (end.y() - start.y()) * progress,
        )
        _append_cursor_frame(
            specs,
            capture_widget=capture_widget,
            source_path=source_path,
            output_dir=output_dir,
            viewer=viewer,
            scene_point=scene_point,
            state=state,
        )


def _qt_click_viewer(
    manager: UIManager,
    app: QApplication,
    scene_point: QPointF,
    *,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
) -> None:
    if manager.video_viewer is None:
        raise RuntimeError("Cannot click viewer before the VideoViewer exists")
    viewport = manager.video_viewer.viewport()
    point = _viewer_point_for_scene(manager.video_viewer, scene_point)
    QCursor.setPos(viewport.mapToGlobal(point))
    _process_events(app, passes=2, delay=0.01)
    QTest.mouseMove(viewport, point)
    QTest.mouseClick(viewport, button, Qt.KeyboardModifier.NoModifier, point)
    _process_events(app, passes=8, delay=0.02)
    _acknowledge_documentation_bead_add(manager)
    _process_events(app, passes=4, delay=0.02)


def _qt_drag_viewer(
    manager: UIManager,
    app: QApplication,
    start: QPointF,
    end: QPointF,
    *,
    steps: int = 6,
) -> None:
    if manager.video_viewer is None:
        raise RuntimeError("Cannot drag viewer before the VideoViewer exists")
    viewport = manager.video_viewer.viewport()
    start_point = _viewer_point_for_scene(manager.video_viewer, start)
    end_point = _viewer_point_for_scene(manager.video_viewer, end)
    QCursor.setPos(viewport.mapToGlobal(start_point))
    _process_events(app, passes=2, delay=0.01)
    QTest.mouseMove(viewport, start_point)
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start_point)
    _process_events(app, passes=2, delay=0.01)
    for step in range(1, steps + 1):
        progress = step / steps
        point = QPoint(
            round(start_point.x() + (end_point.x() - start_point.x()) * progress),
            round(start_point.y() + (end_point.y() - start_point.y()) * progress),
        )
        QTest.mouseMove(viewport, point)
        _process_events(app, passes=1, delay=0.01)
    QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end_point)
    _process_events(app, passes=8, delay=0.02)


def _require_bead_ids(manager: UIManager, expected_ids: set[int], context: str) -> None:
    actual_ids = set(manager._bead_rois)
    if actual_ids != expected_ids:
        raise RuntimeError(
            f"{context}: expected bead IDs {sorted(expected_ids)}, got {sorted(actual_ids)}"
        )


def _acknowledge_documentation_bead_add(manager: UIManager) -> None:
    pending_id = manager._pending_bead_add_id
    pending_roi = manager._pending_bead_add_roi
    if pending_id is None or pending_roi is None:
        return
    if manager.bead_roi_buffer is not None:
        manager.refresh_bead_rois()
        return
    if manager._bead_rois.get(pending_id) == pending_roi:
        manager._clear_pending_bead_add()


def _save_cursor_source_frame(
    widget: QWidget,
    path: Path,
    dry_run: bool,
    visible_capture: bool,
) -> CaptureResult:
    return _save_widget(widget, path, dry_run, visible_capture=visible_capture)


def _capture_bead_selection_workflow(
    manager: UIManager,
    camera: DummyCameraBeads,
    app: QApplication,
    output_dir: Path,
    dry_run: bool,
    skip_gif: bool,
    visible_capture: bool,
) -> list[CaptureResult]:
    if manager.camera_dock is None or manager.video_viewer is None:
        return []

    screenshot_root = output_dir / SCREENSHOT_DIR
    frame_dir = output_dir / FRAME_DIR / "workflows" / "bead-selection-workflow"
    gif_path = output_dir / GIF_DIR / "workflows" / "bead-selection-workflow.gif"
    final_path = screenshot_root / "workflows" / "bead-selection-workflow.png"

    rois = _rois_from_camera(camera, manager.settings["ROI"], limit=3)
    if len(rois) < 2:
        return []

    results: list[CaptureResult] = []
    cursor_specs: list[CursorOverlayFrame] = []
    capture_widget = manager.camera_dock
    viewer = manager.video_viewer
    add_first_point = _roi_center(rois[0])
    add_second_point = _roi_center(rois[1])
    cursor_start = QPointF(
        max(15.0, add_first_point.x() - 130.0),
        max(15.0, add_first_point.y() - 80.0),
    )

    with TemporaryDirectory(prefix="magscope-doc-cursor-") as temp_raw_dir:
        raw_dir = Path(temp_raw_dir)
        raw_index = 0

        def capture_raw_frame() -> Path:
            nonlocal raw_index
            raw_index += 1
            raw_path = raw_dir / f"raw-{raw_index:02d}.png"
            _process_events(app)
            _save_cursor_source_frame(
                capture_widget,
                raw_path,
                dry_run,
                visible_capture,
            )
            return raw_path

        _set_documentation_beads(
            manager,
            {},
            selected_bead_id=None,
            reference_bead_id=None,
            active_bead_id=None,
        )
        viewer.reset_view()
        _process_events(app)

        raw_initial = capture_raw_frame()
        _append_cursor_frame(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_initial,
            output_dir=frame_dir,
            viewer=viewer,
            scene_point=cursor_start,
        )
        _append_cursor_move(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_initial,
            output_dir=frame_dir,
            viewer=viewer,
            start=cursor_start,
            end=add_first_point,
            steps=5,
        )
        for _ in range(2):
            _append_cursor_frame(
                cursor_specs,
                capture_widget=capture_widget,
                source_path=raw_initial,
                output_dir=frame_dir,
                viewer=viewer,
                scene_point=add_first_point,
                state="left_click",
            )

        _qt_click_viewer(manager, app, add_first_point)
        _require_bead_ids(manager, {0}, "first bead click")
        raw_after_first_add = capture_raw_frame()
        _append_cursor_frame(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_after_first_add,
            output_dir=frame_dir,
            viewer=viewer,
            scene_point=add_first_point,
        )
        _append_cursor_move(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_after_first_add,
            output_dir=frame_dir,
            viewer=viewer,
            start=add_first_point,
            end=add_second_point,
            steps=5,
        )
        for _ in range(2):
            _append_cursor_frame(
                cursor_specs,
                capture_widget=capture_widget,
                source_path=raw_after_first_add,
                output_dir=frame_dir,
                viewer=viewer,
                scene_point=add_second_point,
                state="left_click",
            )

        _qt_click_viewer(manager, app, add_second_point)
        _require_bead_ids(manager, {0, 1}, "second bead click")
        raw_after_second_add = capture_raw_frame()
        first_roi_center = _roi_center(manager._bead_rois[0])
        _append_cursor_frame(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_after_second_add,
            output_dir=frame_dir,
            viewer=viewer,
            scene_point=add_second_point,
        )
        _append_cursor_move(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_after_second_add,
            output_dir=frame_dir,
            viewer=viewer,
            start=add_second_point,
            end=first_roi_center,
            steps=4,
        )
        for _ in range(2):
            _append_cursor_frame(
                cursor_specs,
                capture_widget=capture_widget,
                source_path=raw_after_second_add,
                output_dir=frame_dir,
                viewer=viewer,
                scene_point=first_roi_center,
                state="left_click",
            )

        _qt_click_viewer(manager, app, first_roi_center)
        if manager._active_bead_id != 0 or manager.selected_bead != 0:
            raise RuntimeError("select bead click did not activate bead 0")
        raw_after_select = capture_raw_frame()
        drag_start = _roi_center(manager._bead_rois[0])
        drag_end = QPointF(
            drag_start.x() + 45.0,
            drag_start.y() + 28.0,
        )
        _append_cursor_frame(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_after_select,
            output_dir=frame_dir,
            viewer=viewer,
            scene_point=drag_start,
            state="drag",
        )
        _append_cursor_move(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_after_select,
            output_dir=frame_dir,
            viewer=viewer,
            start=drag_start,
            end=drag_end,
            steps=7,
            state="drag",
        )

        old_first_roi = manager._bead_rois[0]
        _qt_drag_viewer(manager, app, drag_start, drag_end)
        if manager._bead_rois[0] == old_first_roi:
            raise RuntimeError("drag interaction did not move bead 0")
        raw_after_drag = capture_raw_frame()
        dragged_center = _roi_center(manager._bead_rois[0])
        second_roi_center = _roi_center(manager._bead_rois[1])
        _append_cursor_frame(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_after_drag,
            output_dir=frame_dir,
            viewer=viewer,
            scene_point=dragged_center,
        )
        _append_cursor_move(
            cursor_specs,
            capture_widget=capture_widget,
            source_path=raw_after_drag,
            output_dir=frame_dir,
            viewer=viewer,
            start=dragged_center,
            end=second_roi_center,
            steps=5,
        )
        for _ in range(2):
            _append_cursor_frame(
                cursor_specs,
                capture_widget=capture_widget,
                source_path=raw_after_drag,
                output_dir=frame_dir,
                viewer=viewer,
                scene_point=second_roi_center,
                state="right_click",
            )

        _qt_click_viewer(manager, app, second_roi_center, button=Qt.MouseButton.RightButton)
        _require_bead_ids(manager, {0}, "right-click remove bead")
        raw_after_remove = capture_raw_frame()
        for _ in range(2):
            _append_cursor_frame(
                cursor_specs,
                capture_widget=capture_widget,
                source_path=raw_after_remove,
                output_dir=frame_dir,
                viewer=viewer,
                scene_point=second_roi_center,
            )

        if dry_run:
            frame_paths = [spec.output_path for spec in cursor_specs]
        else:
            frame_paths = write_cursor_overlay_frames(
                cursor_specs,
                options=CursorOverlayOptions(cursor_size=28, click_radius=17),
            )

    results.extend(CaptureResult("bead selection workflow cursor frame", path) for path in frame_paths)

    results.append(
        _save_widget(
            manager.camera_dock,
            final_path,
            dry_run,
            visible_capture=visible_capture,
        )
    )
    if not skip_gif:
        written_gif = _write_optional_gif(
            frame_paths,
            gif_path,
            dry_run,
            duration_ms=CURSOR_WORKFLOW_DURATION_MS,
        )
        if written_gif is not None:
            results.append(CaptureResult("bead selection workflow gif", written_gif))
    return results


def _capture_saving_enabled_workflow(
    manager: UIManager,
    app: QApplication,
    screenshot_root: Path,
    dry_run: bool,
    visible_capture: bool,
) -> CaptureResult | None:
    if manager.controls is None:
        return None
    panel = manager.controls.acquisition_panel
    _set_panel_expanded(manager, "AcquisitionPanel", True)
    panel.set_acquisition_dir_text(r"C:\MagScopeData\demo-run")
    panel.acquisition_dir_on_checkbox.checkbox.setChecked(True)
    panel.update_save_highlight(True)
    _process_events(app)
    return _save_widget(
        panel,
        screenshot_root / "workflows" / "saving-enabled.png",
        dry_run,
        visible_capture=visible_capture,
    )


def _capture_search_results(
    manager: UIManager,
    app: QApplication,
    screenshot_root: Path,
    dry_run: bool,
) -> CaptureResult | None:
    search_box = manager._search_box
    if search_box is None or manager._menu_row is None:
        return None

    search_box.setFocus()
    search_box.clear()
    QTest.keyClicks(search_box, "ROI")
    _process_events(app, passes=8, delay=0.03)

    completer = search_box.completer()
    popup = completer.popup() if completer is not None else None
    if completer is not None and popup is not None and not popup.isVisible():
        completer.complete()
        _process_events(app, passes=4, delay=0.03)

    if popup is None or not popup.isVisible():
        return _save_widget(
            search_box,
            screenshot_root / "navigation" / "search-box-roi.png",
            dry_run,
        )

    return _save_composite_widget_capture(
        search_box,
        [popup],
        screenshot_root / "navigation" / "search-box-roi.png",
        dry_run,
    )


def capture_assets(
    output_dir: Path,
    *,
    dry_run: bool = False,
    skip_gif: bool = False,
    visible_capture: bool = False,
) -> list[CaptureResult]:
    app = QApplication.instance()
    if app is None:
        app = QApplication([sys.argv[0]])
    _configure_app(app)

    manager, camera = _make_manager(app)
    window = _build_window(manager)
    _set_documentation_beads(
        manager,
        _rois_from_camera(camera, manager.settings["ROI"]),
        selected_bead_id=0,
        reference_bead_id=1,
        active_bead_id=0,
    )
    _process_events(app)
    _render_live_plots(manager, app)

    screenshot_root = output_dir / SCREENSHOT_DIR
    results: list[CaptureResult] = []
    try:
        results.append(
            _save_widget(
                window,
                screenshot_root / "startup" / "main-window.png",
                dry_run,
                visible_capture=visible_capture,
            )
        )
        if manager.camera_dock is not None:
            results.append(
                _save_widget(
                    manager.camera_dock,
                    screenshot_root / "startup" / "live-camera-with-rois.png",
                    dry_run,
                    visible_capture=visible_capture,
                )
            )

        if manager.plots_dock is not None:
            results.append(
                _save_widget(
                    manager.plots_dock,
                    screenshot_root / "live-view" / "live-plots.png",
                    dry_run,
                    visible_capture=visible_capture,
                )
            )

        _set_panel_expanded(manager, "AcquisitionPanel", True)
        _set_panel_expanded(manager, "CameraPanel", True)
        _process_events(app)
        if manager.controls is not None:
            results.append(
                _save_widget(
                    manager.controls,
                    screenshot_root / "controls" / "run-controls.png",
                    dry_run,
                    visible_capture=visible_capture,
                )
            )

        _set_panel_expanded(manager, "PlotSettingsPanel", True)
        _set_panel_expanded(manager, "HistogramPanel", True)
        _process_events(app)
        if manager.controls is not None:
            results.append(
                _save_widget(
                    manager.controls,
                    screenshot_root / "controls" / "analysis-controls.png",
                    dry_run,
                    visible_capture=visible_capture,
                )
            )

        tab_scenarios = [
            ("StatusPanel", screenshot_root / "controls" / "run-tab.png"),
            ("PlotSettingsPanel", screenshot_root / "controls" / "analysis-tab.png"),
            ("XYLockPanel", screenshot_root / "controls" / "locking-tab.png"),
            ("MotorsPlaceholderPanel", screenshot_root / "controls" / "motors-tab.png"),
        ]
        for panel_id, path in tab_scenarios:
            result = _capture_controls_tab(
                manager,
                app,
                panel_id,
                path,
                dry_run,
                visible_capture,
            )
            if result is not None:
                results.append(result)

        panel_scenarios = [
            ("StatusPanel", screenshot_root / "panels" / "status-panel.png"),
            ("AcquisitionPanel", screenshot_root / "panels" / "acquisition-panel.png"),
            ("CameraPanel", screenshot_root / "panels" / "camera-settings-panel.png"),
            ("ScriptPanel", screenshot_root / "panels" / "scripting-panel.png"),
            ("PlotSettingsPanel", screenshot_root / "panels" / "plot-settings-panel.png"),
            ("HistogramPanel", screenshot_root / "panels" / "histogram-panel.png"),
            ("ProfilePanel", screenshot_root / "panels" / "radial-profile-panel.png"),
            ("XYLockPanel", screenshot_root / "panels" / "xy-lock-panel.png"),
            ("ZLockPanel", screenshot_root / "panels" / "z-lock-panel.png"),
            ("MotorsPlaceholderPanel", screenshot_root / "panels" / "hardware-managers-panel.png"),
        ]
        for panel_id, path in panel_scenarios:
            result = _capture_panel(manager, app, panel_id, path, dry_run, visible_capture)
            if result is not None:
                results.append(result)

        results.extend(
            _capture_plot_workflow_assets(
                manager,
                app,
                window,
                screenshot_root,
                dry_run,
                visible_capture,
            )
        )

        results.extend(
            _capture_bead_selection_workflow(
                manager,
                camera,
                app,
                output_dir,
                dry_run,
                skip_gif,
                visible_capture,
            )
        )

        results.extend(
            _capture_auto_bead_selection_assets(
                manager,
                camera,
                app,
                window,
                screenshot_root,
                dry_run,
                visible_capture,
            )
        )

        saving_result = _capture_saving_enabled_workflow(
            manager,
            app,
            screenshot_root,
            dry_run,
            visible_capture,
        )
        if saving_result is not None:
            results.append(saving_result)

        results.append(
            _capture_save_folder_picker(
                app,
                window,
                screenshot_root,
                dry_run,
                visible_capture,
            )
        )

        results.extend(
            _capture_zlut_dialog_assets(
                manager,
                app,
                window,
                screenshot_root,
                dry_run,
                visible_capture,
            )
        )

        results.extend(
            _capture_simulated_focus_motor_assets(
                manager,
                app,
                screenshot_root,
                dry_run,
                visible_capture,
            )
        )

        search_result = _capture_search_results(manager, app, screenshot_root, dry_run)
        if search_result is not None:
            results.append(search_result)

        manager._show_preferences_dialog()
        _process_events(app)
        if manager._preferences_dialog is not None:
            _apply_documentation_font(manager._preferences_dialog)
            _process_events(app)
            results.append(
                _save_widget(
                    manager._preferences_dialog,
                    screenshot_root / "preferences" / "preferences-dialog.png",
                    dry_run,
                    visible_capture=visible_capture,
                )
            )
            manager._preferences_dialog.close()
            _process_events(app)

    finally:
        if manager.plot_worker is not None:
            manager.plot_worker.dispose()
        for widget in QApplication.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        _process_events(app, passes=2)
        _clear_ui_manager_singleton()

    return results


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    output_dir = _resolve_output_dir(args.output)

    if args.settings_dir is None:
        with TemporaryDirectory(prefix="magscope-doc-capture-") as settings_dir:
            with _isolated_qsettings(Path(settings_dir)):
                results = capture_assets(
                    output_dir,
                    dry_run=args.dry_run,
                    skip_gif=args.skip_gif,
                    visible_capture=args.visible,
                )
    else:
        with _isolated_qsettings(Path(args.settings_dir)):
            results = capture_assets(
                output_dir,
                dry_run=args.dry_run,
                skip_gif=args.skip_gif,
                visible_capture=args.visible,
            )

    action = "Would write" if args.dry_run else "Wrote"
    print(f"{action} {len(results)} documentation capture asset(s):")
    for result in results:
        print(f"- {result.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
