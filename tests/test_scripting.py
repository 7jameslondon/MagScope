from __future__ import annotations

import pytest

from magscope.ipc import CommandRegistry, Delivery
from magscope.ipc_commands import (
    Command,
    ShowErrorCommand,
    SleepCommand,
    StartScriptCommand,
    UpdateScriptStatusCommand,
    UpdateScriptStepCommand,
    UpdateWaitingCommand,
)
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


# ---------------------------------------------------------------------------
# start_script edge cases
# ---------------------------------------------------------------------------

def test_start_script_rejects_when_empty():
    manager, _sent_commands = make_script_manager()

    manager.start_script()

    assert manager._script_status == scripting.ScriptStatus.EMPTY


def test_start_script_rejects_when_in_error_state():
    manager, _sent_commands = make_script_manager()
    manager._script_status = scripting.ScriptStatus.ERROR

    manager.start_script()

    assert manager._script_status == scripting.ScriptStatus.ERROR


def test_start_script_rejects_when_already_running():
    manager, _sent_commands = make_script_manager()
    manager._script_status = scripting.ScriptStatus.RUNNING

    manager.start_script()

    assert manager._script_status == scripting.ScriptStatus.RUNNING


def test_start_script_handles_empty_script_transitions_to_finished():
    manager, sent_commands = make_script_manager()
    manager._script_status = scripting.ScriptStatus.LOADED
    manager._script_length = 0

    manager.start_script()

    assert manager._script_status == scripting.ScriptStatus.FINISHED
    assert any(isinstance(c, UpdateScriptStatusCommand) for c in sent_commands)


def test_start_script_resets_index_and_sets_running():
    manager, sent_commands = make_script_manager()
    manager._script_status = scripting.ScriptStatus.LOADED
    manager._script_length = 3
    manager._script_index = 99

    manager.start_script()

    assert manager._script_index == 0
    assert manager._script_status == scripting.ScriptStatus.RUNNING
    assert any(
        isinstance(c, UpdateScriptStatusCommand) and c.status == scripting.ScriptStatus.RUNNING
        for c in sent_commands
    )


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------

def test_pause_rejects_when_not_running():
    manager, _sent_commands = make_script_manager()

    manager.pause_script()

    assert manager._script_status == scripting.ScriptStatus.EMPTY


def test_resume_rejects_when_not_paused():
    manager, _sent_commands = make_script_manager()

    manager.resume_script()

    assert manager._script_status == scripting.ScriptStatus.EMPTY


def test_pause_resume_cycle():
    manager, sent_commands = make_script_manager()
    manager._script_status = scripting.ScriptStatus.RUNNING

    manager.pause_script()
    assert manager._script_status == scripting.ScriptStatus.PAUSED

    manager.resume_script()
    assert manager._script_status == scripting.ScriptStatus.RUNNING

    status_values = [
        c.status for c in sent_commands if isinstance(c, UpdateScriptStatusCommand)
    ]
    assert status_values == [
        scripting.ScriptStatus.PAUSED,
        scripting.ScriptStatus.RUNNING,
    ]


# ---------------------------------------------------------------------------
# load_script edge cases
# ---------------------------------------------------------------------------

def test_load_script_handles_exec_exception(tmp_path):
    manager, sent_commands = make_script_manager()
    script_path = tmp_path / 'broken.py'
    script_path.write_text('raise RuntimeError("boom")', encoding='utf-8')

    manager.load_script(str(script_path))

    assert manager._script_status == scripting.ScriptStatus.ERROR
    assert manager._script == []
    assert any(
        isinstance(command, ShowErrorCommand)
        and 'An error occurred while loading a script.' in command.text
        for command in sent_commands
    )


def test_load_script_handles_check_script_failure(tmp_path):
    manager, sent_commands = make_script_manager()
    manager.script_registry.register_class_methods(scripting.ScriptManager)
    script_path = tmp_path / 'bad.py'
    script_path.write_text(
        'from magscope.ipc_commands import UpdateScriptStatusCommand\n'
        'from magscope.scripting import Script\n'
        'script = Script()\n'
        'script.append(UpdateScriptStatusCommand(status="Running"))\n',
        encoding='utf-8',
    )

    manager.load_script(str(script_path))

    assert manager._script_status == scripting.ScriptStatus.ERROR
    assert manager._script == []
    assert any(
        isinstance(command, ShowErrorCommand)
        and 'Script is invalid' in command.text
        for command in sent_commands
    )


def test_load_script_rejects_while_running(tmp_path):
    manager, _sent_commands = make_script_manager()
    manager._script_status = scripting.ScriptStatus.RUNNING

    manager.load_script('some_path.py')

    assert manager._script_status == scripting.ScriptStatus.RUNNING


def test_load_script_with_empty_path():
    manager, _sent_commands = make_script_manager()
    manager._script_status = scripting.ScriptStatus.LOADED
    manager._script = [scripting.ScriptStep(command=SleepCommand(duration=1.0))]
    manager._script_length = 1

    manager.load_script('')

    assert manager._script_status == scripting.ScriptStatus.EMPTY
    assert manager._script == []
    assert manager._script_length == 0
    assert manager._script_index == 0
    assert manager._script_waiting is False


# ---------------------------------------------------------------------------
# _do_sleep
# ---------------------------------------------------------------------------

def test_do_sleep_elapsed_resets_state(monkeypatch):
    manager, _sent_commands = make_script_manager()
    manager._script_sleep_duration = 2.0
    manager._script_sleep_start = 100.0
    manager._script_waiting = True

    monkeypatch.setattr(scripting, 'time', lambda: 103.0)

    manager._do_sleep()

    assert manager._script_sleep_duration is None
    assert manager._script_waiting is False


def test_do_sleep_not_elapsed_keeps_waiting(monkeypatch):
    manager, _sent_commands = make_script_manager()
    manager._script_sleep_duration = 2.0
    manager._script_sleep_start = 100.0
    manager._script_waiting = True

    monkeypatch.setattr(scripting, 'time', lambda: 101.0)

    manager._do_sleep()

    assert manager._script_sleep_duration == 2.0
    assert manager._script_waiting is True


# ---------------------------------------------------------------------------
# update_waiting
# ---------------------------------------------------------------------------

def test_update_waiting_clears_waiting_flag():
    manager, _sent_commands = make_script_manager()
    manager._script_waiting = True

    manager.update_waiting()

    assert manager._script_waiting is False


# ---------------------------------------------------------------------------
# _handle_script_error
# ---------------------------------------------------------------------------

def test_handle_script_error_clears_state_and_reports():
    manager, sent_commands = make_script_manager()
    manager._script_waiting = True
    manager._script_sleep_duration = 5.0
    manager._script_sleep_start = 99.0
    manager._script_status = scripting.ScriptStatus.RUNNING

    manager._handle_script_error('Something went wrong', details=None)

    assert manager._script_waiting is False
    assert manager._script_sleep_duration is None
    assert manager._script_sleep_start == 0
    assert manager._script_status == scripting.ScriptStatus.ERROR
    assert any(
        isinstance(c, ShowErrorCommand)
        and c.text == 'Something went wrong'
        and c.details is None
        for c in sent_commands
    )


def test_handle_script_error_with_details():
    manager, sent_commands = make_script_manager()

    manager._handle_script_error('Oops', details='Traceback...')

    assert manager._script_status == scripting.ScriptStatus.ERROR
    assert any(
        isinstance(c, ShowErrorCommand)
        and c.text == 'Oops'
        and c.details == 'Traceback...'
        for c in sent_commands
    )


# ---------------------------------------------------------------------------
# _set_script_status
# ---------------------------------------------------------------------------

def test_set_script_status_sends_update_command():
    manager, sent_commands = make_script_manager()

    manager._set_script_status(scripting.ScriptStatus.RUNNING)

    assert manager._script_status == scripting.ScriptStatus.RUNNING
    assert any(
        isinstance(c, UpdateScriptStatusCommand) and c.status == scripting.ScriptStatus.RUNNING
        for c in sent_commands
    )


def test_set_script_status_empty_clears_step_update():
    manager, sent_commands = make_script_manager()
    manager._script_length = 10

    manager._set_script_status(scripting.ScriptStatus.EMPTY)

    assert any(
        isinstance(c, UpdateScriptStepCommand) and c.current_step is None
        for c in sent_commands
    )


def test_set_script_status_error_clears_step_update():
    manager, sent_commands = make_script_manager()

    manager._set_script_status(scripting.ScriptStatus.ERROR)

    assert any(
        isinstance(c, UpdateScriptStepCommand) and c.current_step is None
        for c in sent_commands
    )


def test_set_script_status_running_does_not_clear_step_update():
    manager, sent_commands = make_script_manager()

    manager._set_script_status(scripting.ScriptStatus.RUNNING)

    assert not any(isinstance(c, UpdateScriptStepCommand) for c in sent_commands)


# ---------------------------------------------------------------------------
# _send_script_step_update
# ---------------------------------------------------------------------------

def test_send_script_step_update_with_description():
    manager, sent_commands = make_script_manager()
    manager._script_length = 5

    manager._send_script_step_update(3, description='Moving stage')

    assert len(sent_commands) == 1
    cmd = sent_commands[0]
    assert isinstance(cmd, UpdateScriptStepCommand)
    assert cmd.current_step == 3
    assert cmd.total_steps == 5
    assert cmd.description == 'Moving stage'


def test_send_script_step_update_without_description():
    manager, sent_commands = make_script_manager()
    manager._script_length = 10

    manager._send_script_step_update(None)

    cmd = sent_commands[0]
    assert cmd.current_step is None
    assert cmd.description is None


# ---------------------------------------------------------------------------
# _format_script_step
# ---------------------------------------------------------------------------

def test_format_script_step_uses_repr():
    step = scripting.ScriptStep(command=SleepCommand(duration=2.5))

    result = scripting.ScriptManager._format_script_step(step)

    assert '2.5' in result


def test_format_script_step_fallback_on_repr_error():
    class BrokenCommand(Command):
        def __repr__(self):
            raise RuntimeError('broken')

    step = scripting.ScriptStep(command=BrokenCommand())

    result = scripting.ScriptManager._format_script_step(step)

    assert result == 'BrokenCommand'


# ---------------------------------------------------------------------------
# ScriptStatus enum
# ---------------------------------------------------------------------------

def test_script_status_enum_values():
    assert scripting.ScriptStatus.EMPTY.value == 'Empty'
    assert scripting.ScriptStatus.LOADED.value == 'Loaded'
    assert scripting.ScriptStatus.RUNNING.value == 'Running'
    assert scripting.ScriptStatus.PAUSED.value == 'Paused'
    assert scripting.ScriptStatus.FINISHED.value == 'Finished'
    assert scripting.ScriptStatus.ERROR.value == 'Error'


def test_script_registry_get_class_name_from_instance():
    registry = scripting.ScriptRegistry()
    manager, _ = make_script_manager()
    name = registry.get_class_name(manager)
    assert name == 'ScriptManager'


def test_execute_script_step_registry_command_mismatch():
    manager, _sent_commands = make_registered_script_manager()
    from magscope.ipc_commands import Command

    class FakeRegistry:
        def __call__(self, cmd_type):
            return scripting.ScriptCommandRegistration(
                cls_name='FakeManager',
                meth_name='fake_handler',
                command_type=cmd_type,
                callable=lambda self: None,
            )

    manager.script_registry = FakeRegistry()

    with pytest.raises(Exception):
        manager._execute_script_step(scripting.ScriptStep(command=SleepCommand(duration=0.0)))


def test_script_registry_call_unregistered_raises():
    registry = scripting.ScriptRegistry()
    with pytest.raises(ValueError, match="not registered"):
        registry(SleepCommand)


def test_script_registry_register_same_class_skips():
    registry = scripting.ScriptRegistry()
    registry.register_class_methods(scripting.ScriptManager)
    # Should not raise when registering the same class again
    registry.register_class_methods(scripting.ScriptManager)
    # The command should still be found
    registration = registry(SleepCommand)
    assert registration.meth_name == 'start_sleep'


def test_script_manager_setup_is_noop():
    manager, _ = make_script_manager()
    manager.setup()  # Should not raise
