from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Type

if TYPE_CHECKING:
    from magscope.processes import ManagerProcessBase
    from magscope.scripting import ScriptStatus
    from magscope.utils import AcquisitionMode


class Delivery(StrEnum):
    DIRECT = 'direct'
    BROADCAST = 'broadcast'
    MAG_SCOPE = 'mag_scope'


class CommandRegistrationError(RuntimeError):
    """Base error for command registration problems."""


class CommandConflictError(CommandRegistrationError):
    """Raised when a command is registered more than once with incompatible metadata."""


class MissingCommandHandlerError(CommandRegistrationError):
    """Raised when a handler is missing for a registered command."""


class UnknownCommandError(RuntimeError):
    """Raised when dispatch is attempted for an unknown command."""


@dataclass(frozen=True)
class Command:
    """Typed IPC payload sent between processes."""


@dataclass(frozen=True)
class CommandSpec:
    command_type: type[Command]
    handler: str
    target: str
    delivery: Delivery


@dataclass(frozen=True)
class HandlerRegistration:
    command_type: type[Command]
    handler: str
    delivery: Delivery
    target_override: str | None = None


def register_command(
    command_type: type[Command],
    *,
    delivery: Delivery = Delivery.DIRECT,
    target: str | None = None,
):
    """Decorator to associate an IPC command type with a handler method."""

    def decorator(func):
        func._ipc_command = command_type
        func._ipc_delivery = delivery
        func._ipc_target_override = target
        return func

    return decorator


def _collect_handler_registrations(cls: Type) -> Iterable[HandlerRegistration]:
    """Yield command registrations declared on ``cls`` and its bases."""

    seen: set[str] = set()
    for base in cls.mro():
        for name, func in base.__dict__.items():
            command_type = getattr(func, "_ipc_command", None)
            if command_type is None or name in seen:
                continue
            seen.add(name)
            delivery = getattr(func, "_ipc_delivery", Delivery.DIRECT)
            target_override = getattr(func, "_ipc_target_override", None)
            yield HandlerRegistration(
                command_type=command_type,
                handler=name,
                delivery=delivery,
                target_override=target_override,
            )


def command_kwargs(command: Command) -> dict[str, Any]:
    """Return the payload of a command as keyword arguments."""

    return {field.name: getattr(command, field.name) for field in fields(command)}


class CommandRegistry:
    """Registry mapping IPC command types to their handlers and destinations."""

    def __init__(self):
        self._specs: dict[type[Command], CommandSpec] = {}
        self._handler_index: dict[tuple[str, str], type[Command]] = {}

    def register(
        self,
        *,
        command_type: type[Command],
        handler: str,
        owner: Type,
        delivery: Delivery,
        target: str,
    ) -> None:
        """Register a command handler."""

        if not is_dataclass(command_type):
            raise TypeError(f"{command_type.__name__} must be a dataclass")
        if not issubclass(command_type, Command):
            raise TypeError(f"{command_type.__name__} must subclass Command")

        if not hasattr(owner, handler):
            raise MissingCommandHandlerError(
                f"{owner.__name__}.{handler} is not defined for command {command_type.__name__}"
            )

        spec = CommandSpec(
            command_type=command_type,
            handler=handler,
            target=target,
            delivery=delivery,
        )
        existing = self._specs.get(command_type)
        if existing is not None and existing != spec:
            raise CommandConflictError(
                f"Command {command_type.__name__} already registered with {existing}"
            )
        self._specs[command_type] = spec

        handler_key = (owner.__name__, handler)
        mapped_command = self._handler_index.get(handler_key)
        if mapped_command is not None and mapped_command is not command_type:
            raise CommandConflictError(
                f"Handler {owner.__name__}.{handler} already mapped to {mapped_command.__name__}"
            )
        self._handler_index[handler_key] = command_type
        target_key = (target, handler)
        mapped_target_command = self._handler_index.get(target_key)
        if mapped_target_command is not None and mapped_target_command is not command_type:
            raise CommandConflictError(
                f"Handler {target}.{handler} already mapped to {mapped_target_command.__name__}"
            )
        self._handler_index[target_key] = command_type

    def register_manager(self, manager: "ManagerProcessBase") -> None:
        """Register all decorated command handlers on ``manager``."""

        target = getattr(manager, "name", type(manager).__name__)
        for registration in _collect_handler_registrations(type(manager)):
            target_name = registration.target_override or target
            self.register(
                command_type=registration.command_type,
                handler=registration.handler,
                owner=type(manager),
                delivery=registration.delivery,
                target=target_name,
            )

    def register_object(self, obj: object, *, target: str | None = None) -> None:
        """Register decorated handlers on arbitrary objects (e.g., MagScope)."""

        target_name = target or type(obj).__name__
        for registration in _collect_handler_registrations(type(obj)):
            self.register(
                command_type=registration.command_type,
                handler=registration.handler,
                owner=type(obj),
                delivery=registration.delivery,
                target=registration.target_override or target_name,
            )

    def route_for(self, command: Command) -> CommandSpec:
        """Return the route information for ``command``."""

        spec = self._specs.get(type(command))
        if spec is None:
            raise UnknownCommandError(f"Command {type(command).__name__} is not registered")
        return spec

    def handlers_for_target(self, target: str) -> dict[type[Command], CommandSpec]:
        """Return handler specs applicable to ``target``."""

        handlers: dict[type[Command], CommandSpec] = {}
        for command_type, spec in self._specs.items():
            if spec.delivery == Delivery.BROADCAST or spec.target == target:
                handlers[command_type] = spec
        return handlers

    def validate_targets(self, processes: Mapping[str, "ManagerProcessBase"]) -> None:
        """Ensure every command has a reachable target and handler."""

        for spec in self._specs.values():
            if spec.delivery == Delivery.MAG_SCOPE:
                continue
            if spec.delivery == Delivery.DIRECT:
                process = processes.get(spec.target)
                if process is None:
                    raise MissingCommandHandlerError(
                        f"Command {spec.command_type.__name__} targets unknown process {spec.target}"
                    )
                if not hasattr(process, spec.handler):
                    raise MissingCommandHandlerError(
                        f"Process {spec.target} missing handler {spec.handler} "
                        f"for command {spec.command_type.__name__}"
                    )
            if spec.delivery == Delivery.BROADCAST:
                missing = [
                    name
                    for name, proc in processes.items()
                    if not hasattr(proc, spec.handler)
                ]
                if missing:
                    raise MissingCommandHandlerError(
                        f"Command {spec.command_type.__name__} has no handler "
                        f"{spec.handler} on processes: {', '.join(sorted(missing))}"
                    )

    def command_for_handler(self, owner: str, handler: str) -> type[Command]:
        """Return the command type bound to ``owner.handler``."""

        key = (owner, handler)
        command_type = self._handler_index.get(key)
        if command_type is None:
            raise UnknownCommandError(f"No command registered for {owner}.{handler}")
        return command_type


# === Commands ===


@dataclass(frozen=True)
class QuitCommand(Command):
    """Request that all manager processes exit."""


@dataclass(frozen=True)
class SetSettingsCommand(Command):
    settings: dict


@dataclass(frozen=True)
class SetAcquisitionOnCommand(Command):
    value: bool


@dataclass(frozen=True)
class SetAcquisitionDirOnCommand(Command):
    value: bool


@dataclass(frozen=True)
class SetAcquisitionModeCommand(Command):
    mode: "AcquisitionMode"


@dataclass(frozen=True)
class SetAcquisitionDirCommand(Command):
    value: str | None


@dataclass(frozen=True)
class SetBeadRoisCommand(Command):
    value: dict[int, tuple[int, int, int, int]]


@dataclass(frozen=True)
class LogExceptionCommand(Command):
    process_name: str
    details: str


@dataclass(frozen=True)
class UpdateCameraSettingCommand(Command):
    name: str
    value: str


@dataclass(frozen=True)
class UpdateVideoBufferPurgeCommand(Command):
    t: float


@dataclass(frozen=True)
class MoveBeadCommand(Command):
    id: int
    dx: int
    dy: int


@dataclass(frozen=True)
class UpdateXYLockEnabledCommand(Command):
    value: bool


@dataclass(frozen=True)
class UpdateXYLockIntervalCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateXYLockMaxCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateXYLockWindowCommand(Command):
    value: int


@dataclass(frozen=True)
class UpdateZLockEnabledCommand(Command):
    value: bool


@dataclass(frozen=True)
class UpdateZLockBeadCommand(Command):
    value: int


@dataclass(frozen=True)
class UpdateZLockTargetCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateZLockIntervalCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateZLockMaxCommand(Command):
    value: float


@dataclass(frozen=True)
class UpdateScriptStatusCommand(Command):
    status: "ScriptStatus"


@dataclass(frozen=True)
class ShowMessageCommand(Command):
    text: str
    details: str | None = None


@dataclass(frozen=True)
class UpdateZLUTMetadataCommand(Command):
    filepath: str | None = None
    z_min: float | None = None
    z_max: float | None = None
    step_size: float | None = None
    profile_length: int | None = None


@dataclass(frozen=True)
class LoadZLUTCommand(Command):
    filepath: str


@dataclass(frozen=True)
class UnloadZLUTCommand(Command):
    """Clear the currently loaded Z-LUT."""


@dataclass(frozen=True)
class RemoveBeadFromPendingMovesCommand(Command):
    id: int


@dataclass(frozen=True)
class SetXYLockOnCommand(Command):
    value: bool


@dataclass(frozen=True)
class ExecuteXYLockCommand(Command):
    now: float | None = None


@dataclass(frozen=True)
class SetXYLockIntervalCommand(Command):
    value: float


@dataclass(frozen=True)
class SetXYLockMaxCommand(Command):
    value: float


@dataclass(frozen=True)
class SetXYLockWindowCommand(Command):
    value: int


@dataclass(frozen=True)
class SetZLockOnCommand(Command):
    value: bool


@dataclass(frozen=True)
class SetZLockBeadCommand(Command):
    value: int


@dataclass(frozen=True)
class SetZLockTargetCommand(Command):
    value: float


@dataclass(frozen=True)
class SetZLockIntervalCommand(Command):
    value: float


@dataclass(frozen=True)
class SetZLockMaxCommand(Command):
    value: float


@dataclass(frozen=True)
class GetCameraSettingCommand(Command):
    name: str


@dataclass(frozen=True)
class SetCameraSettingCommand(Command):
    name: str
    value: str


@dataclass(frozen=True)
class LoadScriptCommand(Command):
    path: str


@dataclass(frozen=True)
class StartScriptCommand(Command):
    """Start the currently loaded script."""


@dataclass(frozen=True)
class PauseScriptCommand(Command):
    """Pause the running script."""


@dataclass(frozen=True)
class ResumeScriptCommand(Command):
    """Resume a paused script."""


@dataclass(frozen=True)
class StartSleepCommand(Command):
    duration: float


@dataclass(frozen=True)
class UpdateWaitingCommand(Command):
    """Signal that a wait condition has been satisfied."""
