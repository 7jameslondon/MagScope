"""Internal logging helpers for MagScope.

This module centralizes logging configuration so that the console
output can be silenced by default.

Only warnings and errors remain active unless :func:`configure_logging` is
invoked with ``verbose=True`` (or an explicit log level).  The helper returns
``logging.Logger`` instances namespaced under ``"magscope"`` so that
submodules can log without manually wiring handlers.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

_ROOT_LOGGER_NAME = "magscope"


def get_logger(name: str) -> logging.Logger:
    """Return a child logger scoped under ``magscope``.

    Parameters
    ----------
    name:
        Module or component name to suffix the root logger with.
    """

    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


def configure_logging(*, verbose: bool = False, level: Optional[int] = None) -> None:
    """Configure console logging for MagScope modules.

    By default only warnings and errors propagate to the console.  Passing
    ``verbose=True`` (or an explicit ``level``) elevates the logging so that
    informational messages become visible.
    """

    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    if level is None:
        level = logging.INFO if verbose else logging.WARNING

    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.NOTSET)
        formatter = logging.Formatter("%(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.propagate = False
