from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import statistics
import sys
from time import perf_counter

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from magscope.auto_bead_selection import (  # noqa: E402
    _MIN_CANDIDATE_SCORE,
    _correlate2d_valid_chunked,
    _ones_kernel,
    AutoBeadCandidate,
    crop_roi,
    detect_matching_beads,
)


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    image: np.ndarray
    seed_roi: tuple[int, int, int, int]
    existing_rois: tuple[tuple[int, int, int, int], ...]
    inserted_match_rois: tuple[tuple[int, int, int, int], ...]


@dataclass(frozen=True)
class VariantResult:
    total_seconds: float
    candidates: list[AutoBeadCandidate]
    score_map: np.ndarray
    timings: dict[str, float]


def roi_overlaps(roi_a: tuple[int, int, int, int], roi_b: tuple[int, int, int, int]) -> bool:
    ax0, ax1, ay0, ay1 = roi_a
    bx0, bx1, by0, by1 = roi_b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def build_demo_case(
    *,
    name: str,
    seed: int,
    image_size: int,
    roi_size: int,
    inserted_matches: int,
) -> BenchmarkCase:
    rng = np.random.default_rng(seed)
    image = rng.integers(0, 4096, size=(image_size, image_size), dtype=np.uint16)

    seed_x0 = roi_size
    seed_y0 = roi_size
    seed_roi = (seed_x0, seed_x0 + roi_size, seed_y0, seed_y0 + roi_size)
    template = image[seed_y0:seed_y0 + roi_size, seed_x0:seed_x0 + roi_size].copy()

    margin = roi_size + 20
    usable = max(image_size - 2 * margin, roi_size + 1)
    step = max(roi_size + 35, usable // max(inserted_matches, 1))
    inserted_match_rois: list[tuple[int, int, int, int]] = []
    for index in range(inserted_matches * 3):
        if len(inserted_match_rois) >= inserted_matches:
            break
        x0 = min(margin + (index * step) % usable, image_size - roi_size - 1)
        y0 = min(margin + ((index * step * 3) % usable), image_size - roi_size - 1)
        roi = (x0, x0 + roi_size, y0, y0 + roi_size)
        if roi_overlaps(roi, seed_roi) or any(roi_overlaps(roi, existing) for existing in inserted_match_rois):
            continue
        image[y0:y0 + roi_size, x0:x0 + roi_size] = template
        inserted_match_rois.append(roi)

    return BenchmarkCase(
        name=name,
        image=image,
        seed_roi=seed_roi,
        existing_rois=(),
        inserted_match_rois=tuple(inserted_match_rois),
    )


def build_default_cases() -> list[BenchmarkCase]:
    return [
        build_demo_case(name='medium', seed=202, image_size=700, roi_size=70, inserted_matches=4),
        build_demo_case(name='large', seed=1234, image_size=1000, roi_size=100, inserted_matches=4),
        build_demo_case(name='extra-large', seed=777, image_size=2560, roi_size=50, inserted_matches=4),
    ]


def _filter_candidates_reference(
    score_map: np.ndarray,
    image_shape: tuple[int, int],
    roi_size: tuple[int, int],
    existing_rois: tuple[tuple[int, int, int, int], ...],
    seed_roi: tuple[int, int, int, int],
) -> list[AutoBeadCandidate]:
    roi_height, roi_width = roi_size
    flat_scores = score_map.ravel()
    candidate_indices = np.flatnonzero(flat_scores > _MIN_CANDIDATE_SCORE)
    sorted_order = np.argsort(flat_scores[candidate_indices])[::-1]
    sorted_indices = candidate_indices[sorted_order]

    blocked_rois = [
        (int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3]))
        for roi in existing_rois
    ]
    blocked_rois.append(seed_roi)

    candidates: list[AutoBeadCandidate] = []
    score_map_width = score_map.shape[1]
    image_height, image_width = image_shape
    for flat_index in sorted_indices:
        y0 = int(flat_index // score_map_width)
        x0 = int(flat_index - y0 * score_map_width)
        x1 = x0 + roi_width
        y1 = y0 + roi_height
        if x0 < 0 or x1 > image_width or y0 < 0 or y1 > image_height:
            continue
        roi = (x0, x1, y0, y1)
        if any(roi_overlaps(roi, blocked_roi) for blocked_roi in blocked_rois):
            continue
        candidates.append(AutoBeadCandidate(roi=roi, score=float(score_map[y0, x0])))
        blocked_rois.append(roi)
    return candidates


def _filter_candidates_fast(
    score_map: np.ndarray,
    image_shape: tuple[int, int],
    roi_size: tuple[int, int],
    existing_rois: tuple[tuple[int, int, int, int], ...],
    seed_roi: tuple[int, int, int, int],
) -> list[AutoBeadCandidate]:
    roi_height, roi_width = roi_size
    flat_scores = score_map.ravel()
    candidate_indices = np.flatnonzero(flat_scores > _MIN_CANDIDATE_SCORE)
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
    score_map_width = score_map.shape[1]
    sorted_y0 = sorted_indices // score_map_width
    sorted_x0 = sorted_indices - sorted_y0 * score_map_width
    image_height, image_width = image_shape
    for y0_raw, x0_raw in zip(sorted_y0, sorted_x0, strict=False):
        y0 = int(y0_raw)
        x0 = int(x0_raw)
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
        candidates.append(AutoBeadCandidate(roi=(x0, x1, y0, y1), score=float(score_map[y0, x0])))
        blocked_x0.append(x0)
        blocked_x1.append(x1)
        blocked_y0.append(y0)
        blocked_y1.append(y1)
    return candidates


def _mark_blocked_roi(
    blocked_mask: np.ndarray,
    roi: tuple[int, int, int, int],
    roi_size: tuple[int, int],
) -> None:
    roi_height, roi_width = roi_size
    x0, x1, y0, y1 = roi
    block_x0 = max(0, x0 - roi_width + 1)
    block_x1 = min(blocked_mask.shape[1], x1)
    block_y0 = max(0, y0 - roi_height + 1)
    block_y1 = min(blocked_mask.shape[0], y1)
    if block_x0 < block_x1 and block_y0 < block_y1:
        blocked_mask[block_y0:block_y1, block_x0:block_x1] = True


def _filter_candidates_masked(
    score_map: np.ndarray,
    image_shape: tuple[int, int],
    roi_size: tuple[int, int],
    existing_rois: tuple[tuple[int, int, int, int], ...],
    seed_roi: tuple[int, int, int, int],
) -> list[AutoBeadCandidate]:
    roi_height, roi_width = roi_size
    flat_scores = score_map.ravel()
    candidate_indices = np.flatnonzero(flat_scores > _MIN_CANDIDATE_SCORE)
    sorted_order = np.argsort(flat_scores[candidate_indices])[::-1]
    sorted_indices = candidate_indices[sorted_order]

    blocked_mask = np.zeros(score_map.shape, dtype=bool)
    for roi in existing_rois:
        _mark_blocked_roi(blocked_mask, roi, roi_size)
    _mark_blocked_roi(blocked_mask, seed_roi, roi_size)

    candidates: list[AutoBeadCandidate] = []
    score_map_width = score_map.shape[1]
    image_height, image_width = image_shape
    sorted_y0 = sorted_indices // score_map_width
    sorted_x0 = sorted_indices - sorted_y0 * score_map_width
    for y0_raw, x0_raw in zip(sorted_y0, sorted_x0, strict=False):
        y0 = int(y0_raw)
        x0 = int(x0_raw)
        x1 = x0 + roi_width
        y1 = y0 + roi_height
        if x0 < 0 or x1 > image_width or y0 < 0 or y1 > image_height:
            continue
        if blocked_mask[y0, x0]:
            continue
        roi = (x0, x1, y0, y1)
        candidates.append(AutoBeadCandidate(roi=roi, score=float(score_map[y0, x0])))
        _mark_blocked_roi(blocked_mask, roi, roi_size)
    return candidates


def _window_sum_integral(image: np.ndarray, window_shape: tuple[int, int]) -> np.ndarray:
    window_height, window_width = window_shape
    integral = np.pad(np.cumsum(np.cumsum(image, axis=0), axis=1), ((1, 0), (1, 0)), mode='constant')
    return (
        integral[window_height:, window_width:]
        - integral[:-window_height, window_width:]
        - integral[window_height:, :-window_width]
        + integral[:-window_height, :-window_width]
    )


def run_production_variant(case: BenchmarkCase, *, chunk_rows: int) -> VariantResult:
    start = perf_counter()
    score_map, candidates = detect_matching_beads(
        case.image,
        case.seed_roi,
        case.existing_rois,
        chunk_rows=chunk_rows,
    )
    end = perf_counter()
    return VariantResult(
        total_seconds=end - start,
        candidates=candidates,
        score_map=score_map,
        timings={'total': end - start},
    )


def run_instrumented_variant(
    case: BenchmarkCase,
    *,
    chunk_rows: int,
    use_cached_kernel: bool,
    use_square_out: bool,
    use_fast_filter: bool,
    use_mask_filter: bool,
    use_integral_sums: bool,
) -> VariantResult:
    timings: dict[str, float] = {}

    start = perf_counter()

    t0 = perf_counter()
    template = crop_roi(case.image, case.seed_roi)
    timings['crop_roi'] = perf_counter() - t0

    t0 = perf_counter()
    image_f = np.asarray(case.image, dtype=np.float64)
    template_f = np.asarray(template, dtype=np.float64)
    template_zero_mean = template_f - template_f.mean()
    template_norm = np.sqrt(np.sum(template_zero_mean * template_zero_mean))
    if template_norm == 0:
        raise ValueError('template must have non-zero variance')
    kernel = _ones_kernel(template_f.shape) if use_cached_kernel else np.ones(template_f.shape, dtype=np.float64)
    timings['prepare_arrays'] = perf_counter() - t0

    t0 = perf_counter()
    numerator = _correlate2d_valid_chunked(image_f, template_zero_mean, chunk_rows=chunk_rows)
    timings['correlate_numerator'] = perf_counter() - t0

    t0 = perf_counter()
    if use_integral_sums:
        image_sum = _window_sum_integral(image_f, template_f.shape)
    else:
        image_sum = _correlate2d_valid_chunked(image_f, kernel, chunk_rows=chunk_rows)
    timings['correlate_sum'] = perf_counter() - t0

    t0 = perf_counter()
    if use_square_out:
        image_squared = np.empty_like(image_f)
        np.square(image_f, out=image_squared)
    else:
        image_squared = image_f * image_f
    if use_integral_sums:
        image_sum_sq = _window_sum_integral(image_squared, template_f.shape)
    else:
        image_sum_sq = _correlate2d_valid_chunked(image_squared, kernel, chunk_rows=chunk_rows)
    timings['correlate_sum_sq'] = perf_counter() - t0

    t0 = perf_counter()
    variance = image_sum_sq - (image_sum * image_sum) / template_f.size
    variance = np.maximum(variance, 0.0)
    denominator = np.sqrt(variance) * template_norm
    score_map = np.zeros_like(numerator)
    valid = denominator > 0
    score_map[valid] = numerator[valid] / denominator[valid]
    timings['assemble_score_map'] = perf_counter() - t0

    t0 = perf_counter()
    if use_mask_filter:
        filter_func = _filter_candidates_masked
    elif use_fast_filter:
        filter_func = _filter_candidates_fast
    else:
        filter_func = _filter_candidates_reference
    candidates = filter_func(score_map, case.image.shape, template.shape, case.existing_rois, case.seed_roi)
    timings['filter_candidates'] = perf_counter() - t0

    end = perf_counter()
    timings['total'] = end - start
    return VariantResult(
        total_seconds=end - start,
        candidates=candidates,
        score_map=score_map,
        timings=timings,
    )


def build_variants() -> dict[str, Callable[[BenchmarkCase, int], VariantResult]]:
    return {
        'reference': lambda case, chunk_rows: run_production_variant(case, chunk_rows=chunk_rows),
        'legacy-reference': lambda case, chunk_rows: run_instrumented_variant(
            case,
            chunk_rows=chunk_rows,
            use_cached_kernel=False,
            use_square_out=False,
            use_fast_filter=False,
            use_mask_filter=False,
            use_integral_sums=False,
        ),
        'cached-kernel': lambda case, chunk_rows: run_instrumented_variant(
            case,
            chunk_rows=chunk_rows,
            use_cached_kernel=True,
            use_square_out=False,
            use_fast_filter=False,
            use_mask_filter=False,
            use_integral_sums=False,
        ),
        'square-out': lambda case, chunk_rows: run_instrumented_variant(
            case,
            chunk_rows=chunk_rows,
            use_cached_kernel=False,
            use_square_out=True,
            use_fast_filter=False,
            use_mask_filter=False,
            use_integral_sums=False,
        ),
        'fast-filter': lambda case, chunk_rows: run_instrumented_variant(
            case,
            chunk_rows=chunk_rows,
            use_cached_kernel=False,
            use_square_out=False,
            use_fast_filter=True,
            use_mask_filter=False,
            use_integral_sums=False,
        ),
        'mask-filter': lambda case, chunk_rows: run_instrumented_variant(
            case,
            chunk_rows=chunk_rows,
            use_cached_kernel=False,
            use_square_out=False,
            use_fast_filter=False,
            use_mask_filter=True,
            use_integral_sums=False,
        ),
        'integral-sums': lambda case, chunk_rows: run_instrumented_variant(
            case,
            chunk_rows=chunk_rows,
            use_cached_kernel=False,
            use_square_out=False,
            use_fast_filter=False,
            use_mask_filter=False,
            use_integral_sums=True,
        ),
        'integral-fast': lambda case, chunk_rows: run_instrumented_variant(
            case,
            chunk_rows=chunk_rows,
            use_cached_kernel=False,
            use_square_out=True,
            use_fast_filter=True,
            use_mask_filter=False,
            use_integral_sums=True,
        ),
        'integral-mask': lambda case, chunk_rows: run_instrumented_variant(
            case,
            chunk_rows=chunk_rows,
            use_cached_kernel=False,
            use_square_out=True,
            use_fast_filter=False,
            use_mask_filter=True,
            use_integral_sums=True,
        ),
        'optimized': lambda case, chunk_rows: run_instrumented_variant(
            case,
            chunk_rows=chunk_rows,
            use_cached_kernel=True,
            use_square_out=True,
            use_fast_filter=True,
            use_mask_filter=False,
            use_integral_sums=False,
        ),
    }


def compare_results(baseline: VariantResult, candidate: VariantResult) -> str:
    np.testing.assert_allclose(candidate.score_map, baseline.score_map, rtol=0.0, atol=1e-12)
    baseline_rois = [item.roi for item in baseline.candidates]
    candidate_rois = [item.roi for item in candidate.candidates]
    if candidate_rois != baseline_rois:
        raise AssertionError('Candidate ROI lists differ from baseline')
    np.testing.assert_allclose(
        [item.score for item in candidate.candidates],
        [item.score for item in baseline.candidates],
        rtol=0.0,
        atol=1e-12,
    )
    return 'PASS'


def run_repeated(
    runner: Callable[[BenchmarkCase, int], VariantResult],
    case: BenchmarkCase,
    *,
    chunk_rows: int,
    repeats: int,
) -> tuple[VariantResult, list[float]]:
    results: list[VariantResult] = []
    times: list[float] = []
    for _ in range(repeats):
        result = runner(case, chunk_rows)
        results.append(result)
        times.append(result.total_seconds)
    best_index = min(range(len(times)), key=times.__getitem__)
    return results[best_index], times


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Compare auto bead selection optimization variants.')
    parser.add_argument('--chunk-rows', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument(
        '--cases',
        nargs='*',
        default=['medium', 'large', 'extra-large'],
        help='Case names to run: medium large extra-large',
    )
    parser.add_argument(
        '--variants',
        nargs='*',
        default=[
            'reference',
            'legacy-reference',
            'cached-kernel',
            'square-out',
            'fast-filter',
            'mask-filter',
            'integral-sums',
            'integral-fast',
            'integral-mask',
            'optimized',
        ],
        help='Variant names to run',
    )
    return parser.parse_args()


def print_variant_summary(name: str, times: list[float], compare_status: str, baseline_best: float) -> None:
    best = min(times)
    mean = statistics.mean(times)
    speedup = baseline_best / best if best > 0 else float('inf')
    print(
        f'  {name:14s} best={best:7.3f}s mean={mean:7.3f}s '
        f'speedup={speedup:5.2f}x check={compare_status}'
    )


def main() -> None:
    args = parse_args()
    all_cases = {case.name: case for case in build_default_cases()}
    selected_cases = [all_cases[name] for name in args.cases]
    variants = build_variants()

    unknown_variants = [name for name in args.variants if name not in variants]
    if unknown_variants:
        raise ValueError(f'Unknown variants: {unknown_variants}')

    for case in selected_cases:
        print(f'Case: {case.name}')
        print(
            f'  image={case.image.shape[1]}x{case.image.shape[0]} '
            f'roi={case.seed_roi[1] - case.seed_roi[0]} inserted_matches={len(case.inserted_match_rois)}'
        )
        baseline_result, baseline_times = run_repeated(
            variants['reference'],
            case,
            chunk_rows=args.chunk_rows,
            repeats=args.repeats,
        )
        print_variant_summary('reference', baseline_times, 'BASE', min(baseline_times))

        for variant_name in args.variants:
            if variant_name == 'reference':
                continue
            result, times = run_repeated(
                variants[variant_name],
                case,
                chunk_rows=args.chunk_rows,
                repeats=args.repeats,
            )
            compare_status = compare_results(baseline_result, result)
            print_variant_summary(variant_name, times, compare_status, min(baseline_times))
        print('')


if __name__ == '__main__':
    main()
