import unittest
from importlib import util
from multiprocessing import Lock
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path

import numpy as np

MODULE_PATH = Path(__file__).resolve().parents[1] / "magscope" / "datatypes.py"
SPEC = util.spec_from_file_location("magscope.datatypes", MODULE_PATH)
datatypes = util.module_from_spec(SPEC)
SPEC.loader.exec_module(datatypes)  # type: ignore[union-attr]

BufferOverflow = datatypes.BufferOverflow
BufferUnderflow = datatypes.BufferUnderflow
BeadRoiBuffer = datatypes.BeadRoiBuffer
DatasetNotReadyError = datatypes.DatasetNotReadyError
MatrixBuffer = datatypes.MatrixBuffer
VideoBuffer = datatypes.VideoBuffer
ZLUTSweepDataset = datatypes.ZLUTSweepDataset
int_to_uint_dtype = datatypes.int_to_uint_dtype


VIDEO_BUFFER_NAME = "VideoBuffer"
VIDEO_SUFFIXES = [" Info", "", " Timestamps", " Index"]
BEAD_ROI_BUFFER_NAME = "BeadRoiBuffer"
BEAD_ROI_SUFFIXES = [" Info", " Data", " Occupancy"]
ZLUT_SWEEP_DATASET_NAME = "ZLUTSweepDataset"
ZLUT_SWEEP_DATASET_SUFFIXES = [
    " Info",
    " BeadIds",
    " StepIndices",
    " Timestamps",
    " MotorZ",
    " Valid",
    " Profiles",
]


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


def _cleanup_bead_roi_shared_memory():
    for suffix in BEAD_ROI_SUFFIXES:
        try:
            shm = SharedMemory(name=BEAD_ROI_BUFFER_NAME + suffix)
        except FileNotFoundError:
            continue
        else:
            shm.unlink()
            shm.close()


def _cleanup_zlut_sweep_dataset_shared_memory():
    for suffix in ZLUT_SWEEP_DATASET_SUFFIXES:
        try:
            shm = SharedMemory(name=ZLUT_SWEEP_DATASET_NAME + suffix)
        except FileNotFoundError:
            continue
        else:
            shm.unlink()
            shm.close()


class VideoBufferTestCase(unittest.TestCase):
    def setUp(self):
        _cleanup_video_shared_memory()
        self.locks = {VIDEO_BUFFER_NAME: Lock()}
        self.buffer = VideoBuffer(
            create=True,
            locks=self.locks,
            n_stacks=2,
            width=3,
            height=2,
            n_images=2,
            bits=8,
        )

    def tearDown(self):
        buffer = getattr(self, "buffer", None)
        if buffer is not None:
            for attr in ("_shm", "_ts_shm", "_idx_shm", "_shm_info"):
                shm = getattr(buffer, attr, None)
                if shm is not None:
                    shm.close()
                    try:
                        shm.unlink()
                    except FileNotFoundError:
                        pass
        _cleanup_video_shared_memory()


class TestVideoBuffer(VideoBufferTestCase):
    def test_metadata_shared_across_instances(self):
        consumer = VideoBuffer(create=False, locks=self.locks)
        try:
            self.assertEqual(consumer.stack_shape, self.buffer.stack_shape)
            self.assertEqual(consumer.image_shape, self.buffer.image_shape)
            self.assertEqual(consumer.dtype, self.buffer.dtype)
            self.assertEqual(consumer.n_total_images, self.buffer.n_total_images)
        finally:
            for attr in ("_shm", "_ts_shm", "_idx_shm", "_shm_info"):
                getattr(consumer, attr).close()

    def test_write_and_read_image_round_trip(self):
        width, height = self.buffer.image_shape
        raw_first = np.arange(width * height, dtype=self.buffer.dtype)
        expected_first = raw_first.reshape((height, width)).T
        raw_second = raw_first + 50
        expected_second = raw_second.reshape((height, width)).T

        self.buffer.write_image_and_timestamp(raw_first.tobytes(), 1.5)
        self.assertAlmostEqual(self.buffer.get_level(), 1 / self.buffer.n_total_images)

        self.buffer.write_image_and_timestamp(raw_second.tobytes(), 3.0)
        self.assertAlmostEqual(self.buffer.get_level(), 2 / self.buffer.n_total_images)

        restored_first, ts_first = self.buffer.read_image()
        np.testing.assert_array_equal(restored_first, expected_first)
        self.assertAlmostEqual(ts_first, 1.5)

        restored_second, ts_second = self.buffer.read_image()
        np.testing.assert_array_equal(restored_second, expected_second)
        self.assertAlmostEqual(ts_second, 3.0)

        with self.assertRaises(BufferUnderflow):
            self.buffer.read_image()

    def test_peak_stack_returns_unread_frames(self):
        images = []
        timestamps = []
        for idx in range(self.buffer.n_images):
            raw = np.full(self.buffer.image_shape[0] * self.buffer.image_shape[1], fill_value=idx, dtype=self.buffer.dtype)
            expected = raw.reshape((self.buffer.image_shape[1], self.buffer.image_shape[0])).T
            images.append(expected)
            timestamp = float(idx)
            timestamps.append(timestamp)
            self.buffer.write_image_and_timestamp(raw.tobytes(), timestamp)

        stack, stack_timestamps = self.buffer.peak_stack()
        for idx, image in enumerate(images):
            np.testing.assert_array_equal(stack[:, :, idx], image)
        np.testing.assert_allclose(stack_timestamps, np.asarray(timestamps))

    def test_check_read_stack_and_read_stack_no_return(self):
        self.assertFalse(self.buffer.check_read_stack())

        width, height = self.buffer.image_shape
        for idx in range(self.buffer.n_images):
            raw = np.full(width * height, fill_value=idx, dtype=self.buffer.dtype)
            self.buffer.write_image_and_timestamp(raw.tobytes(), float(idx))

        self.assertTrue(self.buffer.check_read_stack())
        self.buffer.read_stack_no_return()
        self.assertFalse(self.buffer.check_read_stack())

    def test_write_overflow_raises(self):
        image = np.ones(self.buffer.image_shape, dtype=self.buffer.dtype)
        for _ in range(self.buffer.n_total_images):
            self.buffer.write_image_and_timestamp(image.tobytes(), 0.0)

        with self.assertRaises(BufferOverflow):
            self.buffer.write_image_and_timestamp(image.tobytes(), 1.0)

    def test_underflow_detection(self):
        with self.assertRaises(BufferUnderflow):
            self.buffer.read_image()


class MatrixBufferTestCase(unittest.TestCase):
    def setUp(self):
        self.name = "MatrixBuffer-Test"
        self.locks = {self.name: Lock()}
        self.buffer = MatrixBuffer(
            create=True,
            locks=self.locks,
            name=self.name,
            shape=(4, 3),
        )

    def tearDown(self):
        buffer = getattr(self, "buffer", None)
        if buffer is not None:
            for attr in ("_shm", "_idx_shm", "_shm_info"):
                shm = getattr(buffer, attr, None)
                if shm is not None:
                    shm.close()
                    try:
                        shm.unlink()
                    except FileNotFoundError:
                        pass


class TestMatrixBuffer(MatrixBufferTestCase):
    def test_metadata_shared_across_instances(self):
        consumer = MatrixBuffer(create=False, locks=self.locks, name=self.name)
        try:
            self.assertEqual(consumer.shape, self.buffer.shape)
            self.assertEqual(consumer.dtype, self.buffer.dtype)
            self.assertEqual(consumer.strides, self.buffer.strides)
        finally:
            for attr in ("_shm", "_idx_shm", "_shm_info"):
                getattr(consumer, attr).close()

    def test_write_and_read_without_wrap(self):
        data = np.arange(2 * self.buffer.shape[1], dtype=self.buffer.dtype).reshape(2, self.buffer.shape[1])
        self.buffer.write(data)
        self.assertEqual(self.buffer.get_count_index(), data.nbytes)

        restored = self.buffer.read()
        np.testing.assert_array_equal(restored, data)
        self.assertEqual(self.buffer.get_count_index(), 0)

    def test_write_wraps_and_read_returns_chronological_order(self):
        first = np.arange(3 * self.buffer.shape[1], dtype=self.buffer.dtype).reshape(3, self.buffer.shape[1])
        self.buffer.write(first)
        _ = self.buffer.read()

        second = (np.arange(3 * self.buffer.shape[1], dtype=self.buffer.dtype) + 100).reshape(3, self.buffer.shape[1])
        self.buffer.write(second)
        restored = self.buffer.read()
        np.testing.assert_array_equal(restored, second)

    def test_peak_sorted_returns_fifo_view(self):
        data = np.arange(2 * self.buffer.shape[1], dtype=self.buffer.dtype).reshape(2, self.buffer.shape[1])
        self.buffer.write(data)
        peak = self.buffer.peak_sorted()
        np.testing.assert_array_equal(peak[-2:], data)

    def test_write_input_validation(self):
        with self.assertRaises(AssertionError):
            self.buffer.write(np.zeros((self.buffer.shape[0] + 1, self.buffer.shape[1]), dtype=self.buffer.dtype))
        with self.assertRaises(AssertionError):
            self.buffer.write(np.zeros((self.buffer.shape[0], self.buffer.shape[1] + 1), dtype=self.buffer.dtype))


class BeadRoiBufferTestCase(unittest.TestCase):
    def setUp(self):
        _cleanup_bead_roi_shared_memory()
        self.locks = {BEAD_ROI_BUFFER_NAME: Lock()}
        self.buffer = BeadRoiBuffer(
            create=True,
            locks=self.locks,
            capacity=8,
        )

    def tearDown(self):
        buffer = getattr(self, "buffer", None)
        if buffer is not None:
            for attr in ("_shm_data", "_shm_occupancy", "_shm_info"):
                shm = getattr(buffer, attr, None)
                if shm is not None:
                    shm.close()
                    try:
                        shm.unlink()
                    except FileNotFoundError:
                        pass
        _cleanup_bead_roi_shared_memory()


class TestBeadRoiBuffer(BeadRoiBufferTestCase):
    def test_metadata_shared_across_instances(self):
        consumer = BeadRoiBuffer(create=False, locks=self.locks)
        try:
            self.assertEqual(consumer.capacity, self.buffer.capacity)
            self.assertEqual(consumer.max_id_plus_one, self.buffer.max_id_plus_one)
            self.assertEqual(consumer.active_count, self.buffer.active_count)
        finally:
            for attr in ("_shm_data", "_shm_occupancy", "_shm_info"):
                getattr(consumer, attr).close()

    def test_replace_and_get_beads_round_trip(self):
        self.buffer.replace_beads({1: (10, 20, 30, 40), 4: (50, 60, 70, 80)})

        bead_ids, bead_rois = self.buffer.get_beads()

        np.testing.assert_array_equal(bead_ids, np.array([1, 4], dtype=np.uint32))
        np.testing.assert_array_equal(
            bead_rois,
            np.array([[10, 20, 30, 40], [50, 60, 70, 80]], dtype=np.uint32),
        )
        self.assertEqual(self.buffer.active_count, 2)
        self.assertEqual(self.buffer.max_id_plus_one, 5)

    def test_add_update_remove_and_clear(self):
        self.buffer.add_beads({2: (1, 2, 3, 4)})
        self.assertEqual(self.buffer.get_next_available_bead_id(), 3)

        self.buffer.update_beads({2: (11, 12, 13, 14)})
        bead_ids, bead_rois = self.buffer.get_beads()
        np.testing.assert_array_equal(bead_ids, np.array([2], dtype=np.uint32))
        np.testing.assert_array_equal(bead_rois, np.array([[11, 12, 13, 14]], dtype=np.uint32))

        self.buffer.remove_beads([2])
        bead_ids, bead_rois = self.buffer.get_beads()
        self.assertEqual(bead_ids.size, 0)
        self.assertEqual(bead_rois.shape, (0, 4))
        self.assertEqual(self.buffer.get_next_available_bead_id(), 3)

        self.buffer.clear_beads()
        self.assertEqual(self.buffer.get_next_available_bead_id(), 0)
        self.assertEqual(self.buffer.active_count, 0)

    def test_reorder_beads_compacts_ids(self):
        self.buffer.replace_beads({1: (10, 20, 30, 40), 3: (50, 60, 70, 80)})

        mapping = self.buffer.reorder_beads()
        bead_ids, bead_rois = self.buffer.get_beads()

        self.assertEqual(mapping, {1: 0, 3: 1})
        np.testing.assert_array_equal(bead_ids, np.array([0, 1], dtype=np.uint32))
        np.testing.assert_array_equal(
            bead_rois,
            np.array([[10, 20, 30, 40], [50, 60, 70, 80]], dtype=np.uint32),
        )

    def test_validation_rejects_invalid_ids(self):
        with self.assertRaises(ValueError):
            self.buffer.add_beads({8: (1, 2, 3, 4)})


class ZLUTSweepDatasetTestCase(unittest.TestCase):
    def setUp(self):
        _cleanup_zlut_sweep_dataset_shared_memory()
        self.locks = {ZLUT_SWEEP_DATASET_NAME: Lock()}
        self.dataset = ZLUTSweepDataset.create(
            locks=self.locks,
            capacity=6,
            profile_length=4,
            n_steps=3,
            n_beads=2,
            profiles_per_bead=1,
        )

    def tearDown(self):
        dataset = getattr(self, "dataset", None)
        if dataset is not None:
            try:
                dataset.destroy()
            except (FileNotFoundError, RuntimeError):
                dataset.close()
        _cleanup_zlut_sweep_dataset_shared_memory()


class TestZLUTSweepDataset(ZLUTSweepDatasetTestCase):
    def test_metadata_shared_across_instances(self):
        consumer = ZLUTSweepDataset.attach(locks=self.locks)
        try:
            self.assertEqual(consumer.get_capacity(), self.dataset.get_capacity())
            self.assertEqual(consumer.profile_length, self.dataset.profile_length)
            self.assertEqual(consumer.n_steps, self.dataset.n_steps)
            self.assertEqual(consumer.n_beads, self.dataset.n_beads)
            self.assertEqual(consumer.profiles_per_bead, self.dataset.profiles_per_bead)
            self.assertEqual(consumer.state, ZLUTSweepDataset.STATE_READY)
        finally:
            consumer.close()

    def test_write_and_peak_round_trip(self):
        self.dataset.write(
            bead_ids=np.array([3, 4], dtype=np.uint32),
            step_indices=np.array([0, 1], dtype=np.uint32),
            timestamps=np.array([1.5, 2.5], dtype=np.float64),
            motor_z_values=np.array([10.0, 20.0], dtype=np.float64),
            valid_flags=np.array([1, 0], dtype=np.uint8),
            profiles=np.array(
                [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]],
                dtype=np.float64,
            ),
        )

        self.assertEqual(self.dataset.get_count(), 2)
        data = self.dataset.peak()

        np.testing.assert_array_equal(data['bead_ids'], np.array([3, 4], dtype=np.uint32))
        np.testing.assert_array_equal(data['step_indices'], np.array([0, 1], dtype=np.uint32))
        np.testing.assert_allclose(data['timestamps'], np.array([1.5, 2.5], dtype=np.float64))
        np.testing.assert_allclose(data['motor_z_values'], np.array([10.0, 20.0], dtype=np.float64))
        np.testing.assert_array_equal(data['valid_flags'], np.array([1, 0], dtype=np.uint8))
        np.testing.assert_allclose(
            data['profiles'],
            np.array([[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]], dtype=np.float64),
        )

    def test_write_overflow_raises(self):
        self.dataset.write(
            bead_ids=np.arange(6, dtype=np.uint32),
            step_indices=np.arange(6, dtype=np.uint32),
            timestamps=np.arange(6, dtype=np.float64),
            motor_z_values=np.arange(6, dtype=np.float64),
            valid_flags=np.ones(6, dtype=np.uint8),
            profiles=np.ones((6, 4), dtype=np.float64),
        )

        with self.assertRaises(BufferOverflow):
            self.dataset.write(
                bead_ids=np.array([9], dtype=np.uint32),
                step_indices=np.array([9], dtype=np.uint32),
                timestamps=np.array([9.0], dtype=np.float64),
                motor_z_values=np.array([9.0], dtype=np.float64),
                valid_flags=np.array([1], dtype=np.uint8),
                profiles=np.array([[9.0, 9.0, 9.0, 9.0]], dtype=np.float64),
            )

    def test_write_shape_validation(self):
        with self.assertRaises(ValueError):
            self.dataset.write(
                bead_ids=np.array([1, 2], dtype=np.uint32),
                step_indices=np.array([0], dtype=np.uint32),
                timestamps=np.array([1.0, 2.0], dtype=np.float64),
                motor_z_values=np.array([1.0, 2.0], dtype=np.float64),
                valid_flags=np.array([1, 1], dtype=np.uint8),
                profiles=np.ones((2, 4), dtype=np.float64),
            )

        with self.assertRaises(ValueError):
            self.dataset.write(
                bead_ids=np.array([1, 2], dtype=np.uint32),
                step_indices=np.array([0, 1], dtype=np.uint32),
                timestamps=np.array([1.0, 2.0], dtype=np.float64),
                motor_z_values=np.array([1.0, 2.0], dtype=np.float64),
                valid_flags=np.array([1, 1], dtype=np.uint8),
                profiles=np.ones((2, 3), dtype=np.float64),
            )

    def test_non_owner_cannot_destroy(self):
        consumer = ZLUTSweepDataset.attach(locks=self.locks)
        try:
            with self.assertRaises(RuntimeError):
                consumer.destroy()
        finally:
            consumer.close()

    def test_destroy_removes_shared_memory(self):
        self.dataset.destroy()
        self.dataset = None

        with self.assertRaises(DatasetNotReadyError):
            ZLUTSweepDataset.attach(locks=self.locks)


class TestIntToUintDtype(unittest.TestCase):
    def test_success_and_failure(self):
        self.assertEqual(int_to_uint_dtype(8), np.uint8)
        self.assertEqual(int_to_uint_dtype(16), np.uint16)
        self.assertEqual(int_to_uint_dtype(32), np.uint32)
        self.assertEqual(int_to_uint_dtype(64), np.uint64)
        with self.assertRaises(ValueError):
            int_to_uint_dtype(12)
