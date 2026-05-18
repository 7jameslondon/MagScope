"""Shared fixtures for UI tests that need Qt widgets in isolation."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("pytestqt")
pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QWidget


@pytest.fixture
def widget_parent(qtbot):
    """A bare QWidget suitable as a parent for isolated widget tests."""
    widget = QWidget()
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def mock_send_ipc():
    """Collects IPC commands sent via manager.send_ipc for assertion."""
    commands: list = []
    return commands
