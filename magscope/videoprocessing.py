from __future__ import annotations
import traceback
from multiprocessing import Process, Queue, Lock
import numpy as np
import os
import tifffile
from typing import TYPE_CHECKING

from magtrack import auto_conv_multiline_sub_pixel

from magscope.datatypes import VideoBuffer, MatrixBuffer
from magscope.processes import ManagerProcessBase
from magscope.scripting import ScriptManager
from magscope.utils import AcquisitionMode, crop_stack_to_rois, date_timestamp_str, Message, PoolVideoFlag
import magtrack
from magtrack._cupy import cp, is_cupy_available

if TYPE_CHECKING:
    from multiprocessing.synchronize import Lock as LockType
    from multiprocessing.sharedctypes import Synchronized
    from multiprocessing.queues import Queue as QueueType
    ValueTypeUI8 = Synchronized[int]

class VideoProcessorManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self._tasks: QueueType | None = None
        self._n_workers: int | None = None
        self._workers: list[VideoWorker] = []
        self._gpu_lock: LockType = Lock()
        self._loop: int = 0

        # TODO: Check implementation
        self._save_profiles = False
        self._zlut = None

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

    def do_main_loop(self):
        # Check if images are ready for image processing
        if self._acquisition_on:
            if self.shared_values.video_process_flag.value == PoolVideoFlag.READY:
                if self.video_buffer.check_read_stack():
                    self.shared_values.video_process_flag.value = PoolVideoFlag.RUNNING
                    self._add_task()

    def quit(self):
        super().quit()

        # Close
        if hasattr(self, '_workers'):
            for _ in self._workers:
                self._tasks.put(None)

        # Join
        if hasattr(self, '_workers'):
            for worker in self._workers:
                if worker and worker.is_alive():
                    worker.join()

        # Terminate
        if hasattr(self, '_workers'):
            for worker in self._workers:
                if worker and worker.is_alive():
                    worker.terminate()

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

        self._tasks.put(kwargs)

    def script_wait_unitl_acquisition_on(self, value: bool):
        while self._acquisition_on != value:
            self.do_main_loop()
        message = Message(
            to=ScriptManager,
            meth=ScriptManager.update_waiting
        )
        self.send_ipc(message)

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
                print(f'Error in video processing: {e}')
                print(traceback.format_exc())
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

            with self._gpu_lock:
                y, x, z, profiles = magtrack.stack_to_xyzp_advanced(
                    stack_rois_reshaped,
                    zlut,
                    auto_conv_multiline_sub_pixel={
                        'n_local': 7,
                        'line_ratio': 0.1
                    },
                    fft_profile={
                        'rmin': 0.09,
                        'rmax': 0.25,
                        'gaus_factor': 4.
                    },
                    lookup_z={
                        'n_local': 7
                    })
                # TODO: Might be too slow
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
            self._profiles_buffer.write(pad_profiles.T)

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