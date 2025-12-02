from __future__ import annotations

from multiprocessing import Pipe
from multiprocessing.connection import Connection
import time
from typing import Mapping, TYPE_CHECKING
from warnings import warn

from magscope._logging import get_logger
from magscope.ipc_commands import Command

if TYPE_CHECKING:
    from multiprocessing.synchronize import Event as EventType
    from magscope.processes import ManagerProcessBase


def create_pipes(
    processes: Mapping[str, "ManagerProcessBase"],
) -> tuple[dict[str, Connection], dict[str, Connection]]:
    """Create duplex pipes for each managed process.

    Returns a pair of dictionaries mapping process names to the parent and child
    pipe ends, respectively. The parent ends are intended to be owned by the
    coordinating ``MagScope`` instance while the child ends are passed to
    individual manager processes.
    """
    parent_ends: dict[str, Connection] = {}
    child_ends: dict[str, Connection] = {}
    for name in processes:
        parent_end, child_end = Pipe()
        parent_ends[name] = parent_end
        child_ends[name] = child_end
    return parent_ends, child_ends


def broadcast_command(
    command: Command,
    *,
    pipes: Mapping[str, Connection],
    processes: Mapping[str, "ManagerProcessBase"],
    quitting_events: Mapping[str, "EventType"],
) -> None:
    """Send a command to all running, non-quitting processes."""
    logger = get_logger("ipc")
    for name, pipe in pipes.items():
        if processes[name].is_alive() and not quitting_events[name].is_set():
            try:
                pipe.send(command)
            except (BrokenPipeError, EOFError, OSError) as exc:
                warn(f"Failed to send {type(command).__name__} to {name}: {exc!r}")
                logger.debug("Dropping command %s for %s after send error", command, name)


def drain_pipe_until_quit(
    pipe: Connection,
    quitting_event: "EventType",
    *,
    poll_interval: float | None = 0.0,
) -> None:
    """Drain a pipe until the paired quit event is set."""
    while not quitting_event.is_set():
        if pipe.poll():
            pipe.recv()
        elif poll_interval:
            time.sleep(poll_interval)
