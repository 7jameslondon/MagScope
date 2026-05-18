from __future__ import annotations

import queue
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

    def __init__(self, *, fail_on_set=False, set_exception=None):
        self.values = {'framerate': '30', 'gain': '1'}
        self.fail_on_set = fail_on_set
        self.set_exception = set_exception
        self.is_connected = False
        self.shared_values = None
        self.connected_buffer = None
        self.reset_called = False
        self.set_calls = []
        self.fetch_calls = 0
        self.release_calls = 0

    def reset_health_counters(self):
        self.reset_called = True

    def connect(self, video_buffer):
        self.connected_buffer = video_buffer
        self.is_connected = True

    def fetch(self):
        self.fetch_calls += 1

    def release(self):
        self.release_calls += 1

    def __getitem__(self, name):
        return self.values[name]

    def __setitem__(self, name, value):
        self.set_calls.append((name, value))
        if self.fail_on_set:
            raise self.set_exception or RuntimeError('rejected')
        self.values[name] = value


class FakeVideoBuffer:
    def __init__(self, *, unread_stacks=0, n_images=2, n_total_stacks=5, raise_underflow=False):
        self.unread_stacks = unread_stacks
        self.n_images = n_images
        self.n_total_images = n_images * n_total_stacks
        self.stack_shape = (4, 4, n_images)
        self.raise_underflow = raise_underflow
        self.read_calls = 0

    def get_level(self):
        return (self.unread_stacks * self.n_images) / self.n_total_images

    def get_unread_stack_count(self):
        return self.unread_stacks

    def read_stack_no_return(self):
        self.read_calls += 1
        if self.raise_underflow or self.unread_stacks <= 0:
            raise camera.BufferUnderflow
        self.unread_stacks -= 1


class FakeWriteVideoBuffer:
    def __init__(self):
        self.writes = []

    def write_image_and_timestamp(self, image, timestamp):
        self.writes.append((image, timestamp))


class TrackingLock:
    def __init__(self):
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self):
        self.enter_count += 1
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exit_count += 1
        return False


@pytest.fixture(autouse=True)
def clear_camera_manager_singleton():
    type(camera.CameraManager)._instances.pop(camera.CameraManager, None)
    try:
        yield
    finally:
        type(camera.CameraManager)._instances.pop(camera.CameraManager, None)


def fake_shared_values():
    return SimpleNamespace(
        video_process_reserved_stacks=FakeValue(0),
        video_process_completed_stacks=FakeValue(0),
        camera_total_frames=FakeValue(9),
        camera_consecutive_timeouts=FakeValue(4),
        camera_queue_full_events=FakeValue(3),
        camera_last_frame_timestamp=FakeValue(12.0),
    )


def test_camera_base_rejects_invalid_dtype_bits_and_missing_framerate():
    class IncompleteCamera(ConcreteCamera):
        width = None

    class BadDtypeCamera(ConcreteCamera):
        dtype = np.float32

    class BadBitsTypeCamera(ConcreteCamera):
        bits = 8.0

    class BadBitsCamera(ConcreteCamera):
        bits = 16

    class MissingFramerateCamera(ConcreteCamera):
        settings = ['gain']

    with pytest.raises(NotImplementedError):
        IncompleteCamera()
    with pytest.raises(ValueError, match='Invalid dtype'):
        BadDtypeCamera()
    with pytest.raises(ValueError, match='Invalid bits'):
        BadBitsTypeCamera()
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


def test_camera_base_connect_sets_video_buffer_and_abstract_noops_are_callable():
    test_camera = ConcreteCamera()
    video_buffer = object()

    test_camera.connect(video_buffer)
    camera.CameraBase.fetch(test_camera)
    camera.CameraBase.release(test_camera)

    assert test_camera.video_buffer is video_buffer
    assert test_camera.is_connected is True


def test_camera_base_release_all_drains_camera_buffer_queue():
    test_camera = ConcreteCamera()
    test_camera.camera_buffers = queue.Queue()
    test_camera.camera_buffers.put(object())
    test_camera.camera_buffers.put(object())
    release_calls = []

    def release_one():
        release_calls.append(test_camera.camera_buffers.get())

    test_camera.release = release_one

    test_camera.release_all()

    assert len(release_calls) == 2
    assert test_camera.camera_buffers.qsize() == 0


def test_camera_base_del_suppresses_release_errors():
    test_camera = ConcreteCamera()
    test_camera.is_connected = True
    test_camera.video_buffer = object()

    def fail_release_all():
        raise RuntimeError('release failed')

    test_camera.release_all = fail_release_all

    test_camera.__del__()

    assert test_camera.video_buffer is None
    test_camera.is_connected = False


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


def test_camera_manager_setup_warns_when_shared_values_missing():
    manager = camera.CameraManager()
    manager.camera = FakeManagedCamera()
    manager.video_buffer = object()
    sent_commands = []
    manager.send_ipc = sent_commands.append

    with pytest.warns(UserWarning, match='CameraManager has no shared_values'):
        manager.setup()

    assert manager.camera.is_connected is False
    assert sent_commands == []


def test_camera_manager_do_main_loop_releases_purges_and_fetches():
    manager = camera.CameraManager()
    fake_camera = FakeManagedCamera()
    fake_camera.is_connected = True
    sent_commands = []
    calls = []

    manager.camera = fake_camera
    manager.video_buffer = SimpleNamespace(get_level=lambda: 0.95, n_total_images=10)
    manager._acquisition_on = False
    manager.send_ipc = sent_commands.append
    manager._release_completed_pool_buffers = lambda: calls.append('completed')
    manager._release_unattached_buffers = lambda: calls.append('unattached')
    manager._purge_buffers = lambda: calls.append('purge') or 1

    manager.do_main_loop()

    assert calls == ['completed', 'unattached', 'purge']
    assert fake_camera.fetch_calls == 1
    assert len(sent_commands) == 1
    assert sent_commands[0].__class__.__name__ == 'UpdateVideoBufferPurgeCommand'


def test_camera_manager_release_unattached_buffers_releases_each_frame():
    manager = camera.CameraManager()
    manager.camera = FakeManagedCamera()
    manager.shared_values = fake_shared_values()
    manager.video_buffer = FakeVideoBuffer(unread_stacks=2, n_images=3)

    manager._release_unattached_buffers()

    assert manager.video_buffer.read_calls == 2
    assert manager.camera.release_calls == 6
    assert manager.video_buffer.unread_stacks == 0


def test_camera_manager_purge_buffers_until_capacity_available():
    manager = camera.CameraManager()
    manager.camera = FakeManagedCamera()
    manager.shared_values = fake_shared_values()
    manager.video_buffer = FakeVideoBuffer(unread_stacks=4, n_images=2, n_total_stacks=5)

    purged_stacks = manager._purge_buffers()

    assert purged_stacks == 3
    assert manager.video_buffer.read_calls == 3
    assert manager.camera.release_calls == 6
    assert manager.video_buffer.get_level() <= 0.3


def test_camera_manager_release_completed_pool_buffers_releases_new_delta_only():
    manager = camera.CameraManager()
    manager.camera = FakeManagedCamera()
    manager.shared_values = fake_shared_values()
    manager.shared_values.video_process_completed_stacks.value = 5
    manager.video_buffer = SimpleNamespace(stack_shape=(4, 4, 3))
    manager._released_completed_stacks = 2

    manager._release_completed_pool_buffers()

    assert manager.camera.release_calls == 9
    assert manager._released_completed_stacks == 5


def test_camera_manager_take_unreserved_stack_respects_reservations_and_underflow():
    manager = camera.CameraManager()
    manager.shared_values = fake_shared_values()
    manager.video_buffer = FakeVideoBuffer(unread_stacks=1)
    manager.shared_values.video_process_reserved_stacks.value = 1

    assert manager._take_unreserved_stack() is False
    assert manager.video_buffer.read_calls == 0

    manager.shared_values.video_process_reserved_stacks.value = 0
    manager.video_buffer = FakeVideoBuffer(unread_stacks=1, raise_underflow=True)

    assert manager._take_unreserved_stack() is False
    assert manager.video_buffer.read_calls == 1


def test_camera_manager_stack_coordination_lock_uses_named_lock_when_available():
    manager = camera.CameraManager()
    lock = TrackingLock()
    manager.locks = {'VideoProcessingReservation': lock}

    with manager._stack_coordination_lock() as returned_lock:
        assert returned_lock is lock

    assert lock.enter_count == 1
    assert lock.exit_count == 1

    manager.locks = None
    with manager._stack_coordination_lock() as returned_lock:
        assert returned_lock is None


def test_camera_manager_buffer_helpers_noop_without_available_work():
    manager = camera.CameraManager()
    manager.camera = FakeManagedCamera()
    manager.shared_values = fake_shared_values()
    manager.video_buffer = None

    manager._release_unattached_buffers()
    manager._release_completed_pool_buffers()

    assert manager._purge_buffers() == 0
    assert manager._take_unreserved_stack() is False

    manager.video_buffer = FakeVideoBuffer(unread_stacks=0)
    assert manager._purge_buffers() == 0

    manager.video_buffer = SimpleNamespace(stack_shape=(4, 4, 2))
    manager.shared_values.video_process_completed_stacks.value = 0
    manager._release_completed_pool_buffers()
    assert manager.camera.release_calls == 0


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


def test_camera_manager_set_camera_setting_uses_repr_for_empty_error_message():
    manager = camera.CameraManager()
    fake_camera = FakeManagedCamera(fail_on_set=True, set_exception=RuntimeError())
    sent_commands = []
    manager.camera = fake_camera
    manager.send_ipc = sent_commands.append

    with pytest.warns(UserWarning, match=r'Could not set camera setting gain to 5: RuntimeError\(\)'):
        manager.set_camera_setting('gain', '5')

    assert sent_commands == [
        UpdateCameraSettingCommand(name='framerate', value='30'),
        UpdateCameraSettingCommand(name='gain', value='1'),
    ]


def test_camera_manager_set_simulated_focus_ignores_non_bead_camera():
    manager = camera.CameraManager()
    manager.camera = FakeManagedCamera()

    manager.set_simulated_focus(10.0)


def test_camera_manager_set_simulated_focus_updates_bead_camera(monkeypatch):
    manager = camera.CameraManager()
    bead_camera = camera.DummyCameraBeads()
    calls = []
    manager.camera = bead_camera
    monkeypatch.setattr(bead_camera, 'set_focus_offset', calls.append)

    manager.set_simulated_focus(12.5)

    assert calls == [12.5]


def test_camera_manager_set_simulated_focus_warns_on_error(monkeypatch):
    manager = camera.CameraManager()
    bead_camera = camera.DummyCameraBeads()
    manager.camera = bead_camera

    def fail_focus(offset):
        raise ValueError('bad focus')

    monkeypatch.setattr(bead_camera, 'set_focus_offset', fail_focus)

    with pytest.warns(UserWarning, match='Could not update simulated focus to 12.5: bad focus'):
        manager.set_simulated_focus(12.5)


def test_dummy_camera_noise_connect_fetch_and_release(monkeypatch):
    noise_camera = camera.DummyCameraNoise()
    noise_camera.width = 2
    noise_camera.height = 2
    noise_camera.fake_settings['framerate'] = 10.0
    noise_camera.fake_settings['exposure'] = 1.0
    noise_camera.fake_settings['gain'] = 0.0
    video_buffer = FakeWriteVideoBuffer()

    noise_camera.connect(video_buffer)

    assert noise_camera.is_connected is True
    assert noise_camera.video_buffer is video_buffer

    noise_camera.last_time = 1.0
    monkeypatch.setattr(camera, 'time', lambda: 1.05)
    noise_camera.fetch()
    assert video_buffer.writes == []

    noise_camera.last_time = 0.0
    noise_camera.est_fps_time = 0.0
    monkeypatch.setattr(camera, 'time', lambda: 1.25)
    noise_camera.fetch()
    image_bytes, timestamp = video_buffer.writes[0]

    assert timestamp == 1.25
    assert len(image_bytes) == 4
    assert noise_camera.est_fps_time == 1.25
    assert noise_camera.est_fps_count == 0
    noise_camera.release()


def test_dummy_camera_noise_get_setting_rounds_values():
    noise_camera = camera.DummyCameraNoise()
    noise_camera.est_fps = 12.6
    noise_camera.fake_settings['exposure'] = 300.6
    noise_camera.fake_settings['gain'] = 2.0

    assert noise_camera.get_setting('framerate') == '13'
    assert noise_camera.get_setting('exposure') == '301'
    assert noise_camera.get_setting('gain') == '2'


def test_dummy_camera_fast_noise_connect_fetch_and_release(monkeypatch):
    fast_camera = camera.DummyCameraFastNoise()
    fast_camera.width = 2
    fast_camera.height = 2
    fast_camera.fake_images_n = 2
    fast_camera.fake_settings['framerate'] = 10.0
    fast_camera.fake_settings['exposure'] = 1.0
    fast_camera.fake_settings['gain'] = 0.0
    video_buffer = FakeWriteVideoBuffer()

    def fake_rand(height, width, n_images):
        return np.linspace(0.1, 0.8, height * width * n_images).reshape(height, width, n_images)

    monkeypatch.setattr(camera.np.random, 'rand', fake_rand)
    fast_camera.connect(video_buffer)

    assert fast_camera.is_connected is True
    assert fast_camera.video_buffer is video_buffer
    assert fast_camera.fake_images is not None

    fast_camera.last_time = 0.0
    fast_camera.est_fps_time = 0.0
    monkeypatch.setattr(camera, 'time', lambda: 1.25)
    fast_camera.fetch()
    image_bytes, timestamp = video_buffer.writes[0]

    assert timestamp == 1.25
    assert len(image_bytes) == 4
    assert fast_camera.est_fps_time == 1.25
    assert fast_camera.est_fps_count == 0
    fast_camera.release()


def test_dummy_camera_fast_noise_fetch_returns_before_frame_interval(monkeypatch):
    fast_camera = camera.DummyCameraFastNoise()
    fast_camera.fake_settings['framerate'] = 10.0
    fast_camera.video_buffer = FakeWriteVideoBuffer()
    fast_camera.last_time = 1.0
    monkeypatch.setattr(camera, 'time', lambda: 1.05)

    fast_camera.fetch()

    assert fast_camera.video_buffer.writes == []


def test_dummy_camera_fast_noise_get_setting_rounds_values():
    fast_camera = camera.DummyCameraFastNoise()
    fast_camera.est_fps = 19.7
    fast_camera.fake_settings['exposure'] = 123.4
    fast_camera.fake_settings['gain'] = 4.0

    assert fast_camera.get_setting('framerate') == '20'
    assert fast_camera.get_setting('exposure') == '123'
    assert fast_camera.get_setting('gain') == '4'


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
def test_dummy_camera_fast_noise_rejects_invalid_setting_ranges(name, value):
    fast_camera = camera.DummyCameraFastNoise()

    with pytest.raises(ValueError):
        fast_camera.set_setting(name, value)


def test_dummy_camera_fast_noise_accepts_valid_setting_values():
    fast_camera = camera.DummyCameraFastNoise()

    fast_camera.set_setting('framerate', '120.5')
    fast_camera.set_setting('exposure', '300')
    fast_camera.set_setting('gain', '2')

    assert fast_camera.fake_settings['framerate'] == 120.5
    assert fast_camera.fake_settings['exposure'] == 300.0
    assert fast_camera.fake_settings['gain'] == 2


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


def test_dummy_camera_beads_connect_fetch_and_release(monkeypatch):
    current_time = [0.0]

    def fake_simulate_beads(xyz, *, nm_per_px, size_px, radius_nm):
        image = np.linspace(0.2, 0.8, size_px * size_px, dtype=np.float32).reshape(size_px, size_px)
        return image[:, :, None]

    monkeypatch.setattr(camera, 'simulate_beads', fake_simulate_beads)
    monkeypatch.setattr(camera, 'time', lambda: current_time[0])
    bead_camera = camera.DummyCameraBeads()
    bead_camera.width = 12
    bead_camera.height = 10
    bead_camera._bead_size_px = 4
    bead_camera._edge_margin_px = 2.0
    bead_camera._min_sep_px = 1.0
    bead_camera._settings.update({
        'fixed_n': 1,
        'tethered_n': 1,
        'framerate': 2.0,
        'gain': 1000.0,
        'seed': 7,
    })
    bead_camera.shared_values = fake_shared_values()
    video_buffer = FakeWriteVideoBuffer()

    bead_camera.connect(video_buffer)

    assert bead_camera.is_connected is True
    assert bead_camera.video_buffer is video_buffer
    assert bead_camera._centers_fixed.shape == (1, 2)
    assert bead_camera._centers_teth.shape == (1, 2)
    assert bead_camera._z.shape == (1,)

    current_time[0] = 0.25
    bead_camera.fetch()
    assert video_buffer.writes == []

    current_time[0] = 1.0
    bead_camera.fetch()
    image_bytes, timestamp = video_buffer.writes[0]
    image = np.frombuffer(image_bytes, dtype=np.uint8).reshape(bead_camera.height, bead_camera.width)

    assert timestamp == 1.0
    assert image.shape == (10, 12)
    assert bead_camera.last_time == 1.0
    assert bead_camera.shared_values.camera_total_frames.value == 10
    bead_camera.release()


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


def test_dummy_camera_beads_accepts_framerate_counts_and_unknown_setting(monkeypatch):
    def fake_simulate_beads(xyz, *, nm_per_px, size_px, radius_nm):
        return np.full((size_px, size_px, 1), 0.5, dtype=np.float32)

    monkeypatch.setattr(camera, 'simulate_beads', fake_simulate_beads)
    bead_camera = camera.DummyCameraBeads()
    bead_camera._min_sep_px = 1.0

    bead_camera.set_setting('framerate', '60')
    bead_camera.set_setting('fixed_n', '1')
    bead_camera.set_setting('tethered_n', '2')

    assert bead_camera._settings['framerate'] == 60.0
    assert bead_camera._settings['fixed_n'] == 1
    assert bead_camera._settings['tethered_n'] == 2
    assert bead_camera._centers_fixed.shape == (1, 2)
    assert bead_camera._xy.shape == (2, 2)
    with pytest.raises(KeyError, match='Unknown setting missing'):
        bead_camera.set_setting('missing', '1')


def test_dummy_camera_beads_declared_but_unhandled_setting_raises_keyerror():
    bead_camera = camera.DummyCameraBeads()
    bead_camera.settings = [*bead_camera.settings, 'declared_only']

    with pytest.raises(KeyError, match='Unknown setting declared_only'):
        bead_camera.set_setting('declared_only', '1')


def test_dummy_camera_beads_get_setting_rounds_estimated_framerate():
    bead_camera = camera.DummyCameraBeads()
    bead_camera.est_fps = 29.6

    assert bead_camera.get_setting('framerate') == '30'


# ---------------------------------------------------------------------------
# DummyCameraBeads static/class-methods
# ---------------------------------------------------------------------------

def test_ou_step_deterministic_with_seeded_rng():
    rng = np.random.RandomState(42)
    x = camera.DummyCameraBeads._ou_step(0.0, 0.01, 1.0, 0.1, 0.0, rng)
    assert isinstance(x, float)


def test_ou_step_converges_toward_mu():
    rng = np.random.RandomState(0)
    x = 10.0
    for _ in range(5000):
        x = camera.DummyCameraBeads._ou_step(x, 0.01, 1.0, 0.0, 0.0, rng)
    assert abs(x) < 1e-6


def test_ou_step_seed_reproducibility():
    rng_a = np.random.RandomState(123)
    rng_b = np.random.RandomState(123)
    a = camera.DummyCameraBeads._ou_step(1.0, 0.01, 1.0, 0.5, 0.0, rng_a)
    b = camera.DummyCameraBeads._ou_step(1.0, 0.01, 1.0, 0.5, 0.0, rng_b)
    assert a == b


def test_blit_add_full_overlap():
    dst = np.zeros((10, 10), dtype=np.float32)
    src = np.ones((4, 4), dtype=np.float32)
    camera.DummyCameraBeads._blit_add(dst, src, 3, 3, w=1.0)
    assert dst[3, 3] == 1.0
    assert dst[0, 0] == 0.0


def test_blit_add_partial_out_of_bounds_clipped():
    dst = np.zeros((5, 5), dtype=np.float32)
    src = np.full((4, 4), 2.0, dtype=np.float32)
    camera.DummyCameraBeads._blit_add(dst, src, 3, 3, w=1.0)
    assert dst[3, 3] == 2.0
    assert dst[4, 4] == 2.0


def test_blit_add_weight_factor():
    dst = np.zeros((6, 6), dtype=np.float32)
    src = np.ones((4, 4), dtype=np.float32)
    camera.DummyCameraBeads._blit_add(dst, src, 1, 1, w=0.5)
    assert dst[1, 1] == 0.5


def test_accumulate_bilinear_integer_position():
    dst = np.zeros((6, 6), dtype=np.float32)
    src = np.ones((2, 2), dtype=np.float32)
    camera.DummyCameraBeads._accumulate_bilinear(dst, src, 3.0, 3.0)
    assert dst.sum() == pytest.approx(4.0)


def test_accumulate_bilinear_fractional_position():
    dst = np.zeros((6, 6), dtype=np.float32)
    src = np.ones((2, 2), dtype=np.float32)
    camera.DummyCameraBeads._accumulate_bilinear(dst, src, 3.3, 3.7)
    assert dst.sum() == pytest.approx(4.0)


def test_border_median_uniform_image():
    img = np.full((10, 10), 5.0, dtype=np.float32)
    result = camera.DummyCameraBeads._border_median(img)
    assert result == 5.0


def test_border_median_known_border():
    img = np.zeros((10, 10), dtype=np.float32)
    img[0, :] = 1.0
    img[-1, :] = 2.0
    img[:, 0] = 3.0
    img[:, -1] = 4.0
    result = camera.DummyCameraBeads._border_median(img)
    assert 1.0 <= result <= 4.0


def test_tukey_taper_edges_are_zero():
    win = camera.DummyCameraBeads._tukey_taper(20, 30, pad=4)
    assert win[0, 0] == 0.0
    assert win[-1, -1] == 0.0


def test_tukey_taper_center_near_one():
    win = camera.DummyCameraBeads._tukey_taper(100, 100, pad=4)
    assert win[50, 50] == pytest.approx(1.0, abs=0.01)


def test_tukey_taper_shape_matches_input():
    win = camera.DummyCameraBeads._tukey_taper(15, 25, pad=4)
    assert win.shape == (15, 25)


def test_delta_for_crop_flat_input_near_zero():
    img = np.full((20, 20), 10.0, dtype=np.float32)
    crop = img[5:15, 5:15]
    result = camera.DummyCameraBeads._delta_for_crop(crop, pad=4)
    assert np.max(np.abs(result)) < 1e-4


def test_sample_points_uniform_minsep_empty():
    rng = np.random.RandomState(0)
    pts = camera.DummyCameraBeads._sample_points_uniform_minsep(
        100, 100, 0, 5, 10, rng,
    )
    assert pts.shape == (0, 2)


def test_sample_points_uniform_minsep_deterministic():
    rng = np.random.RandomState(42)
    pts = camera.DummyCameraBeads._sample_points_uniform_minsep(
        200, 200, 3, 10, 20, rng,
    )
    assert pts.shape == (3, 2)
    assert np.all(pts >= 10) and np.all(pts[:, 0] <= 190) and np.all(pts[:, 1] <= 190)


def test_sample_points_uniform_minsep_margin_too_large():
    rng = np.random.RandomState(0)
    with pytest.raises(ValueError, match="Margin too large"):
        camera.DummyCameraBeads._sample_points_uniform_minsep(
            20, 20, 1, 15, 1, rng,
        )


def test_set_focus_offset_shifts_z(monkeypatch):
    def fake_simulate_beads(xyz, *, nm_per_px, size_px, radius_nm):
        return np.full((size_px, size_px, 1), 0.5, dtype=np.float32)

    monkeypatch.setattr(camera, 'simulate_beads', fake_simulate_beads)
    bead_camera = camera.DummyCameraBeads()
    bead_camera._settings['tethered_n'] = 2
    bead_camera._settings['tethered_z'] = 50.0
    bead_camera._init_tether_state()

    bead_camera.set_focus_offset(25.0)
    assert bead_camera._focus_offset == 25.0
    np.testing.assert_array_equal(bead_camera._z, np.asarray([75.0, 75.0], dtype=np.float32))


def test_dummy_camera_noise_fake_image_shape():
    noise = camera.DummyCameraNoise()
    noise.fake_settings['gain'] = 0.0
    noise.fake_settings['exposure'] = 1.0
    noise.dtype = np.uint16
    noise.width = 16
    noise.height = 8
    img_bytes = noise._fake_image()
    img = np.frombuffer(img_bytes, dtype=np.uint16).reshape(8, 16)
    assert img.shape == (8, 16)


def test_blit_add_no_overlap_returns_early():
    dst = np.zeros((5, 5), dtype=np.float32)
    src = np.ones((2, 2), dtype=np.float32)
    camera.DummyCameraBeads._blit_add(dst, src, 10, 10, w=1.0)
    assert dst.sum() == 0.0


def test_blit_add_fully_out_of_bounds():
    dst = np.zeros((5, 5), dtype=np.float32)
    src = np.ones((2, 2), dtype=np.float32)
    camera.DummyCameraBeads._blit_add(dst, src, -10, -10, w=1.0)
    assert dst.sum() == 0.0


def test_sample_points_relaxation_fallback():
    rng = np.random.RandomState(123)
    pts = camera.DummyCameraBeads._sample_points_uniform_minsep(
        100, 100, 5, 1, 40, rng, max_tries=1, relax=0.8,
    )
    assert pts.shape == (5, 2)


def test_recompute_fixed_delta_zero_beads(monkeypatch):
    def fake_simulate_beads(xyz, *, nm_per_px, size_px, radius_nm):
        return np.full((size_px, size_px, 1), 0.5, dtype=np.float32)

    monkeypatch.setattr(camera, 'simulate_beads', fake_simulate_beads)
    bead_camera = camera.DummyCameraBeads()
    bead_camera._settings['fixed_n'] = 0
    bead_camera._recompute_fixed_delta()
    assert bead_camera._delta_fixed is None


def test_reinit_centers_and_fixed_regular_grid(monkeypatch):
    def fake_simulate_beads(xyz, *, nm_per_px, size_px, radius_nm):
        return np.full((size_px, size_px, 1), 0.5, dtype=np.float32)

    monkeypatch.setattr(camera, 'simulate_beads', fake_simulate_beads)
    bead_camera = camera.DummyCameraBeads()
    bead_camera._settings['fixed_n'] = 2
    bead_camera._settings['fixed_z'] = 10.0
    bead_camera._reinit_centers_and_fixed()
    assert bead_camera._delta_fixed is not None


def test_init_tether_state():
    bead_camera = camera.DummyCameraBeads()
    bead_camera._settings['tethered_n'] = 3
    bead_camera._settings['tethered_z'] = 25.0
    bead_camera._focus_offset = 0.0
    bead_camera._init_tether_state()
    assert bead_camera._xy.shape == (3, 2)
    np.testing.assert_array_equal(bead_camera._z, np.asarray([25.0, 25.0, 25.0], dtype=np.float32))


def test_dummy_camera_beads_get_setting():
    bead_camera = camera.DummyCameraBeads()
    result = bead_camera.get_setting('fixed_n')
    assert isinstance(result, (str, int))


def test_dummy_camera_beads_set_setting_fixed_z(monkeypatch):
    def fake_simulate_beads(xyz, *, nm_per_px, size_px, radius_nm):
        return np.full((size_px, size_px, 1), 0.5, dtype=np.float32)

    monkeypatch.setattr(camera, 'simulate_beads', fake_simulate_beads)
    bead_camera = camera.DummyCameraBeads()
    bead_camera._settings['fixed_n'] = 1
    bead_camera.set_setting('fixed_z', '75.0')
    assert bead_camera._settings['fixed_z'] == 75.0


def test_dummy_camera_beads_set_setting_gain():
    bead_camera = camera.DummyCameraBeads()
    bead_camera.set_setting('gain', '2.5')
    assert bead_camera._settings['gain'] == 2.5


def test_dummy_camera_noise_set_setting_exposure_boundary():
    noise_cam = camera.DummyCameraNoise()
    noise_cam.set_setting('exposure', '0.01')
    assert noise_cam.fake_settings['exposure'] == 0.01


def test_camera_base_abstract_cannot_instantiate():
    with pytest.raises(TypeError):
        camera.CameraBase()
