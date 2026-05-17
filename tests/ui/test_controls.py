"""Isolated unit tests for control panel classes from magscope/ui/controls.py."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import QWidget

from magscope.scripting import ScriptStatus
from magscope.ui.controls import (
    CameraPanel,
    HelpPanel,
    StatusPanel,
    ScriptPanel,
    ZLUTPanel,
    _LockStatusBadge,
)


# ---------------------------------------------------------------------------
# _LockStatusBadge
# ---------------------------------------------------------------------------

def test_lock_status_badge_active_state(qtbot):
    badge = _LockStatusBadge()
    qtbot.addWidget(badge)
    badge.set_state("Active")
    assert badge._state == "Active"
    assert badge.text() == "Active"
    assert "background-color" in badge.styleSheet()


def test_lock_status_badge_inactive_state(qtbot):
    badge = _LockStatusBadge()
    qtbot.addWidget(badge)
    badge.set_state("Inactive")
    assert badge.text() == "Inactive"


def test_lock_status_badge_arming_state(qtbot):
    badge = _LockStatusBadge()
    qtbot.addWidget(badge)
    badge.set_state("Arming")
    assert badge.text() == "Arming"


def test_lock_status_badge_target_not_set_state(qtbot):
    badge = _LockStatusBadge()
    qtbot.addWidget(badge)
    badge.set_state("Target not set")
    assert badge.text() == "Target not set"


def test_lock_status_badge_unknown_state_defaults_to_inactive_colors(qtbot):
    badge = _LockStatusBadge()
    qtbot.addWidget(badge)
    badge.set_state("bogus")
    assert badge._state == "bogus"
    stylesheet = badge.styleSheet()
    assert "background-color" in stylesheet


# ---------------------------------------------------------------------------
# StatusPanel
# ---------------------------------------------------------------------------

def test_status_panel_update_display_rate_increments_dots(qtbot):
    panel = StatusPanel(manager=SimpleNamespace())
    qtbot.addWidget(panel)
    panel.update_display_rate("30.0 fps")
    panel.update_display_rate("30.0 fps")
    assert ".." in panel.display_rate_status.text()

    for _ in range(2):
        panel.update_display_rate("30.0 fps")
    assert panel.dot_count == 0
    assert "Display Rate" in panel.display_rate_status.text()


def test_status_panel_update_video_processors_status(qtbot):
    panel = StatusPanel(manager=SimpleNamespace())
    qtbot.addWidget(panel)
    panel.update_video_processors_status("2/4 busy")
    assert "Video Processors: 2/4 busy" in panel.video_processors_status.text()


def test_status_panel_update_video_buffer_status_parses_percent(qtbot):
    panel = StatusPanel(manager=SimpleNamespace())
    qtbot.addWidget(panel)
    panel.update_video_buffer_status("75% full")
    assert "Video Buffer: 75% full" in panel.video_buffer_status.text()
    assert panel.video_buffer_status_bar.value() == 75


def test_status_panel_update_video_buffer_status_invalid_percent(qtbot):
    panel = StatusPanel(manager=SimpleNamespace())
    qtbot.addWidget(panel)
    panel.update_video_buffer_status("not a number")
    assert panel.video_buffer_status_bar.value() == 0


def test_status_panel_update_video_buffer_purge(qtbot):
    panel = StatusPanel(manager=SimpleNamespace())
    qtbot.addWidget(panel)
    panel.update_video_buffer_purge(5000.0)
    assert "Video Buffer Purged at:" in panel.video_buffer_purge_label.text()


# ---------------------------------------------------------------------------
# CameraPanel
# ---------------------------------------------------------------------------

def test_camera_panel_format_last_update_text_none(qtbot):
    manager = SimpleNamespace(camera_type=SimpleNamespace(settings=[]))
    panel = CameraPanel(manager=manager)
    qtbot.addWidget(panel)
    assert panel._last_settings_update is None
    assert panel.last_update_label.text() == "Last updated: not yet"


def test_camera_panel_format_last_update_text_with_datetime(qtbot):
    import datetime

    manager = SimpleNamespace(camera_type=SimpleNamespace(settings=["Exposure"]))
    panel = CameraPanel(manager=manager)
    qtbot.addWidget(panel)

    panel._last_settings_update = datetime.datetime(2024, 1, 15, 10, 30, 45)
    panel.last_update_label.setText(panel._format_last_update_text())
    assert "2024-01-15 10:30:45" in panel.last_update_label.text()


def test_camera_panel_update_camera_setting(qtbot):
    manager = SimpleNamespace(camera_type=SimpleNamespace(settings=["Exposure"]))
    sent = []
    manager.send_ipc = sent.append
    panel = CameraPanel(manager=manager)
    qtbot.addWidget(panel)

    panel.update_camera_setting("Exposure", "100")
    assert panel.settings["Exposure"].value_label.text() == "100"
    assert panel._last_settings_update is not None


# ---------------------------------------------------------------------------
# ScriptPanel
# ---------------------------------------------------------------------------

def test_script_panel_update_status_empty(qtbot):
    manager = SimpleNamespace(send_ipc=lambda c: None)
    panel = ScriptPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.update_status(ScriptStatus.EMPTY)
    assert "Status: Empty" in panel.status_label.text()
    assert not panel.start_button.isEnabled()


def test_script_panel_update_status_loaded(qtbot):
    manager = SimpleNamespace(send_ipc=lambda c: None)
    panel = ScriptPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.update_status(ScriptStatus.LOADED)
    assert panel.start_button.isEnabled()
    assert not panel.pause_button.isEnabled()


def test_script_panel_update_status_running(qtbot):
    manager = SimpleNamespace(send_ipc=lambda c: None)
    panel = ScriptPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.update_status(ScriptStatus.RUNNING)
    assert panel.pause_button.isEnabled()
    assert panel.pause_button.text() == "Pause"


def test_script_panel_update_status_paused(qtbot):
    manager = SimpleNamespace(send_ipc=lambda c: None)
    panel = ScriptPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.update_status(ScriptStatus.PAUSED)
    assert panel.pause_button.isEnabled()
    assert panel.pause_button.text() == "Resume"


def test_script_panel_update_step_with_description(qtbot):
    manager = SimpleNamespace(send_ipc=lambda c: None)
    panel = ScriptPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.update_step(2, 5, "Moving stage")
    assert panel.step_position_label.text() == "Step: 2/5"
    assert panel.step_description_label.text() == "Moving stage"


def test_script_panel_update_step_none_current(qtbot):
    manager = SimpleNamespace(send_ipc=lambda c: None)
    panel = ScriptPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.update_step(None, 3, None)
    assert panel.step_position_label.text() == "Step: -/3"
    assert panel.step_description_label.text() == ""


def test_script_panel_update_step_zero_total(qtbot):
    manager = SimpleNamespace(send_ipc=lambda c: None)
    panel = ScriptPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.update_step(1, 0, None)
    assert panel.step_position_label.text() == "Step: 1/-"


# ---------------------------------------------------------------------------
# HelpPanel
# ---------------------------------------------------------------------------

def test_help_panel_default_not_hovered(qtbot):
    panel = HelpPanel(manager=SimpleNamespace())
    qtbot.addWidget(panel)
    assert panel._is_hovered is False
    assert "transparent" in panel.styleSheet()


def test_help_panel_enter_event_sets_hovered(qtbot):
    panel = HelpPanel(manager=SimpleNamespace())
    qtbot.addWidget(panel)

    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QEnterEvent
    event = QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0))
    panel.enterEvent(event)
    assert panel._is_hovered is True
    assert "white" in panel.styleSheet()


def test_help_panel_leave_event_resets_hover(qtbot):
    panel = HelpPanel(manager=SimpleNamespace())
    qtbot.addWidget(panel)
    panel._is_hovered = True
    from PyQt6.QtCore import QEvent
    event = QEvent(QEvent.Type.Leave)
    panel.leaveEvent(event)
    assert panel._is_hovered is False


# ---------------------------------------------------------------------------
# ZLUTPanel._format_number
# ---------------------------------------------------------------------------

def test_zlut_panel_format_number_int():
    assert ZLUTPanel._format_number(42) == "42"


def test_zlut_panel_format_number_float_casts_to_int():
    assert ZLUTPanel._format_number(3.7) == "3"


def test_zlut_panel_format_number_with_suffix():
    assert ZLUTPanel._format_number(100, suffix=" nm") == "100 nm"


def test_zlut_panel_format_number_none():
    assert ZLUTPanel._format_number(None) == ""


def test_zlut_panel_format_number_zero_int():
    assert ZLUTPanel._format_number(0, suffix=" nm") == "0 nm"
