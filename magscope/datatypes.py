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

from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Lock
import numpy as np
import struct

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
            print(f'Creating VideoBuffer with size {self.buffer_size/1e6} MB')

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

class BufferUnderflow(Exception):
    """Raised when attempting to read from a buffer that contains no data."""


class BufferOverflow(Exception):
    """Raised when attempting to write to a buffer that has no free slots."""

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
