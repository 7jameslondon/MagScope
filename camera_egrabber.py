import ctypes
import queue
import sys
import time
import traceback

import egrabber
import egrabber.generated
import numpy as np

from magscope.camera import CameraBase


_SYS_IS_FINALIZING = getattr(sys, "is_finalizing", None)


class EGrabberCamera(CameraBase):
    width = 2560  # 5120 without binning
    height = 2560  # 5120 without binning
    bits = 10
    dtype = np.uint16
    nm_per_px = 5000.0  # 2500. without binning
    settings = ['framerate', 'exposure', 'gain']

    def __init__(self):
        super().__init__()
        self.gentl = None
        self.egrabber: egrabber.EGrabber | None = None  # type: ignore
        self.timestamp_offset = self.calculate_timestamp_offset()

    def __del__(self):
        if sys.is_finalizing():
            return
        try:
            super().__del__()
            if hasattr(self, 'egrabber') and self.egrabber is not None:
                self.egrabber.stop()
        except Exception:
            traceback.print_exc()

    def connect(self, video_buffer):
        super().connect(video_buffer)
        try:
            # Set up the camera
            self.gentl = egrabber.EGenTL()
            self.egrabber = egrabber.EGrabber(self.gentl, 0, 0)

            # Set camera settings
            self.egrabber.remote.set('PixelFormat', 'Mono10')
            self.egrabber.remote.set('BinningHorizontal', 'X2')
            self.egrabber.remote.set('BinningVertical', 'X2')

            self.egrabber.remote.set('Height', 2560)  # 5120

            self.egrabber.remote.set('TargetTemperature', 50)
            self.egrabber.remote.set('FanOperationMode', 'Temperature')  # Off On Temperature

            self.egrabber.stream.set('BufferPartCount', 1)

            # Set video _buf
            self.video_buffer = video_buffer
            self.egrabber.announce_and_queue(
                egrabber.UserMemoryArray(
                    egrabber.UserMemory(video_buffer._buf),
                    video_buffer.image_size,
                )
            )

            # Set camera _buf queue
            n_queue = self.video_buffer.n_stacks * self.video_buffer.stack_shape[2]
            self.camera_buffers = queue.Queue(n_queue)

            self.egrabber.start()

            self.is_connected = True
        except Exception:
            self.camera_buffers = None
            self.is_connected = False
            print('Camera connection error:')
            traceback.print_exc()

    def fetch(self):
        if not self.is_connected or self.egrabber is None or self.camera_buffers is None:
            return
        try:
            # Get _buf
            buffer = egrabber.Buffer(self.egrabber, timeout=1)

            # Store _buf
            self.camera_buffers.put(buffer)

            # Get timestamp
            timestamp = buffer.get_info(
                egrabber.BUFFER_INFO_TIMESTAMP,
                egrabber.INFO_DATATYPE_UINT64,
            )
            timestamp = self.convert_timestamp(timestamp, self.timestamp_offset)

            # Store timestamp
            self.video_buffer.write_timestamp(timestamp)
        except egrabber.generated.errors.TimeoutException:
            pass
        except queue.Full:
            buffer.push()

    def release(self):
        if self.camera_buffers is None:
            return
        buffer = self.camera_buffers.get()
        buffer.push()

    def get_setting(self, name: str) -> str:
        super().get_setting(name)
        if not self.is_connected or self.egrabber is None:
            raise RuntimeError('Camera not connected')

        match name:
            case 'framerate':
                param = 'AcquisitionFrameRate'
            case 'exposure':
                param = 'ExposureTime'
            case 'gain':
                param = 'Gain'
        value = self.egrabber.remote.get(param)
        return str(value)

    def set_setting(self, name: str, value: str):
        super().set_setting(name, value)
        if not self.is_connected or self.egrabber is None:
            raise RuntimeError('Camera not connected')

        try:
            match name:
                case 'framerate':
                    self.egrabber.remote.set('AcquisitionFrameRate', value)
                case 'exposure':
                    self.egrabber.remote.set('ExposureTime', value)
                case 'gain':
                    self.egrabber.remote.set('Gain', value)
        except egrabber.generated.errors.GenTLException:
            pass

    @staticmethod
    def calculate_timestamp_offset():
        kernel32 = ctypes.windll.kernel32
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        time_up = (0.0 + kernel32.GetTickCount64()) / 1e3
        time_clock = time.time()
        return time_clock - time_up

    @staticmethod
    def convert_timestamp(timestamp, offset):
        return ((timestamp + 0.0) / 1e6) + offset
