from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from magscope.python_microscope import (
    PythonMicroscopeCamera,
    PythonMicroscopeFocusMotor,
    PythonMicroscopeHardwareManagerBase,
)


class FakeBuffer:
    def __init__(self):
        self.rows = []

    def write_image_and_timestamp(self, image, timestamp):
        self.rows.append((image, timestamp))


class FakeMicroscopeDevice:
    def __init__(self):
        self.enabled = False
        self.shutdown_called = False

    def enable(self):
        self.enabled = True

    def shutdown(self):
        self.shutdown_called = True


class DummyMicroscopeHardwareManager(PythonMicroscopeHardwareManagerBase):
    def fetch(self):
        pass


def reset_singleton_instance(cls) -> None:
    type(cls)._instances.pop(cls, None)


class FakeAxisLimits:
    def __init__(self, lower: float, upper: float):
        self.lower = lower
        self.upper = upper


class FakeStageAxis:
    def __init__(self):
        self.position = 1.5
        self.limits = FakeAxisLimits(0.0, 10.0)
        self.move_calls = []

    def move_to(self, position: float) -> None:
        self.move_calls.append(position)
        self.position = position


class FakeStage:
    def __init__(self):
        self.enabled = False
        self.shutdown_called = False
        self.axes = {'z': FakeStageAxis()}

    def enable(self):
        self.enabled = True

    def shutdown(self):
        self.shutdown_called = True


class FakeMicroscopeCameraDevice:
    def __init__(self):
        self.enabled = False
        self.disabled = False
        self.shutdown_called = False
        self.settings = {'fps': 12.5, 'exposure': 5.0}
        self.frames = [
            (np.arange(12, dtype=np.uint16).reshape(3, 4), 10.0),
            (np.arange(12, dtype=np.uint16).reshape(3, 4) + 1, 11.0),
        ]

    def enable(self):
        self.enabled = True

    def disable(self):
        self.disabled = True

    def shutdown(self):
        self.shutdown_called = True

    def grab_next_data(self):
        return self.frames.pop(0)

    def get_setting(self, name: str):
        return self.settings[name]

    def set_setting(self, name: str, value: str) -> None:
        self.settings[name] = value


def test_python_microscope_hardware_manager_connects_with_factory():
    reset_singleton_instance(DummyMicroscopeHardwareManager)
    device = FakeMicroscopeDevice()
    manager = DummyMicroscopeHardwareManager(device_factory=lambda: device)

    manager.connect()

    assert manager._is_connected is True
    assert manager.microscope_device is device
    assert device.enabled is True

    manager.disconnect()

    assert manager._is_connected is False
    assert device.shutdown_called is True


def test_python_microscope_hardware_manager_connects_with_pyro_uri(monkeypatch):
    reset_singleton_instance(DummyMicroscopeHardwareManager)
    proxy = FakeMicroscopeDevice()
    proxy.pyro_released = False

    def release():
        proxy.pyro_released = True

    proxy._pyroRelease = release

    pyro4_module = types.SimpleNamespace(Proxy=lambda uri: proxy)
    monkeypatch.setitem(sys.modules, 'Pyro4', pyro4_module)

    manager = DummyMicroscopeHardwareManager(device_uri='PYRO:Device@127.0.0.1:8000')
    manager.connect()
    manager.disconnect()

    assert proxy.enabled is True
    assert proxy.shutdown_called is True
    assert proxy.pyro_released is True


def test_python_microscope_focus_motor_uses_stage_axis_and_scaling():
    stage = FakeStage()
    motor = PythonMicroscopeFocusMotor(device=stage, position_scale=100.0)

    motor.connect()

    assert stage.enabled is True
    assert motor.get_current_z() == pytest.approx(150.0)
    assert motor.get_position_limits() == pytest.approx((0.0, 1000.0))

    motor.move_absolute(400.0)

    assert stage.axes['z'].move_calls == [4.0]
    assert motor.get_is_moving() is False

    motor.disconnect()

    assert stage.shutdown_called is True


def test_python_microscope_camera_fetches_frames_and_maps_settings():
    camera_device = FakeMicroscopeCameraDevice()
    buffer = FakeBuffer()
    camera = PythonMicroscopeCamera(
        width=4,
        height=3,
        dtype=np.uint16,
        bits=12,
        nm_per_px=5000.0,
        device=camera_device,
        settings_map={'framerate': 'fps', 'exposure': 'exposure'},
    )

    camera.connect(buffer)
    camera.fetch()

    assert camera.is_connected is True
    assert camera_device.enabled is True
    assert len(buffer.rows) == 1
    image_bytes, timestamp = buffer.rows[0]
    assert timestamp == pytest.approx(10.0)
    np.testing.assert_array_equal(
        np.frombuffer(image_bytes, dtype=np.uint16).reshape(3, 4),
        np.arange(12, dtype=np.uint16).reshape(3, 4),
    )
    assert camera['framerate'] == '12.5'

    camera['exposure'] = '7.5'

    assert camera_device.settings['exposure'] == '7.5'

    camera.release_all()
    assert camera_device.disabled is True

    camera.shutdown()
    assert camera_device.shutdown_called is True


def test_python_microscope_camera_uses_data_client_for_uri(monkeypatch):
    camera_device = FakeMicroscopeCameraDevice()

    clients_module = types.SimpleNamespace(DataClient=lambda uri: camera_device)
    microscope_module = types.ModuleType('microscope')
    microscope_module.clients = clients_module
    monkeypatch.setitem(sys.modules, 'microscope', microscope_module)
    monkeypatch.setitem(sys.modules, 'microscope.clients', clients_module)

    buffer = FakeBuffer()
    camera = PythonMicroscopeCamera(
        width=4,
        height=3,
        dtype=np.uint16,
        bits=12,
        nm_per_px=5000.0,
        device_uri='PYRO:Camera@127.0.0.1:8000',
    )

    camera.connect(buffer)
    camera.fetch()

    assert len(buffer.rows) == 1
    assert camera['framerate'] == '0'


def test_python_microscope_camera_rejects_wrong_frame_shape():
    camera_device = FakeMicroscopeCameraDevice()
    camera_device.frames = [(np.ones((2, 2), dtype=np.uint16), 10.0)]
    buffer = FakeBuffer()
    camera = PythonMicroscopeCamera(
        width=4,
        height=3,
        dtype=np.uint16,
        bits=12,
        nm_per_px=5000.0,
        device=camera_device,
    )

    camera.connect(buffer)

    with pytest.raises(ValueError, match='Expected frame shape'):
        camera.fetch()
