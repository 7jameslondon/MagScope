from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from magscope.tracking_data import (
    TrackingHDF5File,
    build_tracking_data_batch,
    timestamps_to_epoch_ns,
    tracking_data_path,
)


def _tracks(rows: list[list[float]]) -> np.ndarray:
    return np.asarray(rows, dtype=np.float64)


def test_hdf5_writer_appends_batches_without_roi_positions(tmp_path):
    first_batch = build_tracking_data_batch(
        recording_id=1,
        acquisition_dir=str(tmp_path),
        timestamps=np.asarray([1.0, 1.1], dtype=np.float64),
        tracks=_tracks(
            [
                [1.0, 10.0, 20.0, np.nan, 5.0, 100.0, 200.0],
                [1.0, 11.0, 21.0, 31.0, 6.0, 110.0, 210.0],
                [1.1, 12.0, 22.0, 32.0, 5.0, 100.0, 200.0],
                [1.1, 13.0, 23.0, 33.0, 6.0, 110.0, 210.0],
            ]
        ),
        n_rois=2,
        include_roi_positions=False,
    )
    second_batch = build_tracking_data_batch(
        recording_id=1,
        acquisition_dir=str(tmp_path),
        timestamps=np.asarray([1.2], dtype=np.float64),
        tracks=_tracks(
            [
                [1.2, 14.0, 24.0, 34.0, 5.0, 100.0, 200.0],
                [1.2, 15.0, 25.0, 35.0, 6.0, 110.0, 210.0],
            ]
        ),
        n_rois=2,
        include_roi_positions=False,
    )

    path = tmp_path / "tracking.h5"
    writer = TrackingHDF5File(path, include_roi_positions=False)
    writer.append(first_batch)
    writer.append(second_batch)
    writer.close()

    with h5py.File(path, "r") as file:
        group = file["tracking"]
        assert group["frame_timestamps_ns"].dtype == np.dtype(np.uint64)
        assert group["frame_timestamps_ns"].shape == (3,)
        assert group["frame_timestamps_ns"].maxshape == (None,)
        assert group["frame_timestamps_ns"].chunks is not None
        np.testing.assert_array_equal(
            group["frame_timestamps_ns"][:],
            np.asarray([1_000_000_000, 1_100_000_000, 1_200_000_000], dtype=np.uint64),
        )
        np.testing.assert_array_equal(
            group["frame_offsets"][:],
            np.asarray([0, 2, 4, 6], dtype=np.uint64),
        )
        assert group["bead_ids"].dtype == np.dtype(np.uint16)
        np.testing.assert_array_equal(
            group["bead_ids"][:],
            np.asarray([5, 6, 5, 6, 5, 6], dtype=np.uint16),
        )
        assert group["positions_nm"].dtype == np.dtype(np.float64)
        assert group["positions_nm"].shape == (6, 3)
        assert np.isnan(group["positions_nm"][0, 2])
        assert "roi_positions_px" not in group


def test_hdf5_writer_saves_optional_roi_positions(tmp_path):
    batch = build_tracking_data_batch(
        recording_id=1,
        acquisition_dir=str(tmp_path),
        timestamps=np.asarray([2.0], dtype=np.float64),
        tracks=_tracks(
            [
                [2.0, 10.0, 20.0, 30.0, 5.0, 100.0, 200.0],
                [2.0, 11.0, 21.0, 31.0, 6.0, 110.0, 210.0],
            ]
        ),
        n_rois=2,
        include_roi_positions=True,
    )

    path = tmp_path / "tracking-roi.h5"
    writer = TrackingHDF5File(path, include_roi_positions=True)
    writer.append(batch)
    writer.close()

    with h5py.File(path, "r") as file:
        group = file["tracking"]
        assert group["roi_positions_px"].dtype == np.dtype(np.uint16)
        assert group["roi_positions_px"].shape == (2, 2)
        np.testing.assert_array_equal(
            group["roi_positions_px"][:],
            np.asarray([[100, 200], [110, 210]], dtype=np.uint16),
        )


def test_batch_validation_rejects_bad_timestamps():
    with pytest.raises(ValueError, match="finite"):
        timestamps_to_epoch_ns(np.asarray([1.0, np.nan], dtype=np.float64))


@pytest.mark.parametrize(
    ("field_index", "bad_value", "match"),
    [
        (4, 70000.0, "bead IDs"),
        (5, 70000.0, "ROI positions"),
        (4, 1.5, "bead IDs"),
    ],
)
def test_batch_validation_rejects_values_that_do_not_fit_uint16(
    field_index,
    bad_value,
    match,
    tmp_path,
):
    rows = _tracks([[1.0, 10.0, 20.0, 30.0, 5.0, 100.0, 200.0]])
    rows[0, field_index] = bad_value

    with pytest.raises(ValueError, match=match):
        build_tracking_data_batch(
            recording_id=1,
            acquisition_dir=str(tmp_path),
            timestamps=np.asarray([1.0], dtype=np.float64),
            tracks=rows,
            n_rois=1,
            include_roi_positions=True,
        )


def test_tracking_data_path_adds_numeric_suffix(tmp_path):
    first_timestamp_ns = 1_000_000_000
    first_path = tracking_data_path(tmp_path, first_timestamp_ns)
    first_path.touch()

    second_path = tracking_data_path(tmp_path, first_timestamp_ns)

    assert second_path.parent == Path(tmp_path)
    assert second_path.name.endswith("(1).h5")
