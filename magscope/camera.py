from abc import ABCMeta, abstractmethod
import numpy as np
import queue
from time import time
from warnings import warn
from pathlib import Path
import os
from scipy.interpolate import interp1d
import math
import random

from magscope.datatypes import BufferUnderflow, VideoBuffer
from magscope.processes import ManagerProcessBase
from magscope.utils import Message, PoolVideoFlag

class CameraManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self.camera: CameraBase = DummyCamera()

    def setup(self):
        # Attempt to connect to the camera
        try:
            self.camera.connect(self.video_buffer)
        except Exception as e:
            warn(f"Could not connect to camera: {e}")

        # Send the current camera settings to the GUI
        if self.camera.is_connected:
            for setting in self.camera.settings:
                self.get_camera_setting(setting)

    def do_main_loop(self):
        # Check if images are done processing
        if self._acquisition_on:
            if self.shared_values.video_process_flag.value == PoolVideoFlag.FINISHED:
                self._release_pool_buffers()
                self.shared_values.video_process_flag.value = PoolVideoFlag.READY
        else:
            if self.shared_values.video_process_flag.value == PoolVideoFlag.READY:
                self._release_unattached_buffers()
            elif self.shared_values.video_process_flag.value == PoolVideoFlag.FINISHED:
                self._release_pool_buffers()
                self.shared_values.video_process_flag.value = PoolVideoFlag.READY

        # Check if the video buffer is about to overflow
        fraction_available = (1 - self.video_buffer.get_level())
        frames_available = fraction_available * self.video_buffer.n_total_images
        if frames_available <= 1:
            self._purge_buffers()
            # local import to avoid circular imports
            from magscope.gui import WindowManager
            message = Message(WindowManager, WindowManager.update_video_buffer_purge, time())
            self.send_ipc(message)

        # Check for new images from the camera
        if self.camera.is_connected:
            self.camera.fetch()

    def _release_unattached_buffers(self):
        if self.video_buffer is None:
            return

        try:
            self.video_buffer.read_stack_no_return()
            for _ in range(self.video_buffer.n_images):
                self.camera.release()
        except BufferUnderflow:
            pass

    def _purge_buffers(self):
        if self.video_buffer is None:
            return

        while True:
            try:
                self.video_buffer.read_stack_no_return()
                for _ in range(self.video_buffer.n_images):
                    self.camera.release()
            except BufferUnderflow:
                break
            if self.video_buffer.get_level() <= 0.3:
                break

    def _release_pool_buffers(self):
        if self.video_buffer is None:
            return

        for i in range(self.video_buffer.stack_shape[2]):
            self.camera.release()

    def get_camera_setting(self, name: str):
        value = self.camera[name]
        # local import to avoid circular imports
        from magscope.gui import WindowManager
        message = Message(to=WindowManager,
                          meth=WindowManager.update_camera_setting,
                          args=(name, value))
        self.send_ipc(message)

    def set_camera_setting(self, name: str, value: str):
        try:
            self.camera[name] = value
        except Exception as e:
            warn(f'Could not set camera setting {name} to {value}: {e}')
        for setting in self.camera.settings:
            self.get_camera_setting(setting)


class CameraBase(metaclass=ABCMeta):
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

    @abstractmethod
    def connect(self, video_buffer):
        """
        Attempts to connect to the camera.

        But does not start an acquisition. This method should set the value of self.is_connected to True if successful
        or False if not.
        """
        self.video_buffer = video_buffer

    @abstractmethod
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

    @abstractmethod
    def release(self):
        """
        Gives the buffer back to the camera.
        """
        pass

    def release_all(self):
        while self.camera_buffers is not None and self.camera_buffers.qsize(
        ) > 0:
            self.release()

    @abstractmethod
    def get_setting(self, name: str) -> str: # noqa
        """ Should return the current value of the setting from the camera """
        if name not in self.settings:
            raise KeyError(f"Unknown setting {name}")

    @abstractmethod
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


class DummyCamera(CameraBase):
    width = 1280
    height = 560
    bits = 8
    dtype = np.uint8
    nm_per_px = 5000.
    settings = ['framerate', 'exposure', 'gain']

    def __init__(self):
        super().__init__()
        self.fake_settings = {'framerate': 1000.0, 'exposure': 250.0, 'gain': 0.0}
        self.est_fps = self.fake_settings['framerate']
        self.est_fps_count = 0
        self.est_fps_time = time()
        self.last_time = 0

    def connect(self, video_buffer):
        super().connect(video_buffer)
        self.is_connected = True

    def fetch(self):
        if (timestamp := time()) - self.last_time < 1. / self.fake_settings['framerate']:
            return

        self.est_fps_count += 1
        if timestamp - self.est_fps_time > 1:
            self.est_fps = self.est_fps_count / (timestamp - self.est_fps_time)
            self.est_fps_count = 0
            self.est_fps_time = timestamp

        image = self._fake_image()

        self.last_time = timestamp

        self.video_buffer.write_image_and_timestamp(image, timestamp)

    def _fake_image(self):
        max_int = np.iinfo(self.dtype).max
        images = np.random.rand(self.height, self.width)
        images += self.fake_settings['gain']
        images *= self.fake_settings['exposure']
        images **= (1 + self.fake_settings['gain'])
        np.maximum(images, 0, out=images)
        np.minimum(images, max_int, out=images)
        return images.astype(self.dtype).tobytes()

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


class DummyCameraFast(CameraBase):
    width = 1280
    height = 560
    bits = 8
    dtype = np.uint8
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


class DummyBeadCamera(CameraBase):
    """
    Synthetic microscope camera using a half Z-LUT (radial profile vs z)
    with a contrast-preserving, edge-centered renderer and realistic noise.
    """

    # Output geometry
    width  = 1280
    height = 560
    bits   = 8
    dtype  = np.uint8

    # Imaging scale: 80 nm/px  (matches “yesterday” simulation)
    nm_per_px = 80.0

    # Settings (must include 'framerate')
    settings = [
        'framerate', 'exposure', 'gain',
        'n_fixed', 'n_tethered',
        'photon_count', 'read_noise_std'
    ]

    # Locate LUT beside this file in magscope/data/HalfZLUT_BeadModel.npy
    LUT_PATH = Path(__file__).parent / "data" / "HalfZLUT_BeadModel.npy"

    # LUT axes (match how the LUT was built/saved)
    _Z_MIN_UM, _Z_MAX_UM = -12.0, 12.0
    _R_MAX_UM = 4.0  # half-profile up to ~4 µm

    # Default tether/OU dynamics
    _DEFAULT_K = 2.5      # 1/s (mean reversion)
    _DEFAULT_D = 0.20     # µm^2/s (diffusion)

    def __init__(self):
        super().__init__()

        # Camera settings (all strings via get/set_setting but stored as numeric)
        self._settings = {
            'framerate'     : 60.0,   # Hz
            'exposure'      : 1.0,    # pre-Poisson scale
            'gain'          : 1.0,    # post-noise scale
            'n_fixed'       : 3,
            'n_tethered'    : 3,
            'photon_count'  : 1500,   # mean photons/pixel
            'read_noise_std': 0.01,   # in normalized 0..1 units
        }

        # Runtime
        self.last_time     = 0.0
        self.est_fps       = self._settings['framerate']
        self.est_fps_time  = time()
        self.est_fps_count = 0

        # Random
        self._rng = np.random.default_rng(12345)

        # LUT & grids
        self._lut = None
        self._z_axis = None
        self._r_axis = None
        self._B = None               # background from LUT edge

        # Coordinate grid (µm)
        self._um_per_px = self.nm_per_px / 1000.0
        self._X = None
        self._Y = None

        # Beads
        self._fixed = []  # dicts with x,y,z,I
        self._teth  = []  # dicts with x0,y0,z0, k, D, and current x,y,z

    # ------------------------------------------------------------------ CameraBase
    def connect(self, video_buffer):
        super().connect(video_buffer)

        # Load LUT
        if not self.LUT_PATH.exists():
            raise FileNotFoundError(f"LUT file not found: {self.LUT_PATH}")
        self._lut = np.load(self.LUT_PATH)  # shape (nz, nx)

        # Axes
        nz, nx = self._lut.shape
        self._z_axis = np.linspace(self._Z_MIN_UM, self._Z_MAX_UM, nz)  # µm
        self._r_axis = np.linspace(0.0, self._R_MAX_UM, nx)             # µm

        # Background from LUT edge (used by contrast-preserving normalization)
        self._B = float(np.mean(self._lut[:, -5:]))

        # Precompute camera grid in µm (centered at 0,0)
        xs = (np.arange(self.width)  - self.width  / 2.0) * self._um_per_px
        ys = (np.arange(self.height) - self.height / 2.0) * self._um_per_px
        self._X, self._Y = np.meshgrid(xs, ys)

        # Initialize beads
        self._init_beads()

        self.is_connected = True
        self.last_time    = 0.0
        self.est_fps_time = time()
        self.est_fps_count = 0
        self.est_fps = self._settings['framerate']

    def fetch(self):
        """Generate next frame at the requested framerate and write raw bytes into the video buffer."""
        now = time()
        fr = max(float(self._settings['framerate']), 1e-3)
        if (now - self.last_time) < (1.0 / fr):
            return

        # FPS estimator (like your DummyCamera)
        self.est_fps_count += 1
        if now - self.est_fps_time >= 1.0:
            self.est_fps = self.est_fps_count / (now - self.est_fps_time)
            self.est_fps_count = 0
            self.est_fps_time = now

        # Time step for OU motion
        dt = (now - self.last_time) if self.last_time > 0 else (1.0 / fr)
        self._advance_tethered(dt)

        # Render + write
        img_bytes = self._render_frame()
        self.video_buffer.write_image_and_timestamp(img_bytes, now)
        self.last_time = now

    def release(self):
        """No hardware buffers in synthetic camera."""
        pass

    def get_setting(self, name: str) -> str:
        super().get_setting(name)
        if name == 'framerate':
            return str(self.est_fps)  # report measured fps like DummyCamera
        return str(self._settings[name])

    def set_setting(self, name: str, value: str):
        super().set_setting(name, value)

        if name == 'framerate':
            v = float(value)
            if not (1 <= v <= 10000):
                raise ValueError("framerate out of range [1, 10000]")
            self._settings[name] = v
            return

        if name == 'exposure':
            v = float(value)
            if not (0.0 <= v <= 10.0):
                raise ValueError("exposure out of range [0, 10]")
            self._settings[name] = v
            return

        if name == 'gain':
            v = float(value)
            if not (0.1 <= v <= 10.0):
                raise ValueError("gain out of range [0.1, 10]")
            self._settings[name] = v
            return

        if name == 'n_fixed':
            v = int(value)
            if not (0 <= v <= 1000):
                raise ValueError("n_fixed out of range [0, 1000]")
            self._settings[name] = v
            self._init_fixed()
            return

        if name == 'n_tethered':
            v = int(value)
            if not (0 <= v <= 1000):
                raise ValueError("n_tethered out of range [0, 1000]")
            self._settings[name] = v
            self._init_tethered()
            return

        if name == 'photon_count':
            v = int(value)
            if not (10 <= v <= 2_000_000):
                raise ValueError("photon_count out of range [10, 2e6]")
            self._settings[name] = v
            return

        if name == 'read_noise_std':
            v = float(value)
            if not (0.0 <= v <= 0.2):
                raise ValueError("read_noise_std out of range [0, 0.2]")
            self._settings[name] = v
            return

        raise KeyError(f"Unknown setting {name}")

    # --------------------------------------------------------- internals
    def _init_beads(self):
        self._init_fixed()
        self._init_tethered()

    def _init_fixed(self):
        n = int(self._settings['n_fixed'])
        self._fixed = []
        half_w_um = (self.width  * self._um_per_px) * 0.45
        half_h_um = (self.height * self._um_per_px) * 0.45
        for _ in range(n):
            self._fixed.append({
                'x': self._rng.uniform(-half_w_um, half_w_um),
                'y': self._rng.uniform(-half_h_um, half_h_um),
                'z': self._rng.uniform(-3.0, 3.0),
                'I': 1.0
            })

    def _init_tethered(self):
        n = int(self._settings['n_tethered'])
        self._teth = []
        half_w_um = (self.width  * self._um_per_px) * 0.4
        half_h_um = (self.height * self._um_per_px) * 0.4
        for _ in range(n):
            x0 = self._rng.uniform(-half_w_um, half_w_um)
            y0 = self._rng.uniform(-half_h_um, half_h_um)
            z0 = self._rng.uniform(-2.0,  2.0)
            k  = self._DEFAULT_K + self._rng.uniform(-0.5, 0.5)
            D  = self._DEFAULT_D + self._rng.uniform(-0.05, 0.05)
            self._teth.append({
                'x0': x0, 'y0': y0, 'z0': z0,   # anchors
                'x' : x0, 'y' : y0, 'z' : z0,   # current positions
                'I' : 1.0, 'k': k, 'D': D
            })

    def _advance_tethered(self, dt):
        if dt <= 0:
            return
        for b in self._teth:
            sigma = np.sqrt(max(2.0 * b['D'] * dt, 1e-12))
            b['x'] += -b['k'] * (b['x'] - b['x0']) * dt + sigma * self._rng.standard_normal()
            b['y'] += -b['k'] * (b['y'] - b['y0']) * dt + sigma * self._rng.standard_normal()
            b['z'] += -b['k'] * (b['z'] - b['z0']) * dt + sigma * self._rng.standard_normal()

    # ----- LUT helpers (NumPy-only linear interpolation) -----
    def _profile_at_z_contrast_preserving(self, z_um):
        """Linear interp in z of LUT, then contrast-preserving scaling about LUT edge."""
        lut = self._lut
        z_axis = self._z_axis
        nx = lut.shape[1]

        # clamp and find segment
        if z_um <= z_axis[0]:
            prof_raw = lut[0]
        elif z_um >= z_axis[-1]:
            prof_raw = lut[-1]
        else:
            j = np.searchsorted(z_axis, z_um)
            j0, j1 = j-1, j
            t = (z_um - z_axis[j0]) / (z_axis[j1] - z_axis[j0] + 1e-12)
            prof_raw = (1.0 - t) * lut[j0] + t * lut[j1]  # shape (nx,)

        # contrast-preserving normalization about edge
        edge_val  = float(np.mean(prof_raw[-5:]))
        max_val   = float(np.max(prof_raw))
        min_val   = float(np.min(prof_raw))
        span_above = max_val - edge_val
        span_below = edge_val - min_val
        scale = (1.0 - self._B) / max(span_above, span_below, 1e-12)
        prof = self._B + scale * (prof_raw - edge_val)
        return prof  # shape (nx,)

    def _render_one_bead(self, x_um, y_um, z_um, intensity):
        """Add one bead to an image using physical radial mapping (µm)."""
        prof = self._profile_at_z_contrast_preserving(z_um)        # (nx,)
        R = np.sqrt((self._X - x_um)**2 + (self._Y - y_um)**2)     # µm
        rr = np.clip(R.ravel(), self._r_axis[0], self._r_axis[-1]) # 1D for np.interp
        vals = np.interp(rr, self._r_axis, prof, left=prof[0], right=prof[-1]).reshape(R.shape)
        return intensity * (vals - self._B)  # delta over background

    def _render_frame(self):
        """Compose beads, apply exposure, fast Poisson+read noise, gain, and quantize."""
        img = np.ones((self.height, self.width), dtype=np.float32) * self._B

        # --- Add all beads (fixed + tethered)
        for b in self._fixed:
            img += self._render_one_bead(b['x'], b['y'], b['z'], b['I'])
        for b in self._teth:
            img += self._render_one_bead(b['x'], b['y'], b['z'], b['I'])

        # --- Clip to valid range
        np.clip(img, 0.0, 1.0, out=img)

        # --- Apply exposure before noise
        exposure = float(self._settings['exposure'])
        if exposure != 1.0:
            img *= exposure
            np.clip(img, 0.0, 1.0, out=img)

        # --- Fast approximate Poisson + Gaussian noise
        photon_count = int(self._settings['photon_count'])
        read_noise_std = float(self._settings['read_noise_std'])

        if photon_count > 0:
            lam = img * photon_count
            # Normal approximation to Poisson
            img = lam + np.sqrt(lam, dtype=np.float32) * self._rng.standard_normal(img.shape, dtype=np.float32)
            img /= photon_count

        if read_noise_std > 0:
            img += self._rng.normal(0.0, read_noise_std, img.shape).astype(np.float32)

        np.clip(img, 0.0, 1.0, out=img)

        # --- Apply gain
        gain = float(self._settings['gain'])
        if gain != 1.0:
            img *= gain
            np.clip(img, 0.0, 1.0, out=img)

        # --- Quantize to dtype
        max_int = float(np.iinfo(self.dtype).max)
        img_q = (img * max_int + 0.5).astype(self.dtype)
        return img_q.tobytes()