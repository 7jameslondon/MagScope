"""CameraBase implementation backed by the `harvesters` library."""

from __future__ import annotations

import queue
from time import time
from typing import TYPE_CHECKING, Any

import numpy as np

from magscope.camera import CameraBase

if TYPE_CHECKING:
    from harvesters.core import Harvester, ImageAcquirer  # pragma: no cover
    from harvesters.core.buffer import Buffer  # pragma: no cover


class HarvestersCamera(CameraBase):
    """Camera interface using the `harvesters` GenICam transport layer."""

    width = 640
    height = 480
    bits = 8
    dtype = np.uint8
    nm_per_px = 5000.0
    settings = ["framerate", "exposure", "gain"]

    def __init__(
        self,
        *,
        cti_paths: list[str] | None = None,
        device_index: int = 0,
        pixel_format: str = "Mono8",
        nm_per_px: float | None = None,
        width: int | None = None,
        height: int | None = None,
        bits: int | None = None,
        dtype: np.dtype | None = None,
        fetch_timeout: float = 0.01,
    ) -> None:
        if nm_per_px is not None:
            self.nm_per_px = nm_per_px
        if width is not None:
            self.width = width
        if height is not None:
            self.height = height
        if bits is not None:
            self.bits = bits
        if dtype is not None:
            self.dtype = dtype

        super().__init__()
        self._cti_paths = cti_paths or []
        self._device_index = device_index
        self._pixel_format = pixel_format
        self._fetch_timeout = fetch_timeout

        self._harvester: Harvester | None = None
        self._image_acquirer: ImageAcquirer | None = None
        self._remote_node_map: Any | None = None
        self._cached_settings: dict[str, float] = {
            "framerate": 30.0,
            "exposure": 1000.0,
            "gain": 0.0,
        }

    def __del__(self) -> None:
        try:
            if self._image_acquirer is not None:
                self._image_acquirer.stop_acquisition()
                self._image_acquirer.destroy()
            if self._harvester is not None:
                self._harvester.reset()
        except Exception:
            pass
        super().__del__()

    def connect(self, video_buffer) -> None:  # noqa: D401
        """Connect to the first available GenTL device and start acquisition."""

        super().connect(video_buffer)
        try:
            from harvesters.core import Harvester
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError(
                "The 'harvesters' extra is required for HarvestersCamera"
            ) from exc

        self._harvester = Harvester()
        if self._cti_paths:
            for path in self._cti_paths:
                self._harvester.add_file(path)
        else:
            self._harvester.add_cti_files()
        self._harvester.update()

        if not self._harvester.device_info_list:
            self.is_connected = False
            raise RuntimeError("No GenTL devices found for HarvestersCamera")

        self._image_acquirer = self._harvester.create_image_acquirer(
            list_index=self._device_index
        )
        self._remote_node_map = self._image_acquirer.remote_device.node_map

        self._configure_nodes()
        self._sync_dimensions_from_device()

        n_queue = self.video_buffer.n_stacks * self.video_buffer.stack_shape[2]
        self.camera_buffers = queue.Queue(n_queue)

        self._image_acquirer.start_acquisition()
        self.is_connected = True

    def fetch(self) -> None:
        if not self.is_connected or self._image_acquirer is None:
            return
        try:
            buffer = self._image_acquirer.fetch_buffer(timeout=self._fetch_timeout)
        except Exception:
            return

        self.camera_buffers.put(buffer)
        payload = buffer.payload
        component = payload.components[0]
        image = component.data.reshape(component.height, component.width)
        timestamp = self._timestamp_from_buffer(buffer)
        self.video_buffer.write_image_and_timestamp(image.tobytes(), timestamp)

    def release(self) -> None:
        if self.camera_buffers is None or self._image_acquirer is None:
            return
        buffer = self.camera_buffers.get()
        self._image_acquirer.queue_buffer(buffer)

    def get_setting(self, name: str) -> str:  # noqa: D401
        """Return the current value of a GenICam node when available."""

        super().get_setting(name)
        getter = {
            "framerate": lambda: self._get_node_value("AcquisitionFrameRate"),
            "exposure": lambda: self._get_node_value("ExposureTime"),
            "gain": lambda: self._get_node_value("Gain"),
        }.get(name)
        if getter is None:
            raise KeyError(f"Unknown setting {name}")

        value = getter()
        if value is None:
            value = self._cached_settings[name]
        else:
            self._cached_settings[name] = float(value)
        return str(value)

    def set_setting(self, name: str, value: str) -> None:
        super().set_setting(name, value)
        numeric_value: float = float(value)
        setter = {
            "framerate": lambda v: self._set_node_value("AcquisitionFrameRate", v),
            "exposure": lambda v: self._set_node_value("ExposureTime", v),
            "gain": lambda v: self._set_node_value("Gain", v),
        }.get(name)
        if setter is None:
            raise KeyError(f"Unknown setting {name}")
        setter(numeric_value)
        self._cached_settings[name] = numeric_value

    def _configure_nodes(self) -> None:
        if self._remote_node_map is None:
            return
        self._set_node_value("PixelFormat", self._pixel_format)
        if "AcquisitionMode" in self._remote_node_map:
            try:
                self._remote_node_map.AcquisitionMode.value = "Continuous"
            except Exception:
                pass

    def _sync_dimensions_from_device(self) -> None:
        if self._remote_node_map is None:
            return
        width = getattr(self._remote_node_map, "Width", None)
        height = getattr(self._remote_node_map, "Height", None)
        pixel_format = getattr(self._remote_node_map, "PixelFormat", None)

        if width is not None:
            self.width = int(width.value)
        if height is not None:
            self.height = int(height.value)
        if pixel_format is not None:
            dtype, bits = self._dtype_from_pixel_format(pixel_format.value)
            self.dtype = dtype
            self.bits = bits

    def _dtype_from_pixel_format(self, pixel_format: str) -> tuple[np.dtype, int]:
        mapping = {
            "Mono8": (np.uint8, 8),
            "Mono10": (np.uint16, 10),
            "Mono12": (np.uint16, 12),
            "Mono14": (np.uint16, 14),
            "Mono16": (np.uint16, 16),
        }
        try:
            return mapping[pixel_format]
        except KeyError:
            raise ValueError(f"Unsupported pixel format: {pixel_format}") from None

    def _get_node_value(self, name: str) -> float | None:
        if self._remote_node_map is None or name not in self._remote_node_map:
            return None
        try:
            return float(getattr(self._remote_node_map, name).value)
        except Exception:
            return None

    def _set_node_value(self, name: str, value: float) -> None:
        if self._remote_node_map is None or name not in self._remote_node_map:
            return
        try:
            getattr(self._remote_node_map, name).value = value
        except Exception:
            pass

    @staticmethod
    def _timestamp_from_buffer(buffer: "Buffer") -> float:
        timestamp = getattr(buffer, "timestamp", None)
        if timestamp is None:
            return time()
        # Harvesters timestamps are in nanoseconds
        return float(timestamp) / 1e9
