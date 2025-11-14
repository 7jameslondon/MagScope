import queue
from abc import ABCMeta, abstractmethod
from functools import lru_cache
from time import time
from warnings import warn

import numpy as np
from magtrack.simulation import simulate_beads

from magscope.datatypes import BufferUnderflow, VideoBuffer
from magscope.processes import ManagerProcessBase
from magscope.utils import Message, PoolVideoFlag


class CameraManager(ManagerProcessBase):
    def __init__(self):
        super().__init__()
        self.camera: CameraBase = DummyCameraBeads()

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

        for _ in range(self.video_buffer.stack_shape[2]):
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
            reason = str(e).strip()
            if not reason:
                reason = repr(e)
            warn(f'Could not set camera setting {name} to {value}: {reason}')
        for setting in self.camera.settings:
            self.get_camera_setting(setting)


class CameraBase(metaclass=ABCMeta):
    """ Abstract base class for camera implementation """
    bits: int
    dtype: np.dtype
    height: int
    nm_per_px: float
    width: int
    settings: list[str] = ['framerate']

    def __init__(self):
        self.is_connected = False
        self.video_buffer: VideoBuffer | None = None
        self.camera_buffers: queue.Queue | None = None
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
        try:
            if self.is_connected:
                self.release_all()
        except Exception:
            pass
        self.video_buffer = None

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
        while self.camera_buffers is not None and self.camera_buffers.qsize() > 0:
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


class DummyCameraNoise(CameraBase):
    width = 512
    height = 256
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


class DummyCameraFastNoise(CameraBase):
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

        stride = self.height * self.width * np.dtype(self.dtype).itemsize
        start = self.fake_image_index * stride
        end = start + stride
        image = self.fake_images[start:end]

        self.fake_image_index = (self.fake_image_index + 1) % self.fake_images_n

        return image

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


class DummyCameraBeads(CameraBase):
    width  = 512
    height = 256
    bits   = 8
    dtype  = np.uint8
    nm_per_px = 200.

    # Exposed settings
    settings = [
        'framerate',
        'n_static', 'n_tethered',
        'sigma_xy_px', 'sigma_z_um',
        'z_static_um', 'z_anchor_um',
        'electron_gain',
        'seed'
    ]

    def __init__(self):
        super().__init__()
        self._settings = {
            'framerate'     : 30.0,    # Hz
            'n_static'      : 2,
            'n_tethered'    : 4,
            'sigma_xy_px'   : 3.0,
            'sigma_z_um'    : 0.3,
            'z_static_um'   : 0.0,
            'z_anchor_um'   : 0.0,
            'electron_gain' : 25000.0,
            'seed'          : 12345,
        }
        self._bead_size_px = 32
        self._min_sep_px = 32.0
        self._edge_margin_px = 10.0
        self._background = 0.4
        self._radius_nm = 1500.0
        self._theta_xy = 1.5
        self._theta_z = 2.0
        self._rng = np.random.default_rng(self._settings['seed'])

        # placement and bead state
        self._centers_static = np.empty((0,2), np.float32)
        self._centers_teth   = np.empty((0,2), np.float32)
        self._delta_static   = None  # tapered crop for static beads
        self._xy = np.empty((0,2), np.float32)
        self._z  = np.empty((0,),  np.float32)

        # time keeping
        self.last_time = 0.0
        self.est_fps = self._settings['framerate']
        self.est_fps_count = 0
        self.est_fps_time = time()

    def connect(self, video_buffer):
        super().connect(video_buffer)
        self._rng = np.random.default_rng(int(self._settings['seed']))
        self._reinit_centers_and_static()
        self._init_tether_state()
        self.is_connected = True
        self.last_time = 0.0
        self.est_fps = float(self._settings['framerate'])
        self.est_fps_count = 0
        self.est_fps_time = time()

    def fetch(self):
        now = time()
        fr = max(float(self._settings['framerate']), 1e-6)
        if (now - self.last_time) < (1.0 / fr):
            return

        # fps estimator
        self.est_fps_count += 1
        if now - self.est_fps_time >= 1.0:
            self.est_fps = self.est_fps_count / (now - self.est_fps_time)
            self.est_fps_count = 0
            self.est_fps_time = now

        # dt for OU
        dt = (now - self.last_time) if self.last_time > 0 else (1.0 / fr)

        # compose frame
        frame = np.full((self.height, self.width), float(self._background), np.float32)

        # static beads
        if self._delta_static is not None and self._centers_static.size:
            for cx, cy in self._centers_static:
                self._accumulate_bilinear(frame, self._delta_static, cx, cy)

        # tethered: update OU and render per bead
        n_t = self._centers_teth.shape[0]
        if n_t:
            th_xy   = float(self._theta_xy)
            sig_xy  = float(self._settings['sigma_xy_px'])
            th_z    = float(self._theta_z)
            sig_z   = float(self._settings['sigma_z_um'])
            z_anchor = float(self._settings['z_anchor_um'])
            size_px = int(self._bead_size_px)
            nmpp    = float(self.nm_per_px)
            radius  = float(self._radius_nm)
            xyz     = np.zeros((1, 3), dtype=np.float32)

            for j in range(n_t):
                # OU updates
                self._xy[j,0] = self._ou_step(self._xy[j,0], dt, th_xy, sig_xy, 0.0, self._rng)
                self._xy[j,1] = self._ou_step(self._xy[j,1], dt, th_xy, sig_xy, 0.0, self._rng)
                self._z[j]    = self._ou_step(self._z[j],    dt, th_z,  sig_z,  z_anchor, self._rng)

                # render crop at current z (T=1)
                xyz[0, 2] = float(self._z[j])
                crop_WHT = simulate_beads(xyz, nm_per_px=nmpp, size_px=size_px, radius_nm=radius)  # (w,h,1)
                crop_HW  = crop_WHT[:, :, 0].T
                delta    = self._delta_for_crop(crop_HW, pad=4)

                cx, cy = self._centers_teth[j]
                self._accumulate_bilinear(frame, delta, cx + self._xy[j,0], cy + self._xy[j,1])

        # noise and scaling
        np.clip(frame, 0.0, 1.0, out=frame)

        # Poisson noise always enabled
        egain = float(self._settings['electron_gain'])
        lam = frame * egain
        frame = self._rng.poisson(lam).astype(np.float32) / egain

        # quantize
        np.clip(frame, 0.0, 1.0, out=frame)

        max_int = float(np.iinfo(self.dtype).max)
        img_q = (frame * max_int + 0.5).astype(self.dtype)
        self.video_buffer.write_image_and_timestamp(img_q.tobytes(), now)
        self.last_time = now

    def release(self):
        # no real hardware buffers to free
        pass

    def get_setting(self, name: str) -> str:
        super().get_setting(name)
        if name == 'framerate':
            return str(round(self.est_fps))
        return str(self._settings[name])

    def set_setting(self, name: str, value: str):
        super().set_setting(name, value)

        def f(v): return float(v)
        def i(v): return int(float(v))

        if name == 'framerate':
            v = f(value)
            if not (1 <= v <= 10000):
                raise ValueError("framerate must be between 1 and 10000 Hz")
            self._settings[name] = v
            return

        if name in ('n_static', 'n_tethered'):
            v = i(value)
            if not (0 <= v <= 5000):
                raise ValueError("n_static and n_tethered must be between 0 and 5000")
            self._settings[name] = v
            self._reinit_centers_and_static()
            self._init_tether_state()
            return

        if name in ('z_static_um', 'z_anchor_um',
                    'sigma_xy_px', 'sigma_z_um',
                    'electron_gain'):
            v = f(value)
            self._settings[name] = v
            if name in ('z_static_um',):
                # refresh static crop
                self._recompute_static_delta()
            return

        if name == 'seed':
            v = i(value)
            self._settings[name] = v
            self._rng = np.random.default_rng(v)
            # reinit states deterministically
            self._reinit_centers_and_static()
            self._init_tether_state()
            return

        raise KeyError(f"Unknown setting {name}")

    # ------------------------- internals ----------------------------
    def _reinit_centers_and_static(self):
        w = self.width; h = self.height
        size_px = int(self._bead_size_px)
        base_margin = size_px // 2 + 2
        margin = int(max(base_margin, int(self._edge_margin_px)))
        min_sep = float(self._min_sep_px) if self._min_sep_px else float(size_px)

        n_static   = int(self._settings['n_static'])
        n_tethered = int(self._settings['n_tethered'])
        n_total = n_static + n_tethered

        pts = self._sample_points_uniform_minsep(w, h, n_total, margin, min_sep, self._rng).astype(np.float32)
        self._centers_static = pts[:n_static]   if n_static   else np.empty((0,2), np.float32)
        self._centers_teth   = pts[n_static:]   if n_tethered else np.empty((0,2), np.float32)
        self._recompute_static_delta()

    def _recompute_static_delta(self):
        n_static = int(self._settings['n_static'])
        if n_static <= 0:
            self._delta_static = None
            return
        size_px = int(self._bead_size_px)
        nmpp    = float(self.nm_per_px)
        radius  = float(self._radius_nm)
        z_s     = float(self._settings['z_static_um'])
        xyz = np.array([[0.0, 0.0, z_s]], np.float32)
        crop_WHT = simulate_beads(xyz, nm_per_px=nmpp, size_px=size_px, radius_nm=radius)  # (w,h,1)
        crop_HW  = crop_WHT[:, :, 0].T
        self._delta_static = self._delta_for_crop(crop_HW, pad=4)

    def _init_tether_state(self):
        n_t = int(self._settings['n_tethered'])
        self._xy = np.zeros((n_t, 2), np.float32)
        self._z  = np.full((n_t,), float(self._settings['z_anchor_um']), np.float32)

    @staticmethod
    def _blit_add(dst, src, x, y, w=1.0):
        Hs, Ws = src.shape
        Hd, Wd = dst.shape
        x0 = max(int(x), 0); y0 = max(int(y), 0)
        x1 = min(int(x) + Ws, Wd); y1 = min(int(y) + Hs, Hd)
        if x0 >= x1 or y0 >= y1:
            return
        sx0 = x0 - int(x); sy0 = y0 - int(y)
        sx1 = sx0 + (x1 - x0); sy1 = sy0 + (y1 - y0)
        dst[y0:y1, x0:x1] += w * src[sy0:sy1, sx0:sx1]

    @classmethod
    def _accumulate_bilinear(cls, dst, srcHW, cx, cy):
        H, W = srcHW.shape
        x_int = int(np.floor(cx - W / 2.0))
        y_int = int(np.floor(cy - H / 2.0))
        fx = (cx - W / 2.0) - x_int
        fy = (cy - H / 2.0) - y_int
        cls._blit_add(dst, srcHW, x_int,     y_int,     (1.0 - fx) * (1.0 - fy))
        cls._blit_add(dst, srcHW, x_int + 1, y_int,     fx * (1.0 - fy))
        cls._blit_add(dst, srcHW, x_int,     y_int + 1, (1.0 - fx) * fy)
        cls._blit_add(dst, srcHW, x_int + 1, y_int + 1, fx * fy)

    @staticmethod
    def _border_median(imgHW):
        return np.median(np.r_[imgHW[0,:], imgHW[-1,:], imgHW[:,0], imgHW[:,-1]])

    @staticmethod
    @lru_cache(maxsize=8)
    def _tukey_taper(H, W, pad=4):
        y = np.minimum(np.arange(H), np.arange(H)[::-1])
        x = np.minimum(np.arange(W), np.arange(W)[::-1])
        d = np.minimum.outer(y, x).astype(np.float32) / max(1, pad)
        u = np.clip(d, 0.0, 1.0)
        win = 0.5 - 0.5*np.cos(np.pi*u)  # 0 at edge → 1 inside
        win.setflags(write=False)
        return win

    @classmethod
    def _delta_for_crop(cls, cropHW, pad=4):
        base = cls._border_median(cropHW)
        win = cls._tukey_taper(*cropHW.shape, pad=pad)
        return (cropHW - base) * win

    @staticmethod
    def _ou_step(x, dt, theta, sigma, mu, rng):
        # x_{t+1} = x + theta*(mu - x)*dt + sigma*sqrt(dt)*N(0,1)
        return x + theta*(mu - x)*dt + sigma*np.sqrt(dt)*rng.normal()

    @staticmethod
    def _sample_points_uniform_minsep(W, H, n, margin_px, min_sep_px, rng,
                                      max_tries=100000, relax=0.95):
        """Dart throwing with optional relaxation. Returns (n,2) float32."""
        if n <= 0:
            return np.empty((0, 2), np.float32)
        x_lo, x_hi = margin_px, W - margin_px
        y_lo, y_hi = margin_px, H - margin_px
        if x_hi <= x_lo or y_hi <= y_lo:
            raise ValueError("Margin too large for frame size.")
        pts_list: list[tuple[float, float]] = []
        r2 = float(min_sep_px) * float(min_sep_px)
        tries = 0
        cur_min_sep = float(min_sep_px)
        while len(pts_list) < n:
            if tries >= max_tries:
                cur_min_sep *= relax
                r2 = cur_min_sep * cur_min_sep
                tries = 0
            tries += 1
            x = rng.uniform(x_lo, x_hi); y = rng.uniform(y_lo, y_hi)
            if not pts_list:
                pts_list.append((x, y))
                continue
            pts = np.asarray(pts_list, dtype=np.float32)
            d2 = (pts[:,0] - x)**2 + (pts[:,1] - y)**2
            if np.all(d2 >= r2):
                pts_list.append((x, y))
        return np.asarray(pts_list, dtype=np.float32)
