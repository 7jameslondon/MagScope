from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from scipy.signal import correlate2d


@dataclass(frozen=True, slots=True)
class AutoBeadCandidate:
    roi: tuple[int, int, int, int]
    score: float


def copy_latest_image(
    image_bytes: memoryview | bytes | bytearray,
    image_shape: tuple[int, int],
    dtype: np.dtype,
) -> np.ndarray:
    """Copy a recent image snapshot from the shared video buffer."""

    return np.frombuffer(image_bytes, dtype=dtype).copy().reshape(image_shape)


def crop_roi(image: np.ndarray, roi: tuple[int, int, int, int]) -> np.ndarray:
    """Return the ROI crop using MagScope's ``(x, y)`` image indexing."""

    x0, x1, y0, y1 = roi
    if x0 < 0 or y0 < 0 or x1 > image.shape[0] or y1 > image.shape[1]:
        raise ValueError('ROI is out of bounds for the provided image')
    if x1 <= x0 or y1 <= y0:
        raise ValueError('ROI must have a positive width and height')
    return image[x0:x1, y0:y1]


def normalized_cross_correlation(image: np.ndarray, template: np.ndarray) -> np.ndarray:
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

    kernel = np.ones(template_f.shape, dtype=np.float64)
    numerator = correlate2d(image_f, template_zero_mean, mode='valid')
    image_sum = correlate2d(image_f, kernel, mode='valid')
    image_sum_sq = correlate2d(image_f * image_f, kernel, mode='valid')

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
    return 0 <= x0 < x1 <= image_shape[0] and 0 <= y0 < y1 <= image_shape[1]


def score_threshold_for_percentile(
    candidates: Iterable[AutoBeadCandidate],
    percentile: int,
) -> float:
    """Return the minimum score required to keep candidates at a percentile."""

    scores = np.asarray([candidate.score for candidate in candidates], dtype=np.float64)
    if scores.size == 0:
        return np.inf
    percentile = min(max(int(percentile), 0), 100)
    return float(np.percentile(scores, percentile))


def filter_candidates_by_percentile(
    candidates: Iterable[AutoBeadCandidate],
    percentile: int,
) -> list[AutoBeadCandidate]:
    candidates_list = list(candidates)
    threshold = score_threshold_for_percentile(candidates_list, percentile)
    return [candidate for candidate in candidates_list if candidate.score >= threshold]


def detect_matching_beads(
    image: np.ndarray,
    seed_roi: tuple[int, int, int, int],
    existing_rois: Iterable[tuple[int, int, int, int]],
) -> tuple[np.ndarray, list[AutoBeadCandidate]]:
    """Detect non-overlapping ROI candidates that match the seed ROI template."""

    template = crop_roi(image, seed_roi)
    score_map = normalized_cross_correlation(image, template)
    roi_width = template.shape[0]
    roi_height = template.shape[1]

    blocked_rois = [tuple(int(value) for value in roi) for roi in existing_rois]
    blocked_rois.append(seed_roi)

    candidates: list[AutoBeadCandidate] = []
    for flat_index in np.argsort(score_map, axis=None)[::-1]:
        x0, y0 = np.unravel_index(flat_index, score_map.shape)
        roi = (x0, x0 + roi_width, y0, y0 + roi_height)
        if not roi_is_within_image(roi, image.shape):
            continue
        if any(roi_overlaps(roi, blocked_roi) for blocked_roi in blocked_rois):
            continue
        candidate = AutoBeadCandidate(roi=roi, score=float(score_map[x0, y0]))
        candidates.append(candidate)
        blocked_rois.append(roi)

    return score_map, candidates
