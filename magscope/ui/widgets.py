""""
Miscellaneous custom Qt widgets for the GUI
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import (QEasingCurve, QMimeData, QPoint, QPointF, QPropertyAnimation, QRect,
                          QRectF, QSettings, Qt, QTimer, pyqtSignal)
from PyQt6.QtGui import QBrush, QColor, QDrag, QFont, QPainter, QPalette, QPen, QValidator
from PyQt6.QtWidgets import (QCheckBox, QFrame, QGraphicsItem, QGraphicsRectItem, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                             QSizePolicy, QSplitter, QSplitterHandle, QVBoxLayout, QWidget)

if TYPE_CHECKING:
    from magscope.ui.ui import UIManager


class LabeledLineEditWithValue(QWidget):
    """Horizontally combined QLabel, QLineedit, and a second QLabel to show the value."""

    def __init__(self,
                 *,
                 label_text: str,
                 validator: QValidator = None,
                 widths: tuple[int, int, int] = (0, 0, 0),
                 default=None,
                 callback: callable = None):
        super().__init__()

        # Layout
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)
        self.setLayout(self.layout)

        # Label
        self.label = QLabel(label_text)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.label.setToolTip(label_text)
        if widths[0] > 0:
            self.label.setMaximumWidth(widths[0])
        self.layout.addWidget(self.label)

        # Lineedit
        self.lineedit = QLineEdit()
        if validator:
            self.lineedit.setValidator(validator)
        if callback:
            self.lineedit.editingFinished.connect(callback)  # type: ignore
        if widths[1] > 0:
            self.lineedit.setFixedWidth(widths[1])
        self.layout.addWidget(self.lineedit)

        # Value Label
        self.value_label = QLabel(default)
        if widths[2] > 0:
            self.value_label.setFixedWidth(widths[2])
        self.layout.addWidget(self.value_label)


class LabeledLineEdit(QWidget):
    """Horizontally combined QLabel and QLineedit."""

    def __init__(self,
                 *,
                 label_text: str,
                 widths: tuple[int, int] = (0, 0),
                 default=None,
                 validator: QValidator = None,
                 callback: callable = None):
        super().__init__()

        # Layout
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)
        self.setLayout(self.layout)

        # Label
        self.label = QLabel(label_text)
        if widths[0] > 0:
            self.label.setFixedWidth(widths[0])
        self.layout.addWidget(self.label)

        # Lineedit
        self.lineedit = QLineEdit(default)
        if validator:
            self.lineedit.setValidator(validator)
        if callback:
            self.lineedit.textChanged.connect(callback)  # type: ignore
        if widths[1] > 0:
            self.lineedit.setFixedWidth(widths[1])
        self.layout.addWidget(self.lineedit)


class LabeledCheckbox(QWidget):
    """Horizontally combined QLabel and QCheckbox."""

    def __init__(self,
                 *,
                 label_text: str,
                 widths: tuple[int, int] = (0, 0),
                 default=False,
                 callback: callable = None):
        super().__init__()

        # Layout
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)
        self.setLayout(self.layout)

        # Label
        self.label = QLabel(label_text)
        if widths[0] > 0:
            self.label.setFixedWidth(widths[0])
        self.layout.addWidget(self.label)

        # Checkbox
        self.checkbox = QCheckBox()
        self.checkbox.setChecked(default)
        if callback:
            self.checkbox.toggled.connect(callback) # type: ignore
        if widths[1] > 0:
            self.checkbox.setFixedWidth(widths[1])
        self.checkbox.setMinimumWidth(20)
        self.layout.addWidget(self.checkbox, alignment=Qt.AlignmentFlag.AlignLeft)

        self.layout.addStretch(1)


class LabeledStepperLineEdit(QWidget):
    """Horizontally combined QLabel and QLineedit with a QButton to increment/decrement the value on either side."""

    def __init__(self,
                 *,
                 label_text: str,
                 left_button_text: str,
                 right_button_text: str,
                 widths: tuple[int, int, int, int] = (0, 0, 0, 0),
                 default=None,
                 validator: QValidator = None,
                 callbacks: tuple[callable, callable,
                                  callable] = (None, None, None)):
        super().__init__()

        # Layout
        self.layout = QHBoxLayout()
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)
        self.setLayout(self.layout)

        # Label
        self.name_label = QLabel(label_text)
        if widths[0] > 0:
            self.name_label.setFixedWidth(widths[0])
        self.layout.addWidget(self.name_label)

        # Left Button
        self.left_button = QPushButton(left_button_text)
        self.left_button.clicked.connect(callbacks[0])  # type: ignore
        self.layout.addWidget(self.left_button)

        # Lineedit
        self.lineedit = QLineEdit(default)
        if validator:
            self.lineedit.setValidator(validator)
        if callbacks[1]:
            self.lineedit.editingFinished.connect(callbacks[1])  # type: ignore
        if widths[2] > 0:
            self.lineedit.setFixedWidth(widths[2])
        self.layout.addWidget(self.lineedit)

        # Right Button
        self.right_button = QPushButton(right_button_text)
        self.right_button.clicked.connect(callbacks[2])  # type: ignore
        self.layout.addWidget(self.right_button)


class CollapsibleGroupBox(QGroupBox):
    """A collapsible QGroupBox with the title text as a toggle button to show/hide its content"""

    def __init__(self, title="", collapsed=False):
        super().__init__()

        self.title = title
        self.default_collapsed = collapsed
        self._settings_key = f"{self.title}_Group Box Collapsed"

        # Retrieve last collapse state
        settings = QSettings('MagScope', 'MagScope')
        collapsed = settings.value(self._settings_key, collapsed, type=bool)

        # Set up the toggle button (will be the groupbox's title)
        self.toggle_button = QPushButton(
            self._get_toggle_text(title, not collapsed))
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(not collapsed)
        self.toggle_button.setStyleSheet("""
                text-align: left;
                padding: 0px;
                border: none;
                font-weight: bold;
                font-size: 14px;
        """)

        self.toggle_button.toggled.connect(self.toggle) # type: ignore

        # Replace the groupbox's default title with the button
        self.setLayout(QVBoxLayout())
        self.layout().setContentsMargins(0, 0, 0, 2)
        title_widget = QWidget()
        title_layout = QHBoxLayout(title_widget)
        title_layout.setContentsMargins(4, 4, 4, 4)
        title_layout.setSpacing(6)
        self.toggle_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        title_layout.addWidget(self.toggle_button)

        self.drag_handle = QLabel("᎒᎒᎒")
        self.drag_handle.setObjectName("PanelDragHandle")
        self.drag_handle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drag_handle.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_handle.setToolTip("Drag to reposition panel")
        self.drag_handle.setFixedWidth(20)
        self.drag_handle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.drag_handle.setStyleSheet("font-size: 16px;")
        title_layout.addWidget(self.drag_handle)
        self.setTitle("")
        self.layout().addWidget(title_widget)
        self.layout().setSpacing(0)

        # Content area
        self.content_area = QWidget()
        self.content_area.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Fixed)
        self.layout().addWidget(self.content_area)

        # Animation
        self.animation = QPropertyAnimation(self.content_area,
                                            b'maximumHeight')
        self.animation.setDuration(300)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.animation.finished.connect(self._animation_finished)

        # Start collapsed
        self.collapsed = collapsed
        if collapsed:
            self.content_area.setMaximumHeight(0)
        else:
            self.content_area.setMaximumHeight(16777215)  # QT default maximum

    def _animation_finished(self) -> None:
        if self.collapsed:
            self.content_area.setMaximumHeight(0)
        else:
            self.content_area.setMaximumHeight(16777215)

    @property
    def settings_key(self) -> str:
        return self._settings_key

    def toggle(self, checked):
        self._apply_collapsed_state(not checked, animate=True, persist=True)

    def reset_to_default(self) -> None:
        self._apply_collapsed_state(self.default_collapsed, animate=False, persist=True)

    def _apply_collapsed_state(self, collapsed: bool, *, animate: bool, persist: bool) -> None:
        expanded = not collapsed
        self.collapsed = collapsed
        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(expanded)
        self.toggle_button.blockSignals(False)
        self.toggle_button.setText(self._get_toggle_text(self.title, expanded))

        if persist:
            settings = QSettings('MagScope', 'MagScope')
            settings.setValue(self._settings_key, self.collapsed)

        if animate:
            if expanded:
                # Expand
                self.animation.setStartValue(0)
                self.animation.setEndValue(self.content_area.sizeHint().height())
            else:
                # Collapse
                self.animation.setStartValue(self.content_area.height())
                self.animation.setEndValue(0)

            self.animation.start()
        else:
            self.animation.stop()
            if collapsed:
                self.content_area.setMaximumHeight(0)
            else:
                self.content_area.setMaximumHeight(16777215)  # QT default maximum

    def setContentLayout(self, content_layout):
        wrapper_layout = QVBoxLayout()
        wrapper_layout.setContentsMargins(5, 0, 5, 5)
        #wrapper_layout.setSpacing(4)

        # A subtle horizontal line that will have the same width as the content area
        sep = QFrame(self.content_area)
        sep.setObjectName("groupContentSeparator")
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        sep.setFixedHeight(1)

        wrapper_layout.addWidget(sep)
        wrapper_layout.addLayout(content_layout)

        self.content_area.setLayout(wrapper_layout)

    @staticmethod
    def _get_toggle_text(title, expanded):
        arrow = '▼' if expanded else '❯'
        return f' {arrow} {title}'


class GripHandle(QSplitterHandle):
    """ Simple class for adding '...' to QSplitter handles."""
    released: pyqtSignal = pyqtSignal()
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self._pressed = False

    def mousePressEvent(self, e):
        self._pressed = True
        super().mousePressEvent(e)
        self.update()

    def mouseReleaseEvent(self, e):
        self._pressed = False
        super().mouseReleaseEvent(e)
        self.update()
        self.released.emit()

    def enterEvent(self, e):
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Background with simple states
        base = QPalette().mid().color() # QColor("#1e1e1e")
        pressed = QPalette().light().color()
        hover = QPalette().midlight().color()
        dot = QPalette().light().color()
        if self._pressed:
            color = pressed
        elif self.underMouse():
            color = hover
        else:
            color = base
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 6, 6)

        # Grip dots centered
        if self.orientation() == Qt.Orientation.Horizontal:
            cx = self.width() // 2
            top = self.height() // 2 - 12
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot)
            for i in range(5):
                p.drawEllipse(QRect(cx - 2, top + i * 6, 4, 4))
        else:
            cy = self.height() // 2
            left = self.width() // 2 - 12
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dot)
            for i in range(5):
                p.drawEllipse(QRect(left + i * 6, cy - 2, 4, 4))


class GripSplitter(QSplitter):
    """ Simple class for adding '...' to QSplitter handles."""
    def __init__(self, orientation, name=None, parent=None):
        super().__init__(orientation, parent)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(12)
        self.name = name
        self.shown_once = False
        if name:
            self.setting_name = name + ' Grip Splitter Sizes'
        else:
            self.setting_name = None

    def showEvent(self, e):
        super().showEvent(e)
        if self.setting_name and not self.shown_once:
            self.shown_once = True
            settings = QSettings('MagScope', 'MagScope')
            sizes = settings.value(self.setting_name, None, list)
            if sizes:
                sizes = list(map(int, sizes))
                self.setSizes(sizes)


    def createHandle(self):
        handle = GripHandle(self.orientation(), self)
        handle.released.connect(self.handle_released)
        return handle

    def handle_released(self):
        if self.setting_name:
            settings = QSettings('MagScope', 'MagScope')
            settings.setValue(self.setting_name, self.sizes())


class BeadGraphic(QGraphicsRectItem):
    LABEL_FONT = QFont('Arial', 10)
    LABEL_COLOR = QColor(255, 255, 255, 255)
    LABEL_OFFSET_X = 10
    LABEL_OFFSET_Y = 1
    BORDER_COLOR_DEFAULT = (0, 255, 255, 255)
    FILL_COLOR_DEFAULT = None
    BORDER_COLOR_SELECTED = (255, 0, 0, 255)
    FILL_COLOR_SELECTED = None
    BORDER_COLOR_REFERENCE = (0, 255, 0, 255)
    FILL_COLOR_REFERENCE = None
    _shared_pens: dict[str, QPen] | None = None
    _shared_brushes: dict[str, QBrush] | None = None

    def __init__(
        self,
        parent: UIManager,
        id: int,
        roi: tuple[int, int, int, int],
        view_scene,
    ):
        self._parent: UIManager = parent
        self.id: int = id
        self._initializing: bool = True
        self._is_moving: bool = False
        self._locked: bool
        self._color_state: str = 'default'
        self.scene_rect = None
        self._cached_roi: tuple[int, int, int, int] | None = None
        self.pen_width = 0
        self._ensure_shared_pens_and_brushes()

        # Set up the graphic (must happen in this order)
        super().__init__()

        self.locked = False # initializes colors

        # Add to the scene
        self.view_scene = view_scene
        self.view_scene.addItem(self)

        # Set position
        self.set_roi_bounds(roi)

        # Configure scene
        self.scene_rect = self.scene().sceneRect()
        self._update_cached_roi()
        self._initializing = False

    def remove(self):
        self.view_scene.removeItem(self)

    @classmethod
    def roi_from_center(
        cls,
        x: float,
        y: float,
        width: float,
    ) -> tuple[int, int, int, int]:
        half_width = width / 2
        x0 = int(round(x - half_width))
        y0 = int(round(y - half_width))
        x1 = int(round(x0 + width))
        y1 = int(round(y0 + width))
        return (x0, x1, y0, y1)

    @classmethod
    def label_scene_position_for_roi(cls, roi: tuple[int, int, int, int]) -> QPointF:
        x0, _x1, y0, _y1 = roi
        return QPointF(x0 + cls.LABEL_OFFSET_X, y0 + cls.LABEL_OFFSET_Y)

    @classmethod
    def clamp_roi_to_scene(
        cls,
        roi: tuple[int, int, int, int],
        scene_rect: QRectF,
    ) -> tuple[int, int, int, int]:
        if scene_rect.isNull():
            return roi
        x0, x1, y0, y1 = roi
        width = x1 - x0
        height = y1 - y0
        min_x = int(round(scene_rect.left()))
        max_x = int(round(scene_rect.right() - width))
        min_y = int(round(scene_rect.top()))
        max_y = int(round(scene_rect.bottom() - height))
        if max_x < min_x or max_y < min_y:
            return roi
        x0 = min(max(x0, min_x), max_x)
        y0 = min(max(y0, min_y), max_y)
        return (x0, x0 + width, y0, y0 + height)

    @classmethod
    def move_roi(
        cls,
        roi: tuple[int, int, int, int],
        dx: int,
        dy: int,
        scene_rect: QRectF,
    ) -> tuple[int, int, int, int]:
        x0, x1, y0, y1 = roi
        moved_roi = (x0 + dx, x1 + dx, y0 + dy, y1 + dy)
        return cls.clamp_roi_to_scene(moved_roi, scene_rect)

    @property
    def locked(self):
        return self._locked

    @locked.setter
    def locked(self, locked: bool):
        self._locked = locked

        # Draggable
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, not locked)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemSendsScenePositionChanges, not locked)

        # Color
        self._apply_color()

    def set_selection_state(self, state: str):
        """Update the bead overlay color to match selection/reference state."""
        if state == self._color_state:
            return
        self._color_state = state
        self._apply_color()

    @classmethod
    def _ensure_shared_pens_and_brushes(cls) -> None:
        if cls._shared_pens is None:
            cls._shared_pens = {
                'default': cls._create_pen(cls.BORDER_COLOR_DEFAULT),
                'selected': cls._create_pen(cls.BORDER_COLOR_SELECTED),
                'reference': cls._create_pen(cls.BORDER_COLOR_REFERENCE),
            }
        if cls._shared_brushes is None:
            cls._shared_brushes = {
                'default': cls._create_brush(cls.FILL_COLOR_DEFAULT),
                'selected': cls._create_brush(cls.FILL_COLOR_SELECTED),
                'reference': cls._create_brush(cls.FILL_COLOR_REFERENCE),
            }

    @staticmethod
    def _create_pen(color: tuple[int, int, int, int]) -> QPen:
        pen = QPen(QColor(*color))
        pen.setWidth(0)
        return pen

    @staticmethod
    def _create_brush(color: tuple[int, int, int, int] | None) -> QBrush:
        if color is None:
            return QBrush(Qt.BrushStyle.NoBrush)
        return QBrush(QColor(*color))

    def _apply_color(self):
        assert self._shared_pens is not None
        assert self._shared_brushes is not None
        self.setPen(self._shared_pens[self._color_state])
        self.setBrush(self._shared_brushes[self._color_state])

    def move(self, dx, dy):
        value = self.pos()
        value.setX(value.x() + dx)
        value.setY(value.y() + dy)
        value = self.validate_move(value)
        self.setPos(value)
        self._update_cached_roi()

    def validate_move(self, value):
        """ Prevents the graphic from moving outside the scene border"""
        scene_rect = self.scene().sceneRect()
        if self.scene_rect is not None:
            scene_rect = self.scene_rect

        if value.x() < scene_rect.left() - self.pen_width / 2:
            value.setX(scene_rect.left() - self.pen_width / 2)
        elif value.x() + self.boundingRect().width() > scene_rect.right(
        ) + self.pen_width / 2:
            value.setX(scene_rect.right() + self.pen_width / 2 -
                       self.boundingRect().width())

        if value.y() < scene_rect.top() - self.pen_width / 2:
            value.setY(scene_rect.top() - self.pen_width / 2)
        elif value.y() + self.boundingRect().height() > scene_rect.bottom(
        ) + self.pen_width / 2:
            value.setY(scene_rect.bottom() + self.pen_width / 2 -
                       self.boundingRect().height())

        return value

    def get_label_scene_position(self) -> QPointF:
        return self.label_scene_position_for_roi(self.get_roi_bounds())

    def set_roi_bounds(self, roi: tuple[int, int, int, int]) -> None:
        x0, x1, y0, y1 = roi
        width = x1 - x0
        height = y1 - y0
        offset = self.pen_width / 2
        self.setRect(QRectF(offset, offset, width - self.pen_width, height - self.pen_width))
        self.setPos(QPointF(x0, y0))
        self._update_cached_roi()

    def itemChange(self, change, value):
        # Constrain the item's movement within the scene
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            value = self.validate_move(value)
            if (
                not self._initializing
                and not self._is_moving
                and not self._parent.bead_roi_updates_suppressed
            ):
                self.on_move_completed()
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._update_cached_roi()
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        # Left click - Maybe move
        if event.button() == Qt.MouseButton.LeftButton and not self.locked:
            self._is_moving = True
        # Right Click - Delete self
        elif event.button() == Qt.MouseButton.RightButton:
            if not self.locked:
                self._parent.remove_bead(self.id)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        # Call function when done moving
        if event.button() == Qt.MouseButton.LeftButton and self._is_moving and not self.locked:
            self._is_moving = False
            self.on_move_completed()
        super().mouseReleaseEvent(event)

    def on_move_completed(self):
        self._parent.on_active_bead_move_completed(self.id, self.get_roi_bounds())

    def get_roi_bounds(self) -> tuple[int, int, int, int]:
        if self._cached_roi is None:
            self._update_cached_roi()
        assert self._cached_roi is not None
        return self._cached_roi

    def _update_cached_roi(self):
        rect = self.rect()
        x = self.x()
        y = self.y()
        tl = QPointF(x + rect.left(), y + rect.top())
        br = QPointF(x + rect.right(), y + rect.bottom())
        x0 = int(round(tl.x() - self.pen_width / 2))
        x1 = int(round(br.x() + self.pen_width / 2))
        y0 = int(round(tl.y() - self.pen_width / 2))
        y1 = int(round(br.y() + self.pen_width / 2))
        self._cached_roi = (x0, x1, y0, y1)


class FlashLabel(QLabel):
    def __init__(self, text=""):
        super().__init__(text)
        self._flash_progress = 0.0
        self._timer = QTimer()
        self._timer.timeout.connect(self._update_flash)
        self._step = 0

        # Set initial white text color
        self.setStyleSheet("color: white;")

    def _update_flash(self):
        self._step += 1

        # Quick flash to red, then fade back to white
        if self._step <= 5:
            self._flash_progress = self._step / 5.0  # 0 to 1
        else:
            self._flash_progress = 1.0 - (self._step - 5) / 35.0  # 1 to 0

        # Calculate color (white to red interpolation)
        red = int(255)
        green = int(255 * (1 - self._flash_progress))
        blue = int(255 * (1 - self._flash_progress))

        self.setStyleSheet(f"color: rgb({red}, {green}, {blue});")

        # Stop after 40 steps
        if self._step >= 40:
            self._timer.stop()
            self._step = 0
            self.setStyleSheet("color: white;")

    def setText(self, text):
        if text != self.text():
            super().setText(text)
            # Start flash animation
            if self._timer.isActive():
                self._timer.stop()
            self._step = 0
            self._timer.start(15)
        else:
            super().setText(text)


class ResizableLabel(QLabel):
    """Custom QLabel that emits a signal when it's resized."""
    resized = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)

    def resizeEvent(self, event):
        """Override resize event to emit signal with new dimensions."""
        super().resizeEvent(event)
        size = event.size()
        self.resized.emit(size.width(), size.height())
