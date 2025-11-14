"""Shared helpers for draggable control panel layouts."""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Iterable

from PyQt6.QtCore import QEvent, QMimeData, QObject, QPoint, QSettings, Qt
from PyQt6.QtGui import QDrag, QPixmap
from PyQt6.QtWidgets import QApplication, QFrame, QPushButton, QSizePolicy, QVBoxLayout, QWidget

PANEL_MIME_TYPE = "application/x-magscope-panel"


class _TitleDragFilter(QObject):
    """Convert title-area drags into wrapper move operations."""

    def __init__(self, wrapper: "PanelWrapper", target: QWidget) -> None:
        super().__init__(target)
        self._wrapper = wrapper
        self.target = target
        self._drag_start = QPoint()
        self._dragging = False

    def eventFilter(self, obj, event):  # type: ignore[override]
        if event.type() == QEvent.Type.Enter:
            self.target.setCursor(Qt.CursorShape.OpenHandCursor)
        elif event.type() == QEvent.Type.Leave:
            if not self._dragging:
                self.target.unsetCursor()
        elif event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            self._dragging = False
            self.target.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.type() == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
            distance = (event.position().toPoint() - self._drag_start).manhattanLength()
            if distance >= QApplication.startDragDistance():
                if isinstance(self.target, QPushButton):
                    self.target.setDown(False)
                self._dragging = True
                self._wrapper.start_drag()
                return True
        elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self.target.setCursor(Qt.CursorShape.OpenHandCursor)
            if self._dragging:
                self._dragging = False
                return True
        return QObject.eventFilter(self, obj, event)

    def drag_finished(self) -> None:
        if self._dragging:
            self._dragging = False
        self.target.setCursor(Qt.CursorShape.OpenHandCursor)


class PanelWrapper(QFrame):
    """Wrap a panel widget and make its title initiate drag-and-drop."""

    def __init__(self, manager: "PanelLayoutManager", panel_id: str, widget: QWidget, *, draggable: bool = True) -> None:
        super().__init__()
        self._manager = manager
        self.panel_id = panel_id
        self.panel_widget = widget
        self.column: ReorderableColumn | None = None
        self._drag_filters: list[_TitleDragFilter] = []
        self.draggable = draggable
        self._drop_accepted = False

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName(f"PanelWrapper_{panel_id}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(widget)

        if self.draggable:
            self._attach_title_drag()

    def _attach_title_drag(self) -> None:
        groupbox = getattr(self.panel_widget, "groupbox", None)
        drag_handle = getattr(groupbox, "drag_handle", None) if groupbox is not None else None
        if isinstance(drag_handle, QWidget):
            self._register_drag_source(drag_handle)
            return

        # Fallback for widgets that do not expose a CollapsibleGroupBox title
        self._register_drag_source(self.panel_widget)

    def _register_drag_source(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        for existing in self._drag_filters:
            if existing.target is widget:
                return
        drag_filter = _TitleDragFilter(self, widget)
        widget.installEventFilter(drag_filter)
        self._drag_filters.append(drag_filter)

    def start_drag(self) -> None:
        if not self.draggable:
            return

        column = self.column
        if column is None:
            return

        manager = self._manager
        manager.notify_drag_started()

        try:
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(PANEL_MIME_TYPE, self.panel_id.encode("utf-8"))
            drag.setMimeData(mime)
            drag.setHotSpot(QPoint(self.width() // 2, 0))

            pixmap = QPixmap(self.size())
            pixmap.fill(Qt.GlobalColor.transparent)
            self.render(pixmap)
            drag.setPixmap(pixmap)

            original_index = column.begin_drag(self)
            self._drop_accepted = False

            result = drag.exec(Qt.DropAction.MoveAction)

            if result != Qt.DropAction.MoveAction or not self._drop_accepted:
                column.cancel_drag(self, original_index)
        finally:
            column.finish_drag()
            manager.notify_drag_finished()

            for drag_filter in self._drag_filters:
                drag_filter.drag_finished()

    def mark_drop_accepted(self) -> None:
        self._drop_accepted = True


class ReorderableColumn(QWidget):
    """Vertical column of draggable panels with drop support."""

    def __init__(self, name: str, pinned_ids: Iterable[str] | None = None) -> None:
        super().__init__()
        self.name = name
        self.setAcceptDrops(True)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self._placeholder: QFrame | None = None
        self._pinned_ids = set(pinned_ids or ())
        self._active_drag_height: int | None = None
        self._manager: PanelLayoutManager | None = None

    def set_manager(self, manager: "PanelLayoutManager | None") -> None:
        self._manager = manager

    def panels(self) -> list[PanelWrapper]:
        widgets: list[PanelWrapper] = []
        for index in range(self._layout.count() - 1):  # Exclude stretch
            item = self._layout.itemAt(index)
            widget = item.widget()
            if isinstance(widget, PanelWrapper):
                widgets.append(widget)
        return widgets

    def panel_ids(self) -> list[str]:
        return [wrapper.panel_id for wrapper in self.panels()]

    def add_panel(self, wrapper: PanelWrapper, index: int | None = None) -> None:
        if wrapper.column is self:
            current_index = self._layout.indexOf(wrapper)
            target_index = self._constrain_index(wrapper, self._target_index(index))
            if current_index != -1:
                if current_index == target_index:
                    return

                self._layout.removeWidget(wrapper)
                target_index = self._constrain_index(wrapper, self._target_index(index))
                self._layout.insertWidget(target_index, wrapper)
                return

            wrapper.setParent(self)
            wrapper.column = self
            self._layout.insertWidget(target_index, wrapper)
            wrapper.show()
            return

        if wrapper.column is not None:
            wrapper.column.remove_panel(wrapper)

        wrapper.setParent(self)
        wrapper.column = self
        constrained_index = self._constrain_index(wrapper, self._target_index(index))
        self._layout.insertWidget(constrained_index, wrapper)
        wrapper.show()

    def remove_panel(self, wrapper: PanelWrapper) -> None:
        self._layout.removeWidget(wrapper)
        wrapper.column = None
        wrapper.setParent(None)
        wrapper.hide()

    def clear_panels(self) -> None:
        for wrapper in self.panels():
            self.remove_panel(wrapper)

    def begin_drag(self, wrapper: PanelWrapper) -> int:
        index = self._layout.indexOf(wrapper)
        if index == -1:
            return -1

        placeholder = self._ensure_placeholder()
        height = wrapper.height() or wrapper.sizeHint().height()
        height = max(24, height)
        self._active_drag_height = height
        placeholder.setFixedHeight(height)

        self._layout.removeWidget(wrapper)
        wrapper.hide()
        target_index = min(index, self._layout.count() - 1)
        self._layout.insertWidget(target_index, placeholder)
        placeholder.show()
        return index

    def cancel_drag(self, wrapper: PanelWrapper, index: int) -> None:
        placeholder = self._placeholder
        if placeholder is not None:
            self._layout.removeWidget(placeholder)
            placeholder.hide()
        if index < 0:
            index = self._layout.count() - 1
        target_index = min(index, self._layout.count() - 1)
        self._layout.insertWidget(target_index, wrapper)
        wrapper.show()

    def finish_drag(self) -> None:
        self._active_drag_height = None
        self.clear_placeholder()

    def _target_index(self, index: int | None) -> int:
        stretch_index = self._layout.count() - 1
        if index is None or index < 0 or index > stretch_index:
            return stretch_index
        return min(index, stretch_index)

    def _drop_index(self, cursor_y: float) -> int:
        for i in range(self._layout.count() - 1):
            item = self._layout.itemAt(i)
            widget = item.widget()
            if widget is None:
                continue
            if cursor_y < widget.y() + widget.height() / 2:
                return i
        return self._layout.count() - 1

    def _locked_prefix_length(self) -> int:
        count = 0
        for i in range(self._layout.count() - 1):
            widget = self._layout.itemAt(i).widget()
            if isinstance(widget, PanelWrapper) and widget.panel_id in self._pinned_ids:
                count += 1
            else:
                break
        return count

    def _constrain_index(self, wrapper: PanelWrapper, index: int) -> int:
        if wrapper.panel_id in self._pinned_ids:
            return 0
        return max(index, self._locked_prefix_length())

    def _constrained_drop_index(self, wrapper: PanelWrapper, cursor_y: float) -> int:
        return self._constrain_index(wrapper, self._drop_index(cursor_y))

    def _ensure_placeholder(self) -> QFrame:
        if self._placeholder is None:
            placeholder = QFrame(self)
            placeholder.setObjectName("panel_drop_placeholder")
            placeholder.setStyleSheet(
                "#panel_drop_placeholder { border: 2px dashed palette(midlight); border-radius: 6px; }"
            )
            placeholder.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            placeholder.hide()
            self._placeholder = placeholder
        return self._placeholder

    def _update_placeholder(self, wrapper: PanelWrapper | None, cursor_y: float) -> None:
        if wrapper is None:
            self.clear_placeholder()
            return

        placeholder = self._ensure_placeholder()
        if self._active_drag_height is not None:
            placeholder.setFixedHeight(self._active_drag_height)
        else:
            height = wrapper.height() or wrapper.sizeHint().height()
            placeholder.setFixedHeight(max(24, height))
        target_index = self._constrained_drop_index(wrapper, cursor_y)
        current_index = self._layout.indexOf(placeholder)
        if current_index == -1:
            self._layout.insertWidget(target_index, placeholder)
        elif current_index != target_index:
            self._layout.removeWidget(placeholder)
            stretch_index = self._layout.count() - 1
            target_index = min(target_index, stretch_index)
            self._layout.insertWidget(target_index, placeholder)
        placeholder.show()

    def clear_placeholder(self) -> None:
        if self._placeholder is None:
            return
        index = self._layout.indexOf(self._placeholder)
        if index != -1:
            self._layout.removeWidget(self._placeholder)
        self._placeholder.hide()

    def _placeholder_index(self) -> int | None:
        if self._placeholder is None:
            return None
        index = self._layout.indexOf(self._placeholder)
        return index if index != -1 else None

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(PANEL_MIME_TYPE):
            event.acceptProposedAction()
            wrapper = self._wrapper_from_event(event)
            if wrapper is not None:
                self._update_placeholder(wrapper, event.position().y())
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(PANEL_MIME_TYPE):
            event.acceptProposedAction()
            wrapper = self._wrapper_from_event(event)
            if wrapper is not None:
                self._update_placeholder(wrapper, event.position().y())
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self.clear_placeholder()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if not event.mimeData().hasFormat(PANEL_MIME_TYPE):
            event.ignore()
            self.clear_placeholder()
            return

        manager = self._manager
        if manager is None:
            event.ignore()
            self.clear_placeholder()
            return

        panel_id = bytes(event.mimeData().data(PANEL_MIME_TYPE)).decode("utf-8")
        wrapper = manager.wrapper_for_id(panel_id)
        if wrapper is None:
            event.ignore()
            self.clear_placeholder()
            return

        drop_index = self._constrained_drop_index(wrapper, event.position().y())
        placeholder_index = self._placeholder_index()
        if placeholder_index is not None:
            drop_index = placeholder_index
        self.clear_placeholder()
        self.add_panel(wrapper, drop_index)
        wrapper.mark_drop_accepted()
        manager.layout_changed()
        event.acceptProposedAction()

    def _wrapper_from_event(self, event) -> PanelWrapper | None:
        manager = self._manager
        if manager is None:
            return None
        panel_id_bytes = event.mimeData().data(PANEL_MIME_TYPE)
        if panel_id_bytes.isEmpty():
            return None
        panel_id = bytes(panel_id_bytes).decode("utf-8")
        return manager.wrapper_for_id(panel_id)


class PanelLayoutManager:
    """Coordinate draggable panel columns and persist their layout."""

    def __init__(
        self,
        settings: "QSettings | None",
        settings_group: str,
        columns: dict[str, ReorderableColumn] | Iterable[tuple[str, ReorderableColumn]],
        *,
        on_layout_changed: Callable[[dict[str, list[str]]], None] | None = None,
        on_drag_active_changed: Callable[[bool], None] | None = None,
    ) -> None:
        self._settings: QSettings | None = settings
        self._settings_group = settings_group
        if isinstance(columns, dict):
            self.columns: "OrderedDict[str, ReorderableColumn]" = OrderedDict(columns.items())
        else:
            self.columns = OrderedDict(columns)
        for column in self.columns.values():
            column.set_manager(self)
        self._wrappers: dict[str, PanelWrapper] = {}
        self._default_columns: dict[str, str] = {}
        self._default_order: list[str] = []
        self._on_layout_changed = on_layout_changed
        self._on_drag_active_changed = on_drag_active_changed
        self._active_drag_count = 0

    def wrapper_for_id(self, panel_id: str) -> PanelWrapper | None:
        return self._wrappers.get(panel_id)

    def register_panel(self, panel_id: str, widget: QWidget, default_column: str, *, draggable: bool = True) -> PanelWrapper:
        if panel_id in self._wrappers:
            raise ValueError(f"Panel '{panel_id}' already registered")
        if default_column not in self.columns:
            raise ValueError(f"Unknown column '{default_column}'")
        wrapper = PanelWrapper(self, panel_id, widget, draggable=draggable)
        self._wrappers[panel_id] = wrapper
        self._default_columns[panel_id] = default_column
        self._default_order.append(panel_id)
        return wrapper

    def restore_layout(self) -> None:
        layout: "OrderedDict[str, list[str]]" = OrderedDict((name, []) for name in self.columns)
        used: set[str] = set()

        stored = self._load_layout()
        for name, panel_ids in stored.items():
            if name not in layout:
                continue
            for panel_id in panel_ids:
                if panel_id in self._wrappers and panel_id not in used:
                    layout[name].append(panel_id)
                    used.add(panel_id)

        for panel_id in self._default_order:
            default_column = self._default_columns[panel_id]
            if panel_id in used:
                continue
            column_name = default_column if default_column in layout else next(iter(layout))
            layout[column_name].append(panel_id)
            used.add(panel_id)

        for column in self.columns.values():
            column.clear_placeholder()
            column.clear_panels()

        for column_name, panel_ids in layout.items():
            column = self.columns[column_name]
            for panel_id in panel_ids:
                wrapper = self._wrappers.get(panel_id)
                if wrapper is not None:
                    column.add_panel(wrapper)

    def save_layout(self) -> None:
        if self._settings is None:
            return
        self._settings.beginGroup(self._settings_group)
        order_key = "__column_order__"
        column_names = list(self.columns.keys())
        self._settings.setValue(order_key, column_names)
        stored_keys = set(self._settings.childKeys())
        for name, column in self.columns.items():
            self._settings.setValue(name, column.panel_ids())
            stored_keys.discard(name)
        stored_keys.discard(order_key)
        for obsolete in stored_keys:
            self._settings.remove(obsolete)
        self._settings.endGroup()

    def current_layout(self) -> dict[str, list[str]]:
        return {name: column.panel_ids() for name, column in self.columns.items()}

    def layout_changed(self) -> None:
        self.save_layout()
        if self._on_layout_changed is not None:
            self._on_layout_changed(self.current_layout())

    def notify_drag_started(self) -> None:
        self._active_drag_count += 1
        if self._active_drag_count == 1 and self._on_drag_active_changed is not None:
            self._on_drag_active_changed(True)

    def notify_drag_finished(self) -> None:
        if self._active_drag_count > 0:
            self._active_drag_count -= 1
        if self._active_drag_count == 0 and self._on_drag_active_changed is not None:
            self._on_drag_active_changed(False)

    def _normalise_panel_list(self, stored) -> list[str] | None:
        if isinstance(stored, list):
            return [str(item) for item in stored]
        if isinstance(stored, str) and stored:
            return [item.strip() for item in stored.split(",") if item.strip()]
        return None

    def _load_layout(self) -> "OrderedDict[str, list[str]]":
        layout: "OrderedDict[str, list[str]]" = OrderedDict()
        if self._settings is None:
            return layout
        self._settings.beginGroup(self._settings_group)
        order_key = "__column_order__"
        order_value = self._settings.value(order_key, defaultValue=[])
        column_order: list[str]
        if isinstance(order_value, list):
            column_order = [str(item) for item in order_value]
        elif isinstance(order_value, str) and order_value:
            column_order = [item.strip() for item in order_value.split(",") if item.strip()]
        else:
            column_order = []
        seen: set[str] = set()
        for name in column_order:
            stored = self._settings.value(name, defaultValue=None)
            panel_ids = self._normalise_panel_list(stored)
            if panel_ids is not None:
                layout[name] = panel_ids
                seen.add(name)
        for name in self._settings.childKeys():
            name = str(name)
            if name == order_key or name in seen:
                continue
            stored = self._settings.value(name, defaultValue=None)
            panel_ids = self._normalise_panel_list(stored)
            if panel_ids is not None:
                layout[name] = panel_ids
        self._settings.endGroup()
        return layout

    def stored_layout(self) -> "OrderedDict[str, list[str]]":
        return self._load_layout()

    def stored_column_names(self) -> list[str]:
        stored = self._load_layout()
        return list(stored.keys())

    def add_column(self, name: str, column: ReorderableColumn, index: int | None = None) -> None:
        if name in self.columns:
            raise ValueError(f"Column '{name}' already exists")
        items = list(self.columns.items())
        target_index = len(items) if index is None else max(0, min(index, len(items)))
        items.insert(target_index, (name, column))
        self.columns = OrderedDict(items)
        column.set_manager(self)

    def remove_column(self, name: str) -> None:
        column = self.columns.get(name)
        if column is None:
            return
        if column.panels():
            raise ValueError(f"Column '{name}' is not empty")
        column.set_manager(None)
        del self.columns[name]
        if self._settings is None:
            return
        self._settings.beginGroup(self._settings_group)
        self._settings.remove(name)
        order_key = "__column_order__"
        order_value = self._settings.value(order_key, defaultValue=[])
        if isinstance(order_value, list):
            updated_order = [str(item) for item in order_value if str(item) != name]
        elif isinstance(order_value, str) and order_value:
            updated_order = [item.strip() for item in order_value.split(",") if item.strip() and item.strip() != name]
        else:
            updated_order = []
        self._settings.setValue(order_key, updated_order)
        self._settings.endGroup()
