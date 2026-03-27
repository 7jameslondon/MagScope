from importlib import resources


def _load_logo_pixmap():
    from PyQt6.QtGui import QPixmap

    logo_resource = resources.files("magscope").joinpath("assets/logo.png")
    if not logo_resource.is_file():
        return None

    with resources.as_file(logo_resource) as logo_path:
        pixmap = QPixmap(str(logo_path))

    if pixmap.isNull():
        return None

    return pixmap


def run_startup_splash(close_event) -> None:
    """Display the startup splash until ``close_event`` is set."""

    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

    app = QApplication.instance()
    if app is None:
        app = QApplication(["MagScope Splash"])

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
    )

    layout = QVBoxLayout(window)
    layout.setContentsMargins(36, 36, 36, 36)
    layout.setSpacing(0)

    logo = QLabel()
    logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if pixmap := _load_logo_pixmap():
        logo.setPixmap(
            pixmap.scaled(
                568,
                288,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
    layout.addWidget(logo)

    window.resize(640, 360)
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
