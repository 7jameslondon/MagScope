"""Utilities for coordinating inter-process communication helpers.

This module contains small helper functions that encapsulate recurring
patterns around draining duplex pipes and waiting on multiprocessing events.
They are used by both the main :mod:`magscope.scope` orchestration loop and
manager subprocesses to share the same quit/acknowledgement semantics.
"""

from __future__ import annotations

from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event as EventType
from typing import TYPE_CHECKING, Callable, Mapping, Type

import logging

if TYPE_CHECKING:
    from magscope import ManagerProcessBase


logger = logging.getLogger(__name__)


class Message:
    """Light-weight envelope for MagScope inter-process RPC calls."""

    def __init__(
        self,
        to: Type["ManagerProcessBase"] | str,
        meth: Callable | str,
        *args,
        **kwargs,
    ) -> None:
        if isinstance(to, str):
            self.to = to
        else:
            self.to = to.__name__

        if isinstance(meth, str):
            self.meth = meth
        else:
            self.meth = meth.__name__

        self.args = args
        if "args" in kwargs:
            self.args = self.args + kwargs["args"]
            del kwargs["args"]
        self.kwargs = kwargs

    def __str__(self) -> str:
        return (
            f"Message(to={self.to}, func={self.meth}, args={self.args}, "
            f"kwargs={self.kwargs})"
        )


def _drain_pending_messages(pipe: Connection) -> None:
    """Consume any queued messages on ``pipe`` until it is empty."""

    while pipe.poll():
        try:
            pipe.recv()
        except EOFError:
            break


def wait_for_event_and_drain(
    event: EventType,
    pipe: Connection | None,
    *,
    poll_interval: float = 0.05,
    is_running: Callable[[], bool] | None = None,
) -> None:
    """Block until ``event`` is set while opportunistically draining ``pipe``.

    Parameters
    ----------
    event:
        The event whose signalling indicates the wait may stop.
    pipe:
        The IPC pipe to drain while waiting.  If ``None`` no draining occurs.
    poll_interval:
        The maximum amount of time (in seconds) to wait in each loop
        iteration.  A small value prevents busy-waiting without introducing
        noticeable latency.
    is_running:
        Optional callback returning ``False`` once the associated process has
        exited.  When provided the function returns as soon as the callback
        reports the process is no longer alive.
    """

    while True:
        if event.wait(timeout=poll_interval):
            return
        if is_running is not None and not is_running():
            return
        if pipe is not None:
            try:
                has_data = pipe.poll()
            except (OSError, EOFError):
                break
            if has_data:
                _drain_pending_messages(pipe)


def relay_message_to_processes(
    message: Message,
    processes: Mapping[str, BaseProcess],
    pipes: Mapping[str, Connection],
    quitting_events: Mapping[str, EventType],
    *,
    poll_interval: float = 0.05,
) -> None:
    """Send ``message`` to every active process and await quit acknowledgements.

    The helper mirrors the semantics previously implemented inline inside
    :mod:`magscope.scope`: only living processes with unset quitting events
    receive the broadcast, and ``quit`` messages trigger an acknowledgement
    wait that keeps draining pipes to avoid deadlocks.
    """

    for name, pipe in pipes.items():
        proc = processes.get(name)
        quitting_event = quitting_events.get(name)
        if proc is None or quitting_event is None:
            continue
        if not proc.is_alive() or quitting_event.is_set():
            continue
        try:
            pipe.send(message)
        except (BrokenPipeError, EOFError, OSError):
            logger.warning(
                "Failed to send message %s to process %s; skipping",
                message,
                name,
                exc_info=True,
            )
            continue
        if message.meth == 'quit':
            wait_for_event_and_drain(
                quitting_event,
                pipe,
                poll_interval=poll_interval,
                is_running=proc.is_alive,
            )
