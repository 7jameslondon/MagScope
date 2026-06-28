from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from multiprocessing import Process
from pathlib import Path
from queue import Full
from time import time
from typing import TYPE_CHECKING

import numpy as np

from magscope._logging import get_logger

if TYPE_CHECKING:
    from multiprocessing.queues import Queue as QueueType


logger = get_logger("tracking_data")

_NANOSECONDS_PER_SECOND = 1_000_000_000
_MAX_UINT16 = np.iinfo(np.uint16).max
_BATCH_CHUNK = 1024
_FRAME_CHUNK = 4096
_RECORD_CHUNK = 65536
TRACKING_DATA_WRITER_FAILURE_WARNING = "tracking_data_writer_failure"


@dataclass(frozen=True)
class TrackingDataBatch:
    recording_id: int
    recording_start_ns: int
    batch_sequence: int
    acquisition_dir: str
    include_roi_positions: bool
    file_start_ns: int
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
    recording_start_ns: int | None = None,
    max_file_duration_ns: int | None = None,
    batch_sequence: int = 0,
) -> TrackingDataBatch:
    track_rows = np.asarray(tracks)
    if track_rows.ndim != 2 or track_rows.shape[1] < 7:
        raise ValueError("tracks must have at least seven columns")
    if n_rois <= 0:
        raise ValueError("n_rois must be positive")
    batch_sequence = int(batch_sequence)
    if batch_sequence < 0:
        raise ValueError("batch_sequence must be non-negative")

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
    recording_start_ns, file_start_ns = _tracking_file_bucket_start_ns(
        int(frame_timestamps_ns[0]),
        recording_start_ns=recording_start_ns,
        max_file_duration_ns=max_file_duration_ns,
    )

    return TrackingDataBatch(
        recording_id=int(recording_id),
        recording_start_ns=recording_start_ns,
        batch_sequence=batch_sequence,
        acquisition_dir=str(acquisition_dir),
        include_roi_positions=bool(include_roi_positions),
        file_start_ns=file_start_ns,
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
    def __init__(
        self,
        path: str | Path,
        *,
        include_roi_positions: bool,
        recording_id: int | None = None,
        recording_start_ns: int | None = None,
        file_start_ns: int | None = None,
        rotation_interval_ns: int | None = None,
    ):
        import h5py

        self.path = Path(path)
        self.include_roi_positions = bool(include_roi_positions)
        self._file = h5py.File(self.path, "w")
        group = self._file.create_group("tracking")
        group.attrs["schema_version"] = 1
        group.attrs["include_roi_positions"] = np.uint8(self.include_roi_positions)
        group.attrs["record_order"] = "writer_append_order"
        group.attrs["batch_sequence_order"] = "video_task_enqueue_order"
        if recording_id is not None:
            group.attrs["recording_id"] = np.uint64(recording_id)
        if recording_start_ns is not None:
            group.attrs["recording_start_ns"] = np.uint64(recording_start_ns)
        if file_start_ns is not None:
            group.attrs["file_start_ns"] = np.uint64(file_start_ns)
        if rotation_interval_ns is not None:
            group.attrs["rotation_interval_ns"] = np.uint64(rotation_interval_ns)
        self._group = group
        self._batch_sequence = group.create_dataset(
            "batch_sequence",
            shape=(0,),
            maxshape=(None,),
            chunks=(_BATCH_CHUNK,),
            dtype=np.uint64,
        )
        self._batch_frame_start = group.create_dataset(
            "batch_frame_start",
            shape=(0,),
            maxshape=(None,),
            chunks=(_BATCH_CHUNK,),
            dtype=np.uint64,
        )
        self._batch_frame_count = group.create_dataset(
            "batch_frame_count",
            shape=(0,),
            maxshape=(None,),
            chunks=(_BATCH_CHUNK,),
            dtype=np.uint64,
        )
        self._batch_record_start = group.create_dataset(
            "batch_record_start",
            shape=(0,),
            maxshape=(None,),
            chunks=(_BATCH_CHUNK,),
            dtype=np.uint64,
        )
        self._batch_record_count = group.create_dataset(
            "batch_record_count",
            shape=(0,),
            maxshape=(None,),
            chunks=(_BATCH_CHUNK,),
            dtype=np.uint64,
        )
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
        self._frame_batch_sequence = group.create_dataset(
            "frame_batch_sequence",
            shape=(0,),
            maxshape=(None,),
            chunks=(_FRAME_CHUNK,),
            dtype=np.uint64,
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
        batch_index = self._batch_sequence.shape[0]
        batch_sequence = np.uint64(batch.batch_sequence)

        rollback_state = self._append_rollback_state()
        try:
            self._batch_sequence.resize((batch_index + 1,))
            self._batch_sequence[batch_index] = batch_sequence
            self._batch_frame_start.resize((batch_index + 1,))
            self._batch_frame_start[batch_index] = np.uint64(frame_start)
            self._batch_frame_count.resize((batch_index + 1,))
            self._batch_frame_count[batch_index] = np.uint64(n_frames)
            self._batch_record_start.resize((batch_index + 1,))
            self._batch_record_start[batch_index] = np.uint64(record_start)
            self._batch_record_count.resize((batch_index + 1,))
            self._batch_record_count[batch_index] = np.uint64(n_records)

            self._frame_timestamps.resize((frame_end,))
            self._frame_timestamps[frame_start:frame_end] = batch.frame_timestamps_ns

            self._frame_offsets.resize((frame_end + 1,))
            self._frame_offsets[frame_start + 1:frame_end + 1] = (
                batch.frame_offsets[1:] + np.uint64(record_start)
            )

            self._frame_batch_sequence.resize((frame_end,))
            self._frame_batch_sequence[frame_start:frame_end] = batch_sequence

            self._bead_ids.resize((record_end,))
            self._bead_ids[record_start:record_end] = batch.bead_ids

            self._positions.resize((record_end, 3))
            self._positions[record_start:record_end, :] = batch.positions_nm

            if self._roi_positions is not None:
                self._roi_positions.resize((record_end, 2))
                self._roi_positions[record_start:record_end, :] = batch.roi_positions_px
            self._update_timestamp_range(batch.frame_timestamps_ns)
            self._file.flush()
        except Exception:
            self._rollback_append(rollback_state)
            raise

    def close(self) -> None:
        if getattr(self, "_file", None) is None:
            return
        self._file.close()
        self._file = None

    def _update_timestamp_range(self, frame_timestamps_ns: np.ndarray) -> None:
        batch_min = np.uint64(np.min(frame_timestamps_ns))
        batch_max = np.uint64(np.max(frame_timestamps_ns))
        if "min_frame_timestamp_ns" in self._group.attrs:
            batch_min = min(batch_min, self._group.attrs["min_frame_timestamp_ns"])
            batch_max = max(batch_max, self._group.attrs["max_frame_timestamp_ns"])
        self._group.attrs["min_frame_timestamp_ns"] = np.uint64(batch_min)
        self._group.attrs["max_frame_timestamp_ns"] = np.uint64(batch_max)

    def _append_rollback_state(self) -> dict[str, object]:
        dataset_shapes = {
            "batch_sequence": self._batch_sequence.shape,
            "batch_frame_start": self._batch_frame_start.shape,
            "batch_frame_count": self._batch_frame_count.shape,
            "batch_record_start": self._batch_record_start.shape,
            "batch_record_count": self._batch_record_count.shape,
            "frame_timestamps": self._frame_timestamps.shape,
            "frame_offsets": self._frame_offsets.shape,
            "frame_batch_sequence": self._frame_batch_sequence.shape,
            "bead_ids": self._bead_ids.shape,
            "positions": self._positions.shape,
        }
        if self._roi_positions is not None:
            dataset_shapes["roi_positions"] = self._roi_positions.shape

        timestamp_attrs = {}
        for name in ("min_frame_timestamp_ns", "max_frame_timestamp_ns"):
            timestamp_attrs[name] = (
                name in self._group.attrs,
                self._group.attrs.get(name),
            )
        return {"dataset_shapes": dataset_shapes, "timestamp_attrs": timestamp_attrs}

    def _rollback_append(self, state: dict[str, object]) -> None:
        try:
            dataset_shapes = state["dataset_shapes"]
            self._batch_sequence.resize(dataset_shapes["batch_sequence"])
            self._batch_frame_start.resize(dataset_shapes["batch_frame_start"])
            self._batch_frame_count.resize(dataset_shapes["batch_frame_count"])
            self._batch_record_start.resize(dataset_shapes["batch_record_start"])
            self._batch_record_count.resize(dataset_shapes["batch_record_count"])
            self._frame_timestamps.resize(dataset_shapes["frame_timestamps"])
            self._frame_offsets.resize(dataset_shapes["frame_offsets"])
            self._frame_batch_sequence.resize(dataset_shapes["frame_batch_sequence"])
            self._bead_ids.resize(dataset_shapes["bead_ids"])
            self._positions.resize(dataset_shapes["positions"])
            if self._roi_positions is not None:
                self._roi_positions.resize(dataset_shapes["roi_positions"])

            timestamp_attrs = state["timestamp_attrs"]
            for name, (existed, value) in timestamp_attrs.items():
                if existed:
                    self._group.attrs[name] = value
                elif name in self._group.attrs:
                    del self._group.attrs[name]
            self._file.flush()
        except Exception as exc:
            logger.exception("Failed to roll back partial tracking-data append: %s", exc)


class TrackingDataWriter(Process):
    def __init__(self, queue: "QueueType", warning_queue: "QueueType | None" = None):
        super().__init__()
        self._queue = queue
        self._warning_queue = warning_queue

    def run(self) -> None:
        open_files: dict[tuple[int, str, bool, int], TrackingHDF5File] = {}
        while True:
            batch = self._queue.get()
            if batch is None:
                break
            key: tuple[int, str, bool, int] | None = None
            path: Path | None = None
            try:
                key = _file_key(batch)
                current_file = open_files.get(key)
                if current_file is None:
                    path = tracking_data_path(batch.acquisition_dir, batch.file_start_ns)
                    current_file = TrackingHDF5File(
                        path,
                        include_roi_positions=batch.include_roi_positions,
                        recording_id=batch.recording_id,
                        recording_start_ns=batch.recording_start_ns,
                        file_start_ns=batch.file_start_ns,
                        rotation_interval_ns=(
                            0
                            if batch.max_file_duration_ns is None
                            else batch.max_file_duration_ns
                        ),
                    )
                    open_files[key] = current_file
                else:
                    path = current_file.path
                current_file.append(batch)
            except Exception as exc:
                logger.exception("Skipping tracking data batch after writer failure: %s", exc)
                self._report_writer_failure(batch, path, exc)
                failed_file = open_files.pop(key, None) if key is not None else None
                if failed_file is not None:
                    try:
                        failed_file.close()
                    except Exception as close_exc:
                        logger.exception(
                            "Failed to close tracking data file after writer failure: %s",
                            close_exc,
                        )
        for tracking_file in open_files.values():
            tracking_file.close()

    def _report_writer_failure(
        self,
        batch: TrackingDataBatch,
        path: Path | None,
        exc: Exception,
    ) -> None:
        if self._warning_queue is None:
            return
        error = str(exc).strip() or repr(exc)
        warning = {
            "type": TRACKING_DATA_WRITER_FAILURE_WARNING,
            "timestamp": time(),
            "path": None if path is None else str(path),
            "recording_id": int(batch.recording_id),
            "batch_sequence": int(batch.batch_sequence),
            "frames": int(batch.frame_timestamps_ns.shape[0]),
            "records": int(batch.bead_ids.shape[0]),
            "error": error,
        }
        try:
            self._warning_queue.put_nowait(warning)
        except Full:
            logger.debug("Dropping tracking data writer failure warning because queue is full")


def _to_uint16_array(values: np.ndarray, field_name: str) -> np.ndarray:
    array = np.asarray(values)
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{field_name} must contain only finite values")
    if np.any(array < 0) or np.any(array > _MAX_UINT16):
        raise ValueError(f"{field_name} must fit in uint16")
    if not np.all(array == np.floor(array)):
        raise ValueError(f"{field_name} must contain integer values")
    return array.astype(np.uint16)


def _tracking_file_bucket_start_ns(
    first_frame_timestamp_ns: int,
    *,
    recording_start_ns: int | None,
    max_file_duration_ns: int | None,
) -> tuple[int, int]:
    first_frame_timestamp_ns = int(first_frame_timestamp_ns)
    if recording_start_ns is None:
        recording_start_ns = first_frame_timestamp_ns
    recording_start_ns = int(recording_start_ns)
    if recording_start_ns < 0:
        raise ValueError("recording_start_ns must be non-negative")
    if max_file_duration_ns is None:
        return recording_start_ns, recording_start_ns

    elapsed_ns = max(0, first_frame_timestamp_ns - recording_start_ns)
    bucket_index = elapsed_ns // int(max_file_duration_ns)
    return (
        recording_start_ns,
        recording_start_ns + bucket_index * int(max_file_duration_ns),
    )


def _file_key(batch: TrackingDataBatch) -> tuple[int, str, bool, int]:
    return (
        int(batch.recording_id),
        str(batch.acquisition_dir),
        bool(batch.include_roi_positions),
        int(batch.file_start_ns),
    )


def _format_timestamp_for_filename(timestamp_ns: int) -> str:
    timestamp_ns = int(timestamp_ns)
    seconds = timestamp_ns // _NANOSECONDS_PER_SECOND
    microseconds = (timestamp_ns % _NANOSECONDS_PER_SECOND) // 1000
    timestamp = datetime.fromtimestamp(seconds).replace(microsecond=microseconds)
    return timestamp.strftime("%Y-%m-%d %H-%M-%S.%f")
