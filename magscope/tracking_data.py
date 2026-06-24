from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from multiprocessing import Process
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from magscope._logging import get_logger

if TYPE_CHECKING:
    from multiprocessing.queues import Queue as QueueType


logger = get_logger("tracking_data")

_NANOSECONDS_PER_SECOND = 1_000_000_000
_MAX_UINT16 = np.iinfo(np.uint16).max
_FRAME_CHUNK = 4096
_RECORD_CHUNK = 65536


@dataclass(frozen=True)
class TrackingDataBatch:
    recording_id: int
    acquisition_dir: str
    include_roi_positions: bool
    max_file_duration_ns: int | None
    frame_timestamps_ns: np.ndarray
    frame_offsets: np.ndarray
    bead_ids: np.ndarray
    positions_nm: np.ndarray
    roi_positions_px: np.ndarray | None = None


def timestamps_to_epoch_ns(timestamps: np.ndarray) -> np.ndarray:
    timestamp_array = np.asarray(timestamps, dtype=np.float64)
    if timestamp_array.ndim != 1:
        raise ValueError("timestamps must be a one-dimensional array")
    if timestamp_array.size == 0:
        raise ValueError("timestamps must contain at least one value")
    if not np.all(np.isfinite(timestamp_array)):
        raise ValueError("timestamps must contain only finite values")
    if np.any(timestamp_array < 0.0):
        raise ValueError("timestamps must be Unix epoch seconds")

    nanoseconds = np.rint(timestamp_array * _NANOSECONDS_PER_SECOND)
    if np.any(nanoseconds > float(np.iinfo(np.uint64).max)):
        raise ValueError("timestamps exceed uint64 nanosecond range")
    return nanoseconds.astype(np.uint64)


def build_tracking_data_batch(
    *,
    recording_id: int,
    acquisition_dir: str,
    timestamps: np.ndarray,
    tracks: np.ndarray,
    n_rois: int,
    include_roi_positions: bool,
    max_file_duration_ns: int | None = None,
) -> TrackingDataBatch:
    track_rows = np.asarray(tracks)
    if track_rows.ndim != 2 or track_rows.shape[1] < 7:
        raise ValueError("tracks must have at least seven columns")
    if n_rois <= 0:
        raise ValueError("n_rois must be positive")

    frame_timestamps_ns = timestamps_to_epoch_ns(timestamps)
    expected_rows = int(frame_timestamps_ns.shape[0] * n_rois)
    if track_rows.shape[0] != expected_rows:
        raise ValueError(
            f"tracks contain {track_rows.shape[0]} rows, expected {expected_rows}"
        )

    frame_offsets = np.arange(
        0,
        expected_rows + n_rois,
        n_rois,
        dtype=np.uint64,
    )
    bead_ids = _to_uint16_array(track_rows[:, 4], "bead IDs")
    positions_nm = np.asarray(track_rows[:, 1:4], dtype=np.float64)
    roi_positions_px = None
    if include_roi_positions:
        roi_positions_px = _to_uint16_array(track_rows[:, 5:7], "ROI positions")

    if max_file_duration_ns is not None:
        max_file_duration_ns = int(max_file_duration_ns)
        if max_file_duration_ns <= 0:
            raise ValueError("max_file_duration_ns must be positive")

    return TrackingDataBatch(
        recording_id=int(recording_id),
        acquisition_dir=str(acquisition_dir),
        include_roi_positions=bool(include_roi_positions),
        max_file_duration_ns=max_file_duration_ns,
        frame_timestamps_ns=frame_timestamps_ns,
        frame_offsets=frame_offsets,
        bead_ids=bead_ids,
        positions_nm=positions_nm,
        roi_positions_px=roi_positions_px,
    )


def tracking_data_path(directory: str | Path, first_timestamp_ns: int) -> Path:
    directory_path = Path(directory)
    timestamp = _format_timestamp_for_filename(first_timestamp_ns)
    base_path = directory_path / f"Tracking Data {timestamp}.h5"
    if not base_path.exists():
        return base_path

    suffix = 1
    while True:
        candidate = directory_path / f"Tracking Data {timestamp} ({suffix}).h5"
        if not candidate.exists():
            return candidate
        suffix += 1


class TrackingHDF5File:
    def __init__(self, path: str | Path, *, include_roi_positions: bool):
        import h5py

        self.path = Path(path)
        self.include_roi_positions = bool(include_roi_positions)
        self._file = h5py.File(self.path, "w")
        group = self._file.create_group("tracking")
        group.attrs["schema_version"] = 1
        group.attrs["include_roi_positions"] = np.uint8(self.include_roi_positions)
        self._group = group
        self._frame_timestamps = group.create_dataset(
            "frame_timestamps_ns",
            shape=(0,),
            maxshape=(None,),
            chunks=(_FRAME_CHUNK,),
            dtype=np.uint64,
        )
        self._frame_offsets = group.create_dataset(
            "frame_offsets",
            data=np.zeros((1,), dtype=np.uint64),
            maxshape=(None,),
            chunks=(_FRAME_CHUNK + 1,),
        )
        self._bead_ids = group.create_dataset(
            "bead_ids",
            shape=(0,),
            maxshape=(None,),
            chunks=(_RECORD_CHUNK,),
            dtype=np.uint16,
        )
        self._positions = group.create_dataset(
            "positions_nm",
            shape=(0, 3),
            maxshape=(None, 3),
            chunks=(_RECORD_CHUNK, 3),
            dtype=np.float64,
        )
        self._roi_positions = None
        if self.include_roi_positions:
            self._roi_positions = group.create_dataset(
                "roi_positions_px",
                shape=(0, 2),
                maxshape=(None, 2),
                chunks=(_RECORD_CHUNK, 2),
                dtype=np.uint16,
            )

    def append(self, batch: TrackingDataBatch) -> None:
        if batch.include_roi_positions != self.include_roi_positions:
            raise ValueError("batch ROI-position setting does not match file")
        if batch.include_roi_positions and batch.roi_positions_px is None:
            raise ValueError("batch is missing ROI positions")

        n_frames = int(batch.frame_timestamps_ns.shape[0])
        n_records = int(batch.bead_ids.shape[0])
        if batch.frame_offsets.shape != (n_frames + 1,):
            raise ValueError(
                "frame_offsets must have one more entry than frame timestamps"
            )
        if int(batch.frame_offsets[-1]) != n_records:
            raise ValueError("frame_offsets must end at the batch record count")
        if batch.positions_nm.shape != (n_records, 3):
            raise ValueError("positions_nm must have shape (n_records, 3)")
        if (
            batch.include_roi_positions
            and batch.roi_positions_px.shape != (n_records, 2)
        ):
            raise ValueError("roi_positions_px must have shape (n_records, 2)")

        frame_start = self._frame_timestamps.shape[0]
        record_start = self._bead_ids.shape[0]
        frame_end = frame_start + n_frames
        record_end = record_start + n_records

        self._frame_timestamps.resize((frame_end,))
        self._frame_timestamps[frame_start:frame_end] = batch.frame_timestamps_ns

        self._frame_offsets.resize((frame_end + 1,))
        self._frame_offsets[frame_start + 1:frame_end + 1] = (
            batch.frame_offsets[1:] + np.uint64(record_start)
        )

        self._bead_ids.resize((record_end,))
        self._bead_ids[record_start:record_end] = batch.bead_ids

        self._positions.resize((record_end, 3))
        self._positions[record_start:record_end, :] = batch.positions_nm

        if self._roi_positions is not None:
            self._roi_positions.resize((record_end, 2))
            self._roi_positions[record_start:record_end, :] = batch.roi_positions_px

    def close(self) -> None:
        if getattr(self, "_file", None) is None:
            return
        self._file.close()
        self._file = None


class TrackingDataWriter(Process):
    def __init__(self, queue: "QueueType"):
        super().__init__()
        self._queue = queue

    def run(self) -> None:
        current_recording_id: int | None = None
        current_file: TrackingHDF5File | None = None
        current_file_first_timestamp_ns: int | None = None
        try:
            while True:
                batch = self._queue.get()
                if batch is None:
                    break
                if (
                    current_recording_id != batch.recording_id
                    or _batch_starts_new_file(batch, current_file_first_timestamp_ns)
                ):
                    if current_file is not None:
                        current_file.close()
                    path = tracking_data_path(
                        batch.acquisition_dir,
                        int(batch.frame_timestamps_ns[0]),
                    )
                    current_file = TrackingHDF5File(
                        path,
                        include_roi_positions=batch.include_roi_positions,
                    )
                    current_recording_id = int(batch.recording_id)
                    current_file_first_timestamp_ns = int(batch.frame_timestamps_ns[0])
                current_file.append(batch)
        except Exception as exc:
            logger.exception("Tracking data writer failed: %s", exc)
        finally:
            if current_file is not None:
                current_file.close()


def _to_uint16_array(values: np.ndarray, field_name: str) -> np.ndarray:
    array = np.asarray(values)
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{field_name} must contain only finite values")
    if np.any(array < 0) or np.any(array > _MAX_UINT16):
        raise ValueError(f"{field_name} must fit in uint16")
    if not np.all(array == np.floor(array)):
        raise ValueError(f"{field_name} must contain integer values")
    return array.astype(np.uint16)


def _batch_starts_new_file(
    batch: TrackingDataBatch,
    current_file_first_timestamp_ns: int | None,
) -> bool:
    if current_file_first_timestamp_ns is None or batch.max_file_duration_ns is None:
        return False
    deadline_ns = current_file_first_timestamp_ns + int(batch.max_file_duration_ns)
    return int(batch.frame_timestamps_ns[0]) >= deadline_ns


def _format_timestamp_for_filename(timestamp_ns: int) -> str:
    timestamp_ns = int(timestamp_ns)
    seconds = timestamp_ns // _NANOSECONDS_PER_SECOND
    microseconds = (timestamp_ns % _NANOSECONDS_PER_SECOND) // 1000
    timestamp = datetime.fromtimestamp(seconds).replace(microsecond=microseconds)
    return timestamp.strftime("%Y-%m-%d %H-%M-%S.%f")
