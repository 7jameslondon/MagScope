from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

import magscope.camera as camera
from magscope.ipc_commands import UpdateCameraSettingCommand


class FakeValue:
    def __init__(self, value=0):
        self.value = value


class ConcreteCamera(camera.CameraBase):
    width = 2
    height = 2
    bits = 8
    dtype = np.uint8
    nm_per_px = 100.0
    settings = ['framerate', 'gain']

    def __init__(self):
        self.values = {'framerate': '30', 'gain': '1'}
        super().__init__()

    def connect(self, video_buffer):
        super().connect(video_buffer)
        self.is_connected = True

    def fetch(self):
        pass

    def release(self):
        pass

    def get_setting(self, name: str) -> str:
        super().get_setting(name)
        return self.values[name]

    def set_setting(self, name: str, value: str):
        super().set_setting(name, value)
        self.values[name] = value


class FakeManagedCamera:
    settings = ['framerate', 'gain']

    def __init__(self, *, fail_on_set=False):
        self.values = {'framerate': '30', 'gain': '1'}
        self.fail_on_set = fail_on_set
        self.is_connected = False
        self.shared_values = None
        self.connected_buffer = None
        self.reset_called = False
        self.set_calls = []

    def reset_health_counters(self):
        self.reset_called = True

    def connect(self, video_buffer):
        self.connected_buffer = video_buffer
        self.is_connected = True

    def fetch(self):
        pass

    def release(self):
        pass

    def __getitem__(self, name):
        return self.values[name]

    def __setitem__(self, name, value):
        self.set_calls.append((name, value))
        if self.fail_on_set:
            raise RuntimeError('rejected')
        self.values[name] = value


@pytest.fixture(autouse=True)
def clear_camera_manager_singleton():
    type(camera.CameraManager)._instances.pop(camera.CameraManager, None)
    try:
        yield
    finally:
        type(camera.CameraManager)._instances.pop(camera.CameraManager, None)


def fake_shared_values():
    return SimpleNamespace(
        camera_total_frames=FakeValue(9),
        camera_consecutive_timeouts=FakeValue(4),
        camera_queue_full_events=FakeValue(3),
        camera_last_frame_timestamp=FakeValue(12.0),
    )


def test_camera_base_rejects_invalid_dtype_bits_and_missing_framerate():
    class BadDtypeCamera(ConcreteCamera):
        dtype = np.float32

    class BadBitsCamera(ConcreteCamera):
        bits = 16

    class MissingFramerateCamera(ConcreteCamera):
        settings = ['gain']

    with pytest.raises(ValueError, match='Invalid dtype'):
        BadDtypeCamera()
    with pytest.raises(ValueError, match='Invalid bits'):
        BadBitsCamera()
    with pytest.raises(ValueError, match="'framerate' setting"):
        MissingFramerateCamera()


def test_camera_base_rejects_unknown_settings():
    test_camera = ConcreteCamera()

    with pytest.raises(KeyError, match='Unknown setting missing'):
        test_camera['missing']
    with pytest.raises(KeyError, match='Unknown setting missing'):
        test_camera['missing'] = '1'


def test_camera_base_health_counters_reset_and_update_shared_values():
    test_camera = ConcreteCamera()
    test_camera.shared_values = fake_shared_values()

    test_camera.reset_health_counters()
    assert test_camera.shared_values.camera_total_frames.value == 0
    assert test_camera.shared_values.camera_consecutive_timeouts.value == 0
    assert test_camera.shared_values.camera_queue_full_events.value == 0
    assert test_camera.shared_values.camera_last_frame_timestamp.value == 0.0

    test_camera.report_timeout()
    test_camera.report_queue_full()
    test_camera.report_frame_received(42.5)

    assert test_camera.shared_values.camera_total_frames.value == 1
    assert test_camera.shared_values.camera_consecutive_timeouts.value == 0
    assert test_camera.shared_values.camera_queue_full_events.value == 1
    assert test_camera.shared_values.camera_last_frame_timestamp.value == 42.5


def test_camera_base_health_counter_methods_allow_missing_shared_values():
    test_camera = ConcreteCamera()

    test_camera.reset_health_counters()
    test_camera.report_timeout()
    test_camera.report_queue_full()
    test_camera.report_frame_received(1.0)


def test_camera_manager_setup_connects_camera_and_broadcasts_initial_settings():
    manager = camera.CameraManager()
    fake_camera = FakeManagedCamera()
    sent_commands = []
    video_buffer = object()
    shared_values = fake_shared_values()
    manager.camera = fake_camera
    manager.video_buffer = video_buffer
    manager.shared_values = shared_values
    manager.send_ipc = sent_commands.append

    manager.setup()

    assert fake_camera.shared_values is shared_values
    assert fake_camera.reset_called is True
    assert fake_camera.connected_buffer is video_buffer
    assert sent_commands == [
        UpdateCameraSettingCommand(name='framerate', value='30'),
        UpdateCameraSettingCommand(name='gain', value='1'),
    ]


def test_camera_manager_set_camera_setting_warns_and_rebroadcasts_on_error():
    manager = camera.CameraManager()
    fake_camera = FakeManagedCamera(fail_on_set=True)
    fake_camera.is_connected = True
    sent_commands = []
    manager.camera = fake_camera
    manager.send_ipc = sent_commands.append

    with pytest.warns(UserWarning, match='Could not set camera setting gain to 5: rejected'):
        manager.set_camera_setting('gain', '5')

    assert fake_camera.set_calls == [('gain', '5')]
    assert sent_commands == [
        UpdateCameraSettingCommand(name='framerate', value='30'),
        UpdateCameraSettingCommand(name='gain', value='1'),
    ]


@pytest.mark.parametrize(
    ('name', 'value'),
    [
        ('framerate', '0'),
        ('framerate', '10001'),
        ('exposure', '-1'),
        ('exposure', '10000001'),
        ('gain', '-1'),
        ('gain', '11'),
    ],
)
def test_dummy_camera_noise_rejects_invalid_setting_ranges(name, value):
    noise_camera = camera.DummyCameraNoise()

    with pytest.raises(ValueError):
        noise_camera.set_setting(name, value)


def test_dummy_camera_noise_accepts_valid_setting_values():
    noise_camera = camera.DummyCameraNoise()

    noise_camera.set_setting('framerate', '120.5')
    noise_camera.set_setting('exposure', '300')
    noise_camera.set_setting('gain', '2')

    assert noise_camera.fake_settings['framerate'] == 120.5
    assert noise_camera.fake_settings['exposure'] == 300.0
    assert noise_camera.fake_settings['gain'] == 2


def test_dummy_camera_fast_noise_cycles_cached_frame_bytes(monkeypatch):
    fast_camera = camera.DummyCameraFastNoise()
    fast_camera.width = 2
    fast_camera.height = 2
    fast_camera.fake_images_n = 2
    fast_camera.fake_settings['exposure'] = 100.0
    fast_camera.fake_settings['gain'] = 0.0
    calls = []

    def fake_rand(height, width, n_images):
        calls.append((height, width, n_images))
        return np.linspace(0.01, 0.08, height * width * n_images).reshape(height, width, n_images)

    monkeypatch.setattr(camera.np.random, 'rand', fake_rand)

    first_frame = fast_camera.get_fake_image()
    second_frame = fast_camera.get_fake_image()
    third_frame = fast_camera.get_fake_image()

    assert calls == [(2, 2, 2)]
    assert first_frame != second_frame
    assert third_frame == first_frame
    assert fast_camera.fake_image_index == 1


def test_dummy_camera_beads_seed_setting_reinitializes_state_deterministically(monkeypatch):
    def fake_simulate_beads(xyz, *, nm_per_px, size_px, radius_nm):
        return np.full((size_px, size_px, 1), 0.5, dtype=np.float32)

    monkeypatch.setattr(camera, 'simulate_beads', fake_simulate_beads)
    bead_camera = camera.DummyCameraBeads()
    bead_camera._settings['fixed_n'] = 2
    bead_camera._settings['tethered_n'] = 1

    bead_camera.set_setting('seed', '123')
    first_fixed_centers = bead_camera._centers_fixed.copy()
    first_tethered_centers = bead_camera._centers_teth.copy()

    bead_camera.set_setting('seed', '456')
    bead_camera.set_setting('seed', '123')

    np.testing.assert_array_equal(bead_camera._centers_fixed, first_fixed_centers)
    np.testing.assert_array_equal(bead_camera._centers_teth, first_tethered_centers)
    assert bead_camera._xy.shape == (1, 2)
    np.testing.assert_array_equal(bead_camera._z, np.asarray([0.0], dtype=np.float32))


@pytest.mark.parametrize(
    ('name', 'value', 'message'),
    [
        ('framerate', '0', 'framerate must be between 1 and 10000 Hz'),
        ('fixed_n', '-1', 'fixed_n and tethered_n must be between 0 and 5000'),
        ('tethered_n', '5001', 'fixed_n and tethered_n must be between 0 and 5000'),
    ],
)
def test_dummy_camera_beads_rejects_invalid_setting_ranges(name, value, message):
    bead_camera = camera.DummyCameraBeads()

    with pytest.raises(ValueError, match=message):
        bead_camera.set_setting(name, value)
