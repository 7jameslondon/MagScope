"""Simple demo application showing the MagScope VideoViewer widget."""

from __future__ import annotations

import sys
import time
from typing import Final

import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QCursor, QImage, QPixmap, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QMainWindow,
)


class CrossCircleItem(QGraphicsItem):
    """A lightweight, centered ⊕-style marker drawn with simple geometry."""

    def __init__(
        self,
        x: float,
        y: float,
        radius: float = 6.0,
        color: QColor = QColor("red"),
        thickness: float = 1.0,
        fixed_size: bool = True,
    ) -> None:
        super().__init__()
        self.radius = radius
        self.color = color
        self.thickness = thickness
        self.setPos(x, y)

        if fixed_size:
            self.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations,
                True,
            )

    def boundingRect(self) -> QRectF:
        r = self.radius + self.thickness
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, option, widget) -> None:  # type: ignore[override]
        pen = QPen(self.color, self.thickness)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        r = int(self.radius)
        painter.drawEllipse(QPointF(0, 0), r, r)
        painter.drawLine(-r, 0, r, 0)
        painter.drawLine(0, -r, 0, r)


class NewVideoViewer(QGraphicsView):
    """Local copy of :class:`VideoViewer` for experimentation in the demo."""

    coordinatesChanged: "pyqtSignal" = pyqtSignal(QPoint)
    clicked: "pyqtSignal" = pyqtSignal(QPoint)

    def __init__(self, scale_factor: float = 1.25) -> None:
        super().__init__()
        self._mouse_start_pos = QPoint()
        self._mouse_start_time = 0.0
        self._zoom = 0
        self.scale_factor = scale_factor
        self._empty = True
        self.scene = QGraphicsScene(self)
        self._image = QGraphicsPixmapItem()
        self._image.setShapeMode(QGraphicsPixmapItem.ShapeMode.MaskShape)
        self.scene.addItem(self._image)
        self.setScene(self.scene)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse
        )
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.set_image_to_default()

        self.crosshairs: list[CrossCircleItem] = []

    def plot(self, x, y, size) -> None:
        self.clear_crosshairs()

        color = QColor("red")
        radius = size / 2
        thickness = max(1.0, size / 10)
        offset = 0.5

        for xi, yi in zip(x, y):
            marker = CrossCircleItem(
                xi + offset,
                yi + offset,
                radius=radius,
                color=color,
                thickness=thickness,
            )
            self.scene.addItem(marker)
            self.crosshairs.append(marker)

    def clear_crosshairs(self) -> None:
        for crosshair in self.crosshairs:
            self.scene.removeItem(crosshair)
        self.crosshairs.clear()

    def set_image_to_default(self) -> None:
        width = 128
        default_image = np.zeros((width, width), dtype=np.uint8)
        default_image[1::2, 1::2] = 255
        default_pixmap = QPixmap.fromImage(
            QImage(default_image, width, width, QImage.Format.Format_Grayscale8)
        )
        self._empty = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._image.setPixmap(default_pixmap)
        self.reset_view(round(self.scale_factor**self._zoom))

    def has_image(self) -> bool:
        return not self._empty

    def reset_view(self, scale: int = 1) -> None:
        rect = QRectF(self._image.pixmap().rect())
        if rect.isNull():
            return

        self.setSceneRect(rect)
        scale = max(1, scale)
        if scale == 1:
            self._zoom = 0
        if not self.has_image():
            return

        unity = self.transform().mapRect(QRectF(0, 0, 1, 1))
        self.scale(1 / unity.width(), 1 / unity.height())
        viewrect = self.viewport().rect()
        scenerect = self.transform().mapRect(rect)
        factor = min(
            viewrect.width() / scenerect.width(),
            viewrect.height() / scenerect.height(),
        ) * scale
        self.scale(factor, factor)
        self.centerOn(self._image)
        self.update_coordinates()

    def clear_image(self) -> None:
        self._empty = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._image.setPixmap(QPixmap())
        self.reset_view(round(self.scale_factor**self._zoom))

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._image.setPixmap(pixmap)

    def zoom_level(self) -> int:
        return self._zoom

    def zoom(self, step: int) -> None:
        step = int(step)
        zoom = max(0, self._zoom + step)
        if zoom == self._zoom:
            return

        self._zoom = zoom
        if self._zoom > 0:
            if step > 0:
                factor = self.scale_factor**step
            else:
                factor = 1 / self.scale_factor ** abs(step)
            self.scale(factor, factor)
        else:
            self.reset_view()

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        step = delta and delta // abs(delta)
        self.zoom(step)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.reset_view()

    def toggle_drag_mode(self) -> None:
        if self.dragMode() == QGraphicsView.DragMode.ScrollHandDrag:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        elif not self._image.pixmap().isNull():
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def update_coordinates(self, pos: QPoint | None = None) -> None:
        if self._image.isUnderMouse():
            if pos is None:
                pos = self.mapFromGlobal(QCursor.pos())
            point = self.mapToScene(pos).toPoint()
        else:
            point = QPoint()
        self.coordinatesChanged.emit(point)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        self.update_coordinates(event.position().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.coordinatesChanged.emit(QPoint())
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self._mouse_start_pos = event.position().toPoint()
        self._mouse_start_time = time.time()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        duration = time.time() - self._mouse_start_time
        if duration < 0.5:
            if (
                self._image.isUnderMouse()
                and event.button() == Qt.MouseButton.LeftButton
            ):
                delta_pos = event.position().toPoint() - self._mouse_start_pos
                dist_sq = delta_pos.x() * delta_pos.x() + delta_pos.y() * delta_pos.y()
                if dist_sq < 32:
                    point = self.mapToScene(event.position().toPoint()).toPoint()
                    self.clicked.emit(point)
        super().mouseReleaseEvent(event)


class VideoViewerDemo(QMainWindow):
    """Window displaying the local :class:`NewVideoViewer` with fake video frames."""

    _FRAME_SIZE: Final[int] = 256
    _FRAME_INTERVAL_MS: Final[int] = 50

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("MagScope Video Viewer Demo")
        self.resize(960, 720)

        self.viewer = NewVideoViewer()
        self.setCentralWidget(self.viewer)

        self._frame_counter = 0
        self._frame: np.ndarray | None = None
        self._qimage: QImage | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_frame)

        self._view_initialized = False

        self._update_frame()
        self._timer.start(self._FRAME_INTERVAL_MS)

    def _update_frame(self) -> None:
        size = self._FRAME_SIZE
        # Create a simple animated gradient to simulate changing video frames.
        y = np.arange(size, dtype=np.uint16)[:, None]
        x = np.arange(size, dtype=np.uint16)[None, :]
        frame = ((x + y + self._frame_counter) % 256).astype(np.uint8, copy=False)
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
        if not self._view_initialized:
            self.viewer.reset_view()
            self._view_initialized = True


def main() -> int:
    app = QApplication(sys.argv)
    window = VideoViewerDemo()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
