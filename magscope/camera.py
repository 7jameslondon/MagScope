import abc
from ctypes import c_uint8
from multiprocessing import Value
import numpy as np
import queue
from time import time
from typing import TYPE_CHECKING
from warnings import warn

from magscope import BufferUnderflow, ManagerProcess, Message, VideoBuffer
from magscope.gui import WindowManager
from magscope.videoprocessing import VideoWorkerPool
from magscope.utils import PoolVideoFlag

if TYPE_CHECKING:
    from multiprocessing.sharedctypes import Synchronized
    ValueTypeUI8 = Synchronized[int]

class CameraManager(ManagerProcess):
    def __init__(self):
        super().__init__()
        self.camera: CameraABC = DummyCamera()
        self._pool: VideoWorkerPool | None = None
        self._pool_video_flag: ValueTypeUI8 | None = None
        self._video_buffer: VideoBuffer | None = None

        # TODO: Check implementation
        self._save_profiles = False
        self._zlut = None

    def run(self):
        super().run()

        # Set up the parallel video processing pool
        self._pool_video_flag = Value(c_uint8, 0)
        self._pool = VideoWorkerPool(
            n_workers=self._settings['video processors n'],
            video_flag=self._pool_video_flag,
            locks=self._locks
        )

        # Attempt to connect to the camera
        try:
            self.camera.connect(self._video_buffer)
        except Exception as e:
            warn(f"Could not connect to camera: {e}")

        # Send the current camera settings to the GUI
        if self.camera.is_connected:
            for setting in self.camera.settings:
                self.get_camera_setting(setting)

        while self._running:
            self._do_main_loop()

        self._pool.close()
        self._pool.join()

    def __del__(self):
        self._running = False

        if hasattr(self, 'camera'):
            del self.camera

        if hasattr(self, '_pool') and self._pool:
            del self._pool

    def _do_main_loop(self):
        # Update the GUI's status info
        pool_text = f'{self._pool.busy_count}/{self._pool.n_workers} busy'
        message = Message(WindowManager, WindowManager.update_video_processors_status, pool_text)
        self._send(message)

        # Check if the video buffer is about to overflow
        fraction_available = (1 - self._video_buffer.get_level())
        frames_available = fraction_available * self._video_buffer.n_total_images
        if frames_available <= 1:
            warn(f'Video buffer overflowed. Purging.')
            self._purge_buffers()

        # Check for new images from the camera
        if self.camera.is_connected:
            self.camera.fetch()

        # Check if images are ready for image processing
        if self._acquisition_on:
            if self._pool_video_flag.value == PoolVideoFlag.READY:
                if self._video_buffer.check_read_stack():
                    self._pool_video_flag.value = PoolVideoFlag.RUNNING
                    self._start_batch_on_pool()
            elif self._pool_video_flag.value == PoolVideoFlag.FINISHED:
                self._release_pool_buffers()
                self._pool_video_flag.value = PoolVideoFlag.READY
        else:
            if self._pool_video_flag.value == PoolVideoFlag.READY:
                self._release_unattached_buffers()
            elif self._pool_video_flag.value == PoolVideoFlag.FINISHED:
                self._release_pool_buffers()
                self._pool_video_flag.value = PoolVideoFlag.READY

        # Check the pipe
        self._check_pipe()

    def _start_batch_on_pool(self):
        kwargs = {
            'acquisition_dir': self._acquisition_dir,
            'acquisition_dir_on': self._acquisition_dir_on,
            'acquisition_mode': self._acquisition_mode,
            'bead_rois': self._bead_rois,
            'magnification': self._settings['magnification'],
            'nm_per_px': self._camera_type.nm_per_px,
            'save_profiles': self._save_profiles,
            'zlut': self._zlut
        }

        self._pool.add_task(kwargs)

    def _release_unattached_buffers(self):
        if self._video_buffer is None:
            return

        try:
            self._video_buffer.read_stack_no_return()
            for _ in range(self._video_buffer.n_images):
                self.camera.release()
        except BufferUnderflow:
            pass

    def _purge_buffers(self):
        if self._video_buffer is None:
            return

        while True:
            try:
                self._video_buffer.read_stack_no_return()
                for _ in range(self._video_buffer.n_images):
                    self.camera.release()
            except BufferUnderflow:
                break
            if self._video_buffer.get_level() <= 0.3:
                break

    def _release_pool_buffers(self):
        if self._video_buffer is None:
            return

        for i in range(self._video_buffer.stack_shape[2]):
            self.camera.release()

    def get_camera_setting(self, name: str):
        value = self.camera[name]
        message = Message(to=WindowManager,
                          func=WindowManager.update_camera_setting,
                          args=(name, value))
        self._send(message)

    def set_camera_setting(self, name: str, value: str):
        try:
            self.camera[name] = value
        except Exception as e:
            warn(f'Could not set camera setting {name} to {value}: {e}')
        self.get_camera_setting(name)


class CameraABC(metaclass=abc.ABCMeta):
    """ Abstract base class for camera implementation """
    bits: int
    dtype: np.dtypes
    height: int
    nm_per_px: float
    width: int
    settings: list[str] = ['framerate']

    def __init__(self):
        self.is_connected = False
        self.video_buffer: VideoBuffer | None = None
        self.camera_buffers: queue.Queue = None  # type: ignore
        if None in (self.width, self.height, self.dtype, self.nm_per_px):
            raise NotImplementedError

        # Check dtype is valid
        if self.dtype not in (np.uint8, np.uint16, np.uint32, np.uint64):
            raise ValueError(f"Invalid dtype {self.dtype}")

        # Check bits is valid
        if not isinstance(self.bits, int):
            raise ValueError(f"Invalid bits {self.bits}")
        if self.bits > np.iinfo(self.dtype).bits:
            raise ValueError(f"Invalid bits {self.bits} for dtype {self.dtype}")

        # Check settings
        if 'framerate' not in self.settings:
            raise ValueError("All cameras must declare a 'framerate' setting")

    def __del__(self):
        if self.is_connected:
            self.release_all()
        del self.video_buffer

    @abc.abstractmethod
    def connect(self, video_buffer):
        """
        Attempts to connect to the camera.

        But does not start an acquisition. This method should set the value of self.is_connected to True if successful
        or False if not.
        """
        self.video_buffer = video_buffer

    @abc.abstractmethod
    def fetch(self):
        """
        Checks if the camera has new images.

        If the camera has a new image, then it holds the camera's
        buffered image in a queue (self.camera_buffers). And stores the
        image and timestamp in the video buffer (self._video_buffer).

        The timestamp should be the seconds since the unix epoch:
        (January 1, 1970, 00:00:00 UTC)
        """
        pass

    @abc.abstractmethod
    def release(self):
        """
        Gives the buffer back to the camera.
        """
        pass

    def release_all(self):
        while self.camera_buffers is not None and self.camera_buffers.qsize(
        ) > 0:
            self.release()

    @abc.abstractmethod
    def get_setting(self, name: str) -> str: # noqa
        """ Should return the current value of the setting from the camera """
        if name not in self.settings:
            raise KeyError(f"Unknown setting {name}")

    @abc.abstractmethod
    def set_setting(self, name: str, value: str):
        """ Should set the value of the setting on the camera """
        if name not in self.settings:
            raise KeyError(f"Unknown setting {name}")

    def __getitem__(self, name: str) -> str:
        """ Used to get settings. Example: my_cam['framerate'] """
        return self.get_setting(name)

    def __setitem__(self, name: str, value: str) -> None:
        """ Used to set settings. Example: my_cam['framerate'] = 100.0 """
        self.set_setting(name, value)


class DummyCamera(CameraABC):
    width = 2560
    height = 2560
    bits = 12
    dtype = np.uint16
    nm_per_px = 5000.
    settings = ['framerate', 'exposure', 'gain']

    def __init__(self):
        super().__init__()
        self.fake_settings = {'framerate': 1000.0, 'exposure': 25000.0, 'gain': 0.0}
        self.est_fps = self.fake_settings['framerate']
        self.est_fps_count = 0
        self.est_fps_time = time()
        self.last_time = 0

        self.fake_images = None
        self.fake_images_n = 10
        self.fake_image_index = 0

    def connect(self, video_buffer):
        super().connect(video_buffer)
        self.get_fake_image()
        self.is_connected = True

    def fetch(self):
        if (timestamp := time()) - self.last_time < 1. / self.fake_settings['framerate']:
            return

        self.est_fps_count += 1
        if timestamp - self.est_fps_time > 1:
            self.est_fps = self.est_fps_count / (timestamp - self.est_fps_time)
            self.est_fps_count = 0
            self.est_fps_time = timestamp

        image = self.get_fake_image()

        self.last_time = timestamp

        self.video_buffer.write_image_and_timestamp(image, timestamp)

    def get_fake_image(self):
        if self.fake_images is None:
            max_int = np.iinfo(self.dtype).max
            images = np.random.rand(self.height, self.width, self.fake_images_n)
            images += self.fake_settings['gain']
            images *= self.fake_settings['exposure']
            images **= (1 + self.fake_settings['gain'])
            np.maximum(images, 0, out=images)
            np.minimum(images, max_int, out=images)
            self.fake_images = images.astype(self.dtype).tobytes()
        self.fake_image_index += 1
        if self.fake_image_index >= self.fake_images_n:
            self.fake_image_index = 0

        stride = self.height * self.width * np.dtype(self.dtype).itemsize
        return self.fake_images[self.fake_image_index * stride:
                                (self.fake_image_index + 1) * stride]

    def release(self):
        pass

    def get_setting(self, name: str) -> str:
        super().get_setting(name)
        if name != 'framerate':
            value = self.fake_settings[name]
        else:
            value = self.est_fps
        value = str(round(value))
        return value

    def set_setting(self, name: str, value: str):
        super().set_setting(name, value)
        match name:
            case 'framerate':
                value = float(value)
                if value < 1 or value > 10000:
                    raise ValueError
            case 'exposure':
                value = float(value)
                if value < 0 or value > 10000000:
                    raise ValueError
            case 'gain':
                value = int(value)
                if value < 0 or value > 10:
                    raise ValueError

        self.fake_settings[name] = value
