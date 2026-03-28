import numpy as np
import pytest
from scipy.signal import correlate2d

from magscope.auto_bead_selection import (
    AutoBeadCandidate,
    AutoBeadSearchCancelled,
    copy_latest_image,
    default_candidate_score_threshold,
    detect_matching_beads,
    filter_candidates_by_score_threshold,
    normalized_cross_correlation,
    normalized_cross_correlation_chunked,
    roi_overlaps,
)


def _normalized_cross_correlation_reference(image: np.ndarray, template: np.ndarray) -> np.ndarray:
    image_f = np.asarray(image, dtype=np.float64)
    template_f = np.asarray(template, dtype=np.float64)
    template_zero_mean = template_f - template_f.mean()
    template_norm = np.sqrt(np.sum(template_zero_mean * template_zero_mean))
    kernel = np.ones(template_f.shape, dtype=np.float64)

    numerator = correlate2d(image_f, template_zero_mean, mode='valid')
    image_sum = correlate2d(image_f, kernel, mode='valid')
    image_sum_sq = correlate2d(image_f * image_f, kernel, mode='valid')

    variance = image_sum_sq - (image_sum * image_sum) / template_f.size
    variance = np.maximum(variance, 0.0)
    denominator = np.sqrt(variance) * template_norm

    score_map = np.zeros_like(numerator)
    valid = denominator > 0
    score_map[valid] = numerator[valid] / denominator[valid]
    return score_map


def test_detect_matching_beads_excludes_seed_existing_and_overlaps():
    image = np.zeros((40, 40), dtype=np.uint16)
    template = np.arange(64, dtype=np.uint16).reshape(8, 8)

    seed_roi = (4, 12, 5, 13)
    existing_roi = (20, 28, 6, 14)
    expected_candidate_roi = (10, 18, 20, 28)

    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = template
    image[existing_roi[2]:existing_roi[3], existing_roi[0]:existing_roi[1]] = template
    image[
        expected_candidate_roi[2]:expected_candidate_roi[3],
        expected_candidate_roi[0]:expected_candidate_roi[1],
    ] = template

    _score_map, candidates = detect_matching_beads(image, seed_roi, [existing_roi])

    assert expected_candidate_roi in [candidate.roi for candidate in candidates]
    assert seed_roi not in [candidate.roi for candidate in candidates]
    assert existing_roi not in [candidate.roi for candidate in candidates]
    assert all(not roi_overlaps(candidate.roi, existing_roi) for candidate in candidates)
    assert candidates[0].roi == expected_candidate_roi
    assert candidates[0].score == pytest.approx(1.0)


def test_filter_candidates_by_score_threshold_keeps_top_scores():
    candidates = [
        AutoBeadCandidate((0, 8, 0, 8), 0.1),
        AutoBeadCandidate((8, 16, 0, 8), 0.5),
        AutoBeadCandidate((16, 24, 0, 8), 0.9),
    ]

    filtered = filter_candidates_by_score_threshold(candidates, 0.5)

    assert [candidate.score for candidate in filtered] == [0.5, 0.9]


def test_default_candidate_score_threshold_uses_largest_gap():
    candidates = [
        AutoBeadCandidate((0, 8, 0, 8), 0.97),
        AutoBeadCandidate((8, 16, 0, 8), 0.94),
        AutoBeadCandidate((16, 24, 0, 8), 0.91),
        AutoBeadCandidate((24, 32, 0, 8), 0.52),
        AutoBeadCandidate((32, 40, 0, 8), 0.50),
    ]

    threshold = default_candidate_score_threshold(candidates)

    assert threshold == pytest.approx(0.91)


def test_copy_latest_image_matches_viewer_orientation_for_rectangular_frames():
    width, height = 6, 4
    raw = np.arange(width * height, dtype=np.uint16)

    image = copy_latest_image(raw.tobytes(), (width, height), np.dtype(np.uint16))

    expected = raw.reshape((height, width))
    np.testing.assert_array_equal(image, expected)


def test_detect_matching_beads_uses_viewer_coordinates_on_rectangular_image():
    image = np.zeros((40, 72), dtype=np.uint16)
    template = np.arange(48, dtype=np.uint16).reshape(6, 8)
    seed_roi = (10, 18, 8, 14)
    match_roi = (42, 50, 24, 30)

    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = template
    image[match_roi[2]:match_roi[3], match_roi[0]:match_roi[1]] = template

    _score_map, candidates = detect_matching_beads(image, seed_roi, [])

    assert candidates[0].roi == match_roi


def test_detect_matching_beads_discards_zero_score_candidates():
    image = np.zeros((40, 40), dtype=np.uint16)
    template = np.arange(64, dtype=np.uint16).reshape(8, 8)
    seed_roi = (4, 12, 4, 12)

    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = template

    _score_map, candidates = detect_matching_beads(image, seed_roi, [])

    assert candidates == []


@pytest.mark.parametrize(
    ('image_shape', 'roi_size', 'chunk_rows'),
    [
        ((41, 57), 5, 1),
        ((48, 64), 8, 7),
        ((75, 53), 15, 64),
        ((32, 33), 31, 4),
    ],
)
def test_normalized_cross_correlation_chunked_matches_reference(image_shape, roi_size, chunk_rows):
    rng = np.random.default_rng(42)
    image = rng.integers(0, 4096, size=image_shape, dtype=np.uint16)
    y0 = 3 if image_shape[0] - roi_size > 3 else 0
    x0 = 5 if image_shape[1] - roi_size > 5 else 0
    template = image[y0:y0 + roi_size, x0:x0 + roi_size].copy()

    expected = _normalized_cross_correlation_reference(image, template)
    actual = normalized_cross_correlation_chunked(image, template, chunk_rows=chunk_rows)

    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)


def test_normalized_cross_correlation_default_path_matches_chunked_result():
    rng = np.random.default_rng(7)
    image = rng.integers(0, 1024, size=(52, 79), dtype=np.uint16)
    template = image[11:20, 17:26].copy()

    expected = normalized_cross_correlation_chunked(image, template, chunk_rows=6)
    actual = normalized_cross_correlation(image, template)

    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)


def test_detect_matching_beads_chunked_matches_default_candidates_and_scores():
    rng = np.random.default_rng(99)
    image = rng.integers(0, 2048, size=(60, 72), dtype=np.uint16)
    template = np.arange(64, dtype=np.uint16).reshape(8, 8)
    seed_roi = (4, 12, 6, 14)
    existing_roi = (24, 32, 10, 18)
    match_roi = (44, 52, 32, 40)
    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = template
    image[existing_roi[2]:existing_roi[3], existing_roi[0]:existing_roi[1]] = template
    image[match_roi[2]:match_roi[3], match_roi[0]:match_roi[1]] = template

    default_score_map, default_candidates = detect_matching_beads(image, seed_roi, [existing_roi])
    chunked_score_map, chunked_candidates = detect_matching_beads(
        image,
        seed_roi,
        [existing_roi],
        chunk_rows=5,
    )

    np.testing.assert_allclose(chunked_score_map, default_score_map, rtol=0, atol=1e-12)
    assert [candidate.roi for candidate in chunked_candidates] == [candidate.roi for candidate in default_candidates]
    assert [candidate.score for candidate in chunked_candidates] == pytest.approx(
        [candidate.score for candidate in default_candidates],
    )


def test_detect_matching_beads_chunked_supports_cancellation_between_chunks():
    rng = np.random.default_rng(5)
    image = rng.integers(0, 1024, size=(80, 96), dtype=np.uint16)
    seed_roi = (10, 18, 12, 20)
    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = np.arange(64, dtype=np.uint16).reshape(8, 8)

    progress_calls = []

    def progress_callback(completed_steps: int, total_steps: int) -> None:
        progress_calls.append((completed_steps, total_steps))

    def cancel_check() -> bool:
        return len(progress_calls) >= 2

    with pytest.raises(AutoBeadSearchCancelled):
        detect_matching_beads(
            image,
            seed_roi,
            [],
            chunk_rows=3,
            cancel_check=cancel_check,
            progress_callback=progress_callback,
        )

    assert progress_calls


def test_detect_matching_beads_reports_progress_through_candidate_phase():
    rng = np.random.default_rng(123)
    image = rng.integers(0, 1024, size=(72, 72), dtype=np.uint16)
    template = np.arange(64, dtype=np.uint16).reshape(8, 8)
    seed_roi = (4, 12, 4, 12)
    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = template
    image[20:28, 20:28] = template
    image[40:48, 44:52] = template

    progress_calls = []

    def progress_callback(completed_steps: int, total_steps: int) -> None:
        progress_calls.append((completed_steps, total_steps))

    _score_map, candidates = detect_matching_beads(
        image,
        seed_roi,
        [],
        chunk_rows=5,
        progress_callback=progress_callback,
    )

    assert candidates
    assert progress_calls
    assert progress_calls[-1] == (1000, 1000)
    assert any(800 < completed < 1000 for completed, total in progress_calls if total == 1000)


def test_detect_matching_beads_supports_cancellation_during_candidate_phase():
    image = np.tile(np.arange(96, dtype=np.uint16), (96, 1))
    template = np.arange(64, dtype=np.uint16).reshape(8, 8)
    seed_roi = (8, 16, 8, 16)
    image[seed_roi[2]:seed_roi[3], seed_roi[0]:seed_roi[1]] = template

    progress_calls = []

    def progress_callback(completed_steps: int, total_steps: int) -> None:
        progress_calls.append((completed_steps, total_steps))

    def cancel_check() -> bool:
        return any(completed > 800 for completed, _total in progress_calls)

    with pytest.raises(AutoBeadSearchCancelled):
        detect_matching_beads(
            image,
            seed_roi,
            [],
            chunk_rows=16,
            cancel_check=cancel_check,
            progress_callback=progress_callback,
        )

    assert any(completed > 800 for completed, _total in progress_calls)
