from importlib import resources

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QApplication, QLabel, QProgressBar

from magscope.startup_splash import _STARTUP_SPLASH_LOGO_SIZE, _build_startup_splash_window, _load_logo_pixmap


def test_startup_splash_logo_is_packaged():
    logo_resource = resources.files("magscope").joinpath("assets/logo.png")

    assert logo_resource.is_file()


def test_startup_splash_includes_indeterminate_progress_bar(qtbot):
    window = _build_startup_splash_window()
    qtbot.addWidget(window)

    progress_bar = window.findChild(QProgressBar, "startupSplashProgressBar")
    progress_label = window.findChild(QLabel, "startupSplashProgressLabel")

    assert progress_bar is not None
    assert progress_label is not None
    assert progress_bar.minimum() == 0
    assert progress_bar.maximum() == 0
    assert progress_bar.isTextVisible() is False
    assert progress_bar.height() == 25
    assert progress_label.text() == 'loading ...'


def test_startup_splash_uses_fixed_logical_logo_size(qtbot):
    window = _build_startup_splash_window()
    qtbot.addWidget(window)

    logo = window.findChild(QLabel, "startupSplashLogo")
    progress_bar = window.findChild(QProgressBar, "startupSplashProgressBar")

    assert logo is not None
    assert progress_bar is not None
    target_size = QSize(*_STARTUP_SPLASH_LOGO_SIZE)
    assert logo.size() == target_size

    device_pixel_ratio = 1.0
    if screen := QApplication.instance().primaryScreen():
        device_pixel_ratio = screen.devicePixelRatio()

    assert logo.pixmap().devicePixelRatio() == device_pixel_ratio
    expected_pixmap_bounds = QSize(
        round(target_size.width() * device_pixel_ratio),
        round(target_size.height() * device_pixel_ratio),
    )
    assert logo.pixmap().size().width() <= expected_pixmap_bounds.width()
    assert logo.pixmap().size().height() <= expected_pixmap_bounds.height()
    assert (
        logo.pixmap().size().width() == expected_pixmap_bounds.width()
        or logo.pixmap().size().height() == expected_pixmap_bounds.height()
    )
    assert window.width() == target_size.width() + 30
    assert window.height() == target_size.height() + progress_bar.height() + 30


def test_build_splash_window_is_frameless(qtbot):
    window = _build_startup_splash_window()
    qtbot.addWidget(window)

    flags = window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint


def test_build_splash_window_content_margins(qtbot):
    window = _build_startup_splash_window()
    qtbot.addWidget(window)

    inner_widget = window.findChild(QLabel, "startupSplashLogo")
    assert inner_widget is not None
    parent_layout = inner_widget.parentWidget().layout()
    if parent_layout:
        margins = parent_layout.contentsMargins()
        assert margins.left() >= 0


def test_build_splash_window_white_background(qtbot):
    window = _build_startup_splash_window()
    qtbot.addWidget(window)

    stylesheet = window.styleSheet()
    assert 'white' in stylesheet or 'background' in stylesheet


def test_load_logo_pixmap_returns_pixmap(qtbot):
    pixmap = _load_logo_pixmap()
    assert pixmap is not None
    assert not pixmap.isNull()


def test_load_logo_pixmap_has_correct_logical_size():
    pixmap = _load_logo_pixmap()
    expected = QSize(*_STARTUP_SPLASH_LOGO_SIZE)
    screen = QApplication.instance().primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    assert abs(pixmap.size().width() - round(expected.width() * dpr)) <= 3
    assert abs(pixmap.size().height() - round(expected.height() * dpr)) <= 3


def test_load_logo_pixmap_returns_none_when_missing(monkeypatch):
    import importlib.resources

    class FakeTraversable:
        def is_file(self):
            return False

        def joinpath(self, path):
            return self

    class FakeFiles:
        def __call__(self, package):
            return FakeTraversable()

    monkeypatch.setattr(importlib.resources, 'files', FakeFiles())
    result = _load_logo_pixmap()
    assert result is None
