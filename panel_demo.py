"""Simple standalone demo window showing MagScope control panels.

This script creates a lightweight stub of the MagScope window manager so that
all of the standard GUI control panels can be instantiated outside of the full
application.  It is intended as a starting point for experimenting with panel
layout and behaviour in isolation.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from magscope import AcquisitionMode
from magscope.gui.windows import Controls


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

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MagScope Panel Demo")
        self.resize(720, 800)

        self.manager = DemoWindowManager()
        self.controls = Controls(self.manager)
        self.manager.controls = self.controls

        scroll_area = QScrollArea()
        scroll_area.setWidget(self.controls)
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.addWidget(scroll_area)
        self.setCentralWidget(container)


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
