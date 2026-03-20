import time

import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QCursor, QFontMetricsF, QImage, QPainter, QPen, QPixmap, QStaticText
from PyQt6.QtWidgets import (QFrame, QGraphicsPixmapItem, QGraphicsScene,
                             QGraphicsView, QLabel, QPushButton)

from magscope.ui.widgets import BeadGraphic


class VideoViewer(QGraphicsView):
    coordinatesChanged: 'pyqtSignal' = pyqtSignal(QPoint)
    clicked: 'pyqtSignal' = pyqtSignal(QPoint)
    sceneClicked: 'pyqtSignal' = pyqtSignal(QPoint, object)

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
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

        self._overlay_entries: list[tuple[QRectF, QPointF, str, bool, str]] = []
        self._visible_overlay_entries: list[tuple[QRectF, str, bool]] | None = None
        self._visible_label_entries: list[tuple[QPointF, QStaticText, bool]] | None = None
        self._overlay_cache_pixmap = QPixmap()
        self._overlay_cache_dirty = True
        self._overlay_cache_size = QSize()
        self._overlay_cache_device_pixel_ratio = 0.0
        self._static_label_cache: dict[str, QStaticText] = {}
        self._label_metrics = QFontMetricsF(BeadGraphic.LABEL_FONT)
        self._label_ascent = self._label_metrics.ascent()
        self._marker_x = np.empty((0,), dtype=float)
        self._marker_y = np.empty((0,), dtype=float)
        self._marker_size = 0

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

        self._lock_overlay = QLabel(self.viewport())
        self._lock_overlay.setText("🔒")
        lock_font = self._lock_overlay.font()
        lock_font.setPointSize(36)
        self._lock_overlay.setFont(lock_font)
        self._lock_overlay.setStyleSheet(
            "color: rgba(255, 255, 255, 128);"
            "background-color: transparent;"
        )
        self._lock_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        self._lock_overlay.hide()

        self._minimap_base = QPixmap()
        self._fit_scale = 1.0

        self.set_image_to_default()

    def set_bead_overlay(
        self,
        bead_rois: dict[int, tuple[int, int, int, int]],
        active_bead_id: int | None,
        selected_bead_id: int | None,
        reference_bead_id: int | None,
    ) -> None:
        overlay_entries: list[tuple[QRectF, QPointF, str, bool, str]] = []
        for bead_id, roi in bead_rois.items():
            if bead_id == selected_bead_id:
                state = 'selected'
            elif bead_id == reference_bead_id:
                state = 'reference'
            else:
                state = 'default'
            x0, x1, y0, y1 = roi
            overlay_entries.append((
                QRectF(x0, y0, x1 - x0, y1 - y0),
                BeadGraphic.label_scene_position_for_roi(roi),
                state,
                bead_id == active_bead_id,
                str(bead_id),
            ))
        self._overlay_entries = overlay_entries
        self._invalidate_overlay_view_cache()

    def _invalidate_overlay_view_cache(self) -> None:
        self._visible_overlay_entries = None
        self._visible_label_entries = None
        self._overlay_cache_dirty = True
        self._overlay_cache_pixmap = QPixmap()
        self._overlay_cache_size = QSize()
        self._overlay_cache_device_pixel_ratio = 0.0

    def _get_static_label(self, label_text: str) -> QStaticText:
        static_label = self._static_label_cache.get(label_text)
        if static_label is None:
            static_label = QStaticText(label_text)
            static_label.prepare(font=BeadGraphic.LABEL_FONT)
            self._static_label_cache[label_text] = static_label
        return static_label

    def _rebuild_overlay_view_cache(self) -> None:
        if not self._overlay_entries:
            self._visible_overlay_entries = []
            self._visible_label_entries = []
            return

        visible_scene_rect = self.mapToScene(self.viewport().rect()).boundingRect()
        visible_overlay_entries: list[tuple[QRectF, str, bool]] = []
        visible_label_entries: list[tuple[QPointF, QStaticText, bool]] = []

        for roi_rect, label_point, state, is_active, label_text in self._overlay_entries:
            if not is_active and not roi_rect.intersects(visible_scene_rect):
                continue
            view_rect = QRectF(self.mapFromScene(roi_rect).boundingRect())
            visible_overlay_entries.append((view_rect, state, is_active))
            view_point = self.mapFromScene(label_point)
            visible_label_entries.append((
                QPointF(view_point.x(), view_point.y() + self._label_ascent),
                self._get_static_label(label_text),
                is_active,
            ))

        self._visible_overlay_entries = visible_overlay_entries
        self._visible_label_entries = visible_label_entries

    def _rebuild_overlay_cache_pixmap(self) -> None:
        viewport_size = self.viewport().size()
        if viewport_size.isEmpty() or not self._overlay_entries:
            self._overlay_cache_pixmap = QPixmap()
            self._overlay_cache_dirty = False
            self._overlay_cache_size = QSize()
            self._overlay_cache_device_pixel_ratio = 0.0
            return

        if self._visible_overlay_entries is None or self._visible_label_entries is None:
            self._rebuild_overlay_view_cache()

        visible_overlay_entries = self._visible_overlay_entries
        visible_label_entries = self._visible_label_entries
        assert visible_overlay_entries is not None
        assert visible_label_entries is not None

        device_pixel_ratio = self.devicePixelRatioF()
        overlay_pixmap = QPixmap(
            max(1, int(round(viewport_size.width() * device_pixel_ratio))),
            max(1, int(round(viewport_size.height() * device_pixel_ratio))),
        )
        overlay_pixmap.setDevicePixelRatio(device_pixel_ratio)
        overlay_pixmap.fill(Qt.GlobalColor.transparent)

        BeadGraphic._ensure_shared_pens_and_brushes()
        assert BeadGraphic._shared_pens is not None
        assert BeadGraphic._shared_brushes is not None

        painter = QPainter(overlay_pixmap)
        try:
            state_rects: dict[str, list[QRectF]] = {
                'default': [],
                'selected': [],
                'reference': [],
            }
            for roi_rect, state, is_active in visible_overlay_entries:
                if is_active:
                    continue
                state_rects[state].append(roi_rect)

            for state in ('default', 'selected', 'reference'):
                if not state_rects[state]:
                    continue
                painter.setPen(BeadGraphic._shared_pens[state])
                painter.setBrush(BeadGraphic._shared_brushes[state])
                for roi_rect in state_rects[state]:
                    painter.drawRect(roi_rect)

            painter.setFont(BeadGraphic.LABEL_FONT)
            painter.setPen(BeadGraphic.LABEL_COLOR)
            for label_point, label_text, is_active in visible_label_entries:
                if is_active:
                    continue
                painter.drawStaticText(label_point, label_text)
        finally:
            painter.end()

        self._overlay_cache_pixmap = overlay_pixmap
        self._overlay_cache_dirty = False
        self._overlay_cache_size = viewport_size
        self._overlay_cache_device_pixel_ratio = device_pixel_ratio

    def _ensure_overlay_cache_pixmap(self) -> None:
        viewport_size = self.viewport().size()
        if viewport_size.isEmpty() or not self._overlay_entries:
            self._overlay_cache_pixmap = QPixmap()
            self._overlay_cache_dirty = False
            self._overlay_cache_size = QSize()
            self._overlay_cache_device_pixel_ratio = 0.0
            return

        device_pixel_ratio = self.devicePixelRatioF()
        if (
            self._overlay_cache_dirty
            or self._overlay_cache_pixmap.isNull()
            or self._overlay_cache_size != viewport_size
            or self._overlay_cache_device_pixel_ratio != device_pixel_ratio
        ):
            self._rebuild_overlay_cache_pixmap()

    def plot(self, x, y, size):
        self._marker_x = np.asarray(x, dtype=float)
        self._marker_y = np.asarray(y, dtype=float)
        self._marker_size = max(1, int(round(size)))
        self.viewport().update()

    def clear_crosshairs(self):
        self._marker_x = np.empty((0,), dtype=float)
        self._marker_y = np.empty((0,), dtype=float)
        self._marker_size = 0
        self.viewport().update()

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

    def image_scene_rect(self) -> QRectF:
        return QRectF(self._image.pixmap().rect())

    def reset_view(self, scale=1):
        rect = self.image_scene_rect()
        if not rect.isNull():
            self.scene.setSceneRect(rect)
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
        self._invalidate_overlay_view_cache()
        self._refresh_minimap()

    def clear_image(self):
        self._empty = True
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._image.setPixmap(QPixmap())
        self.scene.setSceneRect(QRectF())
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
            rect = self.image_scene_rect()
            self.scene.setSceneRect(rect)
            self.setSceneRect(rect)
        self._refresh_minimap()

    def set_locked_overlay(self, locked: bool):
        if locked:
            self._lock_overlay.show()
        else:
            self._lock_overlay.hide()
        self._layout_lock_overlay()

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
                self._invalidate_overlay_view_cache()
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
        self._layout_lock_overlay()

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
            if self._image.isUnderMouse() and event.button() in (
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.RightButton,
            ):
                mouse_move_dist = event.position().toPoint(
                ) - self._mouse_start_pos
                mouse_move_dist = mouse_move_dist.x() * mouse_move_dist.x(
                ) + mouse_move_dist.y() * mouse_move_dist.y()
                if mouse_move_dist < 32:
                    point = self.mapToScene(
                        event.position().toPoint()).toPoint()
                    if event.button() == Qt.MouseButton.LeftButton:
                        self.clicked.emit(point)
                    self.sceneClicked.emit(point, event.button())
        super().mouseReleaseEvent(event)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._invalidate_overlay_view_cache()
        self._refresh_minimap()

    def _layout_lock_overlay(self):
        if self._lock_overlay.isHidden():
            return

        margin = 10
        size_hint = self._lock_overlay.sizeHint()
        self._lock_overlay.setGeometry(
            margin,
            margin,
            size_hint.width(),
            size_hint.height(),
        )
        self._lock_overlay.raise_()

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

        self._layout_lock_overlay()

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

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)

        if self._overlay_entries:
            self._ensure_overlay_cache_pixmap()
            if not self._overlay_cache_pixmap.isNull():
                painter.save()
                painter.resetTransform()
                painter.drawPixmap(0, 0, self._overlay_cache_pixmap)
                painter.restore()

        if self._marker_size <= 0 or self._marker_x.size == 0 or self._marker_y.size == 0:
            return

        scene_transform = painter.worldTransform()
        painter.save()
        painter.resetTransform()
        marker_pen = QPen(QColor('red'))
        marker_pen.setWidth(1 if self._marker_size <= 3 else 2)
        painter.setPen(marker_pen)

        half_size = max(1, self._marker_size // 2)
        for x, y in zip(self._marker_x, self._marker_y):
            if not rect.contains(x, y):
                continue
            view_point = scene_transform.map(QPointF(float(x), float(y)))
            px = view_point.x()
            py = view_point.y()
            if half_size <= 1:
                painter.drawPoint(QPointF(px, py))
                continue
            painter.drawLine(QPointF(px - half_size, py), QPointF(px + half_size, py))
            painter.drawLine(QPointF(px, py - half_size), QPointF(px, py + half_size))
        painter.restore()
