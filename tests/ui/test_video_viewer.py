import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from magscope.ui.video_viewer import VideoViewer


def test_overlay_cache_pixmap_reused_until_overlay_changes(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    viewer.set_bead_overlay(
        {1: (10, 30, 20, 40), 2: (60, 90, 70, 100)},
        active_bead_id=None,
        selected_bead_id=1,
        reference_bead_id=2,
    )

    assert viewer._overlay_cache_dirty is True
    viewer._ensure_overlay_cache_pixmap()
    first_cache_key = viewer._overlay_cache_pixmap.cacheKey()

    assert viewer._overlay_cache_dirty is False
    assert not viewer._overlay_cache_pixmap.isNull()

    viewer._ensure_overlay_cache_pixmap()
    assert viewer._overlay_cache_pixmap.cacheKey() == first_cache_key

    viewer.plot([15.0], [25.0], 5)
    viewer._ensure_overlay_cache_pixmap()
    assert viewer._overlay_cache_pixmap.cacheKey() == first_cache_key

    viewer.set_bead_overlay(
        {1: (12, 32, 22, 42), 2: (60, 90, 70, 100)},
        active_bead_id=None,
        selected_bead_id=1,
        reference_bead_id=2,
    )

    assert viewer._overlay_cache_dirty is True
    viewer._ensure_overlay_cache_pixmap()
    assert viewer._overlay_cache_pixmap.cacheKey() != first_cache_key


def test_overlay_cache_invalidates_when_view_changes(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()
    viewer.set_bead_overlay(
        {1: (10, 30, 20, 40)},
        active_bead_id=None,
        selected_bead_id=None,
        reference_bead_id=None,
    )

    viewer._ensure_overlay_cache_pixmap()
    assert viewer._overlay_cache_dirty is False

    viewer.zoom(1)
    assert viewer._overlay_cache_dirty is True

    viewer._ensure_overlay_cache_pixmap()
    assert viewer._overlay_cache_dirty is False

    viewer.scrollContentsBy(5, 0)
    assert viewer._overlay_cache_dirty is True


@pytest.mark.parametrize(
    ("zoom_percent", "expected"),
    [(100.0, "1.0x"), (124.99999999999997, "1.3x"), (325.0, "3.3x")],
)
def test_minimap_zoom_label_uses_multiplier(qtbot, monkeypatch, zoom_percent, expected):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(360, 320)
    viewer.show()
    qtbot.wait(1)
    viewer.zoom(1)

    monkeypatch.setattr(viewer, "_current_zoom_percent", lambda: zoom_percent)
    viewer._refresh_minimap()

    assert viewer._minimap_zoom_label.text() == expected


def test_overlay_cache_excludes_active_bead_label(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    viewer.set_bead_overlay(
        {1: (10, 30, 20, 40), 2: (60, 90, 70, 100)},
        active_bead_id=1,
        selected_bead_id=1,
        reference_bead_id=None,
    )

    viewer._rebuild_overlay_view_cache()
    visible_labels = viewer._visible_label_entries
    assert visible_labels is not None
    assert len(visible_labels) == 2
    assert sum(1 for _point, _text, is_active in visible_labels if is_active) == 1

    viewer._ensure_overlay_cache_pixmap()
    assert viewer._overlay_cache_dirty is False
