from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from multiprocessing.synchronize import Event as EventType
from typing import Iterable

import numpy as np
from scipy.signal import correlate2d


_DEFAULT_CORRELATION_CHUNK_ROWS = 20
_CANDIDATE_PROGRESS_UPDATE_INTERVAL = 512
_MIN_CANDIDATE_SCORE = np.finfo(np.float64).eps


class AutoBeadSearchCancelled(RuntimeError):
    """Raised when auto bead matching is canceled between correlation chunks."""


@dataclass(frozen=True, slots=True)
class AutoBeadCandidate:
    roi: tuple[int, int, int, int]
    score: float


def copy_latest_image(
    image_bytes: memoryview | bytes | bytearray,
    image_shape: tuple[int, int],
    dtype: np.dtype,
) -> np.ndarray:
    """Copy a recent image snapshot in the same orientation shown in the UI."""

    width, height = image_shape
    return np.frombuffer(image_bytes, dtype=dtype).copy().reshape((height, width))


def crop_roi(image: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    """Return the ROI crop from a viewer-oriented ``(height, width)`` image."""

    x0, x1, y0, y1 = roi
    if x0 < 0 or y0 < 0 or x1 > image.shape[1] or y1 > image.shape[0]:
        raise ValueError('ROI is out of bounds for the provided image')
    if x1 <= x0 or y1 <= y0:
        raise ValueError('ROI must have a positive width and height')
    return image[y0:y1, x0:x1]


def normalized_cross_correlation(image: np.ndarray, template: np.ndarray) -> np.ndarray:
    """Compute 2D normalized cross-correlation over valid ROI-sized positions."""

    return normalized_cross_correlation_chunked(image, template)


@lru_cache(maxsize=16)
def _ones_kernel(shape: tuple[int, int]) -> np.ndarray:
    return np.ones(shape, dtype=np.float64)


def _correlate2d_valid_chunked(
    image: np.ndarray,
    kernel: np.ndarray,
    *,
    chunk_rows: int,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> np.ndarray:
    """Compute ``correlate2d(..., mode='valid')`` in row stripes."""

    if image.ndim != 2 or kernel.ndim != 2:
        raise ValueError('image and kernel must both be 2D arrays')
    if kernel.shape[0] > image.shape[0] or kernel.shape[1] > image.shape[1]:
        raise ValueError('kernel must fit inside the image')

    output_height = image.shape[0] - kernel.shape[0] + 1
    output_width = image.shape[1] - kernel.shape[1] + 1
    chunk_rows = max(1, min(int(chunk_rows), output_height))
    total_chunks = (output_height + chunk_rows - 1) // chunk_rows
    output = np.empty((output_height, output_width), dtype=np.float64)

    for chunk_index, row_start in enumerate(range(0, output_height, chunk_rows), start=1):
        if cancel_check is not None and cancel_check():
            raise AutoBeadSearchCancelled('Auto bead selection was canceled')

        row_end = min(row_start + chunk_rows, output_height)
        image_row_end = row_end + kernel.shape[0] - 1
        output[row_start:row_end, :] = correlate2d(
            image[row_start:image_row_end, :],
            kernel,
            mode='valid',
        )

        if progress_callback is not None:
            progress_callback(chunk_index, total_chunks)

    return output


def _window_sum_integral(image: np.ndarray, window_shape: tuple[int, int]) -> np.ndarray:
    """Compute valid sliding-window sums via a summed-area table."""

    window_height, window_width = window_shape
    integral = np.pad(np.cumsum(np.cumsum(image, axis=0), axis=1), ((1, 0), (1, 0)), mode='constant')
    return (
        integral[window_height:, window_width:]
        - integral[:-window_height, window_width:]
        - integral[window_height:, :-window_width]
        + integral[:-window_height, :-window_width]
    )


def normalized_cross_correlation_chunked(
    image: np.ndarray,
    template: np.ndarray,
    *,
    chunk_rows: int = _DEFAULT_CORRELATION_CHUNK_ROWS,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> np.ndarray:
    """Compute 2D normalized cross-correlation over valid ROI-sized positions."""

    if image.ndim != 2 or template.ndim != 2:
        raise ValueError('image and template must both be 2D arrays')
    if template.shape[0] > image.shape[0] or template.shape[1] > image.shape[1]:
        raise ValueError('template must fit inside the image')

    image_f = np.asarray(image, dtype=np.float64)
    template_f = np.asarray(template, dtype=np.float64)
    template_zero_mean = template_f - template_f.mean()
    template_norm = np.sqrt(np.sum(template_zero_mean * template_zero_mean))
    if template_norm == 0:
        raise ValueError('template must have non-zero variance')

    image_height = image_f.shape[0]
    output_height = image_height - template_f.shape[0] + 1
    effective_chunk_rows = max(1, min(int(chunk_rows), output_height))
    numerator_chunks = (output_height + effective_chunk_rows - 1) // effective_chunk_rows
    total_progress_steps = numerator_chunks + 2
    completed_steps = 0

    def report_progress(_chunk_index: int, _total_chunks: int) -> None:
        nonlocal completed_steps
        completed_steps += 1
        if progress_callback is not None:
            progress_callback(completed_steps, total_progress_steps)

    def finish_phase() -> None:
        nonlocal completed_steps
        completed_steps += 1
        if progress_callback is not None:
            progress_callback(completed_steps, total_progress_steps)

    numerator = _correlate2d_valid_chunked(
        image_f,
        template_zero_mean,
        chunk_rows=effective_chunk_rows,
        cancel_check=cancel_check,
        progress_callback=report_progress,
    )
    if cancel_check is not None and cancel_check():
        raise AutoBeadSearchCancelled('Auto bead selection was canceled')
    image_sum = _window_sum_integral(image_f, template_f.shape)
    finish_phase()

    image_squared = np.empty_like(image_f)
    np.square(image_f, out=image_squared)
    if cancel_check is not None and cancel_check():
        raise AutoBeadSearchCancelled('Auto bead selection was canceled')
    image_sum_sq = _window_sum_integral(image_squared, template_f.shape)
    finish_phase()

    if progress_callback is not None and completed_steps < total_progress_steps:
        progress_callback(total_progress_steps, total_progress_steps)

    n = template_f.size
    variance = image_sum_sq - (image_sum * image_sum) / n
    variance = np.maximum(variance, 0.0)
    denominator = np.sqrt(variance) * template_norm

    score_map = np.zeros_like(numerator)
    valid = denominator > 0
    score_map[valid] = numerator[valid] / denominator[valid]
    return score_map


def roi_overlaps(
    roi_a: tuple[int, int, int, int],
    roi_b: tuple[int, int, int, int],
) -> bool:
    ax0, ax1, ay0, ay1 = roi_a
    bx0, bx1, by0, by1 = roi_b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def roi_is_within_image(
    roi: tuple[int, int, int, int],
    image_shape: tuple[int, int],
) -> bool:
    x0, x1, y0, y1 = roi
    return 0 <= x0 < x1 <= image_shape[1] and 0 <= y0 < y1 <= image_shape[0]


def filter_candidates_by_score_threshold(
    candidates: Iterable[AutoBeadCandidate],
    threshold: float,
) -> list[AutoBeadCandidate]:
    return [candidate for candidate in candidates if candidate.score >= threshold]


def default_candidate_score_threshold(
    candidates: Iterable[AutoBeadCandidate],
) -> float:
    """Choose a default score threshold that favors the strongest score cluster."""

    scores = np.asarray(sorted((candidate.score for candidate in candidates), reverse=True), dtype=np.float64)
    if scores.size == 0:
        return np.inf
    if scores.size == 1:
        return float(scores[0])
    if scores.size < 5:
        return float(np.percentile(scores, 75))

    gaps = scores[:-1] - scores[1:]
    gap_index = int(np.argmax(gaps))
    if gaps[gap_index] > 0:
        return float(scores[gap_index])
    return float(np.percentile(scores, 75))


def detect_matching_beads(
    image: np.ndarray,
    seed_roi: tuple[int, int, int, int],
    existing_rois: Iterable[tuple[int, int, int, int]],
    *,
    chunk_rows: int = _DEFAULT_CORRELATION_CHUNK_ROWS,
    cancel_check: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[np.ndarray, list[AutoBeadCandidate]]:
    """Detect non-overlapping ROI candidates that match the seed ROI template."""

    template = crop_roi(image, seed_roi)

    def report_progress(completed_units: int, total_units: int) -> None:
        if progress_callback is not None:
            progress_callback(completed_units, total_units)

    def check_canceled() -> None:
        if cancel_check is not None and cancel_check():
            raise AutoBeadSearchCancelled('Auto bead selection was canceled')

    def report_correlation_progress(completed_steps: int, total_steps: int) -> None:
        report_progress(int((completed_steps * 800) / total_steps), 1000)

    score_map = normalized_cross_correlation_chunked(
        image,
        template,
        chunk_rows=chunk_rows,
        cancel_check=cancel_check,
        progress_callback=report_correlation_progress,
    )
    roi_height, roi_width = template.shape
    report_progress(800, 1000)
    check_canceled()
    flat_scores = score_map.ravel()
    candidate_indices = np.flatnonzero(flat_scores > _MIN_CANDIDATE_SCORE)
    if candidate_indices.size == 0:
        report_progress(1000, 1000)
        return score_map, []

    sorted_order = np.argsort(flat_scores[candidate_indices])[::-1]
    sorted_indices = candidate_indices[sorted_order]

    blocked_x0 = [int(roi[0]) for roi in existing_rois]
    blocked_x1 = [int(roi[1]) for roi in existing_rois]
    blocked_y0 = [int(roi[2]) for roi in existing_rois]
    blocked_y1 = [int(roi[3]) for roi in existing_rois]
    blocked_x0.append(seed_roi[0])
    blocked_x1.append(seed_roi[1])
    blocked_y0.append(seed_roi[2])
    blocked_y1.append(seed_roi[3])

    candidates: list[AutoBeadCandidate] = []
    total_sorted_indices = sorted_indices.size
    score_map_width = score_map.shape[1]
    sorted_y0 = sorted_indices // score_map_width
    sorted_x0 = sorted_indices - sorted_y0 * score_map_width
    image_height, image_width = image.shape
    for index, (y0_raw, x0_raw) in enumerate(zip(sorted_y0, sorted_x0, strict=False), start=1):
        if index == 1 or index % _CANDIDATE_PROGRESS_UPDATE_INTERVAL == 0 or index == total_sorted_indices:
            report_progress(800 + int((index * 200) / total_sorted_indices), 1000)
            check_canceled()

        y0 = int(y0_raw)
        x0 = int(x0_raw)
        score = float(score_map[y0, x0])
        x1 = x0 + roi_width
        y1 = y0 + roi_height
        if x0 < 0 or x1 > image_width or y0 < 0 or y1 > image_height:
            continue

        overlaps_existing = False
        for blocked_index in range(len(blocked_x0)):
            if (
                x0 < blocked_x1[blocked_index]
                and blocked_x0[blocked_index] < x1
                and y0 < blocked_y1[blocked_index]
                and blocked_y0[blocked_index] < y1
            ):
                overlaps_existing = True
                break
        if overlaps_existing:
            continue

        roi = (x0, x1, y0, y1)
        candidate = AutoBeadCandidate(roi=roi, score=score)
        candidates.append(candidate)
        blocked_x0.append(x0)
        blocked_x1.append(x1)
        blocked_y0.append(y0)
        blocked_y1.append(y1)

    report_progress(1000, 1000)
    return score_map, candidates


def run_auto_bead_search_process(
    request_queue,
    result_queue,
    cancel_event: EventType,
    *,
    chunk_rows: int = _DEFAULT_CORRELATION_CHUNK_ROWS,
) -> None:
    """Serve auto-bead search requests from a temporary worker process."""

    while True:
        message = request_queue.get()
        if not isinstance(message, tuple) or not message:
            continue

        kind = message[0]
        if kind == 'shutdown':
            return
        if kind != 'search':
            continue

        _, request_id, image, seed_roi, existing_rois = message

        def report_progress(completed_steps: int, total_steps: int) -> None:
            result_queue.put(('progress', request_id, completed_steps, total_steps))

        try:
            _score_map, candidates = detect_matching_beads(
                image,
                seed_roi,
                existing_rois,
                chunk_rows=chunk_rows,
                cancel_check=cancel_event.is_set,
                progress_callback=report_progress,
            )
        except AutoBeadSearchCancelled:
            result_queue.put(('canceled', request_id))
            continue
        except Exception as exc:
            result_queue.put(('error', request_id, str(exc)))
            continue

        result_queue.put(
            (
                'result',
                request_id,
                [
                    ((int(candidate.roi[0]), int(candidate.roi[1]), int(candidate.roi[2]), int(candidate.roi[3])), float(candidate.score))
                    for candidate in candidates
                ],
            )
        )
