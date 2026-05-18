import os
from collections import OrderedDict

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

pytest.importorskip('pytestqt')
pytest.importorskip('PyQt6')

from PyQt6.QtCore import QMimeData, QPointF
from PyQt6.QtWidgets import QLabel

from magscope.ui.panel_layout import PANEL_MIME_TYPE, PanelLayoutManager, ReorderableColumn


class FakeSettings:
    def __init__(self, groups=None):
        self.groups = {name: dict(values) for name, values in (groups or {}).items()}
        self.current_group = None

    def beginGroup(self, group):  # noqa: N802 - Qt naming
        self.current_group = group
        self.groups.setdefault(group, {})

    def endGroup(self):  # noqa: N802 - Qt naming
        self.current_group = None

    def childKeys(self):  # noqa: N802 - Qt naming
        return list(self.groups[self.current_group])

    def remove(self, key):
        self.groups[self.current_group].pop(key, None)

    def setValue(self, key, value):  # noqa: N802 - Qt naming
        self.groups[self.current_group][key] = value

    def value(self, key, defaultValue=None):  # noqa: N802 - Qt naming
        return self.groups[self.current_group].get(key, defaultValue)


class FakeDropEvent:
    def __init__(self, panel_id=None, *, y=0.0):
        self._mime_data = QMimeData()
        if panel_id is not None:
            self._mime_data.setData(PANEL_MIME_TYPE, panel_id.encode('utf-8'))
        self._position = QPointF(0.0, y)
        self.accepted = False
        self.ignored = False

    def acceptProposedAction(self):  # noqa: N802 - Qt naming
        self.accepted = True

    def ignore(self):
        self.ignored = True

    def mimeData(self):  # noqa: N802 - Qt naming
        return self._mime_data

    def position(self):
        return self._position


def make_column(qtbot, name, *, pinned_ids=()):
    column = ReorderableColumn(name, pinned_ids=pinned_ids)
    qtbot.addWidget(column)
    return column


def make_manager(qtbot, settings=None, *, columns=('left', 'right'), pinned_ids=None, **kwargs):
    pinned_ids = pinned_ids or {}
    column_map = OrderedDict(
        (name, make_column(qtbot, name, pinned_ids=pinned_ids.get(name, ())))
        for name in columns
    )
    return PanelLayoutManager(settings, 'layout', column_map, **kwargs)


def register_panel(qtbot, manager, panel_id, default_column):
    widget = QLabel(panel_id)
    wrapper = manager.register_panel(panel_id, widget, default_column)
    qtbot.addWidget(wrapper)
    return wrapper


def test_register_panel_rejects_duplicate_ids_and_unknown_default_columns(qtbot):
    manager = make_manager(qtbot)

    wrapper = register_panel(qtbot, manager, 'camera', 'left')

    assert wrapper.panel_id == 'camera'
    assert manager.wrapper_for_id('camera') is wrapper
    with pytest.raises(ValueError, match="Panel 'camera' already registered"):
        register_panel(qtbot, manager, 'camera', 'left')
    with pytest.raises(ValueError, match="Unknown column 'missing'"):
        register_panel(qtbot, manager, 'settings', 'missing')


def test_restore_layout_uses_default_columns_when_no_layout_is_saved(qtbot):
    manager = make_manager(qtbot, settings=None)
    register_panel(qtbot, manager, 'camera', 'left')
    register_panel(qtbot, manager, 'plot', 'right')
    register_panel(qtbot, manager, 'status', 'left')

    manager.restore_layout()

    assert manager.current_layout() == {
        'left': ['camera', 'status'],
        'right': ['plot'],
    }


def test_restore_layout_uses_saved_order_ignores_unknown_panels_and_appends_new_defaults(qtbot):
    settings = FakeSettings({
        'layout': {
            '__column_order__': ['right', 'ghost'],
            'right': ['plot', 'unknown', 'camera'],
            'ghost': ['status'],
            'left': 'plot, status',
        },
    })
    manager = make_manager(qtbot, settings=settings)
    register_panel(qtbot, manager, 'camera', 'left')
    register_panel(qtbot, manager, 'plot', 'left')
    register_panel(qtbot, manager, 'status', 'right')
    register_panel(qtbot, manager, 'histogram', 'right')

    manager.restore_layout()

    assert manager.current_layout() == {
        'left': ['status'],
        'right': ['plot', 'camera', 'histogram'],
    }


def test_save_layout_writes_column_order_and_removes_obsolete_settings(qtbot):
    settings = FakeSettings({
        'layout': {
            '__column_order__': ['old'],
            'old': ['legacy'],
            'obsolete': ['legacy'],
        },
    })
    manager = make_manager(qtbot, settings=settings)
    register_panel(qtbot, manager, 'camera', 'left')
    register_panel(qtbot, manager, 'plot', 'right')
    manager.restore_layout()

    manager.save_layout()

    assert settings.groups['layout'] == {
        '__column_order__': ['left', 'right'],
        'left': ['camera'],
        'right': ['plot'],
    }


def test_layout_changed_saves_and_reports_current_layout(qtbot):
    settings = FakeSettings()
    layout_changes = []
    manager = make_manager(qtbot, settings=settings, on_layout_changed=layout_changes.append)
    register_panel(qtbot, manager, 'camera', 'left')
    register_panel(qtbot, manager, 'plot', 'right')
    manager.restore_layout()

    manager.layout_changed()

    assert layout_changes == [{'left': ['camera'], 'right': ['plot']}]
    assert settings.groups['layout']['left'] == ['camera']
    assert settings.groups['layout']['right'] == ['plot']


def test_drag_active_callback_only_reports_first_start_and_final_finish(qtbot):
    active_changes = []
    manager = make_manager(qtbot, on_drag_active_changed=active_changes.append)

    manager.notify_drag_started()
    manager.notify_drag_started()
    manager.notify_drag_finished()
    manager.notify_drag_finished()

    assert active_changes == [True, False]


def test_add_and_remove_column_updates_order_manager_and_settings(qtbot):
    settings = FakeSettings({'layout': {'__column_order__': ['left', 'middle', 'right'], 'middle': []}})
    manager = make_manager(qtbot, settings=settings)
    middle = make_column(qtbot, 'middle')

    manager.add_column('middle', middle, index=1)

    assert list(manager.columns) == ['left', 'middle', 'right']
    assert middle._manager is manager

    wrapper = register_panel(qtbot, manager, 'script', 'middle')
    manager.restore_layout()
    with pytest.raises(ValueError, match="Column 'middle' is not empty"):
        manager.remove_column('middle')

    middle.remove_panel(wrapper)
    manager.remove_column('middle')

    assert list(manager.columns) == ['left', 'right']
    assert middle._manager is None
    assert settings.groups['layout']['__column_order__'] == ['left', 'right']
    assert 'middle' not in settings.groups['layout']


def test_pinned_column_preserves_locked_prefix_when_reordering(qtbot):
    manager = make_manager(qtbot, columns=('main',), pinned_ids={'main': {'pinned'}})
    pinned = register_panel(qtbot, manager, 'pinned', 'main')
    free = register_panel(qtbot, manager, 'free', 'main')
    column = manager.columns['main']
    manager.restore_layout()

    column.add_panel(free, index=0)
    column.add_panel(pinned, index=99)

    assert column.panel_ids() == ['pinned', 'free']


def test_begin_cancel_and_finish_drag_restore_panel_order(qtbot):
    manager = make_manager(qtbot, columns=('main',))
    register_panel(qtbot, manager, 'first', 'main')
    second = register_panel(qtbot, manager, 'second', 'main')
    column = manager.columns['main']
    manager.restore_layout()

    original_index = column.begin_drag(second)

    assert original_index == 1
    assert second.isHidden()
    assert column._placeholder_index() == 1

    column.cancel_drag(second, original_index)
    column.finish_drag()

    assert column.panel_ids() == ['first', 'second']
    assert column._placeholder_index() is None


def test_drop_event_moves_panel_between_columns_and_reports_layout_change(qtbot):
    layout_changes = []
    manager = make_manager(qtbot, on_layout_changed=layout_changes.append)
    register_panel(qtbot, manager, 'camera', 'left')
    register_panel(qtbot, manager, 'plot', 'left')
    right = manager.columns['right']
    manager.restore_layout()

    event = FakeDropEvent('camera')
    right.dropEvent(event)

    assert event.accepted is True
    assert manager.current_layout() == {'left': ['plot'], 'right': ['camera']}
    assert layout_changes == [{'left': ['plot'], 'right': ['camera']}]
    assert manager.wrapper_for_id('camera')._drop_accepted is True


def test_drag_enter_and_move_update_placeholder_for_known_panel(qtbot):
    manager = make_manager(qtbot)
    register_panel(qtbot, manager, 'camera', 'left')
    right = manager.columns['right']
    manager.restore_layout()

    enter_event = FakeDropEvent('camera')
    right.dragEnterEvent(enter_event)
    move_event = FakeDropEvent('camera', y=100.0)
    right.dragMoveEvent(move_event)

    assert enter_event.accepted is True
    assert move_event.accepted is True
    assert right._placeholder_index() == 0


def test_drop_event_ignores_missing_mime_manager_or_wrapper(qtbot):
    manager = make_manager(qtbot)
    right = manager.columns['right']

    no_mime_event = FakeDropEvent()
    right.dropEvent(no_mime_event)
    assert no_mime_event.ignored is True

    no_manager_column = make_column(qtbot, 'orphan')
    no_manager_event = FakeDropEvent('camera')
    no_manager_column.dropEvent(no_manager_event)
    assert no_manager_event.ignored is True

    missing_wrapper_event = FakeDropEvent('missing')
    right.dropEvent(missing_wrapper_event)
    assert missing_wrapper_event.ignored is True


def test_stored_layout_and_column_names_reflect_saved_settings(qtbot):
    settings = FakeSettings({
        'layout': {
            '__column_order__': 'right,left',
            'right': 'plot, camera',
            'left': ['status'],
        },
    })
    manager = make_manager(qtbot, settings=settings)

    assert manager.stored_layout() == OrderedDict([
        ('right', ['plot', 'camera']),
        ('left', ['status']),
    ])
    assert manager.stored_column_names() == ['right', 'left']
    assert manager._normalise_panel_list(None) is None
