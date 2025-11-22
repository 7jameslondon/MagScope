import ctypes
import time
import queue
import traceback

import egrabber
import egrabber.generated
import numpy as np

from magscope.camera import CameraABC


class EGrabberCamera(CameraABC):
    width = 2560  # 5120 without binning
    height = 2560 # 5120 without binning
    bits = 10
    dtype = np.uint16
    nm_per_px = 5000.  # 2500. without binning

    def __init__(self):
        super().__init__()
        self.gentl = None
        self.egrabber: egrabber.EGrabber = None  # type: ignore
        self.timestamp_offset = self.calculate_timestamp_offset()

    def __del__(self):
        super().__del__()
        if hasattr(self, 'egrabber') and self.egrabber is not None:
            self.egrabber.stop()

    def connect(self, video_buffer):
        try:
            # Set up the camera
            self.gentl = egrabber.EGenTL()
            self.egrabber = egrabber.EGrabber(self.gentl, 0, 0)

            # Set camera settings
            self.egrabber.remote.set('PixelFormat', 'Mono10')
            self.egrabber.remote.set('BinningHorizontal', 'X2')
            self.egrabber.remote.set('BinningVertical', 'X2')

            self.egrabber.remote.set('Height', 2560) # 5120

            self.egrabber.remote.set('TargetTemperature', 50)
            self.egrabber.remote.set('FanOperationMode', 'Temperature')  # Off On Temperature

            self.egrabber.stream.set('BufferPartCount', 1)

            # Set video _buf
            self.video_buffer = video_buffer
            self.egrabber.announce_and_queue(
                egrabber.UserMemoryArray(
                    egrabber.UserMemory(video_buffer._buf),
                    video_buffer.image_size))

            # Set camera _buf queue
            n_queue = self.video_buffer.n_stacks * \
                      self.video_buffer.stack_shape[2]
            self.camera_buffers = queue.Queue(n_queue)

            self.egrabber.start()

            self.is_connected = True
        except Exception as e:
            self.is_connected = False
            print('Camera connection error:')
            traceback.print_exc()
            print(e, flush=True)

    def get_camera_settings(self):
        settings = {
            'FrameRate': self.egrabber.remote.get('AcquisitionFrameRate'),
            'Exposure': self.egrabber.remote.get('ExposureTime'),
            'Gain': self.egrabber.remote.get('Gain')
        }
        return settings

    def set_camera_setting(self, setting, value):
        try:
            match setting:
                case 'FrameRate':
                    self.egrabber.remote.set('AcquisitionFrameRate', value)
                case 'Exposure':
                    self.egrabber.remote.set('ExposureTime', value)
                case 'Gain':
                    self.egrabber.remote.set('Gain', value)
        except egrabber.generated.errors.GenTLException:
            pass

    def fetch(self):
        try:
            # Get _buf
            buffer = egrabber.Buffer(self.egrabber, timeout=1)

            # Store _buf
            self.camera_buffers.put(buffer)

            # Get timestamp
            timestamp = buffer.get_info(egrabber.BUFFER_INFO_TIMESTAMP,
                                        egrabber.INFO_DATATYPE_UINT64)
            timestamp = self.convert_timestamp(timestamp,
                                               self.timestamp_offset)

            # Store timestamp
            self.video_buffer.write_timestamp(timestamp)

        except egrabber.generated.errors.TimeoutException:
            pass

    def release(self):
        buffer = self.camera_buffers.get()
        buffer.push()

    @staticmethod
    def calculate_timestamp_offset():
        kernel32 = ctypes.windll.kernel32
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        time_up = (0. + kernel32.GetTickCount64()) / 1e3
        time_clock = time.time()
        return time_clock - time_up

    @staticmethod
    def convert_timestamp(timestamp, offset):
        return ((timestamp + 0.) / 1e6) + offset