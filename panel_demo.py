"""Simple standalone demo window showing MagScope control panels.

This script creates a lightweight stub of the MagScope window manager so that
all of the standard GUI control panels can be instantiated outside of the full
application.  Panels can be rearranged between columns by dragging the
title area of each panel (with the exception of the Help panel, which remains
anchored to the top of the left column).  Dropping a panel onto the "new column"
target at the right-hand side of the window creates an additional column, and
when a column becomes empty it is automatically removed.  The last arrangement is stored with
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
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
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

from magscope.gui.panel_layout import PANEL_MIME_TYPE, PanelLayoutManager, ReorderableColumn, PanelWrapper


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




class AddColumnDropTarget(QFrame):
    """Drop target that creates a new column when a panel is dropped."""

    def __init__(self, window: "PanelDemoWindow") -> None:
        super().__init__()
        self._window = window
        self.setObjectName("add_column_drop_target")
        self.setAcceptDrops(True)
        self.setMinimumWidth(160)
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

        self.setVisible(active)
        if not active:
            self._set_active(False)

    def _set_active(self, active: bool) -> None:
        color = "palette(highlight)" if active else "palette(mid)"
        self.setStyleSheet(
            "#add_column_drop_target { border: 2px dashed %s; border-radius: 6px; }" % color
        )

    def _wrapper_from_event(self, event) -> PanelWrapper | None:
        manager = self._window.layout_manager
        if manager is None:
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
        self._window.create_new_column_with_panel(wrapper)
        event.acceptProposedAction()


class PanelDemoWindow(QMainWindow):
    """Main window containing the standard MagScope control panels."""

    SETTINGS_GROUP = "panel_demo"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MagScope Panel Demo")
        self.resize(720, 800)

        self.settings = QSettings("MagScope", "PanelDemo")
        self.manager = DemoWindowManager()

        self.panels: dict[str, QWidget] = {}
        self._column_scrolls: dict[str, QScrollArea] = {}
        self._column_prefix = "column"
        self._column_counter = 1
        self._base_columns = {"left"}
        self._suppress_layout_callback = False

        self.layout_manager = PanelLayoutManager(
            self.settings,
            self.SETTINGS_GROUP,
            [],
            on_layout_changed=self._on_layout_changed,
            on_drag_active_changed=self._on_drag_active_changed,
        )

        container = QWidget()
        self._columns_layout = QHBoxLayout(container)
        self._columns_layout.setContentsMargins(0, 0, 0, 0)
        self._columns_layout.setSpacing(12)

        self._add_column_target = AddColumnDropTarget(self)
        self._columns_layout.addWidget(self._add_column_target)
        self._columns_layout.addStretch(1)
        self.setCentralWidget(container)

        stored_layout = self.layout_manager.stored_layout()
        self._update_column_counter(stored_layout.keys())

        self._add_column("left", pinned_ids={"HelpPanel"}, index=0)
        for name in stored_layout.keys():
            if name in self.layout_manager.columns:
                continue
            self._add_column(name)
        if "right" not in self.layout_manager.columns and len(self.layout_manager.columns) < 2:
            self._add_column("right")

        self._create_panels()
        self.restore_layout()
        self._prune_empty_columns()

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
        count_before = sum(1 for existing in column_names[:target_index] if existing in self._column_scrolls)
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
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(column)
            scroll.setFixedWidth(320)
            insert_index = self._layout_insert_index(name)
            self._columns_layout.insertWidget(insert_index, scroll)
            self._column_scrolls[name] = scroll
        return column

    def _create_panels(self) -> None:
        self.manager.controls = self

        definitions: list[tuple[str, QWidget, str, bool]] = [
            ("HelpPanel", HelpPanel(self.manager), "left", False),
            ("StatusPanel", StatusPanel(self.manager), "left", True),
            ("CameraPanel", CameraPanel(self.manager), "left", True),
            ("AcquisitionPanel", AcquisitionPanel(self.manager), "left", True),
            ("HistogramPanel", HistogramPanel(self.manager), "left", True),
            ("BeadSelectionPanel", BeadSelectionPanel(self.manager), "left", True),
            ("ProfilePanel", ProfilePanel(self.manager), "left", True),
            ("PlotSettingsPanel", PlotSettingsPanel(self.manager), "right", True),
            ("ZLUTGenerationPanel", ZLUTGenerationPanel(self.manager), "right", True),
            ("ScriptPanel", ScriptPanel(self.manager), "right", True),
            ("XYLockPanel", XYLockPanel(self.manager), "right", True),
            ("ZLockPanel", ZLockPanel(self.manager), "right", True),
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

    def restore_layout(self) -> None:
        self.layout_manager.restore_layout()

    def save_layout(self) -> None:
        self.layout_manager.save_layout()

    def get_panel(self, panel_id: str) -> ControlPanelBase | QWidget | None:
        return self.panels.get(panel_id)

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
