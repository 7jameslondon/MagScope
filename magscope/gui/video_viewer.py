import logging
import os
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QPoint, QPointF, QRectF, QSize, pyqtSignal, QTimer
from PyQt6.QtGui import QPixmap, QImage, QBrush, QColor, QCursor, QPen, QPainter
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QFrame, QGraphicsPixmapItem, QGraphicsItem

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
        self.crosshairs = []

        self._mini_map_visible = False
        self._mini_map_pixmap = QPixmap()
        self._mini_map_image_rect = QRectF()
        self._mini_map_viewport_rect = QRectF()
        self._mini_map_size = QSize(180, 140)
        self._mini_map_outer_margin = 12
        self._mini_map_inner_margin = 8
        self._mini_map_repaint_pending = False

        self._debug_enabled = self._should_enable_debug_logging()
        self._debug_logger = logging.getLogger("magscope.video_viewer")
        if self._debug_enabled:
            self._configure_debug_logger()
            self._debug("VideoViewer initialized", scale_factor=scale_factor)

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
        self.reset_view(round(self.scale_factor**self._zoom))
        self._mini_map_pixmap = default_pixmap

    def has_image(self):
        return not self._empty

    def reset_view(self, scale=1):
        self._debug("reset_view invoked", scale=scale, has_image=self.has_image())
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
        self._update_mini_map()

    def clear_image(self):
        self._empty = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._image.setPixmap(QPixmap())
        self.reset_view(round(self.scale_factor**self._zoom))
        self._mini_map_pixmap = QPixmap()
        self._hide_mini_map()

    def set_pixmap(self, pixmap):
        if pixmap is None:
            pixmap = QPixmap()
        self._image.setPixmap(pixmap)
        self._empty = pixmap.isNull()
        self._mini_map_pixmap = pixmap
        self._update_mini_map(force_repaint=True)

    def zoom_level(self):
        return self._zoom

    def zoom(self, step):
        zoom = max(0, self._zoom + (step := int(step)))
        self._debug("zoom requested", step=step, new_zoom=zoom, current_zoom=self._zoom)
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
            self._update_mini_map()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self._debug("wheel event", delta=delta)
        self.zoom(delta and delta // abs(delta))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._debug("resizeEvent", size=event.size())
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

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._update_mini_map()

    def _update_mini_map(self, force_repaint=False):
        self._debug(
            "_update_mini_map",
            has_image=self.has_image(),
            zoom=self._zoom,
            image_null=self._image.pixmap().isNull(),
        )
        if not self.has_image() or self._zoom == 0:
            self._hide_mini_map()
            return

        image_rect = self._image.mapRectToScene(self._image.boundingRect())
        viewport_polygon = self.mapToScene(self.viewport().rect())
        viewport_rect = viewport_polygon.boundingRect()

        if image_rect.isNull() or viewport_rect.isNull():
            self._hide_mini_map()
            return

        geometry_changed = (
            image_rect != self._mini_map_image_rect
            or viewport_rect != self._mini_map_viewport_rect
        )

        self._mini_map_image_rect = image_rect
        self._mini_map_viewport_rect = viewport_rect
        self._debug(
            "mini map geometry",
            image_rect=self._rect_to_tuple(image_rect),
            viewport_rect=self._rect_to_tuple(viewport_rect),
        )
        became_visible = False
        if not self._mini_map_visible:
            self._mini_map_visible = True
            became_visible = True

        if geometry_changed or force_repaint or became_visible:
            self._request_mini_map_repaint()

    def _hide_mini_map(self):
        was_visible = self._mini_map_visible or not self._mini_map_image_rect.isNull() or not self._mini_map_viewport_rect.isNull()
        self._mini_map_visible = False
        self._mini_map_image_rect = QRectF()
        self._mini_map_viewport_rect = QRectF()
        self._debug("mini map hidden", was_visible=was_visible)
        if was_visible:
            self._request_mini_map_repaint()

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        self._paint_mini_map(painter)

    def _paint_mini_map(self, painter):
        if not self._mini_map_visible:
            return

        pixmap = self._mini_map_pixmap
        if pixmap.isNull() or self._mini_map_image_rect.isNull() or self._mini_map_viewport_rect.isNull():
            self._debug(
                "mini map paint skipped",
                pixmap_null=pixmap.isNull(),
                image_rect_null=self._mini_map_image_rect.isNull(),
                viewport_rect_null=self._mini_map_viewport_rect.isNull(),
            )
            return

        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        view_rect = self.viewport().rect()
        width = self._mini_map_size.width()
        height = self._mini_map_size.height()
        x = view_rect.width() - width - self._mini_map_outer_margin
        y = self._mini_map_outer_margin
        overlay_rect = QRectF(x, y, width, height)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 170))
        painter.drawRoundedRect(overlay_rect, 8, 8)

        available_rect = overlay_rect.adjusted(
            self._mini_map_inner_margin,
            self._mini_map_inner_margin,
            -self._mini_map_inner_margin,
            -self._mini_map_inner_margin,
        )

        available_size = QSize(
            max(1, int(available_rect.width())),
            max(1, int(available_rect.height())),
        )
        scaled = pixmap.scaled(
            available_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        target_rect = QRectF(
            available_rect.x() + (available_rect.width() - scaled.width()) / 2,
            available_rect.y() + (available_rect.height() - scaled.height()) / 2,
            scaled.width(),
            scaled.height(),
        )
        painter.drawPixmap(target_rect, scaled)

        if self._mini_map_image_rect.width() == 0 or self._mini_map_image_rect.height() == 0:
            painter.restore()
            return

        visible = self._mini_map_viewport_rect.intersected(self._mini_map_image_rect)
        if visible.isNull():
            painter.restore()
            return

        rel_x = (visible.x() - self._mini_map_image_rect.x()) / self._mini_map_image_rect.width()
        rel_y = (visible.y() - self._mini_map_image_rect.y()) / self._mini_map_image_rect.height()
        rel_w = visible.width() / self._mini_map_image_rect.width()
        rel_h = visible.height() / self._mini_map_image_rect.height()

        highlight_rect = QRectF(
            target_rect.x() + rel_x * target_rect.width(),
            target_rect.y() + rel_y * target_rect.height(),
            rel_w * target_rect.width(),
            rel_h * target_rect.height(),
        )

        pen = QPen(QColor(255, 255, 255, 220))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(highlight_rect)
        painter.restore()

    def _request_mini_map_repaint(self):
        if self._mini_map_repaint_pending:
            return

        viewport = self.viewport()
        if viewport is None:
            return

        self._mini_map_repaint_pending = True

        def trigger():
            self._mini_map_repaint_pending = False
            vp = self.viewport()
            if vp is not None:
                vp.update()

        QTimer.singleShot(0, trigger)

    def _should_enable_debug_logging(self):
        value = os.environ.get("MAGSCOPE_VIDEO_VIEWER_DEBUG", "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _configure_debug_logger(self):
        if getattr(self._debug_logger, "_magscope_configured", False):
            return
        self._debug_logger.setLevel(logging.DEBUG)
        log_path = Path.cwd() / "video_viewer_debug.log"
        handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self._debug_logger.addHandler(handler)
        self._debug_logger._magscope_configured = True
        self._debug_logger.debug("Debug logging enabled. Writing to %s", log_path)

    def _debug(self, message, **context):
        if not self._debug_enabled:
            return
        if context:
            formatted_context = ", ".join(
                f"{key}={value}" for key, value in context.items()
            )
            message = f"{message} | {formatted_context}"
        self._debug_logger.debug(message)

    @staticmethod
    def _rect_to_tuple(rect):
        return (
            round(rect.x(), 2),
            round(rect.y(), 2),
            round(rect.width(), 2),
            round(rect.height(), 2),
        )

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


