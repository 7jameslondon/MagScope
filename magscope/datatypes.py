"""Shared-memory buffers used across MagScope.

This module introduces two circular buffers that let different processes share
camera frames and other numeric data without copying large arrays:

``VideoBuffer``
    Stores stacks of images in one shared-memory region together with capture
    timestamps. The class is designed for a producer process that records
    frames and one or more consumer processes that read them.

``MatrixBuffer``
    Stores general two-dimensional numeric data such as bead positions or
    motor telemetry. Like :class:`VideoBuffer`, it uses shared memory.

Both buffers rely on external :class:`multiprocessing.synchronize.Lock`
objects to coordinate access between processes. See the class docstrings
below for usage details.
"""

import struct
from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Lock

import numpy as np

from ._logging import get_logger

logger = get_logger("datatypes")

class VideoBuffer:
    """Shared memory ring buffer for video data

    Parameters
    ----------
    create : bool
        ``True`` to allocate the shared-memory regions; ``False`` to attach to
        an existing buffer.
    locks : dict[str, Lock]
        Mapping of buffer names to :class:`multiprocessing.Lock` instances. The
        dictionary must contain an entry for ``VideoBuffer``.
    n_stacks : int, optional
        Number of temporal stacks stored in the buffer. Required when
        ``create`` is ``True``.
    width : int, optional
        Frame width in pixels. Required when ``create`` is ``True``.
    height : int, optional
        Frame height in pixels. Required when ``create`` is ``True``.
    n_images : int, optional
        Number of frames per stack. Required when ``create`` is ``True``.
    bits : int, optional
        Bit depth of each pixel. Required when ``create`` is ``True``.

    Notes
    -----
    The buffer should first be created by a process with ``create=True``. When
    creating, ``n_stacks``, ``width``, ``height``, ``n_images`` and ``bits``
    must be provided. After the shared memory exists, other processes can
    access the buffer with ``create=False``.
    """

    def __init__(self, *,
                 create: bool,
                 locks: dict[str, Lock],
                 n_stacks: int|None=None,
                 width: int|None=None,
                 height: int|None=None,
                 n_images: int|None=None,
                 bits: int|None=None):
        self.name: str = type(self).__name__
        self.lock: Lock = locks[self.name]

        # Some meta-data to describe the buffer is stored in the shared memory
        # along with the buffer itself. The first creator writes that metadata,
        # and subsequent processes read the stored values so they can interpret
        # the underlying byte buffers.
        self._shm_info = SharedMemory(
            create=create, name=self.name + ' Info', size=8 * 5)
        if create:
            if any(param is None for param in [n_stacks, width, height, n_images, bits]):
                raise ValueError("VideoBuffer misconfigured")
            self.n_stacks = n_stacks
            self._shm_info.buf[0:8] = int(n_stacks).to_bytes(8, byteorder='big')
            self._shm_info.buf[8:16] = int(width).to_bytes(8, byteorder='big')
            self._shm_info.buf[16:24] = int(height).to_bytes(8, byteorder='big')
            self._shm_info.buf[24:32] = int(n_images).to_bytes(8, byteorder='big')
            self._shm_info.buf[32:40] = int(bits).to_bytes(8, byteorder='big')
        else:
            self.n_stacks = int.from_bytes(self._shm_info.buf[0:8], byteorder='big')
            width = int.from_bytes(self._shm_info.buf[8:16], byteorder='big')
            height = int.from_bytes(self._shm_info.buf[16:24], byteorder='big')
            n_images = int.from_bytes(self._shm_info.buf[24:32], byteorder='big')
            bits = int.from_bytes(self._shm_info.buf[32:40], byteorder='big')

        # Setup more meta-data
        self.stack_shape = (width, height, n_images)
        self.image_shape = (width, height)
        self.dtype = int_to_uint_dtype(bits)
        self.itemsize = np.dtype(self.dtype).itemsize
        self.n_images = n_images
        self.n_total_images = self.n_images * self.n_stacks
        self.image_size = width * height * self.itemsize
        self.stack_size = self.image_size * self.n_images
        self.buffer_size = self.stack_size * self.n_stacks

        if create:
            logger.info('Creating VideoBuffer with size %s MB', self.buffer_size / 1e6)

        # Setup the buffer and buffer indexes
        self._shm = SharedMemory(
            create=create, name=self.name, size=self.buffer_size)
        self._ts_shm = SharedMemory(
            create=create,
            name=self.name + ' Timestamps',
            size=8 * self.n_total_images)
        self._idx_shm = SharedMemory(
            create=create, name=self.name + ' Index', size=24)
        self._buf = self._shm.buf
        self._ts_buf = self._ts_shm.buf
        self._idx_buf = self._idx_shm.buf

        # Initialise the buffer and indexes when creating for the first time
        if create:
            self._set_read_index(0)
            self._set_write_index(0)
            self._set_count_index(0)

    def __del__(self):
        if hasattr(self, '_shm'):
            self._shm.close()
        if hasattr(self, '_idx_shm'):
            self._idx_shm.close()
        if hasattr(self, '_shm_info'):
            self._shm_info.close()

    def _get_count_index(self):
        return int.from_bytes(self._idx_buf[16:24], byteorder='big')

    def _get_read_index(self):
        return int.from_bytes(self._idx_buf[0:8], byteorder='big')

    def _get_write_index(self):
        return int.from_bytes(self._idx_buf[8:16], byteorder='big')

    def _set_count_index(self, value):
        self._idx_buf[16:24] = int(value).to_bytes(8, byteorder='big')

    def _set_read_index(self, value):
        value = value % self.n_total_images
        self._idx_buf[0:8] = int(value).to_bytes(8, byteorder='big')

    def _set_write_index(self, value):
        value = value % self.n_total_images
        self._idx_buf[8:16] = int(value).to_bytes(8, byteorder='big')

    def _check_read(self, value):
        if value > self._get_count_index():
            raise BufferUnderflow('BufferUnderflow')

    def _check_write(self, value):
        if value > (self.n_total_images - self._get_count_index()):
            raise BufferOverflow('BufferOverflow')

    def _get_timestamps(self, read, length):
        buf = self._ts_buf[(read * 8):((read + length) * 8)]
        return np.ndarray((length, ), dtype='float64', buffer=buf)

    def _set_timestamp(self, write, timestamp):
        self._ts_buf[(write * 8):((write + 1) * 8)] = struct.pack(
            'd', timestamp)

    def get_level(self):
        """Return the fraction of the buffer that currently holds data.

        Returns
        -------
        float
            Ratio between unread frames and total buffer capacity.
        """
        with self.lock:
            return self._get_count_index() / self.n_total_images

    def check_read_stack(self):
        """Return ``True`` when at least one full stack can be read.

        Returns
        -------
        bool
            ``True`` if ``n_images`` frames are available to read; ``False``
            otherwise.
        """
        with self.lock:
            try:
                self._check_read(self.n_images)
            except BufferUnderflow:
                return False
            else:
                return True

    def peak_image(self):
        """Return the newest image and its index without acquiring the lock.

        This helper supports lightweight live previews. Because the method does
        not acquire the lock, it may occasionally return a partially written
        frame or an older image.

        Returns
        -------
        tuple of (int, memoryview)
            Tuple containing the newest image index and a memory view of the
            image bytes. Convert the memory view to a 2D array with
            ``dtype`` and ``image_shape``.
        """
        read = (self._get_write_index() - 1) % self.n_total_images
        return read, self._buf[(read * self.image_size):((read + 1) *
                                                         self.image_size)]

    def peak_stack(self):
        """Return the next unread stack without advancing the read index.

        Returns
        -------
        tuple of numpy.ndarray
            ``(stack, timestamps)`` where ``stack`` has shape
            ``(width, height, n_images)`` and ``timestamps`` is a ``float64``
            array aligned with the returned frames.
        """
        with self.lock:
            self._check_read(self.n_images)
            read = self._get_read_index()
            stack_bytes = self._buf[(read *
                                     self.image_size):((read + self.n_images) *
                                                       self.image_size)]
            # Transposed stack, axes=(T,Y,X)
            trans_stack = np.ndarray(self.stack_shape[::-1],
                                     dtype=self.dtype,
                                     buffer=stack_bytes)
            # Stack, axes=(X,Y,T)
            stack = trans_stack.transpose(2, 1, 0)
            timestamps = self._get_timestamps(read, self.n_images)
            return stack, timestamps

    def read_stack_no_return(self):
        """Advance the read index by one stack without returning data.

        Returns
        -------
        None
            This method updates the internal indices but produces no data.
        """
        with self.lock:
            self._check_read(self.n_images)
            read = self._get_read_index()
            count = self._get_count_index()
            self._set_read_index(read + self.n_images)
            self._set_count_index(count - self.n_images)

    def read_image(self):
        """Return the next unread image and its timestamp.

        Returns
        -------
        tuple of (numpy.ndarray, float)
            Tuple consisting of the next unread frame as a 2D array with shape
            ``(width, height)`` and the corresponding timestamp in seconds.
        """
        with self.lock:
            self._check_read(1)
            read = self._get_read_index()
            count = self._get_count_index()
            self._set_read_index(read + 1)
            self._set_count_index(count - 1)
            image_bytes = self._buf[(read * self.image_size):((read + 1) *
                                                              self.image_size)]
            trans_image = np.ndarray(self.image_shape[::-1],
                                     dtype=self.dtype,
                                     buffer=image_bytes)
            image = trans_image.transpose(1, 0)
            timestamp = self._get_timestamps(read, 1)[0]
            return image, timestamp

    def write_timestamp(self, timestamp):
        """Increment the write index and store a timestamp without frame data.

        Parameters
        ----------
        timestamp : float
            Timestamp in seconds that should be associated with the next frame
            slot.
        """
        with self.lock:
            self._check_write(1)
            write = self._get_write_index()
            count = self._get_count_index()
            self._set_timestamp(write, timestamp)
            self._set_write_index(write + 1)
            self._set_count_index(count + 1)

    def write_image_and_timestamp(self, image, timestamp):
        """Increment the write index, storing one image and its timestamp.

        Parameters
        ----------
        image : numpy.ndarray
            Frame data shaped ``(width, height)`` with the buffer's ``dtype``.
        timestamp : float
            Timestamp in seconds associated with the frame.
        """
        with self.lock:
            self._check_write(1)
            write = self._get_write_index()
            count = self._get_count_index()
            self._buf[(write * self.image_size):((write + 1) *
                                                 self.image_size)] = image
            self._set_timestamp(write, timestamp)
            self._set_write_index(write + 1)
            self._set_count_index(count + 1)

class MatrixBuffer:
    """Shared-memory ring buffer for 2D numeric data.

    Parameters
    ----------
    create : bool
        ``True`` to allocate the shared-memory regions; ``False`` to attach to
        an existing buffer.
    locks : dict[str, Lock]
        Mapping of buffer names to :class:`multiprocessing.Lock` instances. The
        dictionary must contain an entry for ``name``.
    name : str
        Identifier used for the shared-memory segments.
    shape : tuple[int, int], optional
        Buffer shape expressed as ``(rows, columns)``. Required when
        ``create`` is ``True``.

    Notes
    -----
    The buffer stores time-series style data where each row is a timestamp and
    each column is a measurement. Reads consume unread bytes, while ``peak``
    helpers provide views without advancing indices.
    """

    def __init__(self, *,
                 create: bool,
                 locks: dict[str, Lock],
                 name: str,
                 shape: tuple[int, int]=None):
        self.name: str = name
        self.lock: Lock = locks[self.name]

        # Some meta-data to describe the buffer is stored in the shared memory
        # along with the buffer itself. The first creator writes that metadata,
        # and subsequent processes read the stored values so they can interpret
        # the underlying byte buffers.
        self._shm_info = SharedMemory(
            create=create, name=self.name + ' Info', size=8 * 2)
        if create:
            if shape is None:
                raise ValueError('shape must be specified when creating a MatrixBuffer')
            self.shape = shape
            r: int = self.shape[0]
            c: int = self.shape[1]
            self._shm_info.buf[0:8] = int(r).to_bytes(8, byteorder='big')
            self._shm_info.buf[8:16] = int(c).to_bytes(8, byteorder='big')
        else:
            r: int = int.from_bytes(self._shm_info.buf[0:8], byteorder='big')
            c: int = int.from_bytes(self._shm_info.buf[8:16], byteorder='big')
            self.shape: tuple[int, int] = (r, c)

        # Setup more meta-data
        self.dtype: np.dtype = np.dtype(np.float64)
        self.itemsize: int = self.dtype.itemsize
        self.strides: tuple[int, int] = (self.shape[1] * self.itemsize, self.itemsize)
        self.nbytes: int = self.shape[0] * self.shape[1] * self.itemsize

        # Setup the buffer and buffer indexes
        self._shm = SharedMemory(
            create=create, name=self.name, size=self.nbytes)
        self._idx_shm = SharedMemory(
            create=create, name=self.name + ' Index', size=24)
        self._buf = self._shm.buf
        self._idx_buf = self._idx_shm.buf

        # Initialise the buffer and indexes when creating for the first time
        if create:
            self._set_read_index(0)
            self._set_write_index(0)
            self._set_count_index(0)
            self.write(np.ones(shape, dtype=self.dtype) + np.nan)
            self._set_count_index(0)

    def __del__(self):
        self._shm.close()
        self._idx_shm.close()

    def _get_count_index(self):
        return int.from_bytes(self._idx_buf[16:24], byteorder='big')

    def _get_read_index(self):
        return int.from_bytes(self._idx_buf[0:8], byteorder='big')

    def _get_write_index(self):
        return int.from_bytes(self._idx_buf[8:16], byteorder='big')

    def _set_count_index(self, value):
        self._idx_buf[16:24] = int(value).to_bytes(8, byteorder='big')

    def _set_read_index(self, value):
        value = value % self.nbytes
        self._idx_buf[0:8] = int(value).to_bytes(8, byteorder='big')

    def _set_write_index(self, value):
        value = value % self.nbytes
        self._idx_buf[8:16] = int(value).to_bytes(8, byteorder='big')

    def get_count_index(self):
        """Return the number of unread bytes currently stored in the buffer.

        Returns
        -------
        int
            Byte count representing unread data between the read and write
            indices.
        """
        with self.lock:
            return self._get_count_index()

    def get_read_index(self):
        """Return the index of the next byte that will be read.

        Returns
        -------
        int
            Position within the shared buffer corresponding to the next read
            operation.
        """
        with self.lock:
            return self._get_read_index()

    def get_write_index(self):
        """Return the index of the next byte that will be written.

        Returns
        -------
        int
            Position within the shared buffer corresponding to the next write
            operation.
        """
        with self.lock:
            return self._get_write_index()

    def write(self, np_array):
        """Write ``np_array`` into the buffer, advancing the write index.

        Parameters
        ----------
        np_array : numpy.ndarray
            Array with ``shape[1]`` columns. Rows may wrap around to the start
            of the buffer if the write reaches the end of the allocated space.
        """
        assert np_array.shape[0] <= self.shape[0]
        assert np_array.shape[1] == self.shape[1]
        with self.lock:
            write = self._get_write_index()
            count = self._get_count_index()
            r = min(np_array.nbytes, self.nbytes - write)
            l = np_array.nbytes - r
            self._buf[write:(write + r)] = np.ravel(np_array).view('uint8')[0:r].tobytes()  # right
            self._buf[0:l] = np.ravel(np_array).view('uint8')[r:].tobytes()  # left
            self._set_write_index(write + np_array.nbytes)
            self._set_count_index(count + np_array.nbytes)

    def read(self):
        """Return unread rows as a NumPy array and reset the read counter.

        Returns
        -------
        numpy.ndarray
            Copy of the unread rows ordered chronologically.
        """
        with self.lock:
            count = self._get_count_index()
            read = self._get_read_index()
            write = self._get_write_index()
            assert count >= 0
            self._set_read_index(read + count)
            self._set_count_index(0)

            # Does the unread portion wrap around the end of the _buf
            if read <= write:  # no wrap
                n = count // self.shape[1] // self.itemsize
                return np.ndarray(shape=(n, self.shape[1]),
                                  dtype=self.dtype,
                                  buffer=self._buf[read:(read +
                                                         count)]).copy()
            else:  # wrap
                right = self._buf[read:self.nbytes]
                left = self._buf[0:write]
                r = len(right) // self.shape[1] // self.itemsize
                l = len(left) // self.shape[1] // self.itemsize
                np_array_right = np.ndarray(shape=(r, self.shape[1]),
                                            dtype=self.dtype,
                                            buffer=right)
                np_array_left = np.ndarray(shape=(l, self.shape[1]),
                                           dtype=self.dtype,
                                           buffer=left)
                return np.vstack((np_array_right, np_array_left)).copy()

    def peak_unsorted(self):
        """Return a view of the buffer without reordering indices.

        Returns
        -------
        numpy.ndarray
            View into the shared memory representing the buffer layout.
        """
        with self.lock:
            return np.ndarray(self.shape, dtype=self.dtype, buffer=self._buf)

    def peak_sorted(self):
        """Return the buffer contents ordered chronologically.

        Returns
        -------
        numpy.ndarray
            Array containing the buffer rows in FIFO order without updating
            indices.
        """
        with self.lock:
            write = self._get_write_index()
            right = self._buf[write:self.nbytes]
            left = self._buf[0:write]
            r = int(len(right) / self.shape[1] / self.itemsize)
            l = self.shape[0] - r
            np_array_right = np.ndarray((r, self.shape[1]),
                                        dtype=self.dtype,
                                        buffer=right)
            np_array_left = np.ndarray((l, self.shape[1]),
                                       dtype=self.dtype,
                                       buffer=left)
            return np.vstack((np_array_right, np_array_left))


class BeadRoiBuffer:
    """Shared-memory store for bead ROI metadata.

    The buffer uses a fixed row per bead id so readers can attach once and take
    compact snapshots of active ROIs without exchanging Python dictionaries over
    IPC.
    """

    def __init__(
        self,
        *,
        create: bool,
        locks: dict[str, Lock],
        capacity: int | None = None,
        name: str = 'BeadRoiBuffer',
    ):
        self.name = name
        self.lock: Lock = locks[self.name]
        self._info_fields = 4
        self._info_size = 8 * self._info_fields
        self._roi_dtype = np.dtype(np.uint32)
        self._occupancy_dtype = np.dtype(np.uint8)

        self._shm_info = SharedMemory(
            create=create,
            name=self.name + ' Info',
            size=self._info_size,
        )

        if create:
            if capacity is None:
                raise ValueError('capacity must be provided when creating BeadRoiBuffer')
            self._write_info(0, int(capacity))
            self._write_info(1, 0)
            self._write_info(2, 0)
            self._write_info(3, 0)
        elif capacity is not None and capacity != self._read_info(0):
            raise ValueError('capacity does not match existing BeadRoiBuffer')

        self.capacity = self._read_info(0)
        self._roi_shape = (self.capacity, 4)
        self._roi_nbytes = int(np.prod(self._roi_shape)) * self._roi_dtype.itemsize
        self._occupancy_nbytes = self.capacity * self._occupancy_dtype.itemsize

        self._shm_data = SharedMemory(
            create=create,
            name=self.name + ' Data',
            size=self._roi_nbytes,
        )
        self._shm_occupancy = SharedMemory(
            create=create,
            name=self.name + ' Occupancy',
            size=self._occupancy_nbytes,
        )
        self._roi_matrix = np.ndarray(self._roi_shape, dtype=self._roi_dtype, buffer=self._shm_data.buf)
        self._occupancy = np.ndarray((self.capacity,), dtype=self._occupancy_dtype, buffer=self._shm_occupancy.buf)

        if create:
            self._roi_matrix.fill(0)
            self._occupancy.fill(0)

    def __del__(self):
        if hasattr(self, '_shm_data'):
            self._shm_data.close()
        if hasattr(self, '_shm_occupancy'):
            self._shm_occupancy.close()
        if hasattr(self, '_shm_info'):
            self._shm_info.close()

    @property
    def max_id_plus_one(self) -> int:
        with self.lock:
            return self._read_info(1)

    @property
    def active_count(self) -> int:
        with self.lock:
            return self._read_info(2)

    @property
    def version(self) -> int:
        with self.lock:
            return self._read_info(3)

    def replace_beads(self, value: dict[int, tuple[int, int, int, int]]) -> None:
        validated = self._normalize_bead_mapping(value)
        with self.lock:
            self._roi_matrix.fill(0)
            self._occupancy.fill(0)
            if validated:
                bead_ids = np.fromiter(validated.keys(), dtype=np.uint32, count=len(validated))
                rois = np.asarray(list(validated.values()), dtype=self._roi_dtype)
                self._roi_matrix[bead_ids] = rois
                self._occupancy[bead_ids] = 1
                max_id_plus_one = int(bead_ids.max()) + 1
            else:
                max_id_plus_one = 0
            self._write_info(1, max_id_plus_one)
            self._write_info(2, len(validated))
            self._increment_version()

    def add_beads(self, value: dict[int, tuple[int, int, int, int]]) -> None:
        validated = self._normalize_bead_mapping(value)
        if not validated:
            return
        with self.lock:
            bead_ids = np.fromiter(validated.keys(), dtype=np.uint32, count=len(validated))
            occupied = self._occupancy[bead_ids] != 0
            if np.any(occupied):
                existing_ids = bead_ids[occupied].tolist()
                raise ValueError(f'bead ids already exist: {existing_ids}')
            rois = np.asarray(list(validated.values()), dtype=self._roi_dtype)
            self._roi_matrix[bead_ids] = rois
            self._occupancy[bead_ids] = 1
            self._write_info(1, max(self._read_info(1), int(bead_ids.max()) + 1))
            self._write_info(2, self._read_info(2) + len(validated))
            self._increment_version()

    def update_beads(self, value: dict[int, tuple[int, int, int, int]]) -> None:
        validated = self._normalize_bead_mapping(value)
        if not validated:
            return
        with self.lock:
            bead_ids = np.fromiter(validated.keys(), dtype=np.uint32, count=len(validated))
            occupied = self._occupancy[bead_ids] != 0
            if not np.all(occupied):
                missing_ids = bead_ids[~occupied].tolist()
                raise ValueError(f'bead ids do not exist: {missing_ids}')
            self._roi_matrix[bead_ids] = np.asarray(list(validated.values()), dtype=self._roi_dtype)
            self._increment_version()

    def remove_beads(self, ids) -> None:
        normalized_ids = self._normalize_ids(ids)
        if normalized_ids.size == 0:
            return
        with self.lock:
            occupied_mask = self._occupancy[normalized_ids] != 0
            if not np.any(occupied_mask):
                return
            bead_ids = normalized_ids[occupied_mask]
            self._occupancy[bead_ids] = 0
            self._roi_matrix[bead_ids] = 0
            self._write_info(2, max(0, self._read_info(2) - bead_ids.size))
            self._increment_version()

    def clear_beads(self) -> None:
        with self.lock:
            self._roi_matrix.fill(0)
            self._occupancy.fill(0)
            self._write_info(1, 0)
            self._write_info(2, 0)
            self._increment_version()

    def reorder_beads(self) -> dict[int, int]:
        with self.lock:
            bead_ids = np.flatnonzero(self._occupancy[:self._read_info(1)])
            if bead_ids.size == 0:
                self._write_info(1, 0)
                self._write_info(2, 0)
                self._increment_version()
                return {}

            original_rois = self._roi_matrix[bead_ids].copy()
            mapping = {int(old_id): int(new_id) for new_id, old_id in enumerate(bead_ids.tolist())}
            self._roi_matrix.fill(0)
            self._occupancy.fill(0)
            new_ids = np.arange(bead_ids.size, dtype=np.uint32)
            self._roi_matrix[new_ids] = original_rois
            self._occupancy[new_ids] = 1
            self._write_info(1, bead_ids.size)
            self._write_info(2, bead_ids.size)
            self._increment_version()
            return mapping

    def get_next_available_bead_id(self) -> int:
        with self.lock:
            return self._read_info(1)

    def get_beads(self) -> tuple[np.ndarray, np.ndarray]:
        with self.lock:
            occupied = self._occupancy[:self._read_info(1)] != 0
            bead_ids = np.flatnonzero(occupied).astype(np.uint32, copy=False)
            rois = self._roi_matrix[bead_ids].copy()
            return bead_ids, rois

    def _normalize_bead_mapping(
        self,
        value: dict[int, tuple[int, int, int, int]],
    ) -> dict[int, tuple[int, int, int, int]]:
        normalized: dict[int, tuple[int, int, int, int]] = {}
        for bead_id, roi in value.items():
            bead_id_int = self._validate_bead_id(bead_id)
            if len(roi) != 4:
                raise ValueError(f'ROI for bead {bead_id_int} must contain four values')
            roi_values = tuple(int(coord) for coord in roi)
            if min(roi_values) < 0:
                raise ValueError(f'ROI for bead {bead_id_int} cannot contain negative values')
            normalized[bead_id_int] = roi_values
        return normalized

    def _normalize_ids(self, ids) -> np.ndarray:
        normalized = [self._validate_bead_id(bead_id) for bead_id in ids]
        if not normalized:
            return np.zeros((0,), dtype=np.uint32)
        return np.asarray(normalized, dtype=np.uint32)

    def _validate_bead_id(self, bead_id: int) -> int:
        bead_id_int = int(bead_id)
        if bead_id_int < 0 or bead_id_int >= self.capacity:
            raise ValueError(f'bead id {bead_id_int} is out of range 0..{self.capacity - 1}')
        return bead_id_int

    def _read_info(self, index: int) -> int:
        start = index * 8
        end = start + 8
        return int.from_bytes(self._shm_info.buf[start:end], byteorder='big')

    def _write_info(self, index: int, value: int) -> None:
        start = index * 8
        end = start + 8
        self._shm_info.buf[start:end] = int(value).to_bytes(8, byteorder='big')

    def _increment_version(self) -> None:
        self._write_info(3, self._read_info(3) + 1)


class LiveProfileBuffer:
    """Shared buffer that stores the latest radial profile for live display.

    The buffer keeps a single row containing ``timestamp``, ``bead_id``,
    ``profile_length`` and the profile samples. It wraps a
    :class:`MatrixBuffer` for shared-memory transport but hides the padding
    logic from callers so profiles can be written at their native length.
    """

    def __init__(
        self,
        *,
        create: bool,
        locks: dict[str, Lock],
        profile_capacity: int | None = None,
        name: str = 'LiveProfileBuffer',
    ):
        if create and profile_capacity is None:
            raise ValueError('profile_capacity must be provided when creating LiveProfileBuffer')

        shape = None if not create else (1, 3 + profile_capacity)
        self._buffer = MatrixBuffer(create=create, locks=locks, name=name, shape=shape)
        self.profile_capacity = self._buffer.shape[1] - 3

    @property
    def shape(self) -> tuple[int, int]:
        return self._buffer.shape

    def clear(self) -> None:
        """Reset the buffer contents to ``NaN``."""

        empty_row = np.full((1, 3 + self.profile_capacity), np.nan, dtype=np.float64)
        self._buffer.write(empty_row)

    def write_profile(self, timestamp: float, bead_id: int, profile: np.ndarray) -> None:
        """Store the latest profile for a bead.

        Parameters
        ----------
        timestamp : float
            Timestamp associated with the profile.
        bead_id : int
            Bead identifier for the profile.
        profile : numpy.ndarray
            One-dimensional array of profile samples. Length must not exceed
            ``profile_capacity``.
        """

        if profile.shape[0] > self.profile_capacity:
            raise ValueError(
                f'Profile length {profile.shape[0]} exceeds live buffer capacity {self.profile_capacity}'
            )

        row = np.full((1, 3 + self.profile_capacity), np.nan, dtype=np.float64)
        row[0, 0] = timestamp
        row[0, 1] = bead_id
        row[0, 2] = profile.shape[0]
        row[0, 3:3 + profile.shape[0]] = profile
        self._buffer.write(row)

    def peak_unsorted(self) -> np.ndarray:
        return self._buffer.peak_unsorted()


class ZLUTSweepDataset:
    """Temporary shared-memory dataset used for Z-LUT sweep capture.

    The dataset stores one row per captured profile with aligned metadata arrays
    for bead id, step index, timestamp, motor Z, validity, and the full radial
    profile. Unlike :class:`VideoBuffer` and :class:`MatrixBuffer`, this object
    never wraps and never overwrites old entries. It is intended to be created
    and destroyed at runtime by the workflow owner, while peer processes attach
    to the fixed shared-memory names on demand.
    """

    NAME = 'ZLUTSweepDataset'
    STATE_ABSENT = 0
    STATE_CREATING = 1
    STATE_READY = 2
    STATE_CAPTURING = 3
    STATE_COMPLETE = 4
    STATE_DETACHING = 5
    STATE_FAILED = 6
    STATE_DESTROYED = 7

    _INFO_FIELDS = {
        'schema_version': 0,
        'state': 1,
        'capacity': 2,
        'profile_length': 3,
        'n_steps': 4,
        'n_beads': 5,
        'profiles_per_bead': 6,
        'count': 7,
    }
    _INFO_SIZE = 8 * len(_INFO_FIELDS)
    _SCHEMA_VERSION = 1
    _UINT64_DTYPE = np.dtype(np.uint64)
    _BEAD_ID_DTYPE = np.dtype(np.uint32)
    _STEP_INDEX_DTYPE = np.dtype(np.uint32)
    _TIMESTAMP_DTYPE = np.dtype(np.float64)
    _MOTOR_Z_DTYPE = np.dtype(np.float64)
    _VALID_DTYPE = np.dtype(np.uint8)
    _PROFILE_DTYPE = np.dtype(np.float64)
    _SEGMENT_SUFFIXES = {
        'info': ' Info',
        'bead_ids': ' BeadIds',
        'step_indices': ' StepIndices',
        'timestamps': ' Timestamps',
        'motor_z': ' MotorZ',
        'valid': ' Valid',
        'profiles': ' Profiles',
    }
    _SHM_ATTRS = (
        '_shm_profiles',
        '_shm_valid',
        '_shm_motor_z',
        '_shm_timestamps',
        '_shm_step_indices',
        '_shm_bead_ids',
        '_shm_info',
    )

    def __init__(
        self,
        *,
        create: bool,
        locks: dict[str, Lock],
        capacity: int | None = None,
        profile_length: int | None = None,
        n_steps: int | None = None,
        n_beads: int | None = None,
        profiles_per_bead: int | None = None,
        name: str = NAME,
    ):
        self.name = name
        self.lock: Lock = locks[self.name]
        self._owns_shared_memory = create
        self._closed = False
        for attr in self._SHM_ATTRS:
            setattr(self, attr, None)

        if create:
            validated_parameters = self._validate_create_parameters(
                capacity=capacity,
                profile_length=profile_length,
                n_steps=n_steps,
                n_beads=n_beads,
                profiles_per_bead=profiles_per_bead,
            )
            self.capacity = validated_parameters['capacity']
            self.profile_length = validated_parameters['profile_length']
            self.n_steps = validated_parameters['n_steps']
            self.n_beads = validated_parameters['n_beads']
            self.profiles_per_bead = validated_parameters['profiles_per_bead']
        try:
            self._shm_info = SharedMemory(
                create=create,
                name=self.name + self._SEGMENT_SUFFIXES['info'],
                size=self._INFO_SIZE,
            )

            if create:
                self._write_info('schema_version', self._SCHEMA_VERSION)
                self._write_info('state', self.STATE_CREATING)
                self._write_info('capacity', self.capacity)
                self._write_info('profile_length', self.profile_length)
                self._write_info('n_steps', self.n_steps)
                self._write_info('n_beads', self.n_beads)
                self._write_info('profiles_per_bead', self.profiles_per_bead)
                self._write_info('count', 0)
            else:
                self._validate_attach_ready_state()
                self._validate_schema_version()
                self.capacity = self._read_info('capacity')
                self.profile_length = self._read_info('profile_length')
                self.n_steps = self._read_info('n_steps')
                self.n_beads = self._read_info('n_beads')
                self.profiles_per_bead = self._read_info('profiles_per_bead')
                if capacity is not None and int(capacity) != self.capacity:
                    raise ValueError('capacity does not match existing ZLUTSweepDataset')
                if profile_length is not None and int(profile_length) != self.profile_length:
                    raise ValueError('profile_length does not match existing ZLUTSweepDataset')

            self._shm_bead_ids = SharedMemory(
                create=create,
                name=self.name + self._SEGMENT_SUFFIXES['bead_ids'],
                size=self.capacity * self._BEAD_ID_DTYPE.itemsize,
            )
            self._shm_step_indices = SharedMemory(
                create=create,
                name=self.name + self._SEGMENT_SUFFIXES['step_indices'],
                size=self.capacity * self._STEP_INDEX_DTYPE.itemsize,
            )
            self._shm_timestamps = SharedMemory(
                create=create,
                name=self.name + self._SEGMENT_SUFFIXES['timestamps'],
                size=self.capacity * self._TIMESTAMP_DTYPE.itemsize,
            )
            self._shm_motor_z = SharedMemory(
                create=create,
                name=self.name + self._SEGMENT_SUFFIXES['motor_z'],
                size=self.capacity * self._MOTOR_Z_DTYPE.itemsize,
            )
            self._shm_valid = SharedMemory(
                create=create,
                name=self.name + self._SEGMENT_SUFFIXES['valid'],
                size=self.capacity * self._VALID_DTYPE.itemsize,
            )
            self._shm_profiles = SharedMemory(
                create=create,
                name=self.name + self._SEGMENT_SUFFIXES['profiles'],
                size=self.capacity * self.profile_length * self._PROFILE_DTYPE.itemsize,
            )

            self._bead_ids = np.ndarray(
                (self.capacity,), dtype=self._BEAD_ID_DTYPE, buffer=self._shm_bead_ids.buf
            )
            self._step_indices = np.ndarray(
                (self.capacity,), dtype=self._STEP_INDEX_DTYPE, buffer=self._shm_step_indices.buf
            )
            self._timestamps = np.ndarray(
                (self.capacity,), dtype=self._TIMESTAMP_DTYPE, buffer=self._shm_timestamps.buf
            )
            self._motor_z = np.ndarray(
                (self.capacity,), dtype=self._MOTOR_Z_DTYPE, buffer=self._shm_motor_z.buf
            )
            self._valid = np.ndarray(
                (self.capacity,), dtype=self._VALID_DTYPE, buffer=self._shm_valid.buf
            )
            self._profiles = np.ndarray(
                (self.capacity, self.profile_length),
                dtype=self._PROFILE_DTYPE,
                buffer=self._shm_profiles.buf,
            )

            if create:
                self._bead_ids.fill(0)
                self._step_indices.fill(0)
                self._timestamps.fill(np.nan)
                self._motor_z.fill(np.nan)
                self._valid.fill(0)
                self._profiles.fill(np.nan)
                self.set_state(self.STATE_READY)
        except Exception:
            self._cleanup_shared_memory_segments(unlink=create)
            raise

    @classmethod
    def create(
        cls,
        *,
        locks: dict[str, Lock],
        capacity: int,
        profile_length: int,
        n_steps: int,
        n_beads: int,
        profiles_per_bead: int,
        name: str = NAME,
    ) -> 'ZLUTSweepDataset':
        return cls(
            create=True,
            locks=locks,
            capacity=capacity,
            profile_length=profile_length,
            n_steps=n_steps,
            n_beads=n_beads,
            profiles_per_bead=profiles_per_bead,
            name=name,
        )

    @classmethod
    def attach(cls, *, locks: dict[str, Lock], name: str = NAME) -> 'ZLUTSweepDataset':
        try:
            return cls(create=False, locks=locks, name=name)
        except FileNotFoundError as exc:
            raise DatasetNotReadyError('ZLUTSweepDataset shared memory is not available yet.') from exc

    def __del__(self):
        self.close()

    @property
    def state(self) -> int:
        return self._read_info('state')

    def set_state(self, value: int) -> None:
        with self.lock:
            self._write_info('state', int(value))

    def write(
        self,
        *,
        bead_ids: np.ndarray,
        step_indices: np.ndarray,
        timestamps: np.ndarray,
        motor_z_values: np.ndarray,
        valid_flags: np.ndarray,
        profiles: np.ndarray,
    ) -> None:
        if self._closed:
            raise RuntimeError('Cannot write to a closed ZLUTSweepDataset')

        bead_ids_array = np.asarray(bead_ids, dtype=self._BEAD_ID_DTYPE)
        step_indices_array = np.asarray(step_indices, dtype=self._STEP_INDEX_DTYPE)
        timestamps_array = np.asarray(timestamps, dtype=self._TIMESTAMP_DTYPE)
        motor_z_array = np.asarray(motor_z_values, dtype=self._MOTOR_Z_DTYPE)
        valid_array = np.asarray(valid_flags, dtype=self._VALID_DTYPE)
        profiles_array = np.asarray(profiles, dtype=self._PROFILE_DTYPE)

        batch_size = bead_ids_array.shape[0]
        expected_shapes = {
            'step_indices': step_indices_array.shape,
            'timestamps': timestamps_array.shape,
            'motor_z_values': motor_z_array.shape,
            'valid_flags': valid_array.shape,
        }
        for field_name, shape in expected_shapes.items():
            if shape != (batch_size,):
                raise ValueError(f'{field_name} must have shape ({batch_size},)')
        if profiles_array.shape != (batch_size, self.profile_length):
            raise ValueError(
                f'profiles must have shape ({batch_size}, {self.profile_length})'
            )

        with self.lock:
            count = self._read_info('count')
            end = count + batch_size
            if end > self.capacity:
                raise BufferOverflow('ZLUTSweepDataset capacity exceeded')
            self._bead_ids[count:end] = bead_ids_array
            self._step_indices[count:end] = step_indices_array
            self._timestamps[count:end] = timestamps_array
            self._motor_z[count:end] = motor_z_array
            self._valid[count:end] = valid_array
            self._profiles[count:end, :] = profiles_array
            self._write_info('count', end)

    def peak(self) -> dict[str, np.ndarray]:
        if self._closed:
            raise RuntimeError('Cannot read from a closed ZLUTSweepDataset')

        with self.lock:
            count = self._read_info('count')
            return {
                'bead_ids': self._bead_ids[:count].copy(),
                'step_indices': self._step_indices[:count].copy(),
                'timestamps': self._timestamps[:count].copy(),
                'motor_z_values': self._motor_z[:count].copy(),
                'valid_flags': self._valid[:count].copy(),
                'profiles': self._profiles[:count, :].copy(),
            }

    def read_preview(self, selected_bead_id: int | None = None) -> dict[str, object]:
        if self._closed:
            raise RuntimeError('Cannot read from a closed ZLUTSweepDataset')

        with self.lock:
            count = self._read_info('count')
            state = self._read_info('state')

            available_bead_ids: list[int] = []
            effective_selected_bead_id: int | None = None
            motor_z_min: float | None = None
            motor_z_max: float | None = None
            step_indices = np.zeros((0,), dtype=self._STEP_INDEX_DTYPE)
            motor_z_values = np.zeros((0,), dtype=self._MOTOR_Z_DTYPE)
            profiles = np.zeros((0, self.profile_length), dtype=self._PROFILE_DTYPE)

            if count > 0:
                bead_ids = self._bead_ids[:count]
                available_bead_ids = [int(bead_id) for bead_id in np.unique(bead_ids)]

                if available_bead_ids:
                    if selected_bead_id is not None and np.any(bead_ids == int(selected_bead_id)):
                        effective_selected_bead_id = int(selected_bead_id)
                    else:
                        effective_selected_bead_id = available_bead_ids[0]

                all_motor_z_values = self._motor_z[:count]
                finite_motor_z = all_motor_z_values[np.isfinite(all_motor_z_values)]
                if finite_motor_z.size > 0:
                    motor_z_min = float(np.min(finite_motor_z))
                    motor_z_max = float(np.max(finite_motor_z))

                if effective_selected_bead_id is not None:
                    selected_rows = bead_ids == effective_selected_bead_id
                    step_indices = self._step_indices[:count][selected_rows].copy()
                    motor_z_values = all_motor_z_values[selected_rows].copy()
                    profiles = self._profiles[:count, :][selected_rows].copy()

            return {
                'state': state,
                'count': count,
                'capacity': self.capacity,
                'n_steps': self.n_steps,
                'n_beads': self.n_beads,
                'profiles_per_bead': self.profiles_per_bead,
                'profile_length': self.profile_length,
                'available_bead_ids': available_bead_ids,
                'selected_bead_id': effective_selected_bead_id,
                'motor_z_min': motor_z_min,
                'motor_z_max': motor_z_max,
                'step_indices': step_indices,
                'motor_z_values': motor_z_values,
                'profiles': profiles,
            }

    def get_count(self) -> int:
        if self._closed:
            raise RuntimeError('Cannot read from a closed ZLUTSweepDataset')
        with self.lock:
            return self._read_info('count')

    def get_capacity(self) -> int:
        return self.capacity

    def close(self) -> None:
        if self._closed:
            return
        self._cleanup_shared_memory_segments(unlink=False)
        self._closed = True

    def destroy(self) -> None:
        if not self._owns_shared_memory:
            raise RuntimeError('Only the creating process may destroy a ZLUTSweepDataset')
        self.set_state(self.STATE_DESTROYED)
        self._cleanup_shared_memory_segments(unlink=True)
        self._closed = True

    def _validate_schema_version(self) -> None:
        schema_version = self._read_info('schema_version')
        if schema_version == 0:
            raise DatasetNotReadyError('ZLUTSweepDataset schema metadata is not initialized yet.')
        if schema_version != self._SCHEMA_VERSION:
            raise ValueError(
                f'Unsupported ZLUTSweepDataset schema version: {schema_version}'
            )

    def _validate_attach_ready_state(self) -> None:
        state = self._read_info('state')
        if state in {
            self.STATE_ABSENT,
            self.STATE_CREATING,
            self.STATE_DETACHING,
            self.STATE_DESTROYED,
        }:
            raise DatasetNotReadyError(
                f'ZLUTSweepDataset is not attachable while in state {state}.'
            )

    def _read_info(self, field: str) -> int:
        field_index = self._INFO_FIELDS[field]
        start = field_index * 8
        end = start + 8
        return int.from_bytes(self._shm_info.buf[start:end], byteorder='big')

    def _write_info(self, field: str, value: int) -> None:
        field_index = self._INFO_FIELDS[field]
        start = field_index * 8
        end = start + 8
        self._shm_info.buf[start:end] = int(value).to_bytes(8, byteorder='big')

    @classmethod
    def _validate_create_parameters(
        cls,
        *,
        capacity: int | None,
        profile_length: int | None,
        n_steps: int | None,
        n_beads: int | None,
        profiles_per_bead: int | None,
    ) -> dict[str, int]:
        required = {
            'capacity': capacity,
            'profile_length': profile_length,
            'n_steps': n_steps,
            'n_beads': n_beads,
            'profiles_per_bead': profiles_per_bead,
        }
        missing = [field for field, value in required.items() if value is None]
        if missing:
            raise ValueError(
                f"Missing required ZLUTSweepDataset creation parameters: {', '.join(missing)}"
            )

        validated: dict[str, int] = {}
        for field, value in required.items():
            if value <= 0:
                raise ValueError(f'{field} must be positive')
            validated[field] = int(value)
        return validated

    def _cleanup_shared_memory_segments(self, *, unlink: bool) -> None:
        for attr in self._SHM_ATTRS:
            shm = getattr(self, attr, None)
            if shm is None:
                continue
            if unlink:
                try:
                    shm.unlink()
                except FileNotFoundError:
                    pass
            shm.close()
            setattr(self, attr, None)

class BufferUnderflow(Exception):
    """Raised when attempting to read from a buffer that contains no data."""


class BufferOverflow(Exception):
    """Raised when attempting to write to a buffer that has no free slots."""


class DatasetNotReadyError(Exception):
    """Raised when a shared-memory dataset exists but is not attachable yet."""

bit_to_dtype = {
    8:  np.uint8,
    16: np.uint16,
    32: np.uint32,
    64: np.uint64
}

def int_to_uint_dtype(bits: int):
    """Return the unsigned integer NumPy dtype matching ``bits``.

    Parameters
    ----------
    bits : int
        Width of the target integer in bits. Supported values are ``8``, ``16``,
        ``32`` and ``64``.

    Returns
    -------
    numpy.dtype
        Unsigned integer dtype corresponding to ``bits``.

    Raises
    ------
    ValueError
        If ``bits`` is not one of the supported widths.
    """
    if bits not in bit_to_dtype:
        raise ValueError(f"Unsupported bit width: {bits}")
    return bit_to_dtype[bits]
