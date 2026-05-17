import numpy as np
import pytest
from PyQt6.QtGui import QImage

from magscope.ipc_commands import SleepCommand
from magscope.utils import (
    AcquisitionMode,
    PoolVideoFlag,
    Units,
    check_cupy,
    crop_stack_to_rois,
    date_timestamp_str,
    numpy_type_to_qt_image_type,
    register_script_command,
)


def test_acquisition_mode_old_member_names_alias_new_modes():
    assert AcquisitionMode.TRACK_AND_CROP_VIDEO is AcquisitionMode.TRACK_AND_VIDEO_ROIS
    assert AcquisitionMode.TRACK_AND_FULL_VIDEO is AcquisitionMode.TRACK_AND_VIDEO_FULL
    assert AcquisitionMode.CROP_VIDEO is AcquisitionMode.VIDEO_ROIS
    assert AcquisitionMode.FULL_VIDEO is AcquisitionMode.VIDEO_FULL


def test_acquisition_mode_new_members_are_canonical():
    assert list(AcquisitionMode) == [
        AcquisitionMode.TRACK,
        AcquisitionMode.TRACK_AND_VIDEO_ROIS,
        AcquisitionMode.TRACK_AND_VIDEO_FULL,
        AcquisitionMode.VIDEO_ROIS,
        AcquisitionMode.VIDEO_FULL,
    ]
    assert AcquisitionMode.VIDEO_FULL.value == 'Video (Full)'


def test_crop_stack_to_rois_preserves_roi_order_and_frame_axis():
    stack = np.arange(4 * 5 * 2, dtype=np.uint16).reshape(4, 5, 2)
    rois = np.asarray([
        [0, 2, 1, 3],
        [2, 4, 2, 4],
    ])

    cropped = crop_stack_to_rois(stack, rois)

    assert cropped.shape == (2, 2, 2, 2)
    assert cropped.dtype == stack.dtype
    np.testing.assert_array_equal(cropped[:, :, :, 0], stack[0:2, 1:3, :])
    np.testing.assert_array_equal(cropped[:, :, :, 1], stack[2:4, 2:4, :])


def test_numpy_type_to_qt_image_type_maps_supported_grayscale_types():
    assert numpy_type_to_qt_image_type(np.uint8) == QImage.Format.Format_Grayscale8
    assert numpy_type_to_qt_image_type(np.uint16) == QImage.Format.Format_Grayscale16


def test_numpy_type_to_qt_image_type_rejects_unsupported_dtype():
    with pytest.raises(ValueError, match='Unsupported bit type'):
        numpy_type_to_qt_image_type(np.float32)


def test_date_timestamp_str_formats_fractional_seconds():
    result = date_timestamp_str(5 * 3600 + 61.123)
    import re
    assert re.match(r'\d{4}-\d{2}-\d{2} 00-01-01\.123', result)


def test_date_timestamp_str_zero_timestamp():
    result = date_timestamp_str(0)
    import re
    assert re.match(r'\d{4}-\d{2}-\d{2} 19-00-00\.000', result)


def test_date_timestamp_str_rolls_hours():
    result = date_timestamp_str(25 * 3600 + 1.5)
    import re
    assert re.match(r'\d{4}-\d{2}-\d{2} 20-00-01\.500', result)


def test_date_timestamp_str_includes_today_date():
    result = date_timestamp_str(0)
    from datetime import datetime
    today = datetime.today().strftime('%Y-%m-%d')
    assert result.startswith(today)


# ---------------------------------------------------------------------------
# PoolVideoFlag
# ---------------------------------------------------------------------------

def test_pool_video_flag_values():
    assert PoolVideoFlag.READY == 0
    assert PoolVideoFlag.RUNNING == 1
    assert PoolVideoFlag.FINISHED == 2


def test_pool_video_flag_is_int_enum():
    assert issubclass(PoolVideoFlag, int)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

def test_units_meter_constants():
    assert Units.m == 1.0
    assert Units.cm == 1e-2
    assert Units.mm == 1e-3
    assert Units.um == 1e-6
    assert Units.nm == 1e-9


def test_units_newton_constants():
    assert Units.N == 1.0
    assert Units.mN == 1e-3
    assert Units.uN == 1e-6
    assert Units.nN == 1e-9
    assert Units.pN == 1e-12
    assert Units.fN == 1e-15


def test_units_time_constants():
    assert Units.sec == 1.0
    assert Units.s == 1.0
    assert Units.ms == 1e-3
    assert Units.us == 1e-6
    assert Units.ns == 1e-9
    assert Units.ps == 1e-12
    assert Units.fs == 1e-15


def test_units_direction_constants():
    assert Units.clockwise == 1.0
    assert Units.cw == 1.0
    assert Units.counterclockwise == -1.0
    assert Units.ccw == -1.0


# ---------------------------------------------------------------------------
# register_script_command
# ---------------------------------------------------------------------------

def test_register_script_command_sets_metadata():
    @register_script_command(SleepCommand)
    def my_method(self):
        pass

    assert my_method._scriptable is True
    assert my_method._script_command_type is SleepCommand


# ---------------------------------------------------------------------------
# check_cupy
# ---------------------------------------------------------------------------

def test_check_cupy_returns_bool():
    result = check_cupy()
    assert isinstance(result, bool)
