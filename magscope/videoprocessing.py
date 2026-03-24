from __future__ import annotations

import copy
from multiprocessing import Lock, Process, Queue
import os
from pathlib import Path
from queue import Empty, Full
from typing import TYPE_CHECKING
import warnings

import magtrack
from magtrack._cupy import cp, is_cupy_available
import numpy as np
import tifffile

from magscope._logging import get_logger
from magscope.datatypes import (
    DatasetNotReadyError,
    LiveProfileBuffer,
    MatrixBuffer,
    VideoBuffer,
    ZLUTSweepDataset,
)
from magscope.ipc import Delivery, register_ipc_command
from magscope.ipc_commands import (ArmZLUTSweepCaptureCommand, ClearPendingZLUTProfileLengthCommand,
                                   DisarmZLUTSweepCaptureCommand, LoadZLUTCommand,
                                   ReportProfileLengthCommand, ReportZLUTProfileLengthCommand,
                                   RequestProfileLengthCommand, RequestZLUTProfileLengthCommand,
                                   SetSettingsCommand, ShowMessageCommand, UnloadZLUTCommand,
                                   UpdateTrackingOptionsCommand, UpdateWaitingCommand,
                                   UpdateZLUTMetadataCommand, WaitUntilAcquisitionOnCommand,
                                   ZLUTSweepCaptureCompleteCommand)
from magscope.processes import ManagerProcessBase
from magscope.settings import MagScopeSettings
from magscope.utils import (AcquisitionMode, PoolVideoFlag, crop_stack_to_rois, date_timestamp_str,
                            register_script_command)

if TYPE_CHECKING:
    from multiprocessing.queues import Queue as QueueType
    from multiprocessing.sharedctypes import Synchronized
    from multiprocessing.synchronize import Lock as LockType
    ValueTypeUI8 = Synchronized[int]
    ValueTypeInt = Synchronized[int]


logger = get_logger("videoprocessing")

_LOOKUP_Z_PROFILE_WARNING = 'lookup_z_profile_size_warning'
_DEFAULT_TRACKING_OPTIONS = {'center_of_mass': {'background': 'median'}}

class VideoProcessorManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self._tasks: QueueType | None = None
        self._n_workers: int | None = None
        self._workers: list[VideoWorker] = []
        self._gpu_lock: LockType = Lock()
        self._profile_length_queue: QueueType | None = None
        self._pending_profile_length_request = False
        self._warning_queue: QueueType | None = None
        self._zlut_capture_complete_queue: QueueType | None = None
        self._zlut_capture_earliest_timestamp: float | None = None
        self._zlut_capture_motor_z_value: float | None = None
        self._zlut_capture_remaining_profiles_per_bead: int | None = None
        self._zlut_capture_step_index: int | None = None
        self._zlut_frozen_bead_ids = np.zeros((0,), dtype=np.uint32)
        self._zlut_frozen_bead_rois = np.zeros((0, 4), dtype=np.uint32)
        self._zlut_profile_length_queue: QueueType | None = None
        self._pending_zlut_profile_length_request = False
        self._lookup_z_warning_reported = False
        self._waiting_for_acquisition: bool | None = None

        # TODO: Check implementation
        self._save_profiles = False

        self._zlut_path: Path | None = Path(__file__).with_name('simulation_zlut.txt')
        self._zlut_metadata: dict[str, float | int] | None = None
        self._zlut = None
        self._tracking_options: dict = copy.deepcopy(_DEFAULT_TRACKING_OPTIONS)
        self._load_default_zlut()

    @register_ipc_command(SetSettingsCommand, delivery=Delivery.BROADCAST, target='ManagerProcessBase')
    def set_settings(self, settings: MagScopeSettings):
        super().set_settings(settings)
        self._lookup_z_warning_reported = False

    @register_ipc_command(UpdateTrackingOptionsCommand)
    def update_tracking_options(self, value: dict):
        self._tracking_options = copy.deepcopy(value) if value else copy.deepcopy(_DEFAULT_TRACKING_OPTIONS)

    def setup(self):
        self._n_workers = self.settings['video processors n']
        self._tasks = Queue(maxsize=self._n_workers)
        self._profile_length_queue = Queue()
        self._warning_queue = Queue()
        self._zlut_capture_complete_queue = Queue()
        self._zlut_profile_length_queue = Queue()

        # Create the workers
        for _ in range(self._n_workers):
            worker = VideoWorker(tasks=self._tasks,
                                 locks=self.locks,
                                 video_flag=self.shared_values.video_process_flag,
                                  busy_count=self.shared_values.video_process_busy_count,
                                  gpu_lock=self._gpu_lock,
                                 profile_length_queue=self._profile_length_queue,
                                  warning_queue=self._warning_queue,
                                  zlut_capture_complete_queue=self._zlut_capture_complete_queue,
                                  zlut_profile_length_queue=self._zlut_profile_length_queue,
                                   live_profile_enabled=self.shared_values.live_profile_enabled,
                                   live_profile_bead=self.shared_values.live_profile_bead)
            self._workers.append(worker)

        # Start the workers
        for worker in self._workers:
            worker.start()

        self._broadcast_zlut_metadata()

    def do_main_loop(self):
        self._process_profile_length_reports()
        self._process_zlut_profile_length_reports()
        self._process_zlut_capture_reports()
        self._process_worker_warnings()
        if self._waiting_for_acquisition is not None:
            self._finish_waiting_when_ready()

        # Check if images are ready for image processing
        if self._acquisition_on:
            if self.shared_values.video_process_flag.value == PoolVideoFlag.READY:
                if self.video_buffer.check_read_stack():
                    if self._add_task():
                        self.shared_values.video_process_flag.value = PoolVideoFlag.RUNNING

    def quit(self):
        super().quit()

        if hasattr(self, '_workers'):
            if self._tasks is not None:
                for _ in self._workers:
                    self._tasks.put(None)
            for worker in self._workers:
                if worker and worker.is_alive():
                    worker.join()
            for worker in self._workers:
                if worker and worker.is_alive():
                    worker.terminate()

    @register_ipc_command(LoadZLUTCommand)
    def load_zlut_file(self, filepath: str) -> None:
        path = Path(filepath).expanduser()
        self._zlut = None
        try:
            self._set_zlut_from_path(path)
        except Exception as exc:
            logger.exception('Failed to load Z-LUT file: %s', exc)
            self._notify_zlut_error(path, exc)
            return

        self._broadcast_zlut_metadata()

    def _load_default_zlut(self) -> None:
        try:
            self._set_zlut_from_path(self._zlut_path)
        except Exception as exc:
            logger.exception('Failed to load default Z-LUT: %s', exc)

    @register_ipc_command(UnloadZLUTCommand)
    def unload_zlut(self) -> None:
        self._zlut_path = None
        self._zlut_metadata = None
        self._zlut = None
        self._lookup_z_warning_reported = False
        self._broadcast_zlut_metadata()

    def _set_zlut_from_path(self, path: Path) -> None:
        zlut_array = np.loadtxt(path)
        metadata = self._extract_zlut_metadata(zlut_array)

        self._zlut_metadata = metadata
        self._zlut_path = path
        self._zlut = self._to_processing_array(zlut_array)
        self._lookup_z_warning_reported = False

    @staticmethod
    def _extract_zlut_metadata(zlut_array: np.ndarray) -> dict[str, float | int]:
        if zlut_array.ndim != 2:
            raise ValueError('Z-LUT must be a 2D array')
        if zlut_array.shape[0] < 2:
            raise ValueError('Z-LUT must include at least one profile row')
        if zlut_array.shape[1] < 2:
            raise ValueError('Z-LUT must include at least two z-reference values')
        if not np.all(np.isfinite(zlut_array)):
            raise ValueError('Z-LUT contains non-finite values')

        z_references = zlut_array[0, :]
        step_size = float(np.mean(np.diff(z_references)))

        return {
            'z_min': float(np.min(z_references)),
            'z_max': float(np.max(z_references)),
            'step_size': step_size,
            'profile_length': int(zlut_array.shape[0] - 1),
        }

    @staticmethod
    def _to_processing_array(zlut_array: np.ndarray):
        if is_cupy_available():
            return cp.asarray(zlut_array)
        return zlut_array

    def _broadcast_zlut_metadata(self) -> None:
        command = UpdateZLUTMetadataCommand(
            filepath=str(self._zlut_path) if self._zlut_path is not None else None,
            z_min=None if self._zlut_metadata is None else self._zlut_metadata['z_min'],
            z_max=None if self._zlut_metadata is None else self._zlut_metadata['z_max'],
            step_size=None if self._zlut_metadata is None else self._zlut_metadata['step_size'],
            profile_length=None if self._zlut_metadata is None else self._zlut_metadata['profile_length'],
        )
        self.send_ipc(command)

    def _notify_zlut_error(self, path: Path, exc: Exception) -> None:
        reason = str(exc).strip() or repr(exc)
        command = ShowMessageCommand(
            text='Failed to load Z-LUT file',
            details=f'{path}: {reason}',
        )
        self.send_ipc(command)

    @register_ipc_command(RequestProfileLengthCommand)
    def report_profile_length(self) -> None:
        """Arm a one-shot profile-length report for a future processed frame.

        The request intentionally rides along with the normal worker task queue
        instead of probing the current ``VideoBuffer`` contents immediately.
        This keeps the result tied to video processed after the request arrives
        and ensures only one worker handles the request at a time via a normal
        task-local flag.
        """
        if self._profile_length_queue is not None:
            while True:
                try:
                    self._profile_length_queue.get_nowait()
                except Empty:
                    break
        self._pending_profile_length_request = True

    @register_ipc_command(RequestZLUTProfileLengthCommand)
    def report_zlut_profile_length(
        self,
        bead_ids: tuple[int, ...] = (),
        bead_rois: tuple[tuple[int, int, int, int], ...] = (),
    ) -> None:
        if self._zlut_profile_length_queue is not None:
            while True:
                try:
                    self._zlut_profile_length_queue.get_nowait()
                except Empty:
                    break
        self._set_zlut_frozen_rois(bead_ids=bead_ids, bead_rois=bead_rois)
        self._pending_zlut_profile_length_request = True

    def _process_profile_length_reports(self) -> None:
        """Forward the first successful worker measurement back to the UI.

        Workers only enqueue successful measurements, so leaving the pending
        flag armed causes later normal processing tasks to keep carrying the
        request until one succeeds.
        """
        if self._profile_length_queue is None or not self._pending_profile_length_request:
            return

        while True:
            try:
                profile_length = self._profile_length_queue.get_nowait()
            except Empty:
                break

            self._pending_profile_length_request = False
            self.send_ipc(ReportProfileLengthCommand(profile_length=int(profile_length)))
            break

    def _process_zlut_profile_length_reports(self) -> None:
        if self._zlut_profile_length_queue is None or not self._pending_zlut_profile_length_request:
            return

        while True:
            try:
                profile_length = self._zlut_profile_length_queue.get_nowait()
            except Empty:
                break

            self._pending_zlut_profile_length_request = False
            self.send_ipc(ReportZLUTProfileLengthCommand(profile_length=int(profile_length)))
            break

    @register_ipc_command(ClearPendingZLUTProfileLengthCommand)
    def clear_pending_zlut_profile_length_request(self) -> None:
        self._pending_zlut_profile_length_request = False
        self._zlut_frozen_bead_ids = np.zeros((0,), dtype=np.uint32)
        self._zlut_frozen_bead_rois = np.zeros((0, 4), dtype=np.uint32)
        if self._zlut_profile_length_queue is not None:
            while True:
                try:
                    self._zlut_profile_length_queue.get_nowait()
                except Empty:
                    break

    @register_ipc_command(ArmZLUTSweepCaptureCommand)
    def arm_zlut_sweep_capture(
        self,
        step_index: int,
        motor_z_value: float,
        remaining_profiles_per_bead: int,
        earliest_timestamp: float,
        bead_ids: tuple[int, ...] = (),
        bead_rois: tuple[tuple[int, int, int, int], ...] = (),
    ) -> None:
        self._zlut_capture_step_index = int(step_index)
        self._zlut_capture_motor_z_value = float(motor_z_value)
        self._zlut_capture_remaining_profiles_per_bead = int(remaining_profiles_per_bead)
        self._zlut_capture_earliest_timestamp = float(earliest_timestamp)
        self._set_zlut_frozen_rois(bead_ids=bead_ids, bead_rois=bead_rois)

    @register_ipc_command(DisarmZLUTSweepCaptureCommand)
    def disarm_zlut_sweep_capture(self) -> None:
        self._zlut_capture_step_index = None
        self._zlut_capture_motor_z_value = None
        self._zlut_capture_remaining_profiles_per_bead = None
        self._zlut_capture_earliest_timestamp = None
        self._zlut_frozen_bead_ids = np.zeros((0,), dtype=np.uint32)
        self._zlut_frozen_bead_rois = np.zeros((0, 4), dtype=np.uint32)

    def _process_zlut_capture_reports(self) -> None:
        if self._zlut_capture_complete_queue is None:
            return

        while True:
            try:
                step_index, written_count, written_profiles_per_bead, error = self._zlut_capture_complete_queue.get_nowait()
            except Empty:
                break

            self.send_ipc(
                ZLUTSweepCaptureCompleteCommand(
                    step_index=int(step_index),
                    written_count=int(written_count),
                    written_profiles_per_bead=int(written_profiles_per_bead),
                    error=error,
                )
            )

    def _add_task(self):
        bead_ids, bead_rois = self.get_cached_bead_rois()
        if self._should_use_frozen_zlut_rois():
            bead_ids = self._zlut_frozen_bead_ids
            bead_rois = self._zlut_frozen_bead_rois
        kwargs = {
            'acquisition_dir': self._acquisition_dir,
            'acquisition_dir_on': self._acquisition_dir_on,
            'acquisition_mode': self._acquisition_mode,
            'bead_ids': bead_ids,
            'bead_rois': bead_rois,
            'magnification': self.settings['magnification'],
            'nm_per_px': self.camera_type.nm_per_px,
            'report_profile_length': self._pending_profile_length_request,
            'report_zlut_profile_length': self._pending_zlut_profile_length_request,
            'save_profiles': self._save_profiles,
            'tracking_options': copy.deepcopy(self._tracking_options),
            'zlut': self._zlut
        }
        capture_step_index = self._zlut_capture_step_index
        capture_earliest_timestamp = self._zlut_capture_earliest_timestamp
        capture_motor_z_value = self._zlut_capture_motor_z_value
        capture_remaining_profiles_per_bead = self._zlut_capture_remaining_profiles_per_bead
        if (
            capture_step_index is not None
            and capture_earliest_timestamp is not None
            and capture_motor_z_value is not None
            and capture_remaining_profiles_per_bead is not None
        ):
            kwargs['zlut_capture'] = {
                'step_index': int(capture_step_index),
                'earliest_timestamp': float(capture_earliest_timestamp),
                'motor_z_value': float(capture_motor_z_value),
                'remaining_profiles_per_bead': int(capture_remaining_profiles_per_bead),
            }

        try:
            self._tasks.put_nowait(kwargs)
            if 'zlut_capture' in kwargs:
                self._zlut_capture_step_index = None
                self._zlut_capture_earliest_timestamp = None
                self._zlut_capture_motor_z_value = None
                self._zlut_capture_remaining_profiles_per_bead = None
            return True
        except Full:
            logger.warning('Skipping video processing task because worker queue is full')
            return False

    def _set_zlut_frozen_rois(
        self,
        *,
        bead_ids: tuple[int, ...],
        bead_rois: tuple[tuple[int, int, int, int], ...],
    ) -> None:
        self._zlut_frozen_bead_ids = np.asarray(bead_ids, dtype=np.uint32)
        self._zlut_frozen_bead_rois = np.asarray(bead_rois, dtype=np.uint32).reshape((-1, 4))

    def _should_use_frozen_zlut_rois(self) -> bool:
        if self._zlut_frozen_bead_ids.size == 0 or self._zlut_frozen_bead_rois.shape[0] == 0:
            return False
        return self._pending_zlut_profile_length_request or self._zlut_capture_step_index is not None

    def _process_worker_warnings(self) -> None:
        if self._warning_queue is None:
            return

        while True:
            try:
                warning = self._warning_queue.get_nowait()
            except Empty:
                break

            if warning == _LOOKUP_Z_PROFILE_WARNING and not self._lookup_z_warning_reported:
                self._lookup_z_warning_reported = True
                command = ShowMessageCommand(
                    text='Z-LUT may not match current ROI or detection settings.',
                    details='MagTrack reported a LookupZProfileSizeWarning; consider reloading a compatible Z-LUT or adjusting ROI size.',
                )
                self.send_ipc(command)

    @register_ipc_command(WaitUntilAcquisitionOnCommand)
    @register_script_command(WaitUntilAcquisitionOnCommand)
    def script_wait_until_acquisition_on(self, value: bool):
        self._waiting_for_acquisition = value

    def _finish_waiting_when_ready(self):
        if self._acquisition_on == self._waiting_for_acquisition:
            command = UpdateWaitingCommand()
            self.send_ipc(command)
            self._waiting_for_acquisition = None

class VideoWorker(Process):
    def __init__(self,
                 tasks: QueueType,
                 locks: dict[str, LockType],
                 video_flag: ValueTypeUI8,
                 busy_count: ValueTypeUI8,
                 gpu_lock: Lock,
                 profile_length_queue: QueueType | None,
                 warning_queue: QueueType | None,
                 zlut_capture_complete_queue: QueueType | None,
                 zlut_profile_length_queue: QueueType | None,
                 live_profile_enabled: ValueTypeUI8,
                 live_profile_bead: ValueTypeInt):
        super().__init__()
        self._gpu_lock: Lock = gpu_lock
        self._tasks: QueueType = tasks
        self._locks: dict[str, LockType] = locks
        self._video_flag: ValueTypeUI8 = video_flag
        self._busy_count: ValueTypeUI8 = busy_count
        self._profile_length_queue: QueueType | None = profile_length_queue
        self._warning_queue: QueueType | None = warning_queue
        self._zlut_capture_complete_queue: QueueType | None = zlut_capture_complete_queue
        self._zlut_profile_length_queue: QueueType | None = zlut_profile_length_queue
        self._live_profile_enabled = live_profile_enabled
        self._live_profile_bead = live_profile_bead
        self._video_buffer: VideoBuffer | None = None
        self._tracks_buffer: MatrixBuffer | None = None
        self._zlut_sweep_dataset: ZLUTSweepDataset | None = None

    def run(self):
        self._live_profile_buffer = LiveProfileBuffer(
            create=False,
            locks=self._locks,
        )
        self._tracks_buffer = MatrixBuffer(
            create=False,
            name='TracksBuffer',
            locks=self._locks,
        )
        self._video_buffer = VideoBuffer(
            create=False,
            locks=self._locks,
        )

        while True:
            task = self._tasks.get()
            if task is None: # Signal to close
                break
            with self._busy_count.get_lock():
                self._busy_count.value += 1
            try:
                self.process(task)
            except Exception as e:
                logger.exception('Error in video processing: %s', e)
                self._report_zlut_capture_task_failure(task, e)
            with self._busy_count.get_lock():
                self._busy_count.value -= 1
        if self._zlut_sweep_dataset is not None:
            self._zlut_sweep_dataset.close()

    def _report_zlut_capture_task_failure(self, task: dict | None, exc: Exception) -> None:
        if self._zlut_capture_complete_queue is None or not isinstance(task, dict):
            return
        zlut_capture = task.get('zlut_capture')
        if not isinstance(zlut_capture, dict):
            return
        step_index = zlut_capture.get('step_index')
        if step_index is None:
            return
        reason = str(exc).strip() or repr(exc)
        try:
            self._zlut_capture_complete_queue.put_nowait((int(step_index), 0, 0, reason))
        except Full:
            logger.debug('Dropping Z-LUT capture task failure because queue is full')

    def process(self, kwargs):
        acquisition_dir: str = kwargs['acquisition_dir']
        acquisition_dir_on: bool = kwargs['acquisition_dir_on']
        acquisition_mode: AcquisitionMode = kwargs['acquisition_mode']
        bead_ids: np.ndarray = kwargs['bead_ids']
        bead_rois: np.ndarray = kwargs['bead_rois']
        save_profiles = kwargs['save_profiles']
        zlut = kwargs['zlut']
        nm_per_px: float = kwargs['nm_per_px']
        magnification: float = kwargs['magnification']
        report_profile_length: bool = kwargs.get('report_profile_length', False)
        report_zlut_profile_length: bool = kwargs.get('report_zlut_profile_length', False)
        tracking_options: dict = kwargs.get('tracking_options', {}) or {}
        zlut_capture: dict | None = kwargs.get('zlut_capture')

        if bead_ids.size == 0 or bead_rois.shape[0] == 0:
            bead_ids = None
            bead_rois = None

        def _update_live_profile(timestamps: np.ndarray, bead_ids: np.ndarray, profiles: np.ndarray) -> None:
            if self._live_profile_buffer is None or not self._live_profile_enabled.value:
                return

            target_bead = int(self._live_profile_bead.value)
            if target_bead < 0:
                return

            bead_selection = bead_ids == target_bead
            if not np.any(bead_selection):
                return

            bead_indices = np.flatnonzero(bead_selection)
            latest_index = bead_indices[np.argmax(timestamps[bead_selection])]
            profile = np.asarray(profiles[:, latest_index]).astype(np.float64)
            self._live_profile_buffer.write_profile(
                float(timestamps[latest_index]), target_bead, profile
            )

        def _report_profile_length_if_requested(profiles: np.ndarray) -> None:
            """Publish ``profiles.shape[0]`` for an armed one-shot request.

            The manager keeps the request pending until a worker successfully
            emits a value, so this helper only reports usable tracker output and
            stays silent for failed or incomplete processing attempts.
            """
            if not report_profile_length or self._profile_length_queue is None:
                return
            if not hasattr(profiles, 'shape') or len(profiles.shape) == 0:
                return
            try:
                self._profile_length_queue.put_nowait(int(profiles.shape[0]))
            except Full:
                logger.debug('Dropping profile length report because queue is full')

        def _report_zlut_profile_length_if_requested(profiles: np.ndarray) -> None:
            if not report_zlut_profile_length or self._zlut_profile_length_queue is None:
                return
            if not hasattr(profiles, 'shape') or len(profiles.shape) == 0:
                return
            try:
                self._zlut_profile_length_queue.put_nowait(int(profiles.shape[0]))
            except Full:
                logger.debug('Dropping Z-LUT profile length report because queue is full')

        def _capture_zlut_sweep_if_requested(
            timestamps: np.ndarray,
            bead_ids: np.ndarray,
            profiles: np.ndarray,
        ) -> None:
            if zlut_capture is None or self._zlut_capture_complete_queue is None:
                return
            try:
                if self._zlut_sweep_dataset is None:
                    self._zlut_sweep_dataset = ZLUTSweepDataset.attach(locks=self._locks)
                unique_bead_ids = np.unique(bead_ids)
                n_beads = int(unique_bead_ids.shape[0])
                if n_beads <= 0:
                    self._zlut_capture_complete_queue.put_nowait((zlut_capture['step_index'], 0, 0, None))
                    return
                timestamp_rows = np.asarray(timestamps, dtype=np.float64)
                finite_timestamps = timestamp_rows[np.isfinite(timestamp_rows)]
                if finite_timestamps.size == 0:
                    self._zlut_capture_complete_queue.put_nowait((zlut_capture['step_index'], 0, 0, None))
                    return
                if float(np.min(finite_timestamps)) <= float(zlut_capture['earliest_timestamp']):
                    self._zlut_capture_complete_queue.put_nowait((zlut_capture['step_index'], 0, 0, None))
                    return
                available_profiles_per_bead = int(np.asarray(timestamps).shape[0] / n_beads)
                written_profiles_per_bead = min(
                    available_profiles_per_bead,
                    int(zlut_capture['remaining_profiles_per_bead']),
                )
                batch_size = written_profiles_per_bead * n_beads
                if batch_size <= 0:
                    self._zlut_capture_complete_queue.put_nowait((zlut_capture['step_index'], 0, 0, None))
                    return

                profile_rows = np.asarray(profiles, dtype=np.float64).T[:batch_size, :]
                bead_id_rows = np.asarray(bead_ids, dtype=np.uint32)[:batch_size]
                timestamp_rows = timestamp_rows[:batch_size]
                self._zlut_sweep_dataset.write(
                    bead_ids=bead_id_rows,
                    step_indices=np.full((batch_size,), zlut_capture['step_index'], dtype=np.uint32),
                    timestamps=timestamp_rows,
                    motor_z_values=np.full((batch_size,), zlut_capture['motor_z_value'], dtype=np.float64),
                    valid_flags=np.ones((batch_size,), dtype=np.uint8),
                    profiles=profile_rows,
                )
                self._zlut_capture_complete_queue.put_nowait((
                    zlut_capture['step_index'],
                    batch_size,
                    written_profiles_per_bead,
                    None,
                ))
            except DatasetNotReadyError:
                return
            except Full:
                logger.debug('Dropping Z-LUT capture completion because queue is full')
            except Exception as exc:
                reason = str(exc).strip() or repr(exc)
                logger.exception('Failed to capture Z-LUT sweep step: %s', reason)
                try:
                    self._zlut_capture_complete_queue.put_nowait((zlut_capture['step_index'], 0, 0, reason))
                except Full:
                    logger.debug('Dropping Z-LUT capture error because queue is full')
            finally:
                if self._zlut_sweep_dataset is not None:
                    self._zlut_sweep_dataset.close()
                    self._zlut_sweep_dataset = None

        def save_video_full(first_timestamp, stack, timestamps_str,):
            filepath = os.path.join(acquisition_dir, f'Video {first_timestamp}.tiff')
            tifffile.imwrite(
                filepath,
                stack.transpose(2, 1, 0),  # axes=(T,Y,X)
                imagej=True,
                resolution=(1. / (nm_per_px / magnification), 1. / (nm_per_px / magnification)),
                metadata={
                    'axes': 'TYX',
                    'Labels': timestamps_str,
                    'unit': 'nm'
                })

        def save_video_crop(first_timestamp, stack_rois, timestamps_str):
            filepath = os.path.join(acquisition_dir, f'Video {first_timestamp}.tiff')
            tifffile.imwrite(
                filepath,
                stack_rois.transpose(2, 3, 1, 0),  # axes must be (T,ROI,Y,X)
                imagej=True,
                resolution=(1. / (nm_per_px / magnification), 1. / (nm_per_px / magnification)),
                metadata={
                    'axes': 'TCYX',
                    'Labels': timestamps_str,
                    'unit': 'nm'
                })

        def save_tracks_profiles(first_timestamp, profiles, tracks):
            if acquisition_dir_on and acquisition_dir:
                filepath = os.path.join(acquisition_dir,
                                        f'Bead Positions {first_timestamp}.txt')
                np.savetxt(
                    filepath,
                    tracks,
                    header='Time(sec) X(nm) Y(nm) Z(nm) Bead-ID ROI-X(px) ROI-Y(px)')

                if save_profiles:
                    filepath = os.path.join(acquisition_dir,
                                            f'Bead Profiles {first_timestamp}.txt')
                    np.savetxt(filepath, profiles)

        def calculate_tracks(n_images, stack_rois, timestamps):
            # Calculate
            bead_roi_values = bead_rois.astype(np.float64, copy=False)
            roi_width = int(bead_roi_values[0, 1] - bead_roi_values[0, 0])
            n_rois = bead_rois.shape[0]
            stack_rois_reshaped = stack_rois.reshape(roi_width, roi_width, n_rois * n_images)

            # "zlut" can be None; magtrack returns NaN z values in that case.
            with warnings.catch_warnings(record=True) as warning_records:
                warnings.simplefilter('always')
                with self._gpu_lock:
                    y, x, z, profiles = magtrack.stack_to_xyzp_advanced(
                        stack_rois_reshaped,
                        zlut,
                        **tracking_options,
                    )
            if is_cupy_available():
                cp.get_default_memory_pool().free_all_blocks()
            self._notify_lookup_profile_warning(warning_records)

            # Calculate bead indexes (b)
            b = np.tile(bead_ids.astype(np.float64, copy=False), n_images)

            # Tile the roi positions
            roi_x = np.tile(bead_roi_values[:, 0], n_images)
            roi_y = np.tile(bead_roi_values[:, 2], n_images)

            # Convert to the camera's top-left corner reference frame
            x = x + roi_x
            y = y + roi_y

            # Convert x & y to nanometers
            x *= nm_per_px / magnification
            y *= nm_per_px / magnification

            # Tile timestamps corresponding to each bead
            t = np.repeat(timestamps, n_rois)

            tracks = np.column_stack((t, x, y, z, b, roi_x, roi_y))
            _update_live_profile(t, b, profiles)
            _report_profile_length_if_requested(profiles)
            _report_zlut_profile_length_if_requested(profiles)
            _capture_zlut_sweep_if_requested(t, b, profiles)
            return tracks, profiles

        def process_mode_tracks():
            if bead_rois is not None:
                # Get stack and timestamps
                stack, timestamps = self._video_buffer.peak_stack()
                n_images = self._video_buffer.stack_shape[2]

                # Crop/copy stack to ROI
                stack_rois = crop_stack_to_rois(stack, bead_rois)

                # Copy timestamps
                timestamps = timestamps.copy()
                first_timestamp = date_timestamp_str(timestamps[0])

                # Delete the stack from memory ASAP to make memory available
                del stack
                self._release_stack()

                # Calculate tracks
                tracks_data, profiles = calculate_tracks(n_images, stack_rois, timestamps)

                # Store tracks in RAM
                self._tracks_buffer.write(tracks_data)

                # Save tracks and profiles to disk
                save_tracks_profiles(first_timestamp, profiles, tracks_data)

            else:  # No ROIs
                self._release_stack()

        def process_mode_track_and_crop_video():
            if bead_rois is not None:  # Check if there are any ROIs
                # Get stack and timestamps
                stack, timestamps = self._video_buffer.peak_stack()
                n_images = self._video_buffer.stack_shape[2]

                # Format timestamp and filename
                timestamps = timestamps.copy()  # Copy needs to be made for tracks
                timestamps_str = list(map(
                    str, timestamps.tolist()))  # "tolist" creates a copy
                first_timestamp = date_timestamp_str(
                    timestamps[0])

                # Crop/copy stack to ROI
                stack_rois = crop_stack_to_rois(stack, bead_rois)  # axes=(X,Y,T,ROI)

                # Delete the stack from memory ASAP to make memory available
                del stack
                self._release_stack()

                # Calculate tracks
                tracks_data, profiles = calculate_tracks(n_images, stack_rois, timestamps)

                # Store tracks in RAM
                self._tracks_buffer.write(tracks_data)

                # Save tracks and profiles to disk
                save_tracks_profiles(first_timestamp, profiles, tracks_data)

                # Save video to disk
                if acquisition_dir_on and acquisition_dir:
                    save_video_crop(first_timestamp, stack_rois, timestamps_str)

            else:  # No ROIs
                self._release_stack()

        def process_mode_track_and_full_video():
            # Get stack and timestamps from _buf
            stack, timestamps = self._video_buffer.peak_stack()
            n_images = self._video_buffer.stack_shape[2]

            # Format timestamp and filename
            timestamps = timestamps.copy()  # Copy needs to be made for tracks
            timestamps_str = list(map(
                str, timestamps.tolist()))  # "tolist" creates a copy
            first_timestamp = date_timestamp_str(timestamps[0])

            # Save video to disk
            if acquisition_dir_on and acquisition_dir:
                save_video_full(first_timestamp,
                                stack,
                                timestamps_str)

            if bead_rois is not None:  # Check if there are any ROIs
                # Crop/copy stack to ROI
                stack_rois = crop_stack_to_rois(stack, bead_rois)

                # Delete the stack from memory ASAP to make memory available
                del stack
                self._release_stack()

                # Calculate tracks
                tracks_data, profiles = calculate_tracks(n_images, stack_rois, timestamps)

                # Store tracks in RAM
                self._tracks_buffer.write(tracks_data)

                # Save tracks and profiles to disk
                save_tracks_profiles(first_timestamp, profiles, tracks_data)

            else:  # No ROIs
                del stack
                self._release_stack()

        def process_mode_crop_video():
            if bead_rois is not None and acquisition_dir_on and acquisition_dir:
                # Get stack and timestamps
                stack, timestamps = self._video_buffer.peak_stack()

                # Format timestamp
                timestamps_str = list(map(
                    str, timestamps.tolist()))  # "tolist" creates a copy
                first_timestamp = date_timestamp_str(timestamps[0])

                # Crop/copy stack to ROI
                stack_rois = crop_stack_to_rois(stack, bead_rois)  # axes=(X,Y,T,ROI)

                # Delete the stack from memory ASAP to make memory available
                del stack
                self._release_stack()

                # Save video to disk
                save_video_crop(first_timestamp, stack_rois, timestamps_str)

            else:
                self._release_stack()

        def process_mode_full_video():
            if acquisition_dir_on and acquisition_dir:
                # Get stack and timestamps from the video buffer
                stack, timestamps = self._video_buffer.peak_stack()

                # Format timestamps
                # "tolist" creates a copy
                timestamps_str = list(map(str, timestamps.tolist()))
                first_timestamp = date_timestamp_str(timestamps[0])

                # Save video to disk
                save_video_full(first_timestamp, stack, timestamps_str)

                # Delete the stack from memory ASAP to make memory available
                del stack

            self._release_stack()

        match acquisition_mode:
            case AcquisitionMode.TRACK:
                process_mode_tracks()
            case AcquisitionMode.TRACK_AND_CROP_VIDEO:
                process_mode_track_and_crop_video()
            case AcquisitionMode.TRACK_AND_FULL_VIDEO:
                process_mode_track_and_full_video()
            case AcquisitionMode.CROP_VIDEO:
                process_mode_crop_video()
            case AcquisitionMode.FULL_VIDEO:
                process_mode_full_video()

    def _notify_lookup_profile_warning(self, warning_records: list[warnings.WarningMessage]) -> None:
        if self._warning_queue is None:
            return

        warning_type = getattr(magtrack, 'LookupZProfileSizeWarning', None)
        if warning_type is None:
            return

        for warning_record in warning_records:
            if isinstance(warning_record.message, warning_type):
                try:
                    self._warning_queue.put_nowait(_LOOKUP_Z_PROFILE_WARNING)
                except Full:
                    logger.debug('Dropping LookupZProfileSizeWarning notification because queue is full')
                break

    def _release_stack(self):
        self._video_buffer.read_stack_no_return()

        # Allow a new pool process to start
        self._video_flag.value = PoolVideoFlag.FINISHED
