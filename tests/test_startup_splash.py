from importlib import resources

from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QProgressBar

from magscope.startup_splash import _build_startup_splash_window


def _packaged_logo_size():
    logo_resource = resources.files("magscope").joinpath("assets/logo.png")

    with resources.as_file(logo_resource) as logo_path:
        pixmap = QPixmap(str(logo_path))

    assert not pixmap.isNull()
    return pixmap.size()


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


def test_startup_splash_uses_logo_at_native_size(qtbot):
    window = _build_startup_splash_window()
    qtbot.addWidget(window)

    logo = window.findChild(QLabel, "startupSplashLogo")
    progress_bar = window.findChild(QProgressBar, "startupSplashProgressBar")

    assert logo is not None
    assert progress_bar is not None
    assert logo.pixmap().size() == _packaged_logo_size()
    if screen := QApplication.instance().primaryScreen():
        assert logo.pixmap().devicePixelRatio() == screen.devicePixelRatio()
    assert window.width() == logo.sizeHint().width() + 30
    assert window.height() == logo.sizeHint().height() + progress_bar.height() + 30
