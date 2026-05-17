"""Isolated unit tests for custom Qt widgets from magscope/ui/widgets.py."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIntValidator, QPixmap, QResizeEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

from magscope.ui.widgets import (
    CollapsibleGroupBox,
    FlashLabel,
    LabeledCheckbox,
    LabeledLineEdit,
    LabeledLineEditWithValue,
    ResizableLabel,
)


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
