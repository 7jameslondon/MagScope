"""Helpers for optional EGrabber camera integration.

This project can run without the camera stack installed. Load the camera class
dynamically so editors/type checkers do not require a hard dependency on
`camera_egrabber` in this workspace.

Developer note: `camera_egrabber.py` is now source-controlled at the repository
root. Do not rely on `.venv/camera_egrabber.py`.
"""

from __future__ import annotations

from importlib import import_module
import os
from typing import Optional, Type
from warnings import warn


def _debug_enabled() -> bool:
    return os.getenv("MAGSCOPE_EGRABBER_DEBUG", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _debug(message: str):
    if _debug_enabled():
        warn(message, RuntimeWarning, stacklevel=2)


def load_egrabber_camera_class() -> Optional[Type[object]]:
    """Return EGrabberCamera class if available, otherwise None."""
    module_candidates = (
        # Primary local integration module for this workspace.
        "camera_egrabber",
        # Legacy fallback for older MagScope package layouts.
        "magscope.camera_egrabber",
    )
    for module_name in module_candidates:
        try:
            module = import_module(module_name)
        except ImportError as exc:
            _debug(f"Skipping optional camera module '{module_name}': {exc}")
            continue
        except Exception as exc:  # pragma: no cover - defensive path for runtime-only errors
            _debug(f"Failed while importing optional camera module '{module_name}': {exc!r}")
            continue
        camera_cls = getattr(module, "EGrabberCamera", None)
        if camera_cls is not None:
            return camera_cls
        _debug(f"Module '{module_name}' imported, but no EGrabberCamera class was found.")
    return None
