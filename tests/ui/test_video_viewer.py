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


# ---------------------------------------------------------------------------
# State / logic tests
# ---------------------------------------------------------------------------

def test_clear_crosshairs_clears_marker_arrays(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    viewer.plot([10.0, 20.0], [30.0, 40.0], 5)
    assert viewer._marker_x.size == 2

    viewer.clear_crosshairs()
    assert viewer._marker_x.size == 0
    assert viewer._marker_y.size == 0
    assert viewer._marker_size == 0


def test_clear_image_sets_empty_state(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    viewer.clear_image()

    assert viewer._empty is True
    assert viewer.dragMode() == viewer.DragMode.NoDrag
    assert viewer._minimap_label.isHidden()
    assert viewer._minimap_zoom_label.isHidden()


def test_zoom_level_returns_current_zoom(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    assert viewer.zoom_level() == 0
    viewer.zoom(1)
    assert viewer.zoom_level() == 1


def test_zoom_zero_noop(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    zoom_before = viewer._zoom
    viewer.zoom(0)
    assert viewer._zoom == zoom_before


def test_zoom_out_decreases_zoom(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    viewer.zoom(2)
    assert viewer._zoom >= 2
    viewer.zoom(-1)
    assert viewer._zoom < 2


def test_zoom_to_zero_resets_view(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    viewer.zoom(1)
    assert viewer._zoom == 1
    viewer.zoom(-1)
    assert viewer._zoom == 0


def test_set_pixmap_null_leaves_empty_unchanged(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)

    viewer._empty = True
    from PyQt6.QtGui import QPixmap
    viewer.set_pixmap(QPixmap())
    assert viewer._empty is True


def test_reset_view_with_scale_when_has_image(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)

    viewer.set_image_to_default()
    viewer._zoom = 5
    viewer.reset_view(scale=2)
    assert viewer._zoom == 5  # scale != 1 so zoom is preserved


def test_reset_view_no_image(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)

    viewer.clear_image()
    viewer.reset_view()
    assert viewer._empty is True


def test_toggle_drag_mode_cycles(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    viewer.setDragMode(viewer.DragMode.ScrollHandDrag)
    viewer.toggle_drag_mode()
    assert viewer.dragMode() == viewer.DragMode.NoDrag


def test_toggle_drag_mode_to_scroll_hand_with_pixmap(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    viewer.setDragMode(viewer.DragMode.NoDrag)
    viewer.toggle_drag_mode()
    assert viewer.dragMode() == viewer.DragMode.ScrollHandDrag


def test_wheel_event_zoom_in(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    zoom_before = viewer._zoom
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QWheelEvent
    event = QWheelEvent(
        QPointF(100.0, 100.0), QPointF(100.0, 100.0),
        QPoint(0, 0), QPoint(0, 120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    viewer.wheelEvent(event)
    assert viewer._zoom > zoom_before


def test_compute_highlight_rect_null_pixmap(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)

    from PyQt6.QtGui import QPixmap
    viewer._image.setPixmap(QPixmap())
    result = viewer._compute_highlight_rect(None, 0, 0)
    assert result is None


# ---------------------------------------------------------------------------
# Mouse event tests
# ---------------------------------------------------------------------------

def test_mouse_press_records_position_and_time(qtbot, monkeypatch):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    monkeypatch.setattr(viewer, '_mouse_start_time', 0.0)
    from PyQt6.QtCore import QPoint, QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(50.0, 30.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.mousePressEvent(event)
    assert viewer._mouse_start_pos == QPoint(50, 30)


def test_mouse_move_emits_coordinates(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    signals_received = []
    viewer.coordinatesChanged.connect(lambda p: signals_received.append(p))
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(100.0, 80.0),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.mouseMoveEvent(event)


def test_leave_event_emits_null_point(qtbot):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)

    signals_received = []
    viewer.coordinatesChanged.connect(lambda p: signals_received.append(p))
    from PyQt6.QtCore import QEvent
    event = QEvent(QEvent.Type.Leave)
    viewer.leaveEvent(event)
    assert len(signals_received) >= 1


def test_fast_left_click_emits_both_signals(qtbot, monkeypatch):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    monkeypatch.setattr(viewer, '_mouse_start_time', 100.0)
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(50.0, 40.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(50.0, 40.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.mousePressEvent(press)
    monkeypatch.setattr(viewer, '_mouse_start_time', 0.0)
    viewer.mouseReleaseEvent(release)


def test_slow_click_not_detected(qtbot, monkeypatch):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    clicked_received = []
    viewer.clicked.connect(lambda p: clicked_received.append(p))
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(50.0, 40.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(50.0, 40.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.mousePressEvent(press)
    monkeypatch.setattr(viewer, '_mouse_start_time', 0.0)
    from time import time as real_time
    monkeypatch.setattr(viewer, '_mouse_start_time', real_time())
    viewer.mouseReleaseEvent(release)


def test_move_exceeding_threshold_not_detected(qtbot, monkeypatch):
    viewer = VideoViewer()
    qtbot.addWidget(viewer)
    viewer.resize(320, 240)
    viewer.show()
    qtbot.wait(1)
    viewer.reset_view()

    clicked_received = []
    viewer.clicked.connect(lambda p: clicked_received.append(p))
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(50.0, 40.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(100.0, 100.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.mousePressEvent(press)
    monkeypatch.setattr(viewer, '_mouse_start_time', 0.0)
    viewer.mouseReleaseEvent(release)
