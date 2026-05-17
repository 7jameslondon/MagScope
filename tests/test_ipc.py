from dataclasses import dataclass

import pytest

from magscope.ipc import (
    CommandConflictError,
    CommandRegistry,
    Delivery,
    MissingCommandHandlerError,
    UnknownCommandError,
    command_kwargs,
)
from magscope.ipc_commands import Command


@dataclass(frozen=True)
class ExampleCommand(Command):
    value: int
    label: str = 'default'


@dataclass(frozen=True)
class OtherCommand(Command):
    pass


@dataclass(frozen=True)
class BroadcastCommand(Command):
    pass


@dataclass(frozen=True)
class NotACommand:
    value: int


class Owner:
    def handle_example(self):
        pass

    def handle_other(self):
        pass

    def handle_broadcast(self):
        pass


class ProcessWithHandlers:
    def handle_example(self):
        pass

    def handle_broadcast(self):
        pass


class ProcessWithoutHandlers:
    pass


def test_command_kwargs_returns_dataclass_payload():
    command = ExampleCommand(value=7, label='camera')

    assert command_kwargs(command) == {'value': 7, 'label': 'camera'}


def test_register_rejects_non_dataclass_command_type():
    registry = CommandRegistry()

    with pytest.raises(TypeError, match='must be a dataclass'):
        registry.register(
            command_type=object,
            handler='handle_example',
            owner=Owner,
            delivery=Delivery.DIRECT,
            target='Process',
        )


def test_register_rejects_dataclass_that_is_not_command():
    registry = CommandRegistry()

    with pytest.raises(TypeError, match='must subclass Command'):
        registry.register(
            command_type=NotACommand,
            handler='handle_example',
            owner=Owner,
            delivery=Delivery.DIRECT,
            target='Process',
        )


def test_register_rejects_missing_owner_handler():
    registry = CommandRegistry()

    with pytest.raises(MissingCommandHandlerError, match='missing handler missing'):
        registry.register(
            command_type=ExampleCommand,
            handler='missing',
            owner=Owner,
            delivery=Delivery.DIRECT,
            target='Process',
        )


def test_register_detects_handler_conflict():
    registry = CommandRegistry()
    registry.register(
        command_type=ExampleCommand,
        handler='handle_example',
        owner=Owner,
        delivery=Delivery.DIRECT,
        target='Process',
    )

    with pytest.raises(CommandConflictError, match='already mapped'):
        registry.register(
            command_type=OtherCommand,
            handler='handle_example',
            owner=Owner,
            delivery=Delivery.DIRECT,
            target='OtherProcess',
        )


def test_handlers_for_target_includes_broadcast_and_matching_direct_handlers():
    registry = CommandRegistry()
    registry.register(
        command_type=ExampleCommand,
        handler='handle_example',
        owner=Owner,
        delivery=Delivery.DIRECT,
        target='TargetProcess',
    )
    registry.register(
        command_type=OtherCommand,
        handler='handle_other',
        owner=Owner,
        delivery=Delivery.DIRECT,
        target='OtherProcess',
    )
    registry.register(
        command_type=BroadcastCommand,
        handler='handle_broadcast',
        owner=Owner,
        delivery=Delivery.BROADCAST,
        target='ManagerProcessBase',
    )

    handlers = registry.handlers_for_target('TargetProcess')

    assert set(handlers) == {ExampleCommand, BroadcastCommand}
    assert handlers[ExampleCommand].target == 'TargetProcess'
    assert handlers[BroadcastCommand].delivery == Delivery.BROADCAST


def test_route_for_rejects_unknown_command():
    registry = CommandRegistry()

    with pytest.raises(UnknownCommandError, match='is not registered'):
        registry.route_for(ExampleCommand(value=1))


def test_command_for_handler_uses_owner_and_target_aliases():
    registry = CommandRegistry()
    registry.register(
        command_type=ExampleCommand,
        handler='handle_example',
        owner=Owner,
        delivery=Delivery.DIRECT,
        target='TargetProcess',
    )

    assert registry.command_for_handler('Owner', 'handle_example') is ExampleCommand
    assert registry.command_for_handler('TargetProcess', 'handle_example') is ExampleCommand
    with pytest.raises(UnknownCommandError, match='No command registered'):
        registry.command_for_handler('Unknown', 'handle_example')


def test_validate_targets_rejects_unknown_direct_target():
    registry = CommandRegistry()
    registry.register(
        command_type=ExampleCommand,
        handler='handle_example',
        owner=Owner,
        delivery=Delivery.DIRECT,
        target='MissingProcess',
    )

    with pytest.raises(MissingCommandHandlerError, match='targets unknown process MissingProcess'):
        registry.validate_targets({})


def test_validate_targets_rejects_missing_direct_handler():
    registry = CommandRegistry()
    registry.register(
        command_type=ExampleCommand,
        handler='handle_example',
        owner=Owner,
        delivery=Delivery.DIRECT,
        target='TargetProcess',
    )

    with pytest.raises(MissingCommandHandlerError, match='missing handler handle_example'):
        registry.validate_targets({'TargetProcess': ProcessWithoutHandlers()})


def test_validate_targets_checks_broadcast_handlers_on_all_processes():
    registry = CommandRegistry()
    registry.register(
        command_type=BroadcastCommand,
        handler='handle_broadcast',
        owner=Owner,
        delivery=Delivery.BROADCAST,
        target='ManagerProcessBase',
    )

    with pytest.raises(MissingCommandHandlerError, match='MissingProcess'):
        registry.validate_targets({
            'ReadyProcess': ProcessWithHandlers(),
            'MissingProcess': ProcessWithoutHandlers(),
        })

    registry.validate_targets({
        'ReadyProcess': ProcessWithHandlers(),
        'OtherReadyProcess': ProcessWithHandlers(),
    })
