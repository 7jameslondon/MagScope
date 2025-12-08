from __future__ import annotations

from multiprocessing import Lock, Process, Queue
import os
from pathlib import Path
from queue import Full
from typing import TYPE_CHECKING

import magtrack
import numpy as np
import tifffile
from magtrack._cupy import cp, is_cupy_available

from magscope._logging import get_logger
from magscope.datatypes import MatrixBuffer, VideoBuffer
from magscope.ipc import register_ipc_command
from magscope.ipc_commands import (LoadZLUTCommand, ShowMessageCommand, UnloadZLUTCommand,
                                   UpdateWaitingCommand, UpdateZLUTMetadataCommand,
                                   WaitUntilAcquisitionOnCommand)
from magscope.processes import ManagerProcessBase
from magscope.utils import (AcquisitionMode, PoolVideoFlag, crop_stack_to_rois, date_timestamp_str,
                            register_script_command)

if TYPE_CHECKING:
    from multiprocessing.queues import Queue as QueueType
    from multiprocessing.sharedctypes import Synchronized
    from multiprocessing.synchronize import Lock as LockType
    ValueTypeUI8 = Synchronized[int]


logger = get_logger("videoprocessing")

class VideoProcessorManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self._tasks: QueueType | None = None
        self._n_workers: int | None = None
        self._workers: list[VideoWorker] = []
        self._gpu_lock: LockType = Lock()
        self._waiting_for_acquisition: bool | None = None

        # TODO: Check implementation
        self._save_profiles = False

        self._zlut_path: Path | None = Path(__file__).with_name('simulation_zlut.txt')
        self._zlut_metadata: dict[str, float | int] | None = None
        self._zlut = None
        self._load_default_zlut()

    def setup(self):
        self._n_workers = self.settings['video processors n']
        self._tasks = Queue(maxsize=self._n_workers)

        # Create the workers
        for _ in range(self._n_workers):
            worker = VideoWorker(tasks=self._tasks,
                                 locks=self.locks,
                                 video_flag=self.shared_values.video_process_flag,
                                 busy_count=self.shared_values.video_process_busy_count,
                                 gpu_lock=self._gpu_lock)
            self._workers.append(worker)

        # Start the workers
        for worker in self._workers:
            worker.start()

        self._broadcast_zlut_metadata()

    def do_main_loop(self):
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
        self._broadcast_zlut_metadata()

    def _set_zlut_from_path(self, path: Path) -> None:
        zlut_array = np.loadtxt(path)
        metadata = self._extract_zlut_metadata(zlut_array)

        self._zlut_metadata = metadata
        self._zlut_path = path
        self._zlut = self._to_processing_array(zlut_array)

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

    def _add_task(self):
        kwargs = {
            'acquisition_dir': self._acquisition_dir,
            'acquisition_dir_on': self._acquisition_dir_on,
            'acquisition_mode': self._acquisition_mode,
            'bead_rois': self.bead_rois,
            'magnification': self.settings['magnification'],
            'nm_per_px': self.camera_type.nm_per_px,
            'save_profiles': self._save_profiles,
            'zlut': self._zlut
        }

        try:
            self._tasks.put_nowait(kwargs)
            return True
        except Full:
            logger.warning('Skipping video processing task because worker queue is full')
            return False

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
                 gpu_lock: Lock):
        super().__init__()
        self._gpu_lock: Lock = gpu_lock
        self._tasks: QueueType = tasks
        self._locks: dict[str, LockType] = locks
        self._video_flag: ValueTypeUI8 = video_flag
        self._busy_count: ValueTypeUI8 = busy_count
        self._video_buffer: VideoBuffer | None = None
        self._tracks_buffer: MatrixBuffer | None = None

    def run(self):
        self._profiles_buffer = MatrixBuffer(
            create=False,
            name='ProfilesBuffer',
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
            with self._busy_count.get_lock():
                self._busy_count.value -= 1

    def process(self, kwargs):
        acquisition_dir: str = kwargs['acquisition_dir']
        acquisition_dir_on: bool = kwargs['acquisition_dir_on']
        acquisition_mode: AcquisitionMode = kwargs['acquisition_mode']
        bead_rois: dict[int, tuple[int, int, int, int]] = kwargs['bead_rois']
        save_profiles = kwargs['save_profiles']
        zlut = kwargs['zlut']
        nm_per_px: float = kwargs['nm_per_px']
        magnification: float = kwargs['magnification']

        bead_rois = bead_rois if len(bead_rois) > 0 else None

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
            bead_roi_values = np.array(list(bead_rois.values()))
            roi_width = bead_roi_values[0, 1] - bead_roi_values[0, 0]
            n_rois = len(bead_rois)
            stack_rois_reshaped = stack_rois.reshape(roi_width, roi_width, n_rois * n_images)

            # "zlut" can be None; magtrack returns NaN z values in that case.
            with self._gpu_lock:
                y, x, z, profiles = magtrack.stack_to_xyzp_advanced(
                    stack_rois_reshaped,
                    zlut
                )
                # TODO: There might be a faster more robust solution
                if is_cupy_available():
                    cp.get_default_memory_pool().free_all_blocks()

            # Calculate bead indexes (b)
            b = np.tile(np.array(list(bead_rois.keys())).astype(np.float64), n_images)

            # Tile the roi positions
            roi_x = np.tile(bead_roi_values[:, 0].astype(np.float64), n_images)
            roi_y = np.tile(bead_roi_values[:, 2].astype(np.float64), n_images)

            # Convert to the camera's top-left corner reference frame
            for bead_key, bead_value in bead_rois.items():
                sel = b == bead_key
                x[sel] = x[sel] + bead_value[0]
                y[sel] = y[sel] + bead_value[2]

            # Convert x & y to nanometers
            x *= nm_per_px / magnification
            y *= nm_per_px / magnification

            # Tile timestamps corresponding to each bead
            t = np.repeat(timestamps, n_rois)

            tracks = np.column_stack((t, x, y, z, b, roi_x, roi_y))

            # ------- Save Profiles (with padding) to RAM ------- #

            # The buffer has two extra columns for the timestamp and bead-ID
            expected_profiles_width = self._profiles_buffer.shape[1] - 2
            pad_profiles = profiles

            # If the profiles are shorter than the buffer, pad them with "nan"
            if pad_profiles.shape[0] < expected_profiles_width:
                pad_profiles = np.pad(
                    pad_profiles,
                    ((0, expected_profiles_width - pad_profiles.shape[0]), (0, 0)),
                    mode='constant',
                    constant_values=np.nan
                )
            # If the profiles are longer than the buffer, truncate them
            elif pad_profiles.shape[0] > expected_profiles_width:
                pad_profiles = pad_profiles[:expected_profiles_width, :]

            # Join the time and bead-id to the profiles
            pad_profiles = np.vstack((t, b, pad_profiles))

            # Write the profile data(transposed) to the buffer
            # If there are too many profiles, then truncate
            max_rows = self._profiles_buffer.shape[0]
            self._profiles_buffer.write(pad_profiles[:, :max_rows].T)

            return tracks, profiles

        def process_mode_tracks():
            if bead_rois:
                # Get stack and timestamps
                stack, timestamps = self._video_buffer.peak_stack()
                n_images = self._video_buffer.stack_shape[2]

                # Crop/copy stack to ROI
                stack_rois = crop_stack_to_rois(stack, list(bead_rois.values()))

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
            if bead_rois:  # Check if there are any ROIs
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
                stack_rois = crop_stack_to_rois(stack, list(bead_rois.values()))  # axes=(X,Y,T,ROI)

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

            if bead_rois:  # Check if there are any ROIs
                # Crop/copy stack to ROI
                stack_rois = crop_stack_to_rois(stack, list(bead_rois.values()))

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
            if bead_rois and acquisition_dir_on and acquisition_dir:
                # Get stack and timestamps
                stack, timestamps = self._video_buffer.peak_stack()

                # Format timestamp
                timestamps_str = list(map(
                    str, timestamps.tolist()))  # "tolist" creates a copy
                first_timestamp = date_timestamp_str(timestamps[0])

                # Crop/copy stack to ROI
                stack_rois = crop_stack_to_rois(stack, list(bead_rois.values()))  # axes=(X,Y,T,ROI)

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

    def _release_stack(self):
        self._video_buffer.read_stack_no_return()

        # Allow a new pool process to start
        self._video_flag.value = PoolVideoFlag.FINISHED
