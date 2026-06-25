from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

from magscope.tracking_data import (
    TrackingDataWriter,
    TrackingHDF5File,
    build_tracking_data_batch,
    timestamps_to_epoch_ns,
    tracking_data_path,
)


def _tracks(rows: list[list[float]]) -> np.ndarray:
    return np.asarray(rows, dtype=np.float64)


class DummyTrackingDataQueue:
    def __init__(self, *items):
        self.items = list(items)

    def get(self):
        return self.items.pop(0)


def _single_bead_batch(
    tmp_path,
    timestamps: np.ndarray,
    *,
    include_roi_positions: bool = False,
    max_file_duration_ns: int | None = None,
):
    rows = [
        [float(timestamp), 10.0 + index, np.nan, 30.0, 5.0, 100.0, 200.0]
        for index, timestamp in enumerate(timestamps)
    ]
    return build_tracking_data_batch(
        recording_id=1,
        acquisition_dir=str(tmp_path),
        timestamps=timestamps,
        tracks=_tracks(rows),
        n_rois=1,
        include_roi_positions=include_roi_positions,
        max_file_duration_ns=max_file_duration_ns,
    )


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
        batch_sequence=10,
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
        batch_sequence=11,
    )

    path = tmp_path / "tracking.h5"
    writer = TrackingHDF5File(path, include_roi_positions=False)
    writer.append(first_batch)
    writer.append(second_batch)
    writer.close()

    with h5py.File(path, "r") as file:
        group = file["tracking"]
        assert group.attrs["record_order"] == "writer_append_order"
        assert group.attrs["batch_sequence_order"] == "video_task_enqueue_order"
        assert group.attrs["min_frame_timestamp_ns"] == np.uint64(1_000_000_000)
        assert group.attrs["max_frame_timestamp_ns"] == np.uint64(1_200_000_000)
        np.testing.assert_array_equal(
            group["batch_sequence"][:],
            np.asarray([10, 11], dtype=np.uint64),
        )
        np.testing.assert_array_equal(
            group["batch_frame_start"][:],
            np.asarray([0, 2], dtype=np.uint64),
        )
        np.testing.assert_array_equal(
            group["batch_frame_count"][:],
            np.asarray([2, 1], dtype=np.uint64),
        )
        np.testing.assert_array_equal(
            group["batch_record_start"][:],
            np.asarray([0, 4], dtype=np.uint64),
        )
        np.testing.assert_array_equal(
            group["batch_record_count"][:],
            np.asarray([4, 2], dtype=np.uint64),
        )
        np.testing.assert_array_equal(
            group["frame_batch_sequence"][:],
            np.asarray([10, 10, 11], dtype=np.uint64),
        )
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


def test_hdf5_writer_preserves_batch_sequence_for_out_of_order_appends(tmp_path):
    later_batch = build_tracking_data_batch(
        recording_id=1,
        acquisition_dir=str(tmp_path),
        timestamps=np.asarray([2.0], dtype=np.float64),
        tracks=_tracks([[2.0, 20.0, 21.0, 22.0, 5.0, 100.0, 200.0]]),
        n_rois=1,
        include_roi_positions=False,
        batch_sequence=2,
    )
    earlier_batch = build_tracking_data_batch(
        recording_id=1,
        acquisition_dir=str(tmp_path),
        timestamps=np.asarray([1.0], dtype=np.float64),
        tracks=_tracks([[1.0, 10.0, 11.0, 12.0, 5.0, 100.0, 200.0]]),
        n_rois=1,
        include_roi_positions=False,
        batch_sequence=1,
    )

    path = tmp_path / "tracking-out-of-order.h5"
    writer = TrackingHDF5File(path, include_roi_positions=False)
    writer.append(later_batch)
    writer.append(earlier_batch)
    writer.close()

    with h5py.File(path, "r") as file:
        group = file["tracking"]
        np.testing.assert_array_equal(
            group["frame_timestamps_ns"][:],
            np.asarray([2_000_000_000, 1_000_000_000], dtype=np.uint64),
        )
        np.testing.assert_array_equal(
            group["batch_sequence"][:],
            np.asarray([2, 1], dtype=np.uint64),
        )
        np.testing.assert_array_equal(
            group["frame_batch_sequence"][:],
            np.asarray([2, 1], dtype=np.uint64),
        )
        assert group.attrs["min_frame_timestamp_ns"] == np.uint64(1_000_000_000)
        assert group.attrs["max_frame_timestamp_ns"] == np.uint64(2_000_000_000)


def test_tracking_writer_rotates_between_batches_and_preserves_roi_dataset(tmp_path):
    start = 1_700_000_000.0
    max_duration_ns = 60_000_000_000
    first_batch = _single_bead_batch(
        tmp_path,
        np.asarray([start, start + 61.0], dtype=np.float64),
        include_roi_positions=True,
        max_file_duration_ns=max_duration_ns,
    )
    second_batch = _single_bead_batch(
        tmp_path,
        np.asarray([start + 62.0], dtype=np.float64),
        include_roi_positions=True,
        max_file_duration_ns=max_duration_ns,
    )

    writer = TrackingDataWriter(DummyTrackingDataQueue(first_batch, second_batch, None))
    writer.run()

    paths = sorted(tmp_path.glob("Tracking Data *.h5"))
    assert len(paths) == 2

    with h5py.File(paths[0], "r") as file:
        group = file["tracking"]
        np.testing.assert_array_equal(
            group["frame_timestamps_ns"][:],
            timestamps_to_epoch_ns(np.asarray([start, start + 61.0], dtype=np.float64)),
        )
        np.testing.assert_array_equal(
            group["frame_offsets"][:],
            np.asarray([0, 1, 2], dtype=np.uint64),
        )
        assert group["roi_positions_px"].shape == (2, 2)
        assert np.isnan(group["positions_nm"][0, 1])

    with h5py.File(paths[1], "r") as file:
        group = file["tracking"]
        np.testing.assert_array_equal(
            group["frame_timestamps_ns"][:],
            timestamps_to_epoch_ns(np.asarray([start + 62.0], dtype=np.float64)),
        )
        np.testing.assert_array_equal(
            group["frame_offsets"][:],
            np.asarray([0, 1], dtype=np.uint64),
        )
        assert group["roi_positions_px"].shape == (1, 2)


def test_tracking_writer_keeps_one_file_when_rotation_is_disabled(tmp_path):
    start = 1_700_000_000.0
    first_batch = _single_bead_batch(
        tmp_path,
        np.asarray([start], dtype=np.float64),
        max_file_duration_ns=None,
    )
    second_batch = _single_bead_batch(
        tmp_path,
        np.asarray([start + 3_600.0], dtype=np.float64),
        max_file_duration_ns=None,
    )

    writer = TrackingDataWriter(DummyTrackingDataQueue(first_batch, second_batch, None))
    writer.run()

    paths = sorted(tmp_path.glob("Tracking Data *.h5"))
    assert len(paths) == 1
    with h5py.File(paths[0], "r") as file:
        group = file["tracking"]
        np.testing.assert_array_equal(
            group["frame_offsets"][:],
            np.asarray([0, 1, 2], dtype=np.uint64),
        )
        assert "roi_positions_px" not in group
