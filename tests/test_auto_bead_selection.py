import numpy as np
import pytest

from magscope.auto_bead_selection import (
    AutoBeadCandidate,
    detect_matching_beads,
    filter_candidates_by_percentile,
    roi_overlaps,
)


def test_detect_matching_beads_excludes_seed_existing_and_overlaps():
    image = np.zeros((40, 40), dtype=np.uint16)
    template = np.arange(64, dtype=np.uint16).reshape(8, 8)

    seed_roi = (4, 12, 5, 13)
    existing_roi = (20, 28, 6, 14)
    expected_candidate_roi = (10, 18, 20, 28)

    image[seed_roi[0]:seed_roi[1], seed_roi[2]:seed_roi[3]] = template
    image[existing_roi[0]:existing_roi[1], existing_roi[2]:existing_roi[3]] = template
    image[
        expected_candidate_roi[0]:expected_candidate_roi[1],
        expected_candidate_roi[2]:expected_candidate_roi[3],
    ] = template

    _score_map, candidates = detect_matching_beads(image, seed_roi, [existing_roi])

    assert expected_candidate_roi in [candidate.roi for candidate in candidates]
    assert seed_roi not in [candidate.roi for candidate in candidates]
    assert existing_roi not in [candidate.roi for candidate in candidates]
    assert all(not roi_overlaps(candidate.roi, existing_roi) for candidate in candidates)
    assert candidates[0].roi == expected_candidate_roi
    assert candidates[0].score == pytest.approx(1.0)


def test_filter_candidates_by_percentile_keeps_top_scores():
    candidates = [
        AutoBeadCandidate((0, 8, 0, 8), 0.1),
        AutoBeadCandidate((8, 16, 0, 8), 0.5),
        AutoBeadCandidate((16, 24, 0, 8), 0.9),
    ]

    filtered = filter_candidates_by_percentile(candidates, 50)

    assert [candidate.score for candidate in filtered] == [0.5, 0.9]
