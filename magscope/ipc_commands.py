from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


class CommandRegistrationError(RuntimeError):
    """Raised when command registration fails."""


class CommandDispatchError(RuntimeError):
    """Raised when dispatching an unknown or invalid command."""


@dataclass(frozen=True, slots=True)
class Command:
    """A typed IPC command with positional and keyword arguments."""

    name: str
    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:  # pragma: no cover - trivial validation
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "kwargs", dict(self.kwargs))


def command_handler(name: str | None = None):
    """Decorator marking a method as an IPC command handler.

    The decorated callable is registered under ``name`` (or its own ``__name__``
    when ``name`` is omitted) within :class:`CommandRegistry`.
    """

    def decorator(func: Callable):
        setattr(func, "_ipc_command", name or func.__name__)
        return func

    return decorator


class CommandRegistry:
    """Registry mapping command names to callables for a single process."""

    def __init__(self, owner: str):
        self.owner = owner
        self._handlers: dict[str, Callable] = {}

    def register(self, name: str, handler: Callable) -> None:
        if name in self._handlers:
            raise CommandRegistrationError(
                f"Command '{name}' already registered for {self.owner}"
            )
        self._handlers[name] = handler

    def register_all(self, handlers: Iterable[tuple[str, Callable]]) -> None:
        for name, handler in handlers:
            self.register(name, handler)

    def validate(self, command: Command) -> None:
        if command.name not in self._handlers:
            raise CommandDispatchError(
                f"Unknown command '{command.name}' for {self.owner}"
            )

    def dispatch(self, command: Command) -> None:
        self.validate(command)
        self._handlers[command.name](*command.args, **command.kwargs)

    def iter_commands(self) -> Iterable[str]:
        return self._handlers.keys()
