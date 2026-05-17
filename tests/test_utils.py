import numpy as np
import pytest
from PyQt6.QtGui import QImage

from magscope.utils import AcquisitionMode, crop_stack_to_rois, date_timestamp_str, numpy_type_to_qt_image_type


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
