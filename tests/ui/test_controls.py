"""Isolated unit tests for control panel classes from magscope/ui/controls.py."""
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import QWidget

from magscope.ipc_commands import StartNewTrackingDataFileCommand, UpdateSettingsCommand
from magscope.settings import (
    MagScopeSettings,
    SAVE_TRACKING_ROI_POSITIONS_SETTING,
    TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING,
    TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING,
)
from magscope.scripting import ScriptStatus
from magscope.ui.controls import (
    AcquisitionPanel,
    BeadSelectionPanel,
    CameraPanel,
    HelpPanel,
    MagScopeSettingsPanel,
    SavingSettingsPanel,
    ScriptPanel,
    StatusPanel,
    ZLUTPanel,
    _LockActivityIndicator,
    _LockNumberInput,
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
    expected = time.strftime("%Y/%m/%d %I:%M:%S %p", time.localtime(5000.0))
    assert panel.video_buffer_purge_label.text() == f"Video Buffer Purged at: {expected}"


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


# ---------------------------------------------------------------------------
# _LockActivityIndicator
# ---------------------------------------------------------------------------

def test_lock_activity_indicator_tick_increments_progress(qtbot):
    indicator = _LockActivityIndicator()
    qtbot.addWidget(indicator)
    indicator._active = True
    indicator._recalc_interval()
    indicator._progress = 0
    indicator._tick()
    assert indicator._progress > 0


def test_lock_activity_indicator_reset_zeros_progress_when_active(qtbot):
    indicator = _LockActivityIndicator()
    qtbot.addWidget(indicator)
    indicator._active = True
    indicator._progress = 50
    indicator.reset()
    assert indicator._progress == 0


def test_lock_activity_indicator_reset_noop_when_inactive(qtbot):
    indicator = _LockActivityIndicator()
    qtbot.addWidget(indicator)
    indicator._active = False
    indicator._progress = 50
    indicator.reset()
    assert indicator._progress == 50


def test_lock_activity_indicator_set_active_starts_timer(qtbot):
    indicator = _LockActivityIndicator()
    qtbot.addWidget(indicator)
    indicator.set_active(True)
    assert indicator._active is True
    assert indicator._progress == 0
    assert indicator._timer.isActive()


def test_lock_activity_indicator_set_active_stops_timer(qtbot):
    indicator = _LockActivityIndicator()
    qtbot.addWidget(indicator)
    indicator.set_active(True)
    indicator.set_active(False)
    assert indicator._active is False
    assert not indicator._timer.isActive()
    assert indicator._progress == 0


def test_lock_activity_indicator_trigger_once_flash(qtbot):
    indicator = _LockActivityIndicator()
    qtbot.addWidget(indicator)
    indicator.trigger_once_flash()
    assert indicator._flash_mode is True
    assert indicator._progress == indicator._MAXIMUM


def test_lock_activity_indicator_set_cycle_duration(qtbot):
    indicator = _LockActivityIndicator()
    qtbot.addWidget(indicator)
    indicator.set_cycle_duration(5.0)
    assert indicator._cycle_seconds == 5.0


def test_lock_activity_indicator_set_cycle_duration_clamps_min(qtbot):
    indicator = _LockActivityIndicator()
    qtbot.addWidget(indicator)
    indicator.set_cycle_duration(0.0)
    assert indicator._cycle_seconds == 0.1


# ---------------------------------------------------------------------------
# _LockNumberInput
# ---------------------------------------------------------------------------

def test_lock_number_input_int_creates_spinbox(qtbot):
    widget = _LockNumberInput(
        "Count", default=10, unit="beads", is_int=True, minimum=0, maximum=100,
    )
    qtbot.addWidget(widget)
    from PyQt6.QtWidgets import QSpinBox
    assert isinstance(widget.spinbox, QSpinBox)


def test_lock_number_input_float_creates_double_spinbox(qtbot):
    widget = _LockNumberInput(
        "Gain", default=0.5, unit="", is_int=False, minimum=0.0, maximum=1.0, decimals=2,
    )
    qtbot.addWidget(widget)
    from PyQt6.QtWidgets import QDoubleSpinBox
    assert isinstance(widget.spinbox, QDoubleSpinBox)


def test_lock_number_input_hides_unit_label(qtbot):
    widget = _LockNumberInput(
        "Count", default=0, unit="beads", is_int=True, minimum=0, maximum=100,
        show_unit_label=False,
    )
    qtbot.addWidget(widget)
    assert widget.unit_label.isHidden()


def test_lock_number_input_min_max_propagated(qtbot):
    widget = _LockNumberInput(
        "Value", default=10, unit="", is_int=True, minimum=5, maximum=50,
    )
    qtbot.addWidget(widget)
    assert widget.spinbox.minimum() == 5
    assert widget.spinbox.maximum() == 50


def test_lock_number_input_lineedit_property(qtbot):
    widget = _LockNumberInput(
        "Value", default=10, unit="", is_int=True, minimum=0, maximum=100,
    )
    qtbot.addWidget(widget)
    lineedit = widget.lineedit
    from PyQt6.QtWidgets import QLineEdit
    assert isinstance(lineedit, QLineEdit)


# ---------------------------------------------------------------------------
# Panel search_targets and simple methods
# ---------------------------------------------------------------------------

def test_acquisition_panel_search_targets(qtbot):
    from magscope.ui.search import PanelControlTarget
    manager = SimpleNamespace(
        _acquisition_on=False,
        _acquisition_mode='Track',
        _acquisition_dir_on=False,
        _acquisition_dir='',
        settings={'acquisition dir default': ''},
        camera_type=SimpleNamespace(settings=[]),
        send_ipc=lambda c: None,
    )
    panel = AcquisitionPanel(manager=manager)
    qtbot.addWidget(panel)
    targets = panel.search_targets()
    assert isinstance(targets, list)
    assert len(targets) > 0


def test_bead_selection_panel_search_targets(qtbot):
    from magscope.ui.search import PanelControlTarget
    manager = SimpleNamespace(
        settings={'ROI': 64},
        bead_next_id=SimpleNamespace(value=1),
        reset_bead_ids=lambda: None,
        clear_beads=lambda: None,
        send_ipc=lambda c: None,
    )
    panel = BeadSelectionPanel(manager=manager)
    qtbot.addWidget(panel)
    targets = panel.search_targets()
    assert isinstance(targets, list)
    assert len(targets) > 0


def test_bead_selection_panel_update_next_bead_id_label(qtbot):
    manager = SimpleNamespace(
        settings={'ROI': 64},
        bead_next_id=SimpleNamespace(value=1),
        reset_bead_ids=lambda: None,
        clear_beads=lambda: None,
        send_ipc=lambda c: None,
    )
    panel = BeadSelectionPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.update_next_bead_id_label(5)
    assert "5" in panel.next_bead_id_label.text()


def test_acquisition_panel_set_acquisition_dir_text_none(qtbot):
    manager = SimpleNamespace(
        _acquisition_on=False,
        _acquisition_mode='Track',
        _acquisition_dir_on=False,
        _acquisition_dir='',
        settings={'acquisition dir default': ''},
        camera_type=SimpleNamespace(settings=[]),
        send_ipc=lambda c: None,
    )
    panel = AcquisitionPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.set_acquisition_dir_text(None)
    assert panel.acquisition_dir_textedit.text() == AcquisitionPanel.NO_DIRECTORY_SELECTED_TEXT


def test_acquisition_panel_set_acquisition_dir_text_path(qtbot):
    manager = SimpleNamespace(
        _acquisition_on=False,
        _acquisition_mode='Track',
        _acquisition_dir_on=False,
        _acquisition_dir='',
        settings={'acquisition dir default': ''},
        camera_type=SimpleNamespace(settings=[]),
        send_ipc=lambda c: None,
    )
    panel = AcquisitionPanel(manager=manager)
    qtbot.addWidget(panel)
    panel.set_acquisition_dir_text("/some/path")
    assert "/some/path" in panel.acquisition_dir_textedit.text()


def test_magscope_settings_panel_excludes_tracking_save_controls(qtbot):
    manager = SimpleNamespace(
        settings=MagScopeSettings(),
        send_ipc=lambda command: None,
    )
    panel = MagScopeSettingsPanel(manager=manager)
    qtbot.addWidget(panel)

    assert SAVE_TRACKING_ROI_POSITIONS_SETTING not in panel._setting_checkboxes
    assert TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING not in panel._setting_checkboxes
    assert TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING not in panel._setting_inputs
    assert not hasattr(panel, "start_new_tracking_file_button")


def test_saving_settings_panel_uses_checkbox_for_tracking_roi_save(qtbot):
    commands = []
    manager = SimpleNamespace(
        settings=MagScopeSettings(),
        send_ipc=commands.append,
    )
    panel = SavingSettingsPanel(manager=manager)
    qtbot.addWidget(panel)

    checkbox = panel._setting_checkboxes[SAVE_TRACKING_ROI_POSITIONS_SETTING]
    assert SAVE_TRACKING_ROI_POSITIONS_SETTING not in panel._setting_inputs

    checkbox.setChecked(True)

    assert isinstance(commands[-1], UpdateSettingsCommand)
    assert commands[-1].settings[SAVE_TRACKING_ROI_POSITIONS_SETTING] is True


def test_saving_settings_panel_tracks_tracking_file_rotation_controls(qtbot):
    commands = []
    manager = SimpleNamespace(
        settings=MagScopeSettings(),
        send_ipc=commands.append,
    )
    panel = SavingSettingsPanel(manager=manager)
    qtbot.addWidget(panel)

    checkbox = panel._setting_checkboxes[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING]
    duration_input = panel._setting_inputs[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING]
    assert panel.start_new_tracking_file_button.text() == "Start New Tracking File"

    checkbox.setChecked(False)
    assert isinstance(commands[-1], UpdateSettingsCommand)
    assert commands[-1].settings[TRACKING_DATA_FILE_ROTATION_ENABLED_SETTING] is False

    duration_input.setText("15")
    duration_input.editingFinished.emit()
    assert isinstance(commands[-1], UpdateSettingsCommand)
    assert commands[-1].settings[TRACKING_DATA_FILE_ROTATION_INTERVAL_MINUTES_SETTING] == 15

    qtbot.mouseClick(panel.start_new_tracking_file_button, Qt.MouseButton.LeftButton)
    assert isinstance(commands[-1], StartNewTrackingDataFileCommand)
