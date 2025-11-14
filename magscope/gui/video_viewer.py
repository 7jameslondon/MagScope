import time

import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QCursor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (QFrame, QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene,
                             QGraphicsView, QLabel, QPushButton)


class VideoViewer(QGraphicsView):
    coordinatesChanged: 'pyqtSignal' = pyqtSignal(QPoint)
    clicked: 'pyqtSignal' = pyqtSignal(QPoint)

    _MINIMAP_MARGIN = 12
    _MINIMAP_MIN_SIZE = 120
    _MINIMAP_MAX_SIZE = 220
    _MINIMAP_LABEL_SPACING = 6
    _MINIMAP_ZOOM_HEIGHT = 26
    _MINIMAP_BUTTON_SPACING = 6

    def __init__(self, scale_factor=1.25):
        super().__init__()
        self._mouse_start_pos = QPoint()
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

        self.crosshairs = []

        self._minimap_label = QLabel(self.viewport())
        self._minimap_label.setFrameShape(QFrame.Shape.Panel)
        self._minimap_label.setFrameShadow(QFrame.Shadow.Sunken)
        self._minimap_label.setStyleSheet(
            "background-color: rgba(20, 20, 20, 190);"
            "border: 1px solid rgba(255, 255, 255, 120);"
        )
        self._minimap_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._minimap_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._minimap_label.hide()

        self._minimap_zoom_label = QLabel(self.viewport())
        self._minimap_zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._minimap_zoom_label.setStyleSheet(
            "color: white;"
            "background-color: rgba(20, 20, 20, 190);"
            "border: 1px solid rgba(255, 255, 255, 120);"
        )
        self._minimap_zoom_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._minimap_zoom_label.hide()

        self._minimap_reset_button = QPushButton("Reset", self.viewport())
        self._minimap_reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._minimap_reset_button.clicked.connect(lambda: self.reset_view())
        self._minimap_reset_button.hide()

        self._minimap_base = QPixmap()
        self._fit_scale = 1.0

        self.set_image_to_default()

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
        self._minimap_base = default_pixmap
        self.reset_view(round(self.scale_factor**self._zoom))
        self._refresh_minimap()

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
                self._fit_scale = factor if factor > 0 else 1.0
                self.scale(factor, factor)
                self.centerOn(self._image)
                self.update_coordinates()
        self._refresh_minimap()

    def clear_image(self):
        self._empty = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._image.setPixmap(QPixmap())
        self.reset_view(round(self.scale_factor**self._zoom))
        self._minimap_base = QPixmap()
        self._minimap_label.hide()
        self._minimap_zoom_label.hide()
        self._minimap_reset_button.hide()

    def set_pixmap(self, pixmap):
        self._image.setPixmap(pixmap)
        if not pixmap.isNull():
            self._empty = False
            self._minimap_base = pixmap
        self._refresh_minimap()

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
        self._refresh_minimap()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self.zoom(delta and delta // abs(delta))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reset_view()
        self._refresh_minimap()

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

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._refresh_minimap()

    def _refresh_minimap(self):
        if self._minimap_base.isNull() or self._zoom <= 0:
            self._minimap_label.hide()
            self._minimap_zoom_label.hide()
            self._minimap_reset_button.hide()
            return

        if not self._layout_minimap():
            self._minimap_label.hide()
            self._minimap_zoom_label.hide()
            self._minimap_reset_button.hide()
            return

        label_size = self._minimap_label.size()
        if label_size.width() <= 0 or label_size.height() <= 0:
            self._minimap_label.hide()
            self._minimap_zoom_label.hide()
            self._minimap_reset_button.hide()
            return

        scaled_size = self._minimap_base.size().scaled(
            label_size, Qt.AspectRatioMode.KeepAspectRatio)
        if scaled_size.isEmpty():
            self._minimap_label.hide()
            self._minimap_zoom_label.hide()
            self._minimap_reset_button.hide()
            return

        minimap_pixmap = QPixmap(label_size)
        minimap_pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(minimap_pixmap)
        offset_x = (label_size.width() - scaled_size.width()) // 2
        offset_y = (label_size.height() - scaled_size.height()) // 2
        scaled_pixmap = self._minimap_base.scaled(
            scaled_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawPixmap(offset_x, offset_y, scaled_pixmap)

        highlight_rect = self._compute_highlight_rect(
            scaled_size, offset_x, offset_y)
        if highlight_rect is not None and not highlight_rect.isEmpty():
            pen = QPen(QColor(255, 0, 0, 200))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(highlight_rect)

        painter.end()
        self._minimap_label.setPixmap(minimap_pixmap)
        self._minimap_label.show()

        zoom_percent = self._current_zoom_percent()
        if zoom_percent is not None:
            self._minimap_zoom_label.setText(f"{zoom_percent:.0f}%")
            self._minimap_zoom_label.show()
            self._minimap_reset_button.show()
        else:
            self._minimap_zoom_label.hide()
            self._minimap_reset_button.hide()

    def _layout_minimap(self):
        viewport_size = self.viewport().size()
        if viewport_size.isEmpty():
            return False

        size = min(
            max(
                min(viewport_size.width(), viewport_size.height()) // 4,
                self._MINIMAP_MIN_SIZE,
            ),
            self._MINIMAP_MAX_SIZE,
        )
        zoom_height = max(
            self._minimap_zoom_label.sizeHint().height(),
            self._MINIMAP_ZOOM_HEIGHT,
            self._minimap_reset_button.sizeHint().height(),
        )
        required_height = (
            size
            + self._MINIMAP_LABEL_SPACING
            + zoom_height
            + 2 * self._MINIMAP_MARGIN
        )
        if (
            viewport_size.width() <= 2 * self._MINIMAP_MARGIN
            or viewport_size.height() <= required_height
        ):
            return False

        top = self._MINIMAP_MARGIN
        left = viewport_size.width() - size - self._MINIMAP_MARGIN
        self._minimap_label.setGeometry(left, top, size, size)
        available_width = size
        button_hint_width = self._minimap_reset_button.sizeHint().width()
        button_width = min(button_hint_width, available_width)
        spacing = (
            self._MINIMAP_BUTTON_SPACING if available_width > button_width else 0
        )
        label_width = available_width - button_width - spacing
        if label_width <= 0:
            label_width = max(available_width // 2, 1)
            button_width = available_width - label_width - spacing
        if label_width <= 0 or button_width <= 0:
            return False

        row_top = top + size + self._MINIMAP_LABEL_SPACING
        self._minimap_zoom_label.setGeometry(
            left,
            row_top,
            label_width,
            zoom_height,
        )
        self._minimap_reset_button.setGeometry(
            left + label_width + spacing,
            row_top,
            button_width,
            zoom_height,
        )
        self._minimap_label.raise_()
        self._minimap_zoom_label.raise_()
        self._minimap_reset_button.raise_()
        return True

    def _compute_highlight_rect(self, scaled_size, offset_x, offset_y):
        if self._image.pixmap().isNull():
            return None

        viewport_rect = self.viewport().rect()
        if viewport_rect.isNull():
            return None

        scene_polygon = self.mapToScene(viewport_rect)
        scene_rect = scene_polygon.boundingRect()
        image_rect = QRectF(self._image.pixmap().rect())
        scene_rect = scene_rect.intersected(image_rect)
        if scene_rect.isEmpty():
            return QRectF()

        scale_x = scaled_size.width() / image_rect.width()
        scale_y = scaled_size.height() / image_rect.height()

        x = (scene_rect.left() - image_rect.left()) * scale_x + offset_x
        y = (scene_rect.top() - image_rect.top()) * scale_y + offset_y
        width = scene_rect.width() * scale_x
        height = scene_rect.height() * scale_y

        highlight = QRectF(x, y, width, height)
        label_rect = QRectF(0, 0, self._minimap_label.width(), self._minimap_label.height())
        return highlight.intersected(label_rect)

    def _current_zoom_percent(self):
        if self._fit_scale <= 0:
            return None
        current_scale = self.transform().m11()
        if current_scale <= 0:
            return None
        return (current_scale / self._fit_scale) * 100

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
