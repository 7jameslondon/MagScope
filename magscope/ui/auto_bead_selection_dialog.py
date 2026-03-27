from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from magscope.auto_bead_selection import (
    AutoBeadCandidate,
    default_candidate_score_threshold,
    detect_matching_beads,
    filter_candidates_by_score_threshold,
    roi_overlaps,
)
from magscope.ui.video_viewer import VideoViewer
from magscope.ui.widgets import BeadGraphic
from magscope.utils import numpy_type_to_qt_image_type

if TYPE_CHECKING:
    from PyQt6.QtCore import QPoint


class _AutoBeadSearchWorker(QObject):
    failed = pyqtSignal(int, str)
    finished = pyqtSignal(int, object, object)

    def __init__(
        self,
        *,
        request_id: int,
        image: np.ndarray,
        seed_roi: tuple[int, int, int, int],
        existing_rois: tuple[tuple[int, int, int, int], ...],
    ):
        super().__init__()
        self._request_id = request_id
        self._image = image
        self._seed_roi = seed_roi
        self._existing_rois = existing_rois

    @pyqtSlot()
    def run(self) -> None:
        try:
            score_map, candidates = detect_matching_beads(
                self._image,
                self._seed_roi,
                self._existing_rois,
            )
        except Exception as exc:
            self.failed.emit(self._request_id, str(exc))
            return

        self.finished.emit(self._request_id, score_map, candidates)


class AutoBeadSelectionDialog(QDialog):
    selectionAccepted = pyqtSignal(object)
    SLIDER_STEPS = 1000

    def __init__(
        self,
        *,
        parent,
        image: np.ndarray,
        roi_size: int,
        existing_rois: dict[int, tuple[int, int, int, int]],
        display_scale: int,
    ):
        super().__init__(parent)
        self.setWindowTitle('Auto Bead Selection')
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(900, 780)

        self._image = np.asarray(image)
        self._roi_size = int(roi_size)
        self._existing_rois = dict(existing_rois)
        self._display_scale = max(1, int(display_scale))
        self._seed_roi: tuple[int, int, int, int] | None = None
        self._candidates: list[AutoBeadCandidate] = []
        self._visible_candidates: list[AutoBeadCandidate] = []
        self._candidate_min_score = 0.0
        self._candidate_max_score = 1.0
        self._score_map: np.ndarray | None = None
        self._next_search_request_id = 0
        self._active_search_request_id: int | None = None
        self._search_in_progress = False
        self._search_thread: QThread | None = None
        self._search_worker: _AutoBeadSearchWorker | None = None

        layout = QVBoxLayout(self)

        self.step_1_card = self._create_instruction_card(
            'autoBeadStep1',
            'Step 1: Choose a Seed Bead',
            'Click a bead in the frozen image to choose the seed ROI for auto selection.',
        )
        self.step_1_title_label = self.step_1_card.findChild(QLabel, 'autoBeadStep1Title')
        self.step_1_body_label = self.step_1_card.findChild(QLabel, 'autoBeadStep1Body')
        layout.addWidget(self.step_1_card)

        self.step_2_card = self._create_instruction_card(
            'autoBeadStep2',
            'Step 2: Review and Confirm',
            'Select a seed bead first. Then adjust the score threshold to refine the highlighted matches before accepting them.',
        )
        self.step_2_title_label = self.step_2_card.findChild(QLabel, 'autoBeadStep2Title')
        self.step_2_body_label = self.step_2_card.findChild(QLabel, 'autoBeadStep2Body')
        layout.addWidget(self.step_2_card)

        self._set_instruction_cards_state(has_seed=False, has_candidates=False)

        self.video_viewer = VideoViewer()
        self.video_viewer.setMinimumHeight(420)
        self.video_viewer.set_pixmap(self._image_to_pixmap())
        self.video_viewer.reset_view()
        self.video_viewer.sceneClicked.connect(self._on_scene_clicked)
        layout.addWidget(self.video_viewer, 1)

        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel('Score Threshold'))
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setRange(0, self.SLIDER_STEPS)
        self.threshold_slider.setValue(0)
        self.threshold_slider.valueChanged.connect(self._refresh_visible_candidates)
        slider_row.addWidget(self.threshold_slider, 1)
        self.threshold_value_label = QLabel()
        slider_row.addWidget(self.threshold_value_label)
        layout.addLayout(slider_row)

        progress_row = QHBoxLayout()
        self.search_progress_label = QLabel('Searching for matching beads...')
        progress_row.addWidget(self.search_progress_label)
        self.search_progress_bar = QProgressBar()
        self.search_progress_bar.setRange(0, 0)
        progress_row.addWidget(self.search_progress_bar, 1)
        self.search_cancel_button = QPushButton('Cancel Search')
        self.search_cancel_button.clicked.connect(self._cancel_search)
        progress_row.addWidget(self.search_cancel_button)
        layout.addLayout(progress_row)

        self.status_label = QLabel('No seed bead selected yet.')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.close_button = QPushButton('Close')
        self.close_button.clicked.connect(self.reject)
        button_row.addWidget(self.close_button)
        self.accept_button = QPushButton('Accept Proposed Beads')
        self.accept_button.clicked.connect(self._accept_selection)
        button_row.addWidget(self.accept_button)
        layout.addLayout(button_row)

        self._set_search_ui_state(False)
        self._refresh_visible_candidates()

    def _create_instruction_card(self, name: str, title: str, body: str) -> QFrame:
        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        card.setObjectName(name)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setObjectName(f'{name}Title')
        body_label = QLabel(body)
        body_label.setWordWrap(True)
        body_label.setObjectName(f'{name}Body')
        layout.addWidget(title_label)
        layout.addWidget(body_label)
        return card

    @property
    def seed_roi(self) -> tuple[int, int, int, int] | None:
        return self._seed_roi

    @property
    def visible_candidates(self) -> list[AutoBeadCandidate]:
        return list(self._visible_candidates)

    def _image_to_pixmap(self) -> QPixmap:
        display_image = np.ascontiguousarray(self._image)
        if self._display_scale != 1:
            display_image = np.ascontiguousarray(display_image * self._display_scale)
        image_bytes = display_image.tobytes()
        qimage = QImage(
            image_bytes,
            display_image.shape[1],
            display_image.shape[0],
            display_image.strides[0],
            numpy_type_to_qt_image_type(display_image.dtype.type),
        )
        return QPixmap.fromImage(qimage.copy())

    def _accept_selection(self) -> None:
        if self._seed_roi is None or self._search_in_progress:
            return

        accepted_rois: list[tuple[int, int, int, int]] = []
        if not any(roi_overlaps(self._seed_roi, roi) for roi in self._existing_rois.values()):
            accepted_rois.append(self._seed_roi)
        for candidate in self._visible_candidates:
            if candidate.roi != self._seed_roi:
                accepted_rois.append(candidate.roi)

        self.selectionAccepted.emit(accepted_rois)
        self.accept()

    def _on_scene_clicked(self, pos: 'QPoint', button) -> None:
        if button != Qt.MouseButton.LeftButton or self._search_in_progress:
            return
        seed_roi = BeadGraphic.clamp_roi_to_scene(
            BeadGraphic.roi_from_center(pos.x(), pos.y(), self._roi_size),
            self.video_viewer.image_scene_rect(),
        )
        self._set_seed_roi(seed_roi)

    def _set_seed_roi(self, seed_roi: tuple[int, int, int, int]) -> None:
        if self._search_in_progress:
            return

        self._seed_roi = seed_roi
        self._reset_search_results()
        self.status_label.setText('Searching for matching beads...')
        self._set_instruction_cards_state(has_seed=True, has_candidates=False)
        self._set_search_ui_state(True)
        self._update_overlay()

        self._next_search_request_id += 1
        request_id = self._next_search_request_id
        self._active_search_request_id = request_id

        worker = _AutoBeadSearchWorker(
            request_id=request_id,
            image=self._image,
            seed_roi=seed_roi,
            existing_rois=tuple(self._existing_rois.values()),
        )
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_search_finished)
        worker.failed.connect(self._on_search_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_search_thread_finished)

        self._search_worker = worker
        self._search_thread = thread
        thread.start()

    def _reset_search_results(self) -> None:
        self._score_map = None
        self._candidates = []
        self._visible_candidates = []
        self._candidate_min_score = 0.0
        self._candidate_max_score = 1.0

    def _clear_seed_and_results(self) -> None:
        self._seed_roi = None
        self._reset_search_results()
        self._configure_threshold_slider()
        self._refresh_visible_candidates()

    def _set_search_ui_state(self, in_progress: bool) -> None:
        self._search_in_progress = in_progress
        self.search_progress_label.setVisible(in_progress)
        self.search_progress_bar.setVisible(in_progress)
        self.search_cancel_button.setVisible(in_progress)
        self.search_cancel_button.setEnabled(in_progress)
        self.threshold_slider.setEnabled(not in_progress and self.threshold_slider.maximum() > 0)
        self.accept_button.setEnabled(not in_progress and self._seed_roi is not None)
        self.close_button.setEnabled(not in_progress and self._search_thread is None)

    def _cancel_search(self) -> None:
        if not self._search_in_progress:
            return

        self._active_search_request_id = None
        self._set_search_ui_state(False)
        self._clear_seed_and_results()

    @pyqtSlot(int, object, object)
    def _on_search_finished(
        self,
        request_id: int,
        score_map: np.ndarray,
        candidates: list[AutoBeadCandidate],
    ) -> None:
        if request_id != self._active_search_request_id or self._seed_roi is None:
            return

        self._score_map = score_map
        self._candidates = list(candidates)
        self._configure_threshold_slider()
        self._set_search_ui_state(False)
        self._refresh_visible_candidates()

    @pyqtSlot(int, str)
    def _on_search_failed(self, request_id: int, message: str) -> None:
        if request_id != self._active_search_request_id:
            return

        self._active_search_request_id = None
        self._set_search_ui_state(False)
        self._clear_seed_and_results()
        self.status_label.setText(f'Auto bead selection failed: {message}')

    @pyqtSlot()
    def _on_search_thread_finished(self) -> None:
        thread = self.sender()
        if thread is self._search_thread:
            self._search_thread = None
            self._search_worker = None
            if not self._search_in_progress:
                self._set_search_ui_state(False)
        if self._search_in_progress:
            self._active_search_request_id = None
            self._set_search_ui_state(False)

    def _configure_threshold_slider(self) -> None:
        if not self._candidates:
            self._candidate_min_score = 0.0
            self._candidate_max_score = 1.0
            self.threshold_slider.blockSignals(True)
            self.threshold_slider.setRange(0, 0)
            self.threshold_slider.setValue(0)
            self.threshold_slider.setEnabled(False)
            self.threshold_slider.blockSignals(False)
            return

        scores = np.asarray([candidate.score for candidate in self._candidates], dtype=np.float64)
        self._candidate_min_score = float(scores.min())
        self._candidate_max_score = float(scores.max())
        default_threshold = default_candidate_score_threshold(self._candidates)
        default_value = self._score_to_slider_value(default_threshold)

        self.threshold_slider.blockSignals(True)
        self.threshold_slider.setRange(0, self.SLIDER_STEPS)
        self.threshold_slider.setValue(default_value)
        self.threshold_slider.setEnabled(not np.isclose(self._candidate_min_score, self._candidate_max_score))
        self.threshold_slider.blockSignals(False)

    def _score_to_slider_value(self, score: float) -> int:
        if np.isclose(self._candidate_min_score, self._candidate_max_score):
            return 0
        ratio = (float(score) - self._candidate_min_score) / (
            self._candidate_max_score - self._candidate_min_score
        )
        ratio = min(max(ratio, 0.0), 1.0)
        return int(round(ratio * self.SLIDER_STEPS))

    def _slider_value_to_score(self, slider_value: int) -> float:
        if np.isclose(self._candidate_min_score, self._candidate_max_score):
            return self._candidate_min_score
        ratio = min(max(int(slider_value), 0), self.SLIDER_STEPS) / self.SLIDER_STEPS
        return self._candidate_min_score + ratio * (
            self._candidate_max_score - self._candidate_min_score
        )

    def _refresh_visible_candidates(self) -> None:
        threshold = self._slider_value_to_score(self.threshold_slider.value())
        self.threshold_value_label.setText(f'{threshold:.3f}')

        if self._seed_roi is None:
            self._visible_candidates = []
            if not self._search_in_progress:
                self.accept_button.setEnabled(False)
                self.status_label.setText('No seed bead selected yet.')
            self._set_instruction_cards_state(has_seed=False, has_candidates=False)
            self._update_overlay()
            return

        if self._search_in_progress:
            self._visible_candidates = []
            self.accept_button.setEnabled(False)
            self._set_instruction_cards_state(has_seed=True, has_candidates=False)
            self._update_overlay()
            return

        self._visible_candidates = filter_candidates_by_score_threshold(self._candidates, threshold)
        self.accept_button.setEnabled(True)

        if self._candidates:
            self._set_instruction_cards_state(has_seed=True, has_candidates=bool(self._visible_candidates))
            self.status_label.setText(
                f'Showing {len(self._visible_candidates)} of {len(self._candidates)} proposed beads '
                f'at score threshold {threshold:.3f} '
                f'(candidate range {self._candidate_min_score:.3f} to {self._candidate_max_score:.3f}).'
            )
        else:
            self._set_instruction_cards_state(has_seed=True, has_candidates=False)
            self.status_label.setText('No valid proposed beads were found for the selected seed bead.')

        self._update_overlay()

    def _set_instruction_cards_state(self, *, has_seed: bool, has_candidates: bool) -> None:
        if has_seed:
            if self._search_in_progress:
                self.step_1_body_label.setText('Searching for matching beads. Wait for the search to finish or cancel it to choose a different seed ROI.')
                self.step_2_body_label.setText('Search is in progress. Review and threshold controls are unavailable until it completes or is canceled.')
            elif has_candidates:
                self.step_1_body_label.setText('Click another bead in the frozen image any time to choose a different seed ROI.')
                self.step_2_body_label.setText(
                    'Adjust the score threshold to refine the highlighted matches, then click Accept Proposed Beads.'
                )
            else:
                self.step_1_body_label.setText('Click another bead in the frozen image any time to choose a different seed ROI.')
                self.step_2_body_label.setText(
                    'No additional matches are highlighted. You can accept the seed bead alone or click another bead to try again.'
                )
        else:
            self.step_1_body_label.setText('Click a bead in the frozen image to choose the seed ROI for auto selection.')
            self.step_2_body_label.setText(
                'Select a seed bead first. Then adjust the score threshold to refine the highlighted matches before accepting them.'
            )

        self._apply_instruction_card_style(self.step_1_card, active=not has_seed)
        self._apply_instruction_card_style(self.step_2_card, active=has_seed)

    def _apply_instruction_card_style(self, card: QFrame, *, active: bool) -> None:
        if active:
            background = '#eef5ff'
            border = '#aac4ee'
            title_color = '#17365d'
            body_color = '#26476f'
        else:
            background = '#f4f6f8'
            border = '#cfd8e3'
            title_color = '#51657f'
            body_color = '#6b7d93'

        card.setStyleSheet(
            f'QFrame#{card.objectName()} {{'
            f' background-color: {background};'
            f' border: 1px solid {border};'
            ' border-radius: 6px;'
            '}'
            f'QLabel#{card.objectName()}Title {{'
            ' font-weight: 700;'
            f' color: {title_color};'
            '}'
            f'QLabel#{card.objectName()}Body {{'
            f' color: {body_color};'
            '}'
        )

    def _update_overlay(self) -> None:
        overlay_rois: dict[int, tuple[int, int, int, int]] = {}
        label_overrides: dict[int, str] = {}
        state_overrides: dict[int, str] = {}

        for bead_id, roi in self._existing_rois.items():
            overlay_rois[bead_id] = roi

        if self._seed_roi is not None:
            overlay_rois[-1] = self._seed_roi
            label_overrides[-1] = 'seed'
            state_overrides[-1] = 'selected'

        for index, candidate in enumerate(self._visible_candidates, start=2):
            bead_id = -index
            overlay_rois[bead_id] = candidate.roi
            label_overrides[bead_id] = f'{candidate.score:.3f}'
            state_overrides[bead_id] = 'reference'

        self.video_viewer.set_bead_overlay(
            overlay_rois,
            active_bead_id=None,
            selected_bead_id=None,
            reference_bead_id=None,
            label_overrides=label_overrides,
            state_overrides=state_overrides,
        )
        self.video_viewer.viewport().update()

    def reject(self) -> None:
        if self._search_in_progress or self._search_thread is not None:
            return
        super().reject()
