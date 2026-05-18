from importlib import resources

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QApplication, QLabel, QProgressBar

from magscope import startup_splash as startup_splash_module
from magscope.startup_splash import (
    _STARTUP_SPLASH_LOGO_SIZE,
    _build_startup_splash_window,
    _load_logo_pixmap,
)


class FakeIcon:
    def __init__(self, *, null=False):
        self._null = null

    def isNull(self):
        return self._null


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)


class FakeTimer:
    instances = []
    single_shots = []

    def __init__(self):
        self.interval = None
        self.started = False
        self.timeout = FakeSignal()
        self.instances.append(self)

    @classmethod
    def singleShot(cls, interval, callback):
        cls.single_shots.append((interval, callback))
        callback()

    def setInterval(self, interval):
        self.interval = interval

    def start(self):
        self.started = True
        for callback in self.timeout.callbacks:
            callback()


class FakeFrameGeometry:
    def __init__(self):
        self.center = None

    def moveCenter(self, center):
        self.center = center

    def topLeft(self):
        return ("top-left", self.center)


class FakeWindow:
    def __init__(self):
        self.closed = False
        self.frame = FakeFrameGeometry()
        self.moved_to = None
        self.shown = False
        self.window_icons = []

    def close(self):
        self.closed = True

    def frameGeometry(self):
        return self.frame

    def move(self, position):
        self.moved_to = position

    def setWindowIcon(self, icon):
        self.window_icons.append(icon)

    def show(self):
        self.shown = True


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
    expected_bounds = QSize(
        round(expected.width() * dpr),
        round(expected.height() * dpr),
    )
    assert pixmap.size().width() <= expected_bounds.width()
    assert pixmap.size().height() <= expected_bounds.height()
    assert (
        abs(pixmap.size().width() - expected_bounds.width()) <= 5
        or abs(pixmap.size().height() - expected_bounds.height()) <= 5
    )


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


def test_load_logo_pixmap_returns_none_when_pixmap_is_null(monkeypatch):
    class NullPixmap:
        def __init__(self, path):
            self.path = path

        def isNull(self):
            return True

    monkeypatch.setattr("PyQt6.QtGui.QPixmap", NullPixmap)

    assert _load_logo_pixmap() is None


def test_load_logo_pixmap_uses_default_dpr_without_app(monkeypatch):
    class FakeApplication:
        @staticmethod
        def instance():
            return None

    class FakePixmap:
        instances = []

        def __init__(self, path):
            self.device_pixel_ratio = None
            self.path = path
            self.scaled_size = None
            self.instances.append(self)

        def isNull(self):
            return False

        def scaled(self, size, aspect_mode, transform_mode):
            self.scaled_size = size
            return self

        def setDevicePixelRatio(self, device_pixel_ratio):
            self.device_pixel_ratio = device_pixel_ratio

    monkeypatch.setattr("PyQt6.QtWidgets.QApplication", FakeApplication)
    monkeypatch.setattr("PyQt6.QtGui.QPixmap", FakePixmap)

    pixmap = _load_logo_pixmap()

    assert pixmap is FakePixmap.instances[0]
    assert pixmap.device_pixel_ratio == 1.0
    assert pixmap.scaled_size == QSize(*_STARTUP_SPLASH_LOGO_SIZE)


def test_build_splash_window_without_logo_pixmap(qtbot, monkeypatch):
    monkeypatch.setattr(startup_splash_module, "_load_logo_pixmap", lambda: None)

    window = _build_startup_splash_window()
    qtbot.addWidget(window)

    logo = window.findChild(QLabel, "startupSplashLogo")
    progress_bar = window.findChild(QProgressBar, "startupSplashProgressBar")

    assert logo is not None
    assert progress_bar is not None
    pixmap = logo.pixmap()
    assert pixmap is None or pixmap.isNull()


def test_run_startup_splash_creates_app_and_closes_when_event_is_set(monkeypatch):
    class FakeGeometry:
        def center(self):
            return "screen-center"

    class FakeScreen:
        def availableGeometry(self):
            return FakeGeometry()

    class FakeApplication:
        instances = []

        @staticmethod
        def instance():
            return None

        def __init__(self, args):
            self.args = args
            self.exec_calls = 0
            self.quit_calls = 0
            self.window_icons = []
            self.instances.append(self)

        def exec(self):
            self.exec_calls += 1

        def primaryScreen(self):
            return FakeScreen()

        def quit(self):
            self.quit_calls += 1

        def setWindowIcon(self, icon):
            self.window_icons.append(icon)

    class CloseEvent:
        def is_set(self):
            return True

    FakeTimer.instances = []
    FakeTimer.single_shots = []
    icon = FakeIcon()
    native_icon_windows = []
    set_app_user_model_id_calls = []
    window = FakeWindow()

    monkeypatch.setattr("PyQt6.QtCore.QTimer", FakeTimer)
    monkeypatch.setattr("PyQt6.QtWidgets.QApplication", FakeApplication)
    monkeypatch.setattr(
        startup_splash_module,
        "apply_windows_native_window_icon",
        native_icon_windows.append,
    )
    monkeypatch.setattr(startup_splash_module, "load_app_icon", lambda: icon)
    monkeypatch.setattr(
        startup_splash_module,
        "_build_startup_splash_window",
        lambda: window,
    )
    monkeypatch.setattr(
        startup_splash_module,
        "set_windows_app_user_model_id",
        lambda: set_app_user_model_id_calls.append(True),
    )

    startup_splash_module.run_startup_splash(CloseEvent())

    assert set_app_user_model_id_calls == [True]
    assert len(FakeApplication.instances) == 1
    app = FakeApplication.instances[0]
    assert app.args == ["MagScope Splash"]
    assert app.window_icons == [icon]
    assert app.quit_calls == 1
    assert app.exec_calls == 1
    assert window.window_icons == [icon]
    assert window.shown is True
    assert window.closed is True
    assert window.frame.center == "screen-center"
    assert window.moved_to == ("top-left", "screen-center")
    assert native_icon_windows == [window, window]
    assert FakeTimer.single_shots[0][0] == 0
    assert len(FakeTimer.instances) == 1
    assert FakeTimer.instances[0].interval == 30
    assert FakeTimer.instances[0].started is True


def test_run_startup_splash_reuses_app_with_null_icon_and_no_screen(monkeypatch):
    class FakeApplication:
        created_args = []
        existing_app = None

        @classmethod
        def instance(cls):
            return cls.existing_app

        def __init__(self, args):
            self.created_args.append(args)

        def exec(self):
            self.exec_calls += 1

        def primaryScreen(self):
            return None

        def processEvents(self):
            self.process_event_calls += 1

        def quit(self):
            self.quit_calls += 1

        def setWindowIcon(self, icon):
            self.window_icons.append(icon)

    class CloseEvent:
        def is_set(self):
            return False

    existing_app = FakeApplication.__new__(FakeApplication)
    existing_app.exec_calls = 0
    existing_app.process_event_calls = 0
    existing_app.quit_calls = 0
    existing_app.window_icons = []
    FakeApplication.existing_app = existing_app
    FakeTimer.instances = []
    FakeTimer.single_shots = []
    native_icon_windows = []
    window = FakeWindow()

    monkeypatch.setattr("PyQt6.QtCore.QTimer", FakeTimer)
    monkeypatch.setattr("PyQt6.QtWidgets.QApplication", FakeApplication)
    monkeypatch.setattr(
        startup_splash_module,
        "apply_windows_native_window_icon",
        native_icon_windows.append,
    )
    monkeypatch.setattr(
        startup_splash_module,
        "load_app_icon",
        lambda: FakeIcon(null=True),
    )
    monkeypatch.setattr(
        startup_splash_module,
        "_build_startup_splash_window",
        lambda: window,
    )
    monkeypatch.setattr(
        startup_splash_module,
        "set_windows_app_user_model_id",
        lambda: None,
    )

    startup_splash_module.run_startup_splash(CloseEvent())

    assert FakeApplication.created_args == []
    assert existing_app.window_icons == []
    assert existing_app.quit_calls == 0
    assert existing_app.exec_calls == 1
    assert window.window_icons == []
    assert window.shown is True
    assert window.closed is False
    assert window.moved_to is None
    assert native_icon_windows == [window, window]
    assert FakeTimer.single_shots[0][0] == 0
    assert len(FakeTimer.instances) == 1
    assert FakeTimer.instances[0].interval == 30
    assert FakeTimer.instances[0].started is True
