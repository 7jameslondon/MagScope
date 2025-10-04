import numpy as np
from PyQt6.QtCore import Qt, QPoint, QRectF, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage, QBrush, QColor, QCursor, QFont
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QFrame, QGraphicsPixmapItem
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
        Plot crosshairs using numpy arrays of x and y positions.
        Removes old crosshairs and plots new ones.

        Args:
            x: numpy array or list of x positions
            y: numpy array or list of y positions
            size: font size of the crosshairs
        """
        # Remove old crosshairs
        self.clear_crosshairs()

        # Add new crosshairs
        font = QFont('Arial', size)
        font.setWeight(QFont.Weight.Thin)
        qcolor = QColor('red')
        for xi, yi in zip(x, y):
            text = self.scene.addText('⊕')
            text.setFont(font)
            text.setDefaultTextColor(qcolor)

            # Center the '+'
            rect = text.boundingRect()
            text.setPos(xi - rect.width() / 2, yi - rect.height() / 2)

            self.crosshairs.append(text)

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