from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import magscope.python_microscope as python_microscope


class FakeValue:
    def __init__(self, value=0):
        self.value = value


class FakeVideoBuffer:
    def __init__(self):
        self.writes = []

    def write_image_and_timestamp(self, image, timestamp):
        self.writes.append((image, timestamp))


class FakeLimits:
    def __init__(self, lower, upper):
        self.lower = lower
        self.upper = upper


class FakeAxis:
    def __init__(self, *, position=0.0, limits=None):
        self.position = position
        self.limits = limits or FakeLimits(-10.0, 10.0)
        self.move_calls = []

    def move_to(self, position):
        self.move_calls.append(position)


class FakeDevice:
    def __init__(self):
        self.enabled = False
        self.disabled = False
        self.shutdown_called = False
        self.pyro_release_called = False
        self.transforms = []
        self.settings = {}

    def enable(self):
        self.enabled = True

    def disable(self):
        self.disabled = True

    def shutdown(self):
        self.shutdown_called = True

    def _pyroRelease(self):  # noqa: N802 - external API spelling
        self.pyro_release_called = True

    def set_transform(self, transform):
        self.transforms.append(transform)

    def trigger_and_wait(self):
        return np.arange(6, dtype=np.uint16).tobytes(), 12.5

    def grab_next_data(self):
        return np.arange(6, dtype=np.uint16).reshape(2, 3), 13.5

    def get_setting(self, name):
        return self.settings[name]

    def set_setting(self, name, value):
        self.settings[name] = value


@pytest.fixture(autouse=True)
def clear_python_microscope_singletons():
    type(python_microscope.PythonMicroscopeFocusMotor)._instances.pop(
        python_microscope.PythonMicroscopeFocusMotor,
        None,
    )
    try:
        yield
    finally:
        type(python_microscope.PythonMicroscopeFocusMotor)._instances.pop(
            python_microscope.PythonMicroscopeFocusMotor,
            None,
        )


def make_camera(device=None, **kwargs):
    return python_microscope.PythonMicroscopeCamera(
        width=3,
        height=2,
        dtype=np.uint16,
        bits=12,
        nm_per_px=100.0,
        device=device or FakeDevice(),
        **kwargs,
    )


def test_require_dependency_reports_optional_extra_install_hint(monkeypatch):
    def missing_dependency(module_name):
        raise ImportError(module_name)

    monkeypatch.setattr(python_microscope, 'import_module', missing_dependency)

    with pytest.raises(ImportError, match=r'pip install magscope\[python-microscope\]'):
        python_microscope._require_dependency('microscope.clients', 'python-microscope')


def test_device_mixin_requires_exactly_one_device_source():
    with pytest.raises(ValueError, match='exactly one'):
        python_microscope.PythonMicroscopeCamera(
            width=1,
            height=1,
            dtype=np.uint8,
            bits=8,
            nm_per_px=1.0,
        )

    with pytest.raises(ValueError, match='exactly one'):
        python_microscope.PythonMicroscopeCamera(
            width=1,
            height=1,
            dtype=np.uint8,
            bits=8,
            nm_per_px=1.0,
            device=FakeDevice(),
            device_factory=FakeDevice,
        )


def test_focus_motor_converts_position_scale_for_move_position_and_limits():
    axis = FakeAxis(position=4.0, limits=FakeLimits(-2.0, 8.0))
    stage = SimpleNamespace(axes={'z': axis}, enable=lambda: None)
    motor = python_microscope.PythonMicroscopeFocusMotor(device=stage, position_scale=25.0)

    motor.connect()
    motor.move_absolute(100.0)

    assert axis.move_calls == [4.0]
    assert motor.get_current_z() == 100.0
    assert motor.get_position_limits() == (-50.0, 200.0)


def test_focus_motor_reports_available_axes_when_named_axis_is_missing():
    stage = SimpleNamespace(axes={'x': FakeAxis(), 'y': FakeAxis()})
    motor = python_microscope.PythonMicroscopeFocusMotor(axis_name='z', device=stage)

    with pytest.raises(KeyError, match='available axes: x, y'):
        motor.connect()


@pytest.mark.parametrize(
    ('axis_factory', 'expected'),
    [
        (lambda: SimpleNamespace(moving=True, position=0.0, limits=FakeLimits(0.0, 1.0), move_to=lambda z: None), True),
        (
            lambda: SimpleNamespace(
                is_moving=lambda: True,
                position=0.0,
                limits=FakeLimits(0.0, 1.0),
                move_to=lambda z: None,
            ),
            True,
        ),
        (
            lambda: SimpleNamespace(
                get_is_moving=lambda: True,
                position=0.0,
                limits=FakeLimits(0.0, 1.0),
                move_to=lambda z: None,
            ),
            True,
        ),
    ],
)
def test_focus_motor_uses_device_moving_state_when_available(axis_factory, expected):
    axis = axis_factory()
    motor = python_microscope.PythonMicroscopeFocusMotor(device=axis)

    motor.connect()

    assert motor.get_is_moving() is expected


def test_focus_motor_falls_back_to_target_position_comparison_for_moving_state():
    axis = FakeAxis(position=0.0)
    motor = python_microscope.PythonMicroscopeFocusMotor(device=axis)
    motor.connect()

    motor.move_absolute(100.0)
    assert motor.get_is_moving() is True

    axis.position = 100.0
    assert motor.get_is_moving() is False


def test_camera_connect_applies_transform_and_enables_device():
    device = FakeDevice()
    video_buffer = FakeVideoBuffer()
    microscope_camera = make_camera(device=device, readout_transform=(True, False, True))

    microscope_camera.connect(video_buffer)

    assert device.enabled is True
    assert device.transforms == [(True, False, True)]
    assert microscope_camera.is_connected is True
    assert microscope_camera.video_buffer is video_buffer


def test_camera_fetch_accepts_bytes_and_updates_health_counters():
    device = FakeDevice()
    video_buffer = FakeVideoBuffer()
    microscope_camera = make_camera(device=device)
    microscope_camera.shared_values = SimpleNamespace(
        camera_total_frames=FakeValue(0),
        camera_consecutive_timeouts=FakeValue(3),
        camera_queue_full_events=FakeValue(0),
        camera_last_frame_timestamp=FakeValue(0.0),
    )
    microscope_camera.connect(video_buffer)

    microscope_camera.fetch()

    assert video_buffer.writes == [(np.arange(6, dtype=np.uint16).tobytes(), 12.5)]
    assert microscope_camera.shared_values.camera_total_frames.value == 1
    assert microscope_camera.shared_values.camera_consecutive_timeouts.value == 0
    assert microscope_camera.shared_values.camera_last_frame_timestamp.value == 12.5


def test_camera_fetch_accepts_array_from_grab_next_data():
    class GrabOnlyDevice:
        def enable(self):
            pass

        def grab_next_data(self):
            return np.arange(6, dtype=np.uint16).reshape(2, 3), 13.5

    device = GrabOnlyDevice()
    video_buffer = FakeVideoBuffer()
    microscope_camera = make_camera(device=device)
    microscope_camera.connect(video_buffer)

    microscope_camera.fetch()

    assert video_buffer.writes == [(np.arange(6, dtype=np.uint16).tobytes(), 13.5)]


def test_camera_fetch_rejects_unexpected_frame_shape():
    class BadShapeDevice(FakeDevice):
        def trigger_and_wait(self):
            return np.zeros((3, 3), dtype=np.uint16), 12.5

    microscope_camera = make_camera(device=BadShapeDevice())
    microscope_camera.connect(FakeVideoBuffer())

    with pytest.raises(ValueError, match='Expected frame shape'):
        microscope_camera.fetch()


def test_camera_framerate_setting_is_read_only_without_mapping():
    microscope_camera = make_camera()

    with pytest.raises(ValueError, match='framerate is read-only'):
        microscope_camera.set_setting('framerate', '50')

    assert microscope_camera.get_setting('framerate') == '0'


def test_camera_mapped_setting_is_forwarded_to_device():
    device = FakeDevice()
    device.settings['Exposure'] = 10
    microscope_camera = make_camera(device=device, settings_map={'exposure': 'Exposure'})
    microscope_camera.connect(FakeVideoBuffer())

    assert microscope_camera.get_setting('exposure') == '10'
    microscope_camera.set_setting('exposure', '25')
    assert device.settings['Exposure'] == '25'


def test_camera_shutdown_clears_buffer_and_releases_root_device():
    device = FakeDevice()
    microscope_camera = make_camera(device=device)
    microscope_camera.connect(FakeVideoBuffer())

    microscope_camera.shutdown()

    assert microscope_camera.is_connected is False
    assert microscope_camera.video_buffer is None
    assert device.shutdown_called is True
    assert device.pyro_release_called is True
