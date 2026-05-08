from importlib import resources

from magscope.app_icon import load_app_icon, set_windows_app_user_model_id


_STARTUP_SPLASH_LOGO_SIZE = (568, 288)


def _load_logo_pixmap():
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtWidgets import QApplication

    logo_resource = resources.files("magscope").joinpath("assets/logo.png")
    if not logo_resource.is_file():
        return None

    with resources.as_file(logo_resource) as logo_path:
        pixmap = QPixmap(str(logo_path))

    if pixmap.isNull():
        return None

    device_pixel_ratio = 1.0
    app = QApplication.instance()
    if app is not None and (screen := app.primaryScreen()):
        device_pixel_ratio = screen.devicePixelRatio()

    target_size = QSize(
        round(_STARTUP_SPLASH_LOGO_SIZE[0] * device_pixel_ratio),
        round(_STARTUP_SPLASH_LOGO_SIZE[1] * device_pixel_ratio),
    )
    pixmap = pixmap.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    pixmap.setDevicePixelRatio(device_pixel_ratio)

    return pixmap


def _build_startup_splash_window():
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QGridLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

    window = QWidget()
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    window.setObjectName("startupSplash")
    window.setStyleSheet(
        "#startupSplash {"
        "background: #ffffff;"
        "border: 1px solid #d7dde2;"
        "}"
        "QLabel { background: transparent; }"
        "QProgressBar {"
        "background: #eef2f5;"
        "border: 1px solid #d7dde2;"
        "border-radius: 6px;"
        "}"
        "QProgressBar::chunk {"
        "background: #2a7fff;"
        "border-radius: 6px;"
        "}"
        "#startupSplashProgressLabel {"
        "color: #1c1f23;"
        "font-size: 12px;"
        "font-weight: 500;"
        "background: transparent;"
        "}"
    )

    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(15, 15, 15, 15)
    content_layout.setSpacing(0)

    logo = QLabel()
    logo.setObjectName("startupSplashLogo")
    logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if pixmap := _load_logo_pixmap():
        logo.setFixedSize(*_STARTUP_SPLASH_LOGO_SIZE)
        logo.setPixmap(pixmap)
    content_layout.addWidget(logo)
    layout.addWidget(content, 1)

    progress_container = QWidget()
    progress_layout = QGridLayout(progress_container)
    progress_layout.setContentsMargins(0, 0, 0, 0)

    progress_bar = QProgressBar()
    progress_bar.setObjectName("startupSplashProgressBar")
    progress_bar.setRange(0, 0)
    progress_bar.setTextVisible(False)
    progress_bar.setFixedHeight(25)
    progress_layout.addWidget(progress_bar, 0, 0)

    progress_label = QLabel("loading ...")
    progress_label.setObjectName("startupSplashProgressLabel")
    progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    progress_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    progress_layout.addWidget(progress_label, 0, 0)

    layout.addWidget(progress_container)

    window.resize(window.sizeHint())
    return window


def run_startup_splash(close_event) -> None:
    """Display the startup splash until ``close_event`` is set."""

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    set_windows_app_user_model_id()
    app = QApplication.instance()
    if app is None:
        app = QApplication(["MagScope Splash"])
    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    window = _build_startup_splash_window()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.show()

    if screen := app.primaryScreen():
        geometry = screen.availableGeometry()
        frame = window.frameGeometry()
        frame.moveCenter(geometry.center())
        window.move(frame.topLeft())

    timer = QTimer()
    timer.setInterval(30)
    timer.timeout.connect(lambda: window.close() if close_event.is_set() else None)
    timer.timeout.connect(lambda: app.quit() if close_event.is_set() else None)
    timer.start()
    app.exec()
