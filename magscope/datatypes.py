from multiprocessing.shared_memory import SharedMemory
from multiprocessing.synchronize import Lock
import numpy as np
import struct

class VideoBuffer:
    """ Shared memory ring buffer for video data

    The buffer should first be created by a process with create=True.
    When creating, n_stacks, width, height, n_images and bits must be provided.
    After which the buffer can be accessed in a diffrent process with create=False.

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
        # along with the buffer itself.
        # If creating a new buffer then that meta-data needs to be set up and stored.
        # Else if connected to a created buffer then that meta-data needs to be retrieved
        # to be able to access the actual buffer.
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

        # Initalize the buffer and indexes when creating for the first time
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
        """ Returns a fraction of how full the _buf is. """
        with self.lock:
            return self._get_count_index() / self.n_total_images

    def check_read_stack(self):
        with self.lock:
            try:
                self._check_read(self.n_images)
            except BufferUnderflow:
                return False
            else:
                return True

    def peak_image(self):
        """
        Returns the last image written and image's _write in the _buf

        This is intended to be used for a live view of the camera. The _buf
        is accessed without a lock to reduce overhead and because this
        operation is not intended to return "perfect" data. As a result it is
        possible the data returned will not be the exact last image or the
        image could be partially written over while being read resulting in
        part of the image being older than another part. However, overall
        this fairly reliably returns the last image as intended.

        Returns
        ----------
        _write : int
            _write of the last image written into the _buf
        image : memoryview, can be converted into a 2D array
            memoryview of the last image written into the _buf. Can be
            converted into a 2D array with the buffers dtype and image_shape
            attributes
        """
        read = (self._get_write_index() - 1) % self.n_total_images
        return read, self._buf[(read * self.image_size):((read + 1) *
                                                         self.image_size)]

    def peak_stack(self):
        """ Returns a stack but does not update the read _write """
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
        """ Updates the read _write by a stack but does not return data """
        with self.lock:
            self._check_read(self.n_images)
            read = self._get_read_index()
            count = self._get_count_index()
            self._set_read_index(read + self.n_images)
            self._set_count_index(count - self.n_images)

    def read_image(self):
        """
        Returns the last image written and timestamp

        Returns
        ----------
        image : memoryview, can be converted into a 2D array
            memoryview of the last image written into the _buf. Can be
            converted into a 2D array with the buffers dtype and image_shape
            attributes
        timestamp : float
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
        """ Increment write _write and write a timestamp without writing video data"""
        with self.lock:
            self._check_write(1)
            write = self._get_write_index()
            count = self._get_count_index()
            self._set_timestamp(write, timestamp)
            self._set_write_index(write + 1)
            self._set_count_index(count + 1)

    def write_image_and_timestamp(self, image, timestamp):
        """ Increment write _write, write one image and timestamp"""
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
    """
    A shared memory ring buffer for 2d-array(matrix) data

    This class is for the bead positions (t,x,y,z,b) and for the motor
    position data.

    Features:
        * Shared Memory
        * Stores 2D Numpy Arrays
        * Ring Buffer (specialized)
            * Read is not synced
            * Read returns the full _buf content in FIFO order
            * Write is synced
            * Write takes 2D numpy arrays of any length in the first axis
              but a fixed length in the second axis
            * Data that has never been written over is nan
            * Once filled data is simply over-written

    """

    def __init__(self, *,
                 create: bool,
                 locks: dict[str, Lock],
                 name: str,
                 shape: tuple[int, int]=None):
        self.name: str = name
        self.lock: Lock = locks[self.name]

        # Some meta-data to describe the buffer is stored in the shared memory
        # along with the buffer itself.
        # If creating a new buffer then that meta-data needs to be set up and stored.
        # Else if connected to a created buffer then that meta-data needs to be retrieved
        # to be able to access the actual buffer.
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

        # Initalize the buffer and indexes when creating for the first time
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
        with self.lock:
            return self._get_count_index()

    def get_read_index(self):
        with self.lock:
            return self._get_read_index()

    def get_write_index(self):
        with self.lock:
            return self._get_write_index()

    def write(self, np_array):
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
        """ Returns a copy of the unread portion of the matrix and updates the read and count indexes """
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
        """ Returns the whole matrix without sorting but does not update the indexes """
        with self.lock:
            return np.ndarray(self.shape, dtype=self.dtype, buffer=self._buf)

    def peak_sorted(self):
        """ Returns the whole sorted matrix but does not update the indexes """
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
    pass

class BufferOverflow(Exception):
    pass

bit_to_dtype = {
    8:  np.uint8,
    16: np.uint16,
    32: np.uint32,
    64: np.uint64
}

def int_to_uint_dtype(bits: int):
    if bits not in bit_to_dtype:
        raise ValueError(f"Unsupported bit width: {bits}")
    return bit_to_dtype[bits]