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
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QScrollArea,
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

from magscope.gui.panel_layout import PanelLayoutManager, ReorderableColumn


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
        self.columns: "OrderedDict[str, ReorderableColumn]" = OrderedDict(
            [
                ("left", ReorderableColumn("left", pinned_ids={"HelpPanel"})),
                ("right", ReorderableColumn("right")),
            ]
        )
        for column in self.columns.values():
            column.setFixedWidth(300)

        self.layout_manager = PanelLayoutManager(
            self.settings,
            self.SETTINGS_GROUP,
            self.columns,
        )

        self._create_panels()

        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        for column in self.columns.values():
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

        column_names = list(self.columns.keys())

        for panel_id, widget, column_name, draggable in definitions:
            self.panels[panel_id] = widget
            self.layout_manager.register_panel(
                panel_id,
                widget,
                column_name,
                draggable=draggable,
            )

        for control_factory, column in self.manager.controls_to_add:
            widget = control_factory(self.manager)
            panel_id = widget.__class__.__name__
            if isinstance(column, int):
                column_name = column_names[min(max(column, 0), len(column_names) - 1)]
            else:
                column_name = str(column)
                if column_name not in self.columns:
                    column_name = column_names[0]
            self.panels[panel_id] = widget
            self.layout_manager.register_panel(panel_id, widget, column_name)

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
