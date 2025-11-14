""""
Miscellaneous custom Qt widgets for the GUI
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import (QEasingCurve, QMimeData, QPoint, QPointF, QPropertyAnimation, QRect,
                          QRectF, QSettings, Qt, QTimer, pyqtSignal)
from PyQt6.QtGui import QBrush, QColor, QDrag, QFont, QPainter, QPalette, QPen, QValidator
from PyQt6.QtWidgets import (QCheckBox, QFrame, QGraphicsItem, QGraphicsRectItem, QGraphicsTextItem,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                             QSizePolicy, QSplitter, QSplitterHandle, QVBoxLayout, QWidget)

if TYPE_CHECKING:
    from magscope.gui.windows import WindowManager


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
        if widths[0] > 0:
            self.label.setFixedWidth(widths[0])
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

        # Retrieve last collapse state
        settings = QSettings('MagScope', 'MagScope')
        collapsed = settings.value(self.title + '_Group Box Collapsed',
                                   collapsed,
                                   type=bool)

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

        # Start collapsed
        self.collapsed = collapsed
        if collapsed:
            self.content_area.setMaximumHeight(0)
        else:
            self.content_area.setMaximumHeight(16777215)  # QT default maximum

    def toggle(self, checked):
        self.collapsed = not checked
        self.toggle_button.setText(self._get_toggle_text(self.title, checked))

        settings = QSettings('MagScope', 'MagScope')
        settings.setValue(self.title + '_Group Box Collapsed', self.collapsed)

        if checked:
            # Expand
            self.animation.setStartValue(0)
            self.animation.setEndValue(self.content_area.sizeHint().height())
        else:
            # Collapse
            self.animation.setStartValue(self.content_area.height())
            self.animation.setEndValue(0)

        self.animation.start()

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

    def __init__(self, parent: WindowManager, id: int, x, y, width, view_scene):
        self._parent: WindowManager = parent
        self.id: int = id
        self._is_moving: bool = False
        self._locked: bool
        self.scene_rect = None
        self.border_color_unlocked = (0, 255, 255, 255)
        self.fill_color_unlocked = (0, 183, 235, 25)
        self.border_color_locked = (255, 0, 0, 255)
        self.fill_color_locked = (255, 0, 0, 25)
        self.pen_width = 0
        self.width = width

        # Calculate shape of rect (accounting for pen/border width)
        offset_pos = self.pen_width / 2
        offset_width = self.width - self.pen_width
        rect = QRectF(offset_pos, offset_pos, offset_width, offset_width)

        # Set up the graphic (must happen in this order)
        super().__init__(rect)

        # Label
        self.label = QGraphicsTextItem('', self)
        self.label.setFont(QFont('Arial', int(view_scene.width() / 100)))

        self.locked = False # initializes colors/text

        # Add to the scene
        self.view_scene = view_scene
        self.view_scene.addItem(self)

        # Set position
        pos = QPointF()
        pos.setX(x - self.width / 2)  # convert from center to top-left
        pos.setY(y - self.width / 2)  # convert from center to top-left
        self.setPos(pos)

        # Configure scene
        self.scene_rect = self.scene().sceneRect()

    def remove(self):
        self.view_scene.removeItem(self)

    @property
    def locked(self):
        return self._locked

    @locked.setter
    def locked(self, locked: bool):
        self._locked = locked

        # Text
        text = '🔒' if locked else '🔓'
        self.label.setPlainText(f'{text} {self.id}')

        # Draggable
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsMovable, not locked)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemSendsScenePositionChanges, not locked)

        # Border
        if locked:
            pen = QPen(QColor(*self.border_color_locked))
        else:
            pen = QPen(QColor(*self.border_color_unlocked))
        pen.setWidth(self.pen_width)
        self.setPen(pen)

        # Fill
        if locked:
            brush = QBrush(QColor(*self.fill_color_locked))
        else:
            brush = QBrush(QColor(*self.fill_color_unlocked))
        self.setBrush(brush)

    def move(self, dx, dy):
        value = self.pos()
        value.setX(value.x() + dx)
        value.setY(value.y() + dy)
        value = self.validate_move(value)
        self.setPos(value)

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

    def move_label(self):
        # Update the labels position
        rect = self.rect()
        x = rect.x() + 10
        y = rect.y() + 1
        self.label.setPos(x, y)

    def itemChange(self, change, value):
        # Constrain the item's movement within the scene
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            value = self.validate_move(value)
            self.move_label()
            if not self._is_moving:
                self.on_move_completed()
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
        self._parent.update_bead_rois()


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