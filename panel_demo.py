"""Simple standalone demo window showing MagScope control panels.

This script creates a lightweight stub of the MagScope window manager so that
all of the standard GUI control panels can be instantiated outside of the full
application.  Panels can be rearranged between the two columns by dragging the
title area of each panel (with the exception of the Help panel, which remains
anchored to the top of the left column) and the last arrangement is stored with
``QSettings`` so it is restored the next time the demo is launched.  It is
intended as a starting point for experimenting with panel layout and behaviour
in isolation.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
from PyQt6.QtCore import QPoint, Qt, QSettings, QMimeData, QEvent, QObject
from PyQt6.QtGui import QDrag, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from magscope import AcquisitionMode
from magscope.gui.controls import (
    AcquisitionPanel,
    BeadSelectionPanel,
    CameraPanel,
    ControlPanelBase,
    HelpPanel,
    HistogramPanel,
    PlotSettingsPanel,
    ProfilePanel,
    ScriptPanel,
    StatusPanel,
    XYLockPanel,
    ZLockPanel,
    ZLUTGenerationPanel,
)


class DummySignal:
    """Minimal Qt-like signal object used by the demo plot worker."""

    def __init__(self, name: str):
        self._name = name

    def emit(self, *args: Any, **kwargs: Any) -> None:
        """Log emitted values so the demo remains transparent."""
        print(f"Signal '{self._name}' emitted with args={args} kwargs={kwargs}")


@dataclass
class DemoPlot:
    ylabel: str


class DemoPlotWorker:
    """Subset of the real plot worker required by :class:`PlotSettingsPanel`."""

    def __init__(self) -> None:
        self.plots = [DemoPlot("Position (nm)"), DemoPlot("Force (pN)")]
        self.limits_signal = DummySignal("limits")
        self.selected_bead_signal = DummySignal("selected_bead")
        self.reference_bead_signal = DummySignal("reference_bead")


class DemoVideoBuffer:
    """Placeholder for the video buffer used by several panels."""

    buffer_size: int = 32 * 1024 * 1024  # 32 MB
    image_shape: tuple[int, int] = (480, 640)


class DemoProfilesBuffer:
    """Simple container that returns a static bead profile trace."""

    def peak_unsorted(self) -> np.ndarray:
        now = time.time()
        # Columns: timestamp, bead id, profile values...
        return np.array([[now, 0, 1.0, 0.8, 0.6, 0.4, 0.2]])


class DemoCameraType:
    """Lightweight object that mimics camera metadata used by the panels."""

    def __init__(self) -> None:
        self.settings: Iterable[str] = ("Exposure", "Gain", "Frame Rate")
        self.dtype = np.uint16
        self.bits = 16
        self.nm_per_px = 100.0


class DemoWindowManager:
    """Stub window manager that satisfies panel dependencies."""

    def __init__(self) -> None:
        self._acquisition_on = False
        self._acquisition_mode = AcquisitionMode.TRACK
        self._acquisition_dir_on = False
        self._bead_rois: dict[int, Any] = {}
        self.beads_in_view_on = False
        self.beads_in_view_count = 1
        self.beads_in_view_marker_size = 20
        self.camera_type = DemoCameraType()
        self.controls_to_add: list[tuple[Any, int]] = []
        self.plot_worker = DemoPlotWorker()
        self.profiles_buffer = DemoProfilesBuffer()
        self.selected_bead = 0
        self.settings = {
            "bead roi width": 32,
            "xy-lock default interval": 5.0,
            "xy-lock default max": 4.0,
            "z-lock default interval": 2.5,
            "z-lock default max": 500.0,
        }
        self.video_buffer = DemoVideoBuffer()

    def send_ipc(self, message: Any) -> None:
        """Log outgoing IPC calls that panels would normally trigger."""
        print(f"IPC message sent: {message}")

    def clear_beads(self) -> None:
        print("Clear beads requested")

    def lock_beads(self, value: bool) -> None:
        print(f"Lock beads set to {value}")


PANEL_MIME_TYPE = "application/x-magscope-panel"


class _TitleDragFilter(QObject):
    """Convert title-area drags into wrapper move operations."""

    def __init__(self, wrapper: "PanelWrapper", target: QWidget) -> None:
        super().__init__(target)
        self._wrapper = wrapper
        self.target = target
        self._drag_start = QPoint()
        self._dragging = False

    def eventFilter(self, obj, event):  # type: ignore[override]
        if event.type() == QEvent.Type.Enter:
            self.target.setCursor(Qt.CursorShape.OpenHandCursor)
        elif event.type() == QEvent.Type.Leave:
            if not self._dragging:
                self.target.unsetCursor()
        elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            self._dragging = False
            self.target.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.type() == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.position().toPoint() - self._drag_start).manhattanLength()
            if distance >= QApplication.startDragDistance():
                if isinstance(self.target, QPushButton):
                    self.target.setDown(False)
                self._dragging = True
                self._wrapper.start_drag()
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self.target.setCursor(Qt.CursorShape.OpenHandCursor)
            if self._dragging:
                self._dragging = False
                return True
        return QObject.eventFilter(self, obj, event)

    def drag_finished(self) -> None:
        if self._dragging:
            self._dragging = False
        self.target.setCursor(Qt.CursorShape.OpenHandCursor)


class PanelWrapper(QFrame):
    """Wrap a panel widget and make its title initiate drag-and-drop."""

    def __init__(self, panel_id: str, widget: QWidget, *, draggable: bool = True) -> None:
        super().__init__()
        self.panel_id = panel_id
        self.panel_widget = widget
        self.column: ReorderableColumn | None = None
        self._drag_filters: list[_TitleDragFilter] = []
        self.draggable = draggable

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName(f"PanelWrapper_{panel_id}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(widget)

        if self.draggable:
            self._attach_title_drag()

    def _attach_title_drag(self) -> None:
        groupbox = getattr(self.panel_widget, "groupbox", None)
        toggle_button = getattr(groupbox, "toggle_button", None) if groupbox is not None else None
        if isinstance(toggle_button, QWidget):
            self._register_drag_source(toggle_button)
            title_container = toggle_button.parentWidget()
            if isinstance(title_container, QWidget) and title_container is not toggle_button:
                self._register_drag_source(title_container)
            return

        # Fallback for widgets that do not expose a CollapsibleGroupBox title
        self._register_drag_source(self.panel_widget)

    def _register_drag_source(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        for existing in self._drag_filters:
            if existing.target is widget:
                return
        drag_filter = _TitleDragFilter(self, widget)
        widget.installEventFilter(drag_filter)
        self._drag_filters.append(drag_filter)

    def start_drag(self) -> None:
        if not self.draggable:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(PANEL_MIME_TYPE, self.panel_id.encode("utf-8"))
        drag.setMimeData(mime)
        drag.setHotSpot(QPoint(self.width() // 2, 0))

        # Provide lightweight visual feedback.
        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        self.render(pixmap)
        drag.setPixmap(pixmap)

        drag.exec(Qt.DropAction.MoveAction)

        for drag_filter in self._drag_filters:
            drag_filter.drag_finished()


class ReorderableColumn(QWidget):
    """Vertical column of draggable panels with drop support."""

    def __init__(self, name: str, pinned_ids: Iterable[str] | None = None) -> None:
        super().__init__()
        self.name = name
        self.setAcceptDrops(True)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self._placeholder: QFrame | None = None
        self._pinned_ids = set(pinned_ids or ())

    def panels(self) -> list[PanelWrapper]:
        widgets: list[PanelWrapper] = []
        for index in range(self._layout.count() - 1):  # Exclude stretch
            item = self._layout.itemAt(index)
            widget = item.widget()
            if isinstance(widget, PanelWrapper):
                widgets.append(widget)
        return widgets

    def panel_ids(self) -> list[str]:
        return [wrapper.panel_id for wrapper in self.panels()]

    def add_panel(self, wrapper: PanelWrapper, index: int | None = None) -> None:
        if wrapper.column is self:
            current_index = self._layout.indexOf(wrapper)
            target_index = self._constrain_index(wrapper, self._target_index(index))
            if current_index != target_index:
                self._layout.removeWidget(wrapper)
                self._layout.insertWidget(target_index, wrapper)
            return

        if wrapper.column is not None:
            wrapper.column.remove_panel(wrapper)

        wrapper.setParent(self)
        wrapper.column = self
        constrained_index = self._constrain_index(wrapper, self._target_index(index))
        self._layout.insertWidget(constrained_index, wrapper)
        wrapper.show()

    def remove_panel(self, wrapper: PanelWrapper) -> None:
        self._layout.removeWidget(wrapper)
        wrapper.column = None

    def _target_index(self, index: int | None) -> int:
        stretch_index = self._layout.count() - 1
        if index is None or index < 0 or index > stretch_index:
            return stretch_index
        return min(index, stretch_index)

    def _drop_index(self, cursor_y: float) -> int:
        for i in range(self._layout.count() - 1):
            item = self._layout.itemAt(i)
            widget = item.widget()
            if widget is None:
                continue
            if cursor_y < widget.y() + widget.height() / 2:
                return i
        return self._layout.count() - 1

    def _locked_prefix_length(self) -> int:
        count = 0
        for i in range(self._layout.count() - 1):
            widget = self._layout.itemAt(i).widget()
            if isinstance(widget, PanelWrapper) and widget.panel_id in self._pinned_ids:
                count += 1
            else:
                break
        return count

    def _constrain_index(self, wrapper: PanelWrapper, index: int) -> int:
        if wrapper.panel_id in self._pinned_ids:
            return 0
        return max(index, self._locked_prefix_length())

    def _constrained_drop_index(self, wrapper: PanelWrapper, cursor_y: float) -> int:
        return self._constrain_index(wrapper, self._drop_index(cursor_y))

    def _ensure_placeholder(self) -> QFrame:
        if self._placeholder is None:
            placeholder = QFrame(self)
            placeholder.setObjectName("panel_drop_placeholder")
            placeholder.setStyleSheet(
                "#panel_drop_placeholder { border: 2px dashed palette(mid); background: transparent; }"
            )
            placeholder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            placeholder.hide()
            self._placeholder = placeholder
        return self._placeholder

    def _update_placeholder(self, wrapper: PanelWrapper | None, cursor_y: float) -> None:
        if wrapper is None:
            self.clear_placeholder()
            return

        placeholder = self._ensure_placeholder()
        height = wrapper.height() or wrapper.sizeHint().height()
        placeholder.setFixedHeight(max(24, height))
        target_index = self._constrained_drop_index(wrapper, cursor_y)
        current_index = self._layout.indexOf(placeholder)
        if current_index == -1:
            self._layout.insertWidget(target_index, placeholder)
        elif current_index != target_index:
            self._layout.removeWidget(placeholder)
            self._layout.insertWidget(target_index, placeholder)
        placeholder.show()

    def clear_placeholder(self) -> None:
        if self._placeholder is None:
            return
        index = self._layout.indexOf(self._placeholder)
        if index != -1:
            self._layout.removeWidget(self._placeholder)
        self._placeholder.hide()

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(PANEL_MIME_TYPE):
            event.acceptProposedAction()
            wrapper = self._wrapper_from_event(event)
            if wrapper is not None:
                self._update_placeholder(wrapper, event.position().y())
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(PANEL_MIME_TYPE):
            event.acceptProposedAction()
            wrapper = self._wrapper_from_event(event)
            if wrapper is not None:
                self._update_placeholder(wrapper, event.position().y())
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self.clear_placeholder()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if not event.mimeData().hasFormat(PANEL_MIME_TYPE):
            event.ignore()
            self.clear_placeholder()
            return

        panel_id = bytes(event.mimeData().data(PANEL_MIME_TYPE)).decode("utf-8")
        window = self.window()
        if isinstance(window, PanelDemoWindow):
            wrapper = window.panel_wrappers.get(panel_id)
            if wrapper is not None:
                drop_index = self._constrained_drop_index(wrapper, event.position().y())
                self.add_panel(wrapper, drop_index)
                window.save_layout()
                event.acceptProposedAction()
                self.clear_placeholder()
                return
        event.ignore()
        self.clear_placeholder()

    def _wrapper_from_event(self, event) -> PanelWrapper | None:
        panel_id_bytes = event.mimeData().data(PANEL_MIME_TYPE)
        if panel_id_bytes.isEmpty():
            return None
        panel_id = bytes(panel_id_bytes).decode("utf-8")
        window = self.window()
        if isinstance(window, PanelDemoWindow):
            return window.panel_wrappers.get(panel_id)
        return None


class PanelDemoWindow(QMainWindow):
    """Main window containing the standard MagScope control panels."""

    SETTINGS_GROUP = "panel_demo"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MagScope Panel Demo")
        self.resize(720, 800)

        self.settings = QSettings("MagScope", "PanelDemo")

        self.manager = DemoWindowManager()

        self.panel_wrappers: dict[str, PanelWrapper] = {}
        self.panels: dict[str, QWidget] = {}
        self.columns = [
            ReorderableColumn("left", pinned_ids={"HelpPanel"}),
            ReorderableColumn("right"),
        ]
        for column in self.columns:
            column.setFixedWidth(300)

        self._create_panels()

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        for column in self.columns:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(column)
            scroll.setFixedWidth(320)
            layout.addWidget(scroll)

        layout.addStretch(1)
        self.setCentralWidget(container)

        self.restore_layout()

    def _create_panels(self) -> None:
        panels: list[tuple[str, QWidget]] = [
            ("HelpPanel", HelpPanel(self.manager)),
            ("StatusPanel", StatusPanel(self.manager)),
            ("CameraPanel", CameraPanel(self.manager)),
            ("AcquisitionPanel", AcquisitionPanel(self.manager)),
            ("HistogramPanel", HistogramPanel(self.manager)),
            ("BeadSelectionPanel", BeadSelectionPanel(self.manager)),
            ("ProfilePanel", ProfilePanel(self.manager)),
            ("PlotSettingsPanel", PlotSettingsPanel(self.manager)),
            ("ZLUTGenerationPanel", ZLUTGenerationPanel(self.manager)),
            ("ScriptPanel", ScriptPanel(self.manager)),
            ("XYLockPanel", XYLockPanel(self.manager)),
            ("ZLockPanel", ZLockPanel(self.manager)),
        ]

        for panel_id, panel_widget in panels:
            self.panels[panel_id] = panel_widget
            self.panel_wrappers[panel_id] = PanelWrapper(
                panel_id,
                panel_widget,
                draggable=panel_id != "HelpPanel",
            )

        self.manager.controls = self

    def restore_layout(self) -> None:
        default_layout = {
            "left": [
                "HelpPanel",
                "StatusPanel",
                "CameraPanel",
                "AcquisitionPanel",
                "HistogramPanel",
                "BeadSelectionPanel",
                "ProfilePanel",
            ],
            "right": [
                "PlotSettingsPanel",
                "ZLUTGenerationPanel",
                "ScriptPanel",
                "XYLockPanel",
                "ZLockPanel",
            ],
        }

        restored_layout: dict[str, list[str]] = {}
        self.settings.beginGroup(self.SETTINGS_GROUP)
        for column_name in ("left", "right"):
            stored = self.settings.value(column_name, defaultValue=None)
            if isinstance(stored, list):
                restored_layout[column_name] = [str(item) for item in stored]
            elif isinstance(stored, str) and stored:
                restored_layout[column_name] = [item for item in stored.split("|") if item]
        self.settings.endGroup()

        # Ensure pinned panels remain in their designated columns.
        pinned_targets = {"HelpPanel": "left"}
        for panel_id, column_name in pinned_targets.items():
            for other_name, layout in restored_layout.items():
                if other_name != column_name:
                    layout[:] = [item for item in layout if item != panel_id]
            target_layout = restored_layout.setdefault(column_name, [])
            if panel_id not in target_layout:
                target_layout.insert(0, panel_id)

        used: set[str] = set()
        for column_name, column in zip(("left", "right"), self.columns):
            column_order = restored_layout.get(column_name, default_layout[column_name])
            for panel_id in column_order:
                wrapper = self.panel_wrappers.get(panel_id)
                if wrapper is None or panel_id in used:
                    continue
                column.add_panel(wrapper)
                used.add(panel_id)

        for column_name, column in zip(("left", "right"), self.columns):
            for panel_id in default_layout[column_name]:
                if panel_id in used:
                    continue
                wrapper = self.panel_wrappers.get(panel_id)
                if wrapper is not None:
                    column.add_panel(wrapper)
                    used.add(panel_id)

        # If new panels were added they may not be in the defaults yet.
        for panel_id, wrapper in self.panel_wrappers.items():
            if panel_id not in used:
                self.columns[0].add_panel(wrapper)
                used.add(panel_id)

    def save_layout(self) -> None:
        self.settings.beginGroup(self.SETTINGS_GROUP)
        for column_name, column in zip(("left", "right"), self.columns):
            self.settings.setValue(column_name, column.panel_ids())
        self.settings.endGroup()
        self.settings.sync()

    def get_panel(self, panel_id: str) -> ControlPanelBase | QWidget | None:
        wrapper = self.panel_wrappers.get(panel_id)
        if wrapper:
            return wrapper.panel_widget
        return None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.save_layout()
        super().closeEvent(event)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # Apply the same stylesheet as the full application if available.
    style_path = os.path.join(os.path.dirname(__file__), "magscope", "gui", "style.qss")
    if os.path.exists(style_path):
        with open(style_path, "r", encoding="utf-8") as fh:
            app.setStyleSheet(fh.read())

    window = PanelDemoWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
