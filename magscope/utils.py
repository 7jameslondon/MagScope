from __future__ import annotations
from enum import IntEnum, StrEnum
from datetime import datetime
import numpy as np
from PyQt6.QtGui import QImage
from typing import Callable, TYPE_CHECKING, Type

# Import only for the type check to avoid circular import
if TYPE_CHECKING:
    from magscope import ManagerProcessBase

class Message:
    def __init__(self, to: Type['ManagerProcessBase'] | str, meth: Callable | str, *args, **kwargs):
        if isinstance(to, str):
            self.to = to
        else:
            self.to: str = to.__name__

        if isinstance(meth, str):
            self.meth = meth
        else:
            self.meth: str = meth.__name__

        self.args = args
        if 'args' in kwargs:
            self.args = self.args + kwargs['args']
            del kwargs['args']
        self.kwargs = kwargs

    def __str__(self):
        return f"Message(to={self.to}, func={self.meth}, args={self.args}, kwargs={self.kwargs})"

class AcquisitionMode(StrEnum):
    """ Enum for the different acquisition modes """
    TRACK = 'track'
    TRACK_AND_CROP_VIDEO = 'track & video (cropped)'
    TRACK_AND_FULL_VIDEO = 'track & video (full)'
    CROP_VIDEO = 'video (cropped)'
    FULL_VIDEO = 'video (full)'
    ZLUT = 'zlut'

def crop_stack_to_rois(stack, rois: list[tuple[int, int, int, int]]):
    # Pre-allocate space for cropped_stack
    n_images = stack.shape[2]
    n_rois = len(rois)
    width = rois[0][1] - rois[0][0]
    shape = (width, width, n_images, n_rois)
    cropped_stack = np.ndarray(
        shape, dtype=stack.dtype
    )  # width, width, frames, rois

    # Crop
    for i, roi in enumerate(rois):
        cropped_stack[:, :, :, i] = (
            stack[roi[0]:roi[1], roi[2]:roi[3], :]
        )

    return cropped_stack

def numpy_type_to_qt_image_type(numpy_type):
    NP2QT = {
        np.uint8: QImage.Format.Format_Grayscale8,
        np.uint16: QImage.Format.Format_Grayscale16
    }

    if numpy_type not in NP2QT:
        raise ValueError(f"Unsupported bit type: {numpy_type}")
    return NP2QT[numpy_type]

def date_timestamp_str(timestamp):
    date_str = datetime.today().strftime('%Y-%m-%d')
    hour = (timestamp // 3600 % 24 - 5) % 24
    minutes = timestamp // 60 % 60
    seconds = timestamp // 1 % 60
    milliseconds = timestamp % 1 * 1000
    return f'{date_str} {hour:02.0f}-{minutes:02.0f}-{seconds:02.0f}.{milliseconds:03.0f}'

class PoolVideoFlag(IntEnum):
    READY = 0
    RUNNING = 1
    FINISHED = 2

class Units:
    # Meters
    m = 1.
    cm = 1e-2
    mm = 1e-3
    um = 1e-6
    nm = 1e-9

    # Newtons
    N = 1.
    mN = 1e-3
    uN = 1e-6
    nN = 1e-9
    pN = 1e-12
    fN = 1e-15

    # Seconds
    sec = s = 1.
    ms = 1e-3
    us = 1e-6
    ns = 1e-9
    ps = 1e-12
    fs = 1e-15

    # Directions
    clockwise = cw = 1.
    counterclockwise = ccw = -1.

def registerwithscript(meth_str: str):
    def decorator(meth: callable):
        meth._scriptable = True
        meth._script_str = meth_str
        meth._script_cls = meth.__qualname__.split('.')[0]
        return meth

    return decorator