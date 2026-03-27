from pathlib import Path


def run_startup_splash(close_event) -> None:
    """Display the startup splash until ``close_event`` is set."""

    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QPixmap
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

    logo_path = Path(__file__).resolve().parents[1] / 'assets' / 'logo.png'
    logo = QLabel()
    logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if logo_path.exists():
        pixmap = QPixmap(str(logo_path))
        if not pixmap.isNull():
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
