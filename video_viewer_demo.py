"""Simple demo application showing the MagScope VideoViewer widget."""

from __future__ import annotations

import sys
from typing import Final

import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QMainWindow

from magscope.gui.video_viewer import VideoViewer


class VideoViewerDemo(QMainWindow):
    """Window displaying the existing :class:`VideoViewer` with fake video frames."""

    _FRAME_SIZE: Final[int] = 256
    _FRAME_INTERVAL_MS: Final[int] = 50

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("MagScope Video Viewer Demo")
        self.resize(960, 720)

        self.viewer = VideoViewer()
        self.setCentralWidget(self.viewer)

        self._frame_counter = 0
        self._frame: np.ndarray | None = None
        self._qimage: QImage | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_frame)

        self._update_frame()
        self._timer.start(self._FRAME_INTERVAL_MS)

    def _update_frame(self) -> None:
        size = self._FRAME_SIZE
        # Create a simple animated gradient to simulate changing video frames.
        y = np.arange(size, dtype=np.uint8)[:, None]
        x = np.arange(size, dtype=np.uint8)[None, :]
        frame = (x + y + self._frame_counter) % 256
        self._frame_counter = (self._frame_counter + 3) % 256

        self._frame = np.require(frame, requirements=("C",))
        self._qimage = QImage(
            self._frame.data,
            size,
            size,
            self._frame.strides[0],
            QImage.Format.Format_Grayscale8,
        )
        pixmap = QPixmap.fromImage(self._qimage)
        self.viewer.set_pixmap(pixmap)
        self.viewer.reset_view()


def main() -> int:
    app = QApplication(sys.argv)
    window = VideoViewerDemo()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
