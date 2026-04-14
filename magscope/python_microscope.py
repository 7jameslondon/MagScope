from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from importlib import import_module
from time import time
from typing import Any

import numpy as np

from magscope.camera import CameraBase
from magscope.hardware import FocusMotorBase, HardwareManagerBase


def _require_dependency(module_name: str, package_name: str) -> Any:
    try:
        return import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f'{package_name} support requires installing the optional dependency: '
            f'pip install magscope[{package_name}]'
        ) from exc


class _PythonMicroscopeDeviceMixin:
    def __init__(
        self,
        *,
        device: Any | None = None,
        device_factory: Callable[[], Any] | None = None,
        device_uri: str | None = None,
        device_getter: Callable[[Any], Any] | None = None,
    ) -> None:
        source_count = sum(value is not None for value in (device, device_factory, device_uri))
        if source_count != 1:
            raise ValueError('Provide exactly one of device, device_factory, or device_uri')

        self._device_source = device
        self._device_factory = device_factory
        self._device_uri = device_uri
        self._device_getter = device_getter
        self._microscope_root_device: Any | None = None
        self._microscope_device: Any | None = None

    @property
    def microscope_device(self) -> Any:
        if self._microscope_device is None:
            raise RuntimeError('python-microscope device is not connected')
        return self._microscope_device

    @property
    def microscope_root_device(self) -> Any:
        if self._microscope_root_device is None:
            raise RuntimeError('python-microscope device is not connected')
        return self._microscope_root_device

    def _connect_microscope_device(self, *, use_data_client: bool) -> Any:
        if self._device_factory is not None:
            root_device = self._device_factory()
        elif self._device_uri is not None:
            if use_data_client:
                clients_module = _require_dependency(
                    'microscope.clients',
                    'python-microscope',
                )
                root_device = clients_module.DataClient(self._device_uri)
            else:
                pyro4 = _require_dependency('Pyro4', 'python-microscope')
                root_device = pyro4.Proxy(self._device_uri)
        else:
            root_device = self._device_source

        device = self._device_getter(root_device) if self._device_getter is not None else root_device
        self._microscope_root_device = root_device
        self._microscope_device = device
        return device

    def _disconnect_microscope_device(self) -> None:
        root_device = self._microscope_root_device
        self._microscope_device = None
        self._microscope_root_device = None
        if root_device is None:
            return

        try:
            shutdown = getattr(root_device, 'shutdown', None)
        except Exception:
            shutdown = None
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass

        try:
            pyro_release = getattr(root_device, '_pyroRelease', None)
        except Exception:
            pyro_release = None
        if callable(pyro_release):
            try:
                pyro_release()
            except Exception:
                pass


class PythonMicroscopeHardwareManagerBase(
    _PythonMicroscopeDeviceMixin,
    HardwareManagerBase,
    ABC,
):
    """Base class for hardware managers backed by python-microscope devices."""

    def __init__(
        self,
        *,
        device: Any | None = None,
        device_factory: Callable[[], Any] | None = None,
        device_uri: str | None = None,
        device_getter: Callable[[Any], Any] | None = None,
    ) -> None:
        HardwareManagerBase.__init__(self)
        _PythonMicroscopeDeviceMixin.__init__(
            self,
            device=device,
            device_factory=device_factory,
            device_uri=device_uri,
            device_getter=device_getter,
        )

    def connect(self) -> None:
        device = self._connect_microscope_device(use_data_client=False)
        enable = getattr(device, 'enable', None)
        if callable(enable):
            enable()
        self._is_connected = True

    def disconnect(self) -> None:
        self._is_connected = False
        self._disconnect_microscope_device()


class PythonMicroscopeFocusMotor(_PythonMicroscopeDeviceMixin, FocusMotorBase):
    """Focus motor adapter for a python-microscope stage or stage axis."""

    def __init__(
        self,
        *,
        axis_name: str = 'z',
        device: Any | None = None,
        device_factory: Callable[[], Any] | None = None,
        device_uri: str | None = None,
        device_getter: Callable[[Any], Any] | None = None,
        position_scale: float = 1.0,
    ) -> None:
        FocusMotorBase.__init__(self)
        _PythonMicroscopeDeviceMixin.__init__(
            self,
            device=device,
            device_factory=device_factory,
            device_uri=device_uri,
            device_getter=device_getter,
        )
        self.axis_name = axis_name
        self.position_scale = float(position_scale)
        if np.isclose(self.position_scale, 0.0):
            raise ValueError('position_scale must be non-zero')
        self._axis: Any | None = None
        self._moving_target: float | None = None

    def connect(self) -> None:
        device = self._connect_microscope_device(use_data_client=False)
        enable = getattr(device, 'enable', None)
        if callable(enable):
            enable()
        self._axis = self._resolve_axis(device)
        self._is_connected = True

    def disconnect(self) -> None:
        self._axis = None
        self._moving_target = None
        self._is_connected = False
        self._disconnect_microscope_device()

    def move_absolute(self, z: float) -> None:
        axis = self._require_axis()
        device_units = self._to_device_units(z)
        axis.move_to(device_units)
        self._moving_target = float(z)

    def get_current_z(self) -> float:
        axis = self._require_axis()
        return self._from_device_units(float(axis.position))

    def get_is_moving(self) -> bool:
        axis = self._require_axis()

        for attr_name in ('moving', 'is_moving'):
            attr = getattr(axis, attr_name, None)
            if isinstance(attr, bool):
                return attr
            if callable(attr):
                return bool(attr())

        get_is_moving = getattr(axis, 'get_is_moving', None)
        if callable(get_is_moving):
            return bool(get_is_moving())

        if self._moving_target is None:
            return False

        current_z = self.get_current_z()
        return not np.isclose(current_z, self._moving_target, atol=self.at_target_tolerance)

    def get_position_limits(self) -> tuple[float, float]:
        axis = self._require_axis()
        limits = axis.limits
        return (
            self._from_device_units(float(limits.lower)),
            self._from_device_units(float(limits.upper)),
        )

    def _resolve_axis(self, device: Any) -> Any:
        axes = getattr(device, 'axes', None)
        if isinstance(axes, Mapping):
            try:
                return axes[self.axis_name]
            except KeyError as exc:
                available = ', '.join(sorted(str(name) for name in axes))
                raise KeyError(
                    f'python-microscope stage has no axis {self.axis_name!r}; '
                    f'available axes: {available}'
                ) from exc

        required_attrs = ('move_to', 'position', 'limits')
        if all(hasattr(device, attr_name) for attr_name in required_attrs):
            return device

        raise TypeError(
            'python-microscope focus integration requires a Stage with axes or a StageAxis-like '
            'object exposing move_to(), position, and limits'
        )

    def _require_axis(self) -> Any:
        if self._axis is None:
            raise RuntimeError('python-microscope focus axis is not connected')
        return self._axis

    def _to_device_units(self, z: float) -> float:
        return float(z) / self.position_scale

    def _from_device_units(self, z: float) -> float:
        return float(z) * self.position_scale


class PythonMicroscopeCamera(_PythonMicroscopeDeviceMixin, CameraBase):
    """Camera adapter for python-microscope devices and device-server URIs."""

    settings = ['framerate']

    def __init__(
        self,
        *,
        width: int,
        height: int,
        dtype: np.dtype,
        bits: int,
        nm_per_px: float,
        settings_map: Mapping[str, str] | None = None,
        readout_transform: tuple[bool, bool, bool] | None = None,
        device: Any | None = None,
        device_factory: Callable[[], Any] | None = None,
        device_uri: str | None = None,
        device_getter: Callable[[Any], Any] | None = None,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self.dtype = np.dtype(dtype)
        self.bits = int(bits)
        self.nm_per_px = float(nm_per_px)
        self.settings_map = dict(settings_map or {})
        self.settings = self._build_settings(self.settings_map)
        self._last_fetch_time = 0.0
        self._fps_estimate = 0.0
        self._fetch_count = 0
        self._fps_window_start = time()
        self._readout_transform = tuple(readout_transform or (False, False, False))
        CameraBase.__init__(self)
        _PythonMicroscopeDeviceMixin.__init__(
            self,
            device=device,
            device_factory=device_factory,
            device_uri=device_uri,
            device_getter=device_getter,
        )

    def connect(self, video_buffer) -> None:
        super().connect(video_buffer)
        device = self._connect_microscope_device(use_data_client=True)
        set_transform = getattr(device, 'set_transform', None)
        if callable(set_transform):
            set_transform(self._readout_transform)
        enable = getattr(device, 'enable', None)
        if callable(enable):
            enable()
        self._last_fetch_time = 0.0
        self._fps_estimate = 0.0
        self._fetch_count = 0
        self._fps_window_start = time()
        self.is_connected = True

    def fetch(self) -> None:
        device = self.microscope_device
        image, timestamp = self._grab_frame(device)
        if isinstance(image, (bytes, bytearray, memoryview)):
            image_array = np.frombuffer(image, dtype=self.dtype)
        else:
            image_array = np.asarray(image, dtype=self.dtype)
        if image_array.ndim == 1 and image_array.size == (self.height * self.width):
            image_array = image_array.reshape(self.height, self.width)
        if image_array.shape != (self.height, self.width):
            raise ValueError(
                f'Expected frame shape {(self.height, self.width)} but received {image_array.shape}'
            )

        self.video_buffer.write_image_and_timestamp(image_array.tobytes(), float(timestamp))
        self._update_framerate_estimate(float(timestamp))
        self.report_frame_received(float(timestamp))

    def release(self) -> None:
        release = getattr(self.microscope_device, 'release', None)
        if callable(release):
            release()

    def release_all(self) -> None:
        if self._microscope_device is None:
            return
        disable = getattr(self.microscope_device, 'disable', None)
        if callable(disable):
            disable()

    def get_setting(self, name: str) -> str:
        super().get_setting(name)
        if name == 'framerate' and name not in self.settings_map:
            return str(round(self._fps_estimate))

        microscope_name = self.settings_map[name]
        value = self.microscope_device.get_setting(microscope_name)
        return str(value)

    def set_setting(self, name: str, value: str) -> None:
        super().set_setting(name, value)
        if name == 'framerate' and name not in self.settings_map:
            raise ValueError('framerate is read-only unless settings_map maps it to a microscope setting')

        microscope_name = self.settings_map[name]
        self.microscope_device.set_setting(microscope_name, value)

    def shutdown(self) -> None:
        self.is_connected = False
        self.video_buffer = None
        self._disconnect_microscope_device()

    @staticmethod
    def _build_settings(settings_map: Mapping[str, str]) -> list[str]:
        ordered = ['framerate']
        for name in settings_map:
            if name != 'framerate':
                ordered.append(name)
        return ordered

    @staticmethod
    def _grab_frame(device: Any) -> tuple[Any, float]:
        if hasattr(device, 'trigger_and_wait'):
            return device.trigger_and_wait()
        if hasattr(device, 'grab_next_data'):
            return device.grab_next_data()
        raise TypeError(
            'python-microscope camera integration requires a device exposing '
            'trigger_and_wait() or grab_next_data()'
        )

    def _update_framerate_estimate(self, timestamp: float) -> None:
        if self._last_fetch_time == 0.0:
            self._fps_window_start = timestamp
        self._fetch_count += 1
        if timestamp <= self._last_fetch_time:
            return
        window = timestamp - self._fps_window_start
        self._last_fetch_time = timestamp
        if window >= 1.0:
            self._fps_estimate = self._fetch_count / window
            self._fetch_count = 0
            self._fps_window_start = timestamp


class PythonMicroscopeHardwareManager(PythonMicroscopeHardwareManagerBase):
    """Concrete alias for users who only need the connection helper mixin."""

    @abstractmethod
    def fetch(self) -> None:
        raise NotImplementedError()


__all__ = [
    'PythonMicroscopeCamera',
    'PythonMicroscopeFocusMotor',
    'PythonMicroscopeHardwareManager',
    'PythonMicroscopeHardwareManagerBase',
]
