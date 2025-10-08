import numpy as np
from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage, QBrush, QColor, QCursor, QFont, QPen
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QFrame, QGraphicsPixmapItem, QGraphicsItem
import time

class VideoViewer(QGraphicsView):
    coordinatesChanged: 'pyqtSignal' = pyqtSignal(QPoint)
    clicked: 'pyqtSignal' = pyqtSignal(QPoint)

    def __init__(self, scale_factor=1.25):
        super().__init__()
        self._mouse_start_pos = 0.
        self._mouse_start_time = 0.
        self._zoom = 0
        self.scale_factor = scale_factor
        self._empty = True
        self.scene = QGraphicsScene(self)
        self._image = QGraphicsPixmapItem()
        self._image.setShapeMode(QGraphicsPixmapItem.ShapeMode.MaskShape)
        self.scene.addItem(self._image)
        self.setScene(self.scene)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.set_image_to_default()

        self.crosshairs = []

    def plot(self, x, y, size):
        """
        Plot precise, lightweight cross+circle markers at each (x, y).
        """
        self.clear_crosshairs()

        color = QColor("red")
        radius = size / 2
        thickness = max(1.0, size / 10)
        offset = 0.5

        for xi, yi in zip(x, y):
            marker = CrossCircleItem(xi+offset, yi+offset, radius=radius, color=color, thickness=thickness)
            self.scene.addItem(marker)
            self.crosshairs.append(marker)

    def clear_crosshairs(self):
        """Remove all crosshairs"""
        for ch in self.crosshairs:
            self.scene.removeItem(ch)
        self.crosshairs.clear()

    def set_image_to_default(self):
        width = 128
        default_image = np.zeros((width, width), dtype=np.uint8)
        default_image[1::2, 1::2] = 255
        default_pixmap = QPixmap.fromImage(
            QImage(default_image, width, width,
                   QImage.Format.Format_Grayscale8))
        self._empty = False
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._image.setPixmap(default_pixmap)
        self.reset_view(round(self.scale_factor**self._zoom))

    def has_image(self):
        return not self._empty

    def reset_view(self, scale=1):
        rect = QRectF(self._image.pixmap().rect())
        if not rect.isNull():
            self.setSceneRect(rect)
            if (scale := max(1, scale)) == 1:
                self._zoom = 0
            if self.has_image():
                unity = self.transform().mapRect(QRectF(0, 0, 1, 1))
                self.scale(1 / unity.width(), 1 / unity.height())
                viewrect = self.viewport().rect()
                scenerect = self.transform().mapRect(rect)
                factor = min(viewrect.width() / scenerect.width(),
                             viewrect.height() / scenerect.height()) * scale
                self.scale(factor, factor)
                self.centerOn(self._image)
                self.update_coordinates()

    def clear_image(self):
        self._empty = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._image.setPixmap(QPixmap())
        self.reset_view(round(self.scale_factor**self._zoom))

    def set_pixmap(self, pixmap):
        self._image.setPixmap(pixmap)

    def zoom_level(self):
        return self._zoom

    def zoom(self, step):
        zoom = max(0, self._zoom + (step := int(step)))
        if zoom != self._zoom:
            self._zoom = zoom
            if self._zoom > 0:
                if step > 0:
                    factor = self.scale_factor**step
                else:
                    factor = 1 / self.scale_factor**abs(step)
                self.scale(factor, factor)
            else:
                self.reset_view()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self.zoom(delta and delta // abs(delta))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reset_view()

    def toggle_drag_mode(self):
        if self.dragMode() == QGraphicsView.DragMode.ScrollHandDrag:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        elif not self._image.pixmap().isNull():
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def update_coordinates(self, pos=None):
        if self._image.isUnderMouse():
            if pos is None:
                pos = self.mapFromGlobal(QCursor.pos())
            point = self.mapToScene(pos).toPoint()
        else:
            point = QPoint()
        self.coordinatesChanged.emit(point)

    def mouseMoveEvent(self, event):
        self.update_coordinates(event.position().toPoint())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.coordinatesChanged.emit(QPoint())
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._mouse_start_pos = event.position().toPoint()
        self._mouse_start_time = time.time()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        duration = time.time() - self._mouse_start_time
        if duration < 0.5:
            if self._image.isUnderMouse() and event.button(
            ) == Qt.MouseButton.LeftButton:
                mouse_move_dist = event.position().toPoint(
                ) - self._mouse_start_pos
                mouse_move_dist = mouse_move_dist.x() * mouse_move_dist.x(
                ) + mouse_move_dist.y() * mouse_move_dist.y()
                if mouse_move_dist < 32:
                    point = self.mapToScene(
                        event.position().toPoint()).toPoint()
                    self.clicked.emit(point)
        super().mouseReleaseEvent(event)

class CrossCircleItem(QGraphicsItem):
    """A lightweight, centered ⊕-style marker drawn with simple geometry."""
    def __init__(self, x, y, radius=6.0, color=QColor("red"), thickness=1.0, fixed_size=True):
        super().__init__()
        self.radius = radius
        self.color = color
        self.thickness = thickness
        self.setPos(x, y)

        # Keeps marker size constant when zooming, optional
        if fixed_size:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)

    def boundingRect(self):
        r = self.radius + self.thickness
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, option, widget):
        pen = QPen(self.color, self.thickness)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        r = int(self.radius)
        # Circle outline
        painter.drawEllipse(QPointF(0, 0), r, r)
        # Crosshair lines
        painter.drawLine(-r, 0, r, 0)
        painter.drawLine(0, -r, 0, r)