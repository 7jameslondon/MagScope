import unittest

from magscope.datatypes import VideoBuffer
from multiprocessing import Lock


class CreateVideoBuffer(unittest.TestCase):
    def test_args_required(self):
        locks = {'VideoBuffer': Lock()}
        with self.assertRaises(ValueError):
            buffer = VideoBuffer(create=True, locks=locks)

    def test_bit_supported(self):
        locks = {'VideoBuffer': Lock()}
        with self.assertRaises(ValueError):
            buffer = VideoBuffer(create=True, locks=locks,
                                 n_stacks=10, width=100, height=100, n_images=10, bits=12)
        buffer = VideoBuffer(create=True, locks=locks,
                             n_stacks=10, width=100, height=100, n_images=10, bits=16)

    def test_creation_and_deletion(self):
        locks = {'VideoBuffer': Lock()}
        buffer = VideoBuffer(create=True, locks=locks,
                             n_stacks=10, width=100, height=100, n_images=10, bits=16)
        del buffer



if __name__ == '__main__':
    unittest.main()
