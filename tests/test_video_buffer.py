import uuid

import numpy as np
import pytest
from multiprocessing import Lock
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
from importlib import util

MODULE_PATH = Path(__file__).resolve().parents[1] / "magscope" / "datatypes.py"
SPEC = util.spec_from_file_location("magscope.datatypes", MODULE_PATH)
datatypes = util.module_from_spec(SPEC)
SPEC.loader.exec_module(datatypes)  # type: ignore[union-attr]

BufferOverflow = datatypes.BufferOverflow
BufferUnderflow = datatypes.BufferUnderflow
MatrixBuffer = datatypes.MatrixBuffer
VideoBuffer = datatypes.VideoBuffer
int_to_uint_dtype = datatypes.int_to_uint_dtype


VIDEO_BUFFER_NAME = "VideoBuffer"
VIDEO_SUFFIXES = [" Info", "", " Timestamps", " Index"]


def _cleanup_video_shared_memory():
    """Remove any lingering shared-memory segments for ``VideoBuffer``."""
    for suffix in VIDEO_SUFFIXES:
        try:
            shm = SharedMemory(name=VIDEO_BUFFER_NAME + suffix)
        except FileNotFoundError:
            continue
        else:
            shm.unlink()
            shm.close()


@pytest.fixture
def video_buffer():
    _cleanup_video_shared_memory()
    locks = {VIDEO_BUFFER_NAME: Lock()}
    buffer = VideoBuffer(
        create=True,
        locks=locks,
        n_stacks=2,
        width=3,
        height=2,
        n_images=2,
        bits=8,
    )
    try:
        yield buffer, locks
    finally:
        for attr in ("_shm", "_ts_shm", "_idx_shm", "_shm_info"):
            shm = getattr(buffer, attr, None)
            if shm is not None:
                shm.close()
                try:
                    shm.unlink()
                except FileNotFoundError:
                    pass
        _cleanup_video_shared_memory()


@pytest.fixture
def matrix_buffer():
    name = f"MatrixBuffer-{uuid.uuid4()}"
    locks = {name: Lock()}
    buffer = MatrixBuffer(
        create=True,
        locks=locks,
        name=name,
        shape=(4, 3),
    )
    try:
        yield buffer, locks, name
    finally:
        for attr in ("_shm", "_idx_shm", "_shm_info"):
            shm = getattr(buffer, attr, None)
            if shm is not None:
                shm.close()
                try:
                    shm.unlink()
                except FileNotFoundError:
                    pass


class TestVideoBuffer:
    def test_metadata_shared_across_instances(self, video_buffer):
        buffer, locks = video_buffer
        consumer = VideoBuffer(create=False, locks=locks)
        try:
            assert consumer.stack_shape == buffer.stack_shape
            assert consumer.image_shape == buffer.image_shape
            assert consumer.dtype == buffer.dtype
            assert consumer.n_total_images == buffer.n_total_images
        finally:
            for attr in ("_shm", "_ts_shm", "_idx_shm", "_shm_info"):
                getattr(consumer, attr).close()

    def test_write_and_read_image_round_trip(self, video_buffer):
        buffer, _ = video_buffer
        width, height = buffer.image_shape
        raw_first = np.arange(width * height, dtype=buffer.dtype)
        expected_first = raw_first.reshape((height, width)).T
        raw_second = raw_first + 50
        expected_second = raw_second.reshape((height, width)).T

        buffer.write_image_and_timestamp(raw_first.tobytes(), 1.5)
        assert buffer.get_level() == pytest.approx(1 / buffer.n_total_images)

        buffer.write_image_and_timestamp(raw_second.tobytes(), 3.0)
        assert buffer.get_level() == pytest.approx(2 / buffer.n_total_images)

        restored_first, ts_first = buffer.read_image()
        np.testing.assert_array_equal(restored_first, expected_first)
        assert ts_first == pytest.approx(1.5)

        restored_second, ts_second = buffer.read_image()
        np.testing.assert_array_equal(restored_second, expected_second)
        assert ts_second == pytest.approx(3.0)

        with pytest.raises(BufferUnderflow):
            buffer.read_image()

    def test_peak_stack_returns_unread_frames(self, video_buffer):
        buffer, _ = video_buffer
        images = []
        timestamps = []
        for idx in range(buffer.n_images):
            raw = np.full(buffer.image_shape[0] * buffer.image_shape[1], fill_value=idx, dtype=buffer.dtype)
            expected = raw.reshape((buffer.image_shape[1], buffer.image_shape[0])).T
            images.append(expected)
            timestamp = float(idx)
            timestamps.append(timestamp)
            buffer.write_image_and_timestamp(raw.tobytes(), timestamp)

        stack, stack_timestamps = buffer.peak_stack()
        for idx, image in enumerate(images):
            np.testing.assert_array_equal(stack[:, :, idx], image)
        np.testing.assert_allclose(stack_timestamps, np.asarray(timestamps))

    def test_check_read_stack_and_read_stack_no_return(self, video_buffer):
        buffer, _ = video_buffer
        assert buffer.check_read_stack() is False

        width, height = buffer.image_shape
        for idx in range(buffer.n_images):
            raw = np.full(width * height, fill_value=idx, dtype=buffer.dtype)
            buffer.write_image_and_timestamp(raw.tobytes(), float(idx))

        assert buffer.check_read_stack() is True
        buffer.read_stack_no_return()
        assert buffer.check_read_stack() is False

    def test_write_overflow_raises(self, video_buffer):
        buffer, _ = video_buffer
        image = np.ones(buffer.image_shape, dtype=buffer.dtype)
        for _ in range(buffer.n_total_images):
            buffer.write_image_and_timestamp(image.tobytes(), 0.0)

        with pytest.raises(BufferOverflow):
            buffer.write_image_and_timestamp(image.tobytes(), 1.0)

    def test_underflow_detection(self, video_buffer):
        buffer, _ = video_buffer
        with pytest.raises(BufferUnderflow):
            buffer.read_image()


class TestMatrixBuffer:
    def test_metadata_shared_across_instances(self, matrix_buffer):
        buffer, locks, name = matrix_buffer
        consumer = MatrixBuffer(create=False, locks=locks, name=name)
        try:
            assert consumer.shape == buffer.shape
            assert consumer.dtype == buffer.dtype
            assert consumer.strides == buffer.strides
        finally:
            for attr in ("_shm", "_idx_shm", "_shm_info"):
                getattr(consumer, attr).close()

    def test_write_and_read_without_wrap(self, matrix_buffer):
        buffer, _, _ = matrix_buffer
        data = np.arange(2 * buffer.shape[1], dtype=buffer.dtype).reshape(2, buffer.shape[1])
        buffer.write(data)
        assert buffer.get_count_index() == data.nbytes

        restored = buffer.read()
        np.testing.assert_array_equal(restored, data)
        assert buffer.get_count_index() == 0

    def test_write_wraps_and_read_returns_chronological_order(self, matrix_buffer):
        buffer, _, _ = matrix_buffer
        first = np.arange(3 * buffer.shape[1], dtype=buffer.dtype).reshape(3, buffer.shape[1])
        buffer.write(first)
        _ = buffer.read()

        second = (np.arange(3 * buffer.shape[1], dtype=buffer.dtype) + 100).reshape(3, buffer.shape[1])
        buffer.write(second)
        restored = buffer.read()
        np.testing.assert_array_equal(restored, second)

    def test_peak_sorted_returns_fifo_view(self, matrix_buffer):
        buffer, _, _ = matrix_buffer
        data = np.arange(2 * buffer.shape[1], dtype=buffer.dtype).reshape(2, buffer.shape[1])
        buffer.write(data)
        peak = buffer.peak_sorted()
        np.testing.assert_array_equal(peak[-2:], data)

    def test_write_input_validation(self, matrix_buffer):
        buffer, _, _ = matrix_buffer
        with pytest.raises(AssertionError):
            buffer.write(np.zeros((buffer.shape[0] + 1, buffer.shape[1]), dtype=buffer.dtype))
        with pytest.raises(AssertionError):
            buffer.write(np.zeros((buffer.shape[0], buffer.shape[1] + 1), dtype=buffer.dtype))


def test_int_to_uint_dtype_success_and_failure():
    assert int_to_uint_dtype(8) == np.uint8
    assert int_to_uint_dtype(16) == np.uint16
    assert int_to_uint_dtype(32) == np.uint32
    assert int_to_uint_dtype(64) == np.uint64
    with pytest.raises(ValueError):
        int_to_uint_dtype(12)
