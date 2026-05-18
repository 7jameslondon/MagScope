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


# ---------------------------------------------------------------------------
# create_pipes, broadcast_command, drain_pipe_until_quit
# ---------------------------------------------------------------------------

def test_create_pipes_returns_parent_and_child_ends():
    from magscope.ipc import create_pipes

    class StubProcess:
        def __init__(self, name):
            self.name = name

    processes = {"CameraManager": StubProcess("CameraManager"), "UIManager": StubProcess("UIManager")}
    parent_ends, child_ends = create_pipes(processes)
    assert set(parent_ends.keys()) == {"CameraManager", "UIManager"}
    assert set(child_ends.keys()) == {"CameraManager", "UIManager"}


def test_broadcast_command_sends_to_alive_not_quitting():
    from magscope.ipc import broadcast_command

    class FakeProcess:
        def __init__(self, alive=True):
            self._alive = alive

        def is_alive(self):
            return self._alive

    class FakeEvent:
        def __init__(self, set_flag=False):
            self._flag = set_flag

        def is_set(self):
            return self._flag

    class FakePipe:
        def __init__(self):
            self.sent = []

        def send(self, command):
            self.sent.append(command)

    pipe_live = FakePipe()
    pipe_dead = FakePipe()
    pipe_quitting = FakePipe()
    command = ExampleCommand(value=1)

    broadcast_command(
        command,
        pipes={"live": pipe_live, "dead": pipe_dead, "quitting": pipe_quitting},
        processes={
            "live": FakeProcess(alive=True),
            "dead": FakeProcess(alive=False),
            "quitting": FakeProcess(alive=True),
        },
        quitting_events={
            "live": FakeEvent(),
            "dead": FakeEvent(),
            "quitting": FakeEvent(set_flag=True),
        },
    )
    assert len(pipe_live.sent) == 1
    assert isinstance(pipe_live.sent[0], ExampleCommand)
    assert pipe_dead.sent == []
    assert pipe_quitting.sent == []


def test_drain_pipe_until_quit_stops_on_event(monkeypatch):
    from magscope.ipc import drain_pipe_until_quit

    class FakeEvent:
        def __init__(self):
            self._set = False
            self._set_after = 3
            self._poll_count = 0

        def is_set(self):
            return self._set

        def set(self):
            self._set = True

    class FakePipe:
        def __init__(self):
            self._data = [ExampleCommand(value=1), OtherCommand()]

        def poll(self):
            return bool(self._data)

        def recv(self):
            return self._data.pop(0) if self._data else None

    quitting_event = FakeEvent()
    pipe = FakePipe()

    monkeypatch.setattr(quitting_event, "is_set", lambda: not bool(pipe._data))

    drain_pipe_until_quit(pipe, quitting_event, poll_interval=None)


def test_register_rejects_empty_target():
    registry = CommandRegistry()

    class TestOwner:
        def handle(self):
            pass

    with pytest.raises(ValueError, match="Target cannot be empty"):
        registry.register(
            command_type=ExampleCommand,
            handler="handle",
            owner=TestOwner,
            delivery=Delivery.DIRECT,
            target="",
        )


def test_register_target_key_conflict():
    registry = CommandRegistry()

    class OwnerA:
        def handle(self):
            pass

    class OwnerB:
        def handle(self):
            pass

    registry.register(
        command_type=ExampleCommand,
        handler="handle",
        owner=OwnerA,
        delivery=Delivery.DIRECT,
        target="ManagerA",
    )
    with pytest.raises(CommandConflictError, match="already mapped"):
        registry.register(
            command_type=OtherCommand,
            handler="handle",
            owner=OwnerB,
            delivery=Delivery.DIRECT,
            target="ManagerA",
        )


def test_validate_targets_skips_mag_scope():
    registry = CommandRegistry()

    class ScopeOwner:
        def handle_scope(self):
            pass

    registry.register(
        command_type=ExampleCommand,
        handler="handle_scope",
        owner=ScopeOwner,
        delivery=Delivery.MAG_SCOPE,
        target="MagScope",
    )

    registry.validate_targets({})
