from __future__ import annotations

import pytest

from magscope.ipc import CommandRegistry, Delivery
from magscope.ipc_commands import ShowErrorCommand, SleepCommand, StartScriptCommand
import magscope.scripting as scripting
from magscope.utils import register_script_command


@pytest.fixture(autouse=True)
def clear_script_manager_singleton():
    type(scripting.ScriptManager)._instances.pop(scripting.ScriptManager, None)
    try:
        yield
    finally:
        type(scripting.ScriptManager)._instances.pop(scripting.ScriptManager, None)


def make_script_manager():
    manager = scripting.ScriptManager()
    sent_commands = []
    manager.send_ipc = sent_commands.append  # type: ignore[method-assign]
    return manager, sent_commands


def make_registered_script_manager():
    manager, sent_commands = make_script_manager()
    command_registry = CommandRegistry()
    command_registry.register_manager(manager)
    manager._command_registry = command_registry
    manager.script_registry.register_class_methods(scripting.ScriptManager)
    return manager, sent_commands


def test_script_append_records_command_and_wait_flag():
    script = scripting.Script()
    command = SleepCommand(duration=0.5)

    script.append(command, wait=True)

    assert script.steps == [scripting.ScriptStep(command=command, wait=True)]


def test_script_append_rejects_non_command_and_non_bool_wait():
    script = scripting.Script()

    with pytest.raises(TypeError, match='must be IPC commands'):
        script.append(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="'wait' must be a boolean"):
        script.append(SleepCommand(duration=0.0), wait='yes')  # type: ignore[arg-type]


def test_script_registry_collects_scriptable_methods_from_manager():
    registry = scripting.ScriptRegistry()

    registry.register_class_methods(scripting.ScriptManager)
    registration = registry(SleepCommand)

    assert registration.cls_name == 'ScriptManager'
    assert registration.meth_name == 'start_sleep'
    assert registration.command_type is SleepCommand
    assert registration.callable is scripting.ScriptManager.start_sleep


def test_script_registry_rejects_duplicate_command_mapping():
    registry = scripting.ScriptRegistry()

    class OtherManager:
        @register_script_command(SleepCommand)
        def sleep_somewhere_else(self):
            pass

    registry.register_class_methods(scripting.ScriptManager)
    with pytest.raises(ValueError, match='already registered'):
        registry.register_class_methods(OtherManager)


def test_check_script_rejects_invalid_steps():
    registry = scripting.ScriptRegistry()
    registry.register_class_methods(scripting.ScriptManager)

    with pytest.raises(TypeError, match='non-command step'):
        registry.check_script([
            scripting.ScriptStep(command=object()),  # type: ignore[arg-type]
        ])
    with pytest.raises(ValueError, match="'wait' must be a boolean"):
        registry.check_script([
            scripting.ScriptStep(command=SleepCommand(duration=0.0), wait='yes'),  # type: ignore[arg-type]
        ])


def test_check_script_rejects_unregistered_command():
    registry = scripting.ScriptRegistry()

    with pytest.raises(ValueError, match='unknown command'):
        registry.check_script([
            scripting.ScriptStep(command=SleepCommand(duration=0.0)),
        ])


def test_check_script_rejects_ipc_registry_mismatch():
    registry = scripting.ScriptRegistry()
    command_registry = CommandRegistry()

    class WrongScriptTarget:
        def start_sleep(self):
            pass

    registry.register_class_methods(scripting.ScriptManager)
    command_registry.register(
        command_type=StartScriptCommand,
        handler='start_sleep',
        owner=WrongScriptTarget,
        delivery=Delivery.DIRECT,
        target='ScriptManager',
    )

    with pytest.raises(ValueError, match='IPC registry maps that handler'):
        registry.check_script(
            [scripting.ScriptStep(command=SleepCommand(duration=0.0))],
            command_registry=command_registry,
        )


def test_execute_script_step_sets_waiting_and_sends_registered_command():
    manager, sent_commands = make_registered_script_manager()
    command = SleepCommand(duration=1.0)

    manager._execute_script_step(scripting.ScriptStep(command=command))

    assert manager._script_waiting is True
    assert sent_commands == [command]


def test_execute_script_step_rejects_missing_command_registry():
    manager, _sent_commands = make_script_manager()

    with pytest.raises(RuntimeError, match='without a registry'):
        manager._execute_script_step(scripting.ScriptStep(command=SleepCommand(duration=0.0)))


@pytest.mark.parametrize(
    ('source', 'message'),
    [
        ('answer = 42\n', 'No Script instance found in script file.'),
        (
            'from magscope.scripting import Script\n'
            'first = Script()\n'
            'second = Script()\n',
            'Multiple Script instances found in script file.',
        ),
    ],
)
def test_load_script_reports_error_for_zero_or_multiple_script_instances(tmp_path, source, message):
    manager, sent_commands = make_script_manager()
    script_path = tmp_path / 'automation.py'
    script_path.write_text(source, encoding='utf-8')

    manager.load_script(str(script_path))

    assert manager._script_status == scripting.ScriptStatus.ERROR
    assert manager._script == []
    assert any(
        isinstance(command, ShowErrorCommand) and command.text == message
        for command in sent_commands
    )


def test_load_script_accepts_valid_registered_script(tmp_path):
    manager, sent_commands = make_registered_script_manager()
    script_path = tmp_path / 'automation.py'
    script_path.write_text(
        'from magscope.ipc_commands import SleepCommand\n'
        'from magscope.scripting import Script\n'
        'script = Script()\n'
        'script.append(SleepCommand(duration=0.0))\n',
        encoding='utf-8',
    )

    manager.load_script(str(script_path))

    assert manager._script_status == scripting.ScriptStatus.LOADED
    assert manager._script_length == 1
    assert isinstance(manager._script[0].command, SleepCommand)
    assert not any(isinstance(command, ShowErrorCommand) for command in sent_commands)


def test_zero_duration_sleep_resumes_immediately():
    manager, _sent_commands = make_script_manager()

    manager.start_sleep(0.0)

    assert manager._script_waiting is False
    assert manager._script_sleep_duration is None


def test_negative_sleep_reports_error_to_gui():
    manager, sent_commands = make_script_manager()

    manager.start_sleep(-1.0)

    assert manager._script_status == scripting.ScriptStatus.ERROR
    assert not manager._script_waiting
    assert manager._script_sleep_duration is None
    assert any(
        isinstance(command, ShowErrorCommand)
        and command.text == 'Sleep duration must be non-negative'
        for command in sent_commands
    )
