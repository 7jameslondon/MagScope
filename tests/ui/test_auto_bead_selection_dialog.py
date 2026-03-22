import os

import numpy as np
import pytest

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

    dialog._set_seed_roi(seed_roi)

    assert len(dialog.visible_candidates) >= 2
    assert dialog.threshold_value_label.text() != '0%'
    assert 'score threshold' in dialog.status_label.text()

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
    dialog._visible_candidates = []
    dialog._accept_selection()

    assert dialog.accept_button.isEnabled()
    assert accepted == [[seed_roi]]


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
