from magscope.utils import AcquisitionMode


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
