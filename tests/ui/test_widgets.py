"""Isolated unit tests for custom Qt widgets from magscope/ui/widgets.py."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QPointF, QRectF, QSettings, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QIntValidator, QResizeEvent, QShowEvent
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from magscope.ui.widgets import (
    BeadGraphic,
    CollapsibleGroupBox,
    FlashLabel,
    GripHandle,
    GripSplitter,
    LabeledCheckbox,
    LabeledLineEdit,
    LabeledLineEditWithValue,
    LabeledStepperLineEdit,
    ResizableLabel,
)


@pytest.fixture(autouse=True)
def isolated_qsettings(tmp_path):
    previous_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QSettings('MagScope', 'MagScope').clear()
    yield
    QSettings('MagScope', 'MagScope').clear()
    QSettings.setDefaultFormat(previous_format)


class _StubScene:
    def __init__(self, scene_rect: QRectF):
        self._scene_rect = scene_rect

    def sceneRect(self) -> QRectF:
        return self._scene_rect


class _StubBeadParent:
    def __init__(self, scene_rect: QRectF | None = None):
        self.bead_roi_updates_suppressed = False
        self.scene_rect = scene_rect if scene_rect is not None else QRectF(0, 0, 512, 512)
        self.move_completed_calls: list[tuple[int, tuple[int, int, int, int]]] = []

    def _current_scene_rect(self) -> QRectF:
        return self.scene_rect

    def on_active_bead_move_completed(self, bead_id, roi) -> None:
        self.move_completed_calls.append((bead_id, roi))


class _StubBeadGraphic:
    BORDER_COLOR_DEFAULT = BeadGraphic.BORDER_COLOR_DEFAULT
    BORDER_COLOR_SELECTED = BeadGraphic.BORDER_COLOR_SELECTED
    BORDER_COLOR_REFERENCE = BeadGraphic.BORDER_COLOR_REFERENCE
    HOVER_BORDER_COLOR = BeadGraphic.HOVER_BORDER_COLOR
    DRAG_BORDER_COLOR = BeadGraphic.DRAG_BORDER_COLOR
    IDLE_PEN_WIDTH = BeadGraphic.IDLE_PEN_WIDTH
    HOVER_PEN_WIDTH = BeadGraphic.HOVER_PEN_WIDTH
    SELECTED_PEN_WIDTH = BeadGraphic.SELECTED_PEN_WIDTH
    DRAG_PEN_WIDTH = BeadGraphic.DRAG_PEN_WIDTH
    CORNER_GRIP_SIZE = BeadGraphic.CORNER_GRIP_SIZE
    locked = property(BeadGraphic.locked.fget, BeadGraphic.locked.fset)

    def __init__(
        self,
        *,
        parent: _StubBeadParent | None = None,
        scene_rect: QRectF | None = None,
    ):
        BeadGraphic._ensure_shared_pens_and_brushes()
        self._parent = parent if parent is not None else _StubBeadParent()
        self._scene = _StubScene(scene_rect if scene_rect is not None else QRectF(0, 0, 512, 512))
        self.id = 12
        self._initializing = False
        self._is_moving = False
        self._is_hovered = False
        self._locked = False
        self._color_state = 'default'
        self._cached_roi = None
        self.pen_width = 0
        self._shared_pens = BeadGraphic._shared_pens
        self._shared_brushes = BeadGraphic._shared_brushes
        self._rect = QRectF(0, 0, 40, 40)
        self._pos = QPointF(100, 100)
        self.flags = []
        self.pen = None
        self.brush_value = QBrush()
        self.cursor_shape = Qt.CursorShape.ArrowCursor
        self.update_count = 0

    def setFlag(self, flag, enabled) -> None:
        self.flags.append((flag, enabled))

    def setPen(self, pen) -> None:
        self.pen = pen

    def setBrush(self, brush) -> None:
        self.brush_value = brush

    def brush(self):
        return self.brush_value

    def setCursor(self, cursor_shape) -> None:
        self.cursor_shape = cursor_shape

    def unsetCursor(self) -> None:
        self.cursor_shape = Qt.CursorShape.ArrowCursor

    def update(self) -> None:
        self.update_count += 1

    def rect(self) -> QRectF:
        return self._rect

    def setRect(self, rect) -> None:
        self._rect = QRectF(rect)

    def pos(self) -> QPointF:
        return QPointF(self._pos)

    def setPos(self, value) -> None:
        self._pos = QPointF(value)

    def x(self) -> float:
        return self._pos.x()

    def y(self) -> float:
        return self._pos.y()

    def scene(self) -> _StubScene:
        return self._scene

    def _apply_color(self) -> None:
        BeadGraphic._apply_color(self)

    def _update_cursor(self) -> None:
        BeadGraphic._update_cursor(self)

    def _current_visual_state(self) -> str:
        return BeadGraphic._current_visual_state(self)

    def _current_pen_width(self) -> int:
        return BeadGraphic._current_pen_width(self)

    def _current_border_color(self) -> QColor:
        return BeadGraphic._current_border_color(self)

    def _corner_grip_rects(self) -> list[QRectF]:
        return BeadGraphic._corner_grip_rects(self)

    def _paint_rect(self) -> QRectF:
        return BeadGraphic._paint_rect(self)

    def _current_scene_rect(self) -> QRectF:
        return BeadGraphic._current_scene_rect(self)

    def validate_move(self, value):
        return BeadGraphic.validate_move(self, value)

    def _update_cached_roi(self) -> None:
        BeadGraphic._update_cached_roi(self)

    def get_roi_bounds(self) -> tuple[int, int, int, int]:
        return BeadGraphic.get_roi_bounds(self)

    def label_scene_position_for_roi(self, roi) -> QPointF:
        return BeadGraphic.label_scene_position_for_roi(roi)


# ---------------------------------------------------------------------------
# LabeledLineEdit
# ---------------------------------------------------------------------------

def test_labeled_lineedit_constructs_with_label_text(qtbot):
    widget = LabeledLineEdit(label_text="Exposure (ms)")
    qtbot.addWidget(widget)
    assert widget.label.text() == "Exposure (ms)"


def test_labeled_lineedit_constructs_with_default(qtbot):
    widget = LabeledLineEdit(label_text="X", default="100")
    qtbot.addWidget(widget)
    assert widget.lineedit.text() == "100"


def test_labeled_lineedit_callback_connected(qtbot):
    calls = []

    def on_changed():
        calls.append(True)

    widget = LabeledLineEdit(label_text="X", callback=on_changed)
    qtbot.addWidget(widget)
    widget.lineedit.setText("hello")
    assert len(calls) >= 1


def test_labeled_lineedit_validator(qtbot):
    validator = QIntValidator(0, 100)
    widget = LabeledLineEdit(label_text="X", validator=validator)
    qtbot.addWidget(widget)
    assert widget.lineedit.validator() is validator


def test_labeled_lineedit_applies_widths(qtbot):
    widget = LabeledLineEdit(label_text="X", widths=(70, 90))
    qtbot.addWidget(widget)
    assert widget.label.minimumWidth() == 70
    assert widget.label.maximumWidth() == 70
    assert widget.lineedit.minimumWidth() == 90
    assert widget.lineedit.maximumWidth() == 90


# ---------------------------------------------------------------------------
# LabeledLineEditWithValue
# ---------------------------------------------------------------------------

def test_labeled_lineedit_with_value_constructs_with_label(qtbot):
    widget = LabeledLineEditWithValue(label_text="Gain")
    qtbot.addWidget(widget)
    assert widget.label.text() == "Gain"


def test_labeled_lineedit_with_value_default_shows_in_value_label(qtbot):
    widget = LabeledLineEditWithValue(label_text="Gain", default="0.5")
    qtbot.addWidget(widget)
    assert widget.value_label.text() == "0.5"


def test_labeled_lineedit_with_value_callback(qtbot):
    calls = []

    def on_edit_finished():
        calls.append(True)

    widget = LabeledLineEditWithValue(label_text="Gain", callback=on_edit_finished)
    qtbot.addWidget(widget)
    widget.lineedit.editingFinished.emit()
    assert len(calls) >= 1


def test_labeled_lineedit_with_value_validator(qtbot):
    validator = QIntValidator(0, 100)
    widget = LabeledLineEditWithValue(label_text="Gain", validator=validator)
    qtbot.addWidget(widget)
    assert widget.lineedit.validator() is validator


def test_labeled_lineedit_with_value_applies_widths(qtbot):
    widget = LabeledLineEditWithValue(label_text="Gain", widths=(80, 50, 40), default="1")
    qtbot.addWidget(widget)
    assert widget.label.maximumWidth() == 80
    assert widget.lineedit.minimumWidth() == 50
    assert widget.lineedit.maximumWidth() == 50
    assert widget.value_label.minimumWidth() == 40
    assert widget.value_label.maximumWidth() == 40


# ---------------------------------------------------------------------------
# LabeledCheckbox
# ---------------------------------------------------------------------------

def test_labeled_checkbox_label_text(qtbot):
    widget = LabeledCheckbox(label_text="Enable tracking")
    qtbot.addWidget(widget)
    assert widget.label.text() == "Enable tracking"


def test_labeled_checkbox_default_unchecked(qtbot):
    widget = LabeledCheckbox(label_text="Enable")
    qtbot.addWidget(widget)
    assert widget.checkbox.isChecked() is False


def test_labeled_checkbox_default_checked(qtbot):
    widget = LabeledCheckbox(label_text="Enable", default=True)
    qtbot.addWidget(widget)
    assert widget.checkbox.isChecked() is True


def test_labeled_checkbox_callback(qtbot):
    calls = []

    def on_toggled(checked):
        calls.append(checked)

    widget = LabeledCheckbox(label_text="Enable", callback=on_toggled)
    qtbot.addWidget(widget)
    widget.checkbox.toggled.emit(True)
    assert calls == [True]


def test_labeled_checkbox_applies_widths(qtbot):
    widget = LabeledCheckbox(label_text="Enable", widths=(100, 30))
    qtbot.addWidget(widget)
    assert widget.label.minimumWidth() == 100
    assert widget.label.maximumWidth() == 100
    assert widget.checkbox.minimumWidth() == 20
    assert widget.checkbox.maximumWidth() == 30


# ---------------------------------------------------------------------------
# LabeledStepperLineEdit
# ---------------------------------------------------------------------------

def test_labeled_stepper_lineedit_constructs_and_connects_callbacks(qtbot):
    calls = []

    def on_left():
        calls.append('left')

    def on_edit_finished():
        calls.append('edit')

    def on_right():
        calls.append('right')

    validator = QIntValidator(0, 100)
    widget = LabeledStepperLineEdit(
        label_text="Step",
        left_button_text="-",
        right_button_text="+",
        widths=(75, 0, 60, 0),
        default="10",
        validator=validator,
        callbacks=(on_left, on_edit_finished, on_right),
    )
    qtbot.addWidget(widget)

    widget.left_button.click()
    widget.lineedit.editingFinished.emit()
    widget.right_button.click()

    assert widget.name_label.text() == "Step"
    assert widget.name_label.minimumWidth() == 75
    assert widget.name_label.maximumWidth() == 75
    assert widget.lineedit.text() == "10"
    assert widget.lineedit.validator() is validator
    assert widget.lineedit.minimumWidth() == 60
    assert widget.lineedit.maximumWidth() == 60
    assert calls == ['left', 'edit', 'right']


# ---------------------------------------------------------------------------
# FlashLabel
# ---------------------------------------------------------------------------

def test_flash_label_starts_timer_on_new_text(qtbot):
    label = FlashLabel("Initial")
    qtbot.addWidget(label)
    label.setText("New text")
    assert label._timer.isActive()


def test_flash_label_does_not_restart_for_same_text(qtbot):
    label = FlashLabel("Hello")
    qtbot.addWidget(label)
    label.setText("Hello")
    assert not label._timer.isActive()


def test_flash_label_update_flash_interpolates_colors(qtbot):
    label = FlashLabel("Test")
    qtbot.addWidget(label)
    label._step = 0
    label._flash_progress = 0.0
    label._update_flash()
    assert label._step == 1
    assert '255' in label.styleSheet()


def test_flash_label_flash_completes_after_40_steps(qtbot):
    label = FlashLabel("Test")
    qtbot.addWidget(label)
    label.setText("Trigger")
    label._step = 39
    label._update_flash()
    assert label._timer.isActive() is False
    assert label.styleSheet() == 'color: white;'


def test_flash_label_restarts_active_timer_on_new_text(qtbot):
    label = FlashLabel("Initial")
    qtbot.addWidget(label)
    label.setText("First")
    label._step = 12

    label.setText("Second")

    assert label._timer.isActive()
    assert label._step == 0


def test_flash_label_update_flash_fades_after_peak(qtbot):
    label = FlashLabel("Test")
    qtbot.addWidget(label)
    label._step = 5

    label._update_flash()

    assert label._step == 6
    assert 0.0 < label._flash_progress < 1.0
    assert label.styleSheet() != "color: white;"


# ---------------------------------------------------------------------------
# ResizableLabel
# ---------------------------------------------------------------------------

def test_resizable_label_emit_resized_signal(qtbot):
    label = ResizableLabel()
    qtbot.addWidget(label)
    calls = []
    label.resized.connect(lambda w, h: calls.append((w, h)))
    event = QResizeEvent(QSize(100, 50), QSize(10, 10))
    label.resizeEvent(event)
    assert calls == [(100, 50)]


def test_resizable_label_use_pixmap_size_hint(qtbot):
    label = ResizableLabel(ignore_pixmap_size_hint=False)
    qtbot.addWidget(label)
    assert label.sizeHint().width() > 0 or label.sizeHint().width() == -1


def test_resizable_label_uses_default_minimum_size_hint(qtbot):
    label = ResizableLabel(ignore_pixmap_size_hint=False)
    qtbot.addWidget(label)
    assert label.minimumSizeHint() == super(ResizableLabel, label).minimumSizeHint()


def test_resizable_label_ignore_pixmap_size_hint(qtbot):
    label = ResizableLabel(ignore_pixmap_size_hint=True)
    qtbot.addWidget(label)
    assert label.sizeHint() == QSize(1, 1)
    assert label.minimumSizeHint() == QSize(1, 1)


# ---------------------------------------------------------------------------
# CollapsibleGroupBox
# ---------------------------------------------------------------------------

def test_collapsible_groupbox_get_toggle_text_expanded():
    result = CollapsibleGroupBox._get_toggle_text("Settings", True)
    assert "▼" in result
    assert "Settings" in result


def test_collapsible_groupbox_get_toggle_text_collapsed():
    result = CollapsibleGroupBox._get_toggle_text("Settings", False)
    assert "❯" in result
    assert "Settings" in result


def test_collapsible_groupbox_get_toggle_text_non_collapsible():
    result = CollapsibleGroupBox._get_toggle_text("Settings", True, collapsible=False)
    assert "▼" not in result
    assert "❯" not in result
    assert "Settings" in result


def test_collapsible_groupbox_settings_key_property(qtbot):
    box = CollapsibleGroupBox(title="Camera", collapsed=True)
    qtbot.addWidget(box)
    assert box.settings_key == "Camera_Group Box Collapsed"


def test_collapsible_groupbox_reset_to_default(qtbot):
    box = CollapsibleGroupBox(title="Panel", collapsed=False)
    qtbot.addWidget(box)
    box.collapsed = False
    box.content_area.setMaximumHeight(16777215)
    box.default_collapsed = True
    box.reset_to_default()
    assert box.collapsed is True


def test_collapsible_groupbox_set_highlight_border(qtbot):
    box = CollapsibleGroupBox(title="Panel")
    qtbot.addWidget(box)
    box.set_highlight_border("red")
    assert "red" in box.styleSheet()
    box.set_highlight_border(None)
    assert "red" not in box.styleSheet()


def test_collapsible_groupbox_reads_persisted_collapsed_state(qtbot):
    settings = QSettings('MagScope', 'MagScope')
    settings.setValue("Persisted_Group Box Collapsed", True)

    box = CollapsibleGroupBox(title="Persisted", collapsed=False)
    qtbot.addWidget(box)

    assert box.collapsed is True
    assert box.content_area.maximumHeight() == 0
    assert "❯" in box.toggle_button.text()


def test_collapsible_groupbox_toggle_collapses_and_persists(qtbot):
    box = CollapsibleGroupBox(title="Panel", collapsed=False)
    qtbot.addWidget(box)

    box.toggle(False)

    assert box.collapsed is True
    assert "❯" in box.toggle_button.text()
    assert QSettings('MagScope', 'MagScope').value(box.settings_key, type=bool) is True
    box.animation.stop()


def test_collapsible_groupbox_toggle_expands_with_animation(qtbot):
    box = CollapsibleGroupBox(title="Panel", collapsed=True)
    qtbot.addWidget(box)

    box.content_area.resize(10, 20)

    box.toggle(True)

    assert box.collapsed is False
    assert "▼" in box.toggle_button.text()
    assert QSettings('MagScope', 'MagScope').value(box.settings_key, type=bool) is False
    box.animation.stop()


def test_collapsible_groupbox_non_collapsible_ignores_collapse(qtbot):
    box = CollapsibleGroupBox(title="Static", collapsed=True, collapsible=False)
    qtbot.addWidget(box)

    box._apply_collapsed_state(True, animate=True, persist=True)
    box.toggle(False)

    assert box.collapsed is False
    assert box.default_collapsed is False
    assert box.toggle_button.isCheckable() is False
    assert box.drag_handle.isHidden() is True
    assert "▼" not in box.toggle_button.text()
    assert "❯" not in box.toggle_button.text()
    assert QSettings('MagScope', 'MagScope').value(box.settings_key, None) is None


def test_collapsible_groupbox_animation_finished_sets_final_height(qtbot):
    box = CollapsibleGroupBox(title="Panel")
    qtbot.addWidget(box)

    box.collapsed = True
    box.content_area.setMaximumHeight(123)
    box._animation_finished()
    assert box.content_area.maximumHeight() == 0

    box.collapsed = False
    box._animation_finished()
    assert box.content_area.maximumHeight() == 16777215


def test_collapsible_groupbox_set_content_layout_wraps_separator(qtbot):
    box = CollapsibleGroupBox(title="Panel")
    qtbot.addWidget(box)
    content_layout = QVBoxLayout()
    content_layout.addWidget(QWidget())

    box.setContentLayout(content_layout)

    wrapper_layout = box.content_area.layout()
    assert wrapper_layout is not None
    assert wrapper_layout.count() == 2


# ---------------------------------------------------------------------------
# GripSplitter
# ---------------------------------------------------------------------------

def test_grip_splitter_initializes_named_and_unnamed(qtbot):
    unnamed = GripSplitter(Qt.Orientation.Horizontal)
    named = GripSplitter(Qt.Orientation.Vertical, name="Main")
    qtbot.addWidget(unnamed)
    qtbot.addWidget(named)

    assert unnamed.childrenCollapsible() is False
    assert unnamed.handleWidth() == 12
    assert unnamed.setting_name is None
    assert named.setting_name == "Main Grip Splitter Sizes"


def test_grip_splitter_create_handle_connects_release_signal(qtbot):
    splitter = GripSplitter(Qt.Orientation.Horizontal, name="Main")
    qtbot.addWidget(splitter)

    handle = splitter.createHandle()
    released = []
    handle.released.connect(lambda: released.append(True))
    handle.released.emit()

    assert isinstance(handle, GripHandle)
    assert released == [True]


def test_grip_splitter_show_event_restores_saved_sizes(qtbot):
    class RecordingGripSplitter(GripSplitter):
        def __init__(self):
            super().__init__(Qt.Orientation.Horizontal, name="Saved")
            self.recorded_sizes = []

        def setSizes(self, sizes):
            self.recorded_sizes.append(list(sizes))
            super().setSizes(sizes)

    QSettings('MagScope', 'MagScope').setValue("Saved Grip Splitter Sizes", [10, 30])
    splitter = RecordingGripSplitter()
    splitter.addWidget(QWidget())
    splitter.addWidget(QWidget())
    qtbot.addWidget(splitter)

    splitter.showEvent(QShowEvent())

    assert splitter.shown_once is True
    assert splitter.recorded_sizes == [[10, 30]]


def test_grip_splitter_show_event_only_restores_once(qtbot):
    class RecordingGripSplitter(GripSplitter):
        def __init__(self):
            super().__init__(Qt.Orientation.Horizontal, name="Saved")
            self.recorded_sizes = []

        def setSizes(self, sizes):
            self.recorded_sizes.append(list(sizes))
            super().setSizes(sizes)

    QSettings('MagScope', 'MagScope').setValue("Saved Grip Splitter Sizes", [10, 30])
    splitter = RecordingGripSplitter()
    splitter.addWidget(QWidget())
    splitter.addWidget(QWidget())
    qtbot.addWidget(splitter)

    splitter.showEvent(QShowEvent())
    splitter.showEvent(QShowEvent())

    assert splitter.recorded_sizes == [[10, 30]]


def test_grip_splitter_handle_released_persists_sizes(qtbot):
    class FixedSizeGripSplitter(GripSplitter):
        def sizes(self):
            return [12, 34]

    splitter = FixedSizeGripSplitter(Qt.Orientation.Horizontal, name="Persist")
    qtbot.addWidget(splitter)

    splitter.handle_released()

    saved_sizes = QSettings('MagScope', 'MagScope').value(
        "Persist Grip Splitter Sizes",
        [],
        list,
    )
    assert list(map(int, saved_sizes)) == [12, 34]


def test_grip_splitter_handle_released_without_name_is_noop(qtbot):
    splitter = GripSplitter(Qt.Orientation.Horizontal)
    qtbot.addWidget(splitter)
    settings = QSettings('MagScope', 'MagScope')
    settings.setFallbacksEnabled(False)
    keys_before = settings.allKeys()

    splitter.handle_released()

    assert settings.allKeys() == keys_before


# ---------------------------------------------------------------------------
# BeadGraphic classmethods (pure logic, no Qt needed for most)
# ---------------------------------------------------------------------------

def test_bead_graphic_roi_from_center_exact():
    result = BeadGraphic.roi_from_center(0.0, 0.0, 10.0)
    assert result == (-5, 5, -5, 5)


def test_bead_graphic_roi_from_center_offset():
    result = BeadGraphic.roi_from_center(100.0, 200.0, 64.0)
    assert result == (68, 132, 168, 232)


def test_bead_graphic_label_scene_position():
    from PyQt6.QtCore import QPointF
    result = BeadGraphic.label_scene_position_for_roi((50, 114, 60, 124))
    assert result == QPointF(60, 61)


def test_bead_graphic_clamp_roi_within_scene():
    from PyQt6.QtCore import QRectF
    roi = (0, 64, 0, 64)
    scene = QRectF(0, 0, 512, 256)
    result = BeadGraphic.clamp_roi_to_scene(roi, scene)
    assert result == (0, 64, 0, 64)


def test_bead_graphic_clamp_roi_left_overflow():
    from PyQt6.QtCore import QRectF
    roi = (-20, 44, 0, 64)
    scene = QRectF(0, 0, 512, 256)
    result = BeadGraphic.clamp_roi_to_scene(roi, scene)
    assert result == (0, 64, 0, 64)


def test_bead_graphic_clamp_roi_right_overflow():
    from PyQt6.QtCore import QRectF
    roi = (500, 564, 0, 64)
    scene = QRectF(0, 0, 512, 256)
    result = BeadGraphic.clamp_roi_to_scene(roi, scene)
    assert result == (448, 512, 0, 64)


def test_bead_graphic_clamp_roi_null_scene_unchanged():
    from PyQt6.QtCore import QRectF
    roi = (-5, 59, -5, 59)
    scene = QRectF()
    result = BeadGraphic.clamp_roi_to_scene(roi, scene)
    assert result == (-5, 59, -5, 59)


def test_bead_graphic_clamp_roi_scene_too_small_unchanged():
    from PyQt6.QtCore import QRectF
    roi = (0, 100, 0, 100)
    scene = QRectF(0, 0, 50, 50)
    result = BeadGraphic.clamp_roi_to_scene(roi, scene)
    assert result == (0, 100, 0, 100)


def test_bead_graphic_move_roi_positive():
    from PyQt6.QtCore import QRectF
    roi = (0, 64, 0, 64)
    scene = QRectF(0, 0, 512, 256)
    result = BeadGraphic.move_roi(roi, 10, 20, scene)
    assert result == (10, 74, 20, 84)


def test_bead_graphic_move_roi_clamped_at_boundary():
    from PyQt6.QtCore import QRectF
    roi = (440, 504, 0, 64)
    scene = QRectF(0, 0, 512, 256)
    result = BeadGraphic.move_roi(roi, 20, 0, scene)
    assert result == (448, 512, 0, 64)


def test_bead_graphic_move_roi_clamped_at_bottom_boundary():
    roi = (0, 64, 220, 284)
    scene = QRectF(0, 0, 512, 256)
    result = BeadGraphic.move_roi(roi, 0, 20, scene)
    assert result == (0, 64, 192, 256)


# ---------------------------------------------------------------------------
# BeadGraphic state helpers, exercised without QGraphicsScene on Windows 3.13
# ---------------------------------------------------------------------------

def test_bead_graphic_remove_delegates_to_scene():
    removed = []
    stub = _StubBeadGraphic()
    stub.view_scene = type('Scene', (), {'removeItem': lambda self, item: removed.append(item)})()

    BeadGraphic.remove(stub)

    assert removed == [stub]


def test_bead_graphic_shared_pens_and_brushes_initialize_lazily():
    original_pens = BeadGraphic._shared_pens
    original_brushes = BeadGraphic._shared_brushes
    BeadGraphic._shared_pens = None
    BeadGraphic._shared_brushes = None
    try:
        BeadGraphic._ensure_shared_pens_and_brushes()

        assert set(BeadGraphic._shared_pens) == {'default', 'selected', 'reference'}
        assert set(BeadGraphic._shared_brushes) == {'default', 'selected', 'reference'}
        assert BeadGraphic._shared_pens['default'].width() == 0
        assert BeadGraphic._shared_pens['default'].isCosmetic() is True
        assert BeadGraphic._shared_brushes['default'].style() == Qt.BrushStyle.NoBrush
    finally:
        BeadGraphic._shared_pens = original_pens
        BeadGraphic._shared_brushes = original_brushes


def test_bead_graphic_create_brush_with_fill_color():
    brush = BeadGraphic._create_brush((1, 2, 3, 4))
    assert brush.color() == QColor(1, 2, 3, 4)


def test_bead_graphic_locked_setter_toggles_flags_and_applies_color():
    stub = _StubBeadGraphic()

    stub.locked = True

    assert stub.locked is True
    assert [enabled for _flag, enabled in stub.flags] == [False, False]
    assert stub.pen is stub._shared_pens['default']
    assert stub.brush_value is stub._shared_brushes['default']
    assert stub.cursor_shape == Qt.CursorShape.ArrowCursor
    assert stub.update_count == 1


def test_bead_graphic_unlocked_setter_enables_movable_flags():
    stub = _StubBeadGraphic()
    stub._locked = True

    stub.locked = False

    assert stub.locked is False
    assert [enabled for _flag, enabled in stub.flags] == [True, True]


def test_bead_graphic_set_selection_state_noops_when_unchanged():
    stub = _StubBeadGraphic()

    BeadGraphic.set_selection_state(stub, 'default')

    assert stub.update_count == 0


def test_bead_graphic_set_selection_state_updates_color():
    stub = _StubBeadGraphic()

    BeadGraphic.set_selection_state(stub, 'selected')

    assert stub._color_state == 'selected'
    assert stub.pen is stub._shared_pens['selected']
    assert stub.update_count == 1


def test_bead_graphic_visual_state_prefers_dragging_then_hover_then_color_state():
    stub = _StubBeadGraphic()
    stub._color_state = 'selected'

    assert stub._current_visual_state() == 'selected'

    stub._is_hovered = True
    assert stub._current_visual_state() == 'hover'

    stub._is_moving = True
    assert stub._current_visual_state() == 'dragging'

    stub._locked = True
    assert stub._current_visual_state() == 'selected'


def test_bead_graphic_current_border_colors():
    stub = _StubBeadGraphic()

    assert stub._current_border_color() == QColor(*BeadGraphic.BORDER_COLOR_DEFAULT)

    stub._color_state = 'selected'
    assert stub._current_border_color() == QColor(*BeadGraphic.BORDER_COLOR_SELECTED)

    stub._color_state = 'reference'
    assert stub._current_border_color() == QColor(*BeadGraphic.BORDER_COLOR_REFERENCE)

    stub._is_hovered = True
    assert stub._current_border_color() == QColor(*BeadGraphic.HOVER_BORDER_COLOR)

    stub._is_moving = True
    assert stub._current_border_color() == QColor(*BeadGraphic.DRAG_BORDER_COLOR)


def test_bead_graphic_current_pen_widths():
    stub = _StubBeadGraphic()

    assert stub._current_pen_width() == BeadGraphic.IDLE_PEN_WIDTH

    stub._color_state = 'selected'
    assert stub._current_pen_width() == BeadGraphic.SELECTED_PEN_WIDTH

    stub._is_hovered = True
    assert stub._current_pen_width() == BeadGraphic.HOVER_PEN_WIDTH

    stub._is_moving = True
    assert stub._current_pen_width() == BeadGraphic.DRAG_PEN_WIDTH


def test_bead_graphic_update_cursor_for_locked_drag_hover_and_idle():
    stub = _StubBeadGraphic()

    stub._locked = True
    stub._update_cursor()
    assert stub.cursor_shape == Qt.CursorShape.ArrowCursor

    stub._locked = False
    stub._is_moving = True
    stub._update_cursor()
    assert stub.cursor_shape == Qt.CursorShape.ClosedHandCursor

    stub._is_moving = False
    stub._is_hovered = True
    stub._update_cursor()
    assert stub.cursor_shape == Qt.CursorShape.OpenHandCursor

    stub._is_hovered = False
    stub._update_cursor()
    assert stub.cursor_shape == Qt.CursorShape.ArrowCursor


def test_bead_graphic_corner_grips_only_for_selected_state():
    stub = _StubBeadGraphic()
    assert stub._corner_grip_rects() == []

    stub._color_state = 'selected'
    stub._rect = QRectF(0, 0, 30, 30)

    grip_rects = stub._corner_grip_rects()

    assert len(grip_rects) == 4
    assert all(rect.width() == BeadGraphic.CORNER_GRIP_SIZE for rect in grip_rects)
    assert all(rect.height() == BeadGraphic.CORNER_GRIP_SIZE for rect in grip_rects)


def test_bead_graphic_paint_rect_accounts_for_pen_width():
    stub = _StubBeadGraphic()
    stub._rect = QRectF(0, 0, 40, 40)
    assert stub._paint_rect() == QRectF(0, 0, 40, 40)

    stub._color_state = 'selected'
    assert stub._paint_rect() == QRectF(1, 1, 38, 38)


def test_bead_graphic_current_scene_rect_prefers_parent_non_null_rect():
    parent = _StubBeadParent(QRectF(5, 6, 100, 80))
    stub = _StubBeadGraphic(parent=parent, scene_rect=QRectF(0, 0, 512, 512))
    assert stub._current_scene_rect() == QRectF(5, 6, 100, 80)


def test_bead_graphic_current_scene_rect_falls_back_to_scene_rect():
    parent = _StubBeadParent(QRectF())
    stub = _StubBeadGraphic(parent=parent, scene_rect=QRectF(1, 2, 30, 40))
    assert stub._current_scene_rect() == QRectF(1, 2, 30, 40)


def test_bead_graphic_current_scene_rect_uses_scene_when_parent_has_no_getter():
    stub = _StubBeadGraphic(parent=object(), scene_rect=QRectF(1, 2, 30, 40))
    assert stub._current_scene_rect() == QRectF(1, 2, 30, 40)


def test_bead_graphic_validate_move_clamps_all_edges():
    stub = _StubBeadGraphic(scene_rect=QRectF(0, 0, 512, 512))

    assert stub.validate_move(QPointF(-100, -50)) == QPointF(0, 0)
    assert stub.validate_move(QPointF(600, 700)) == QPointF(472, 472)


def test_bead_graphic_validate_move_allows_positions_inside_scene():
    stub = _StubBeadGraphic(scene_rect=QRectF(0, 0, 512, 512))
    assert stub.validate_move(QPointF(10, 20)) == QPointF(10, 20)


def test_bead_graphic_set_roi_bounds_updates_rect_position_and_cache():
    stub = _StubBeadGraphic()

    BeadGraphic.set_roi_bounds(stub, (10, 50, 20, 60))

    assert stub.rect() == QRectF(0, 0, 40, 40)
    assert stub.pos() == QPointF(10, 20)
    assert stub.get_roi_bounds() == (10, 50, 20, 60)


def test_bead_graphic_move_updates_position_and_cached_roi():
    stub = _StubBeadGraphic(scene_rect=QRectF(0, 0, 512, 512))

    BeadGraphic.move(stub, 30, 40)

    assert stub.pos() == QPointF(130, 140)
    assert stub.get_roi_bounds() == (130, 170, 140, 180)


def test_bead_graphic_get_roi_bounds_recomputes_missing_cache():
    stub = _StubBeadGraphic()
    stub._cached_roi = None

    assert stub.get_roi_bounds() == (100, 140, 100, 140)


def test_bead_graphic_get_label_scene_position_uses_cached_roi():
    stub = _StubBeadGraphic()
    stub._cached_roi = (50, 90, 70, 110)

    assert BeadGraphic.get_label_scene_position(stub) == QPointF(60, 71)


def test_bead_graphic_on_move_completed_notifies_parent():
    parent = _StubBeadParent()
    stub = _StubBeadGraphic(parent=parent)
    stub._cached_roi = (100, 140, 100, 140)

    BeadGraphic.on_move_completed(stub)

    assert parent.move_completed_calls == [(12, (100, 140, 100, 140))]


def test_bead_graphic_paint_draws_roi_and_selected_corner_grips():
    class FakePainter:
        def __init__(self):
            self.rects = []
            self.saved = False
            self.restored = False

        def save(self):
            self.saved = True

        def setRenderHint(self, *_args):
            pass

        def setPen(self, _pen):
            pass

        def setBrush(self, _brush):
            pass

        def drawRect(self, rect):
            self.rects.append(rect)

        def restore(self):
            self.restored = True

    stub = _StubBeadGraphic()
    stub._color_state = 'selected'
    painter = FakePainter()

    BeadGraphic.paint(stub, painter, None)

    assert painter.saved is True
    assert painter.restored is True
    assert len(painter.rects) == 5
