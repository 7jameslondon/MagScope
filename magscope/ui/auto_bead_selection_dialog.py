from __future__ import annotations

import multiprocessing as mp
from queue import Empty
from typing import TYPE_CHECKING

import numpy as np
from PyQt6.QtCore import QTimer, Qt, pyqtSignal, pyqtSlot
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
    filter_candidates_by_score_threshold,
    roi_overlaps,
    run_auto_bead_search_process,
)
from magscope.ui.video_viewer import VideoViewer
from magscope.ui.widgets import BeadGraphic
from magscope.utils import numpy_type_to_qt_image_type

if TYPE_CHECKING:
    from PyQt6.QtCore import QPoint


class _AutoBeadSearchProcessBackend:
    _NO_ACTIVE_REQUEST_ID = -1

    def __init__(self) -> None:
        self._context = mp.get_context('spawn')
        self._request_queue = self._context.Queue()
        self._result_queue = self._context.Queue()
        self._active_request_id = self._context.Value('q', self._NO_ACTIVE_REQUEST_ID)
        self._process = self._context.Process(
            target=run_auto_bead_search_process,
            args=(self._request_queue, self._result_queue, self._active_request_id),
            daemon=True,
        )
        self._process.start()

    def start_search(
        self,
        *,
        request_id: int,
        image: np.ndarray,
        seed_roi: tuple[int, int, int, int],
        existing_rois: tuple[tuple[int, int, int, int], ...],
    ) -> None:
        with self._active_request_id.get_lock():
            self._active_request_id.value = int(request_id)
        self._request_queue.put(('search', request_id, image, seed_roi, existing_rois))

    def cancel_search(self) -> None:
        with self._active_request_id.get_lock():
            self._active_request_id.value = self._NO_ACTIVE_REQUEST_ID

    def poll_messages(self) -> list[tuple]:
        messages: list[tuple] = []
        while True:
            try:
                messages.append(self._result_queue.get_nowait())
            except Empty:
                return messages

    def shutdown(self) -> None:
        if self._process is None:
            return

        if self._process.is_alive():
            self.cancel_search()
            self._request_queue.put(('shutdown',))
            self._process.join(timeout=1.0)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=1.0)

        self._request_queue.close()
        self._result_queue.close()
        self._process = None


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
        self._search_backend: _AutoBeadSearchProcessBackend | None = None
        self._search_poll_timer = QTimer(self)
        self._search_poll_timer.setInterval(25)
        self._search_poll_timer.timeout.connect(self._poll_search_backend)

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
        self.search_progress_bar.setRange(0, 100)
        self.search_progress_bar.setValue(0)
        self.search_progress_bar.setTextVisible(True)
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

    def _create_search_backend(self) -> _AutoBeadSearchProcessBackend:
        return _AutoBeadSearchProcessBackend()

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

        if self._search_backend is None:
            self._search_backend = self._create_search_backend()
            self._search_poll_timer.start()

        self._search_backend.start_search(
            request_id=request_id,
            image=self._image,
            seed_roi=seed_roi,
            existing_rois=tuple(self._existing_rois.values()),
        )

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
        if in_progress:
            self.search_progress_label.setText('Searching for matching beads...')
            self.search_progress_bar.setRange(0, 1)
            self.search_progress_bar.setValue(0)
        self.threshold_slider.setEnabled(not in_progress and self.threshold_slider.maximum() > 0)
        self.accept_button.setEnabled(not in_progress and self._seed_roi is not None)
        self.close_button.setEnabled(not in_progress)

    def _cancel_search(self) -> None:
        if not self._search_in_progress:
            return

        self._active_search_request_id = None
        if self._search_backend is not None:
            self._search_backend.cancel_search()
        self._set_search_ui_state(False)
        self._clear_seed_and_results()

    @pyqtSlot()
    def _poll_search_backend(self) -> None:
        if self._search_backend is None:
            return

        for message in self._search_backend.poll_messages():
            if not isinstance(message, tuple) or not message:
                continue
            kind = message[0]
            if kind == 'progress':
                _, request_id, completed_steps, total_steps = message
                if request_id == self._active_search_request_id:
                    self._on_search_progress_changed(completed_steps, total_steps)
            elif kind == 'canceled':
                _, request_id = message
                self._on_search_canceled(request_id)
            elif kind == 'result':
                _, request_id, candidate_payload = message
                self._on_search_finished(request_id, candidate_payload)
            elif kind == 'error':
                _, request_id, error_message = message
                self._on_search_failed(request_id, error_message)

    @pyqtSlot(int, int)
    def _on_search_progress_changed(self, completed_steps: int, total_steps: int) -> None:
        if total_steps <= 0 or not self._search_in_progress:
            return

        self.search_progress_bar.setRange(0, total_steps)
        self.search_progress_bar.setValue(min(completed_steps, total_steps))
        if completed_steps >= int(total_steps * 0.8):
            self.search_progress_label.setText('Ranking candidate matches...')
            self.status_label.setText('Ranking candidate matches...')
        else:
            self.search_progress_label.setText('Searching for matching beads...')
            self.status_label.setText('Searching for matching beads...')

    @pyqtSlot(int)
    def _on_search_canceled(self, request_id: int) -> None:
        if self._active_search_request_id not in (None, request_id):
            return

        self._active_search_request_id = None

    @pyqtSlot(int, object)
    def _on_search_finished(
        self,
        request_id: int,
        candidate_payload: list[tuple[tuple[int, int, int, int], float]],
    ) -> None:
        if request_id != self._active_search_request_id or self._seed_roi is None:
            return

        self._active_search_request_id = None
        self._score_map = None
        self._candidates = [
            AutoBeadCandidate(tuple(int(value) for value in roi), float(score))
            for roi, score in candidate_payload
        ]
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

    def _shutdown_search_backend(self) -> None:
        self._search_poll_timer.stop()
        if self._search_backend is None:
            return
        self._search_backend.shutdown()
        self._search_backend = None

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
        if self._search_in_progress:
            return
        self._shutdown_search_backend()
        super().reject()

    def accept(self) -> None:
        if self._search_in_progress:
            return
        self._shutdown_search_backend()
        super().accept()

    def force_close(self) -> None:
        self._search_in_progress = False
        self._shutdown_search_backend()
        self.close()

    def closeEvent(self, event) -> None:
        if self._search_in_progress:
            event.ignore()
            return
        self._shutdown_search_backend()
        super().closeEvent(event)
