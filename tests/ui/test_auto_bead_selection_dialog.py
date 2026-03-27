import os
from threading import Event

import numpy as np
import pytest
from PyQt6.QtCore import QPoint, Qt

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('pytestqt')
pytest.importorskip('PyQt6')

from magscope.auto_bead_selection import AutoBeadCandidate
from magscope.ui.auto_bead_selection_dialog import AutoBeadSelectionDialog


def _build_test_image() -> tuple[np.ndarray, tuple[int, int, int, int]]:
    image = np.zeros((48, 48), dtype=np.uint16)
    template = np.arange(64, dtype=np.uint16).reshape(8, 8)
    seed_roi = (4, 12, 4, 12)
    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = template
    image[6:14, 20:28] = template
    image[28:36, 28:36] = template
    return image, seed_roi


def _wait_for_search_complete(qtbot, dialog: AutoBeadSelectionDialog) -> None:
    qtbot.waitUntil(lambda: not dialog._search_in_progress, timeout=5000)


def test_auto_bead_selection_dialog_updates_preview_with_score_threshold(qtbot):
    image, seed_roi = _build_test_image()
    dialog = AutoBeadSelectionDialog(
        parent=None,
        image=image,
        roi_size=8,
        existing_rois={},
        display_scale=1,
    )
    qtbot.addWidget(dialog)

    assert dialog.step_1_title_label.text() == 'Step 1: Choose a Seed Bead'
    assert dialog.step_2_title_label.text() == 'Step 2: Review and Confirm'
    assert 'Click a bead in the frozen image' in dialog.step_1_body_label.text()
    assert 'Select a seed bead first' in dialog.step_2_body_label.text()

    dialog._set_seed_roi(seed_roi)
    _wait_for_search_complete(qtbot, dialog)

    assert len(dialog.visible_candidates) >= 2
    assert dialog.threshold_value_label.text() != '0%'
    assert 'score threshold' in dialog.status_label.text()
    assert 'Click another bead' in dialog.step_1_body_label.text()
    assert 'Adjust the score threshold' in dialog.step_2_body_label.text()

    dialog.threshold_slider.setValue(dialog.threshold_slider.maximum())

    assert len(dialog.visible_candidates) >= 1
    assert dialog.accept_button.isEnabled()
    assert '%' not in dialog.threshold_value_label.text()


def test_auto_bead_selection_dialog_accepts_visible_rois(qtbot):
    image, seed_roi = _build_test_image()
    dialog = AutoBeadSelectionDialog(
        parent=None,
        image=image,
        roi_size=8,
        existing_rois={},
        display_scale=1,
    )
    qtbot.addWidget(dialog)

    accepted = []
    dialog.selectionAccepted.connect(lambda rois: accepted.append(rois))

    dialog._set_seed_roi(seed_roi)
    _wait_for_search_complete(qtbot, dialog)
    dialog.threshold_slider.setValue(dialog.threshold_slider.maximum())
    dialog._accept_selection()

    assert accepted
    assert accepted[0][0] == seed_roi
    assert accepted[0][1:] == [candidate.roi for candidate in dialog.visible_candidates]


def test_auto_bead_selection_dialog_allows_seed_only_acceptance(qtbot):
    image = np.zeros((40, 40), dtype=np.uint16)
    template = np.arange(64, dtype=np.uint16).reshape(8, 8)
    seed_roi = (4, 12, 4, 12)
    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = template

    dialog = AutoBeadSelectionDialog(
        parent=None,
        image=image,
        roi_size=8,
        existing_rois={},
        display_scale=1,
    )
    qtbot.addWidget(dialog)

    accepted = []
    dialog.selectionAccepted.connect(lambda rois: accepted.append(rois))

    dialog._set_seed_roi(seed_roi)
    _wait_for_search_complete(qtbot, dialog)
    dialog._visible_candidates = []
    dialog._accept_selection()

    assert dialog.accept_button.isEnabled()
    assert accepted == [[seed_roi]]


def test_auto_bead_selection_dialog_skips_overlapping_existing_seed_on_accept(qtbot):
    image, _seed_roi = _build_test_image()
    seed_roi = (5, 13, 4, 12)
    dialog = AutoBeadSelectionDialog(
        parent=None,
        image=image,
        roi_size=8,
        existing_rois={7: (4, 12, 4, 12)},
        display_scale=1,
    )
    qtbot.addWidget(dialog)

    accepted = []
    dialog.selectionAccepted.connect(lambda rois: accepted.append(rois))

    dialog._set_seed_roi(seed_roi)
    _wait_for_search_complete(qtbot, dialog)
    dialog.threshold_slider.setValue(dialog.threshold_slider.maximum())
    dialog._accept_selection()

    assert accepted
    assert seed_roi not in accepted[0]
    assert accepted[0] == [candidate.roi for candidate in dialog.visible_candidates]


def test_auto_bead_selection_dialog_defaults_to_largest_gap_threshold(qtbot):
    image = np.zeros((40, 40), dtype=np.uint16)
    dialog = AutoBeadSelectionDialog(
        parent=None,
        image=image,
        roi_size=8,
        existing_rois={},
        display_scale=1,
    )
    qtbot.addWidget(dialog)

    dialog._candidates = [
        AutoBeadCandidate((0, 8, 0, 8), 0.97),
        AutoBeadCandidate((8, 16, 0, 8), 0.94),
        AutoBeadCandidate((16, 24, 0, 8), 0.91),
        AutoBeadCandidate((24, 32, 0, 8), 0.52),
        AutoBeadCandidate((32, 40, 0, 8), 0.50),
    ]
    dialog._configure_threshold_slider()
    dialog._refresh_visible_candidates()

    assert dialog.threshold_value_label.text() == '0.910'


def test_auto_bead_selection_dialog_shows_no_matches_for_seed_only_image(qtbot):
    image = np.zeros((40, 40), dtype=np.uint16)
    template = np.arange(64, dtype=np.uint16).reshape(8, 8)
    seed_roi = (4, 12, 4, 12)
    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = template

    dialog = AutoBeadSelectionDialog(
        parent=None,
        image=image,
        roi_size=8,
        existing_rois={},
        display_scale=1,
    )
    qtbot.addWidget(dialog)

    dialog._set_seed_roi(seed_roi)
    _wait_for_search_complete(qtbot, dialog)

    assert dialog.visible_candidates == []
    assert dialog.status_label.text() == 'No valid proposed beads were found for the selected seed bead.'
    assert dialog.accept_button.isEnabled()


def test_auto_bead_selection_dialog_blocks_new_seed_during_active_search(qtbot, monkeypatch):
    image, seed_roi = _build_test_image()
    release_search = Event()
    visited_seeds = []

    def fake_detect(image_arg, seed_roi_arg, existing_rois_arg):
        visited_seeds.append(seed_roi_arg)
        release_search.wait(timeout=5)
        return np.zeros((1, 1), dtype=np.float64), []

    monkeypatch.setattr('magscope.ui.auto_bead_selection_dialog.detect_matching_beads', fake_detect)

    dialog = AutoBeadSelectionDialog(
        parent=None,
        image=image,
        roi_size=8,
        existing_rois={},
        display_scale=1,
    )
    qtbot.addWidget(dialog)
    dialog.show()

    dialog._set_seed_roi(seed_roi)
    qtbot.waitUntil(lambda: dialog._search_in_progress, timeout=1000)
    qtbot.waitUntil(lambda: visited_seeds == [seed_roi], timeout=1000)

    assert dialog.search_progress_bar.isVisible()
    assert dialog.search_cancel_button.isVisible()
    assert dialog.search_cancel_button.isEnabled()
    assert dialog.threshold_slider.isEnabled() is False
    assert dialog.accept_button.isEnabled() is False

    dialog._on_scene_clicked(QPoint(24, 24), Qt.MouseButton.LeftButton)

    assert dialog.seed_roi == seed_roi

    release_search.set()
    _wait_for_search_complete(qtbot, dialog)


def test_auto_bead_selection_dialog_cancel_clears_seed_and_ignores_late_results(qtbot, monkeypatch):
    image, seed_roi = _build_test_image()
    release_search = Event()

    def fake_detect(image_arg, seed_roi_arg, existing_rois_arg):
        release_search.wait(timeout=5)
        return np.ones((2, 2), dtype=np.float64), [AutoBeadCandidate((20, 28, 6, 14), 0.9)]

    monkeypatch.setattr('magscope.ui.auto_bead_selection_dialog.detect_matching_beads', fake_detect)

    dialog = AutoBeadSelectionDialog(
        parent=None,
        image=image,
        roi_size=8,
        existing_rois={},
        display_scale=1,
    )
    qtbot.addWidget(dialog)
    dialog.show()

    dialog._set_seed_roi(seed_roi)
    qtbot.waitUntil(lambda: dialog._search_in_progress, timeout=1000)

    dialog.search_cancel_button.click()

    assert dialog.seed_roi is None
    assert dialog.visible_candidates == []
    assert dialog.status_label.text() == 'No seed bead selected yet.'
    assert dialog.accept_button.isEnabled() is False

    release_search.set()
    qtbot.waitUntil(lambda: dialog._search_thread is None, timeout=5000)

    assert dialog.seed_roi is None
    assert dialog.visible_candidates == []
    assert dialog.status_label.text() == 'No seed bead selected yet.'
