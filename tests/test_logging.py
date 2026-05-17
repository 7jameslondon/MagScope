import logging

import pytest

from magscope._logging import _ROOT_LOGGER_NAME, configure_logging, get_logger


@pytest.fixture(autouse=True)
def _clean_root_logger():
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    yield
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


def test_get_logger_returns_child_under_magscope():
    child = get_logger("camera")
    assert isinstance(child, logging.Logger)
    assert child.name == "magscope.camera"


def test_configure_logging_sets_warning_by_default():
    configure_logging()
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    assert logger.level == logging.WARNING


def test_configure_logging_sets_info_when_verbose():
    configure_logging(verbose=True)
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    assert logger.level == logging.INFO


def test_configure_logging_uses_explicit_level():
    configure_logging(level=logging.ERROR)
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    assert logger.level == logging.ERROR


def test_configure_logging_explicit_level_overrides_verbose():
    configure_logging(verbose=True, level=logging.ERROR)
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    assert logger.level == logging.ERROR


def test_configure_logging_adds_stream_handler_when_no_handlers_exist():
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    assert len(logger.handlers) == 0

    configure_logging()
    assert len(logger.handlers) == 1
    handler = logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.level == logging.NOTSET


def test_configure_logging_uses_message_formatter():
    configure_logging()
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    handler = logger.handlers[0]
    assert handler.formatter is not None
    assert handler.formatter._fmt == "%(message)s"


def test_configure_logging_does_not_add_extra_handlers_on_second_call():
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    configure_logging()
    assert len(logger.handlers) == 1

    configure_logging(verbose=True)
    assert len(logger.handlers) == 1
    assert logger.level == logging.INFO


def test_configure_logging_sets_propagate_to_false():
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    assert logger.propagate is True

    configure_logging()
    assert logger.propagate is False
