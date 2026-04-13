from importlib import resources

from PyQt6.QtWidgets import QLabel, QProgressBar

from magscope.startup_splash import _build_startup_splash_window


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
