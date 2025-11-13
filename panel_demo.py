"""Demo utilities for experimenting with reorderable panel columns.

This module intentionally avoids depending on a GUI backend so that it can be
imported and unit tested inside headless environments.  The classes model the
logic that a Qt based implementation would need in order to keep track of panel
positions while dragging and dropping items between columns.  The important part
for this exercise is the :meth:`ReorderableColumn.add_panel` method which mimics
how the real widget updates its layout when a user drops a panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class _StretchItem:
    """Sentinel object representing the layout stretch."""

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<Stretch>"


class DummyLayout:
    """Simplified stand-in for ``QVBoxLayout``.

    The dummy layout keeps the items that would be managed by a real Qt layout
    in a Python list and exposes a subset of the API used by the demo classes.
    Only the behaviour that is relevant for the drag and drop logic is
    implemented.
    """

    def __init__(self) -> None:
        self._items: List[object] = []

    # -- layout helpers -------------------------------------------------
    def addStretch(self) -> _StretchItem:
        stretch = _StretchItem()
        self._items.append(stretch)
        return stretch

    def insertWidget(self, index: int, widget: object) -> None:
        if index < 0:
            index = 0
        if index > len(self._items):
            index = len(self._items)
        self._items.insert(index, widget)

    def removeWidget(self, widget: object) -> None:
        try:
            self._items.remove(widget)
        except ValueError:
            pass

    def takeAt(self, index: int) -> Optional[object]:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def indexOf(self, widget: object) -> int:
        try:
            return self._items.index(widget)
        except ValueError:
            return -1

    def count(self) -> int:
        return len(self._items)

    # -- debug utilities ------------------------------------------------
    def dump(self) -> List[object]:  # pragma: no cover - debug helper
        return list(self._items)


@dataclass
class ReorderablePanel:
    """Model object that represents an individual panel."""

    name: str
    pinned: bool = False
    column: Optional[ReorderableColumn] = field(default=None, repr=False)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"Panel({self.name!r}, pinned={self.pinned})"


class DropPlaceholder:
    """Placeholder widget shown while dragging panels."""

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return "<Placeholder>"


class ReorderableColumn:
    """Collection of panels that can be reordered via drag and drop."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._layout = DummyLayout()
        self._stretch_item = self._layout.addStretch()
        self._panels: List[ReorderablePanel] = []
        self._placeholder: Optional[DropPlaceholder] = None

    # -- public API -----------------------------------------------------
    def set_placeholder(self, placeholder: DropPlaceholder) -> None:
        """Register a drop placeholder with the column."""

        self._placeholder = placeholder
        # Always keep the placeholder just before the stretch in the layout so
        # that it does not participate in index calculations for real panels.
        stretch_index = self._layout.indexOf(self._stretch_item)
        insertion_index = stretch_index if stretch_index != -1 else self._layout.count()
        self._layout.insertWidget(insertion_index, placeholder)

    def panels(self) -> List[ReorderablePanel]:
        """Return the panels in their visual order."""

        return list(self._panels)

    def add_panel(self, panel: ReorderablePanel, target_index: Optional[int] = None) -> int:
        """Insert *panel* at ``target_index`` while respecting pinned panels.

        When a panel is dragged within the same column the target index coming
        from the drop operation still references the layout that contains the
        panel and possibly a placeholder widget.  If we insert before removing
        the panel the index may land after the stretch item which keeps the
        layout's remaining space at the bottom.  The updated logic removes both
        the panel and any placeholder widget before constraining the index,
        ensuring that the panel snaps directly beneath the last real panel.
        """

        # Temporarily remove the placeholder from the layout so that it does
        # not influence the index calculations.  We keep a reference around so
        # it can be restored afterwards.
        placeholder_item: Optional[object] = None
        placeholder_index = -1
        if self._placeholder is not None:
            placeholder_index = self._layout.indexOf(self._placeholder)
            if placeholder_index != -1:
                placeholder_item = self._layout.takeAt(placeholder_index)

        # If the panel already lives in this column remove it from both the
        # layout and the internal bookkeeping structures before calculating the
        # constrained drop index.  This mirrors the behaviour of the Qt
        # implementation where the widget is taken out of the layout during the
        # drag.
        current_index = self._panel_index(panel)
        if current_index != -1:
            self._panels.pop(current_index)
            self._layout.removeWidget(panel)
            # Once the panel has been removed the drop index must be clamped
            # against the shorter list of panels to avoid inserting below the
            # stretch item.
            if target_index is not None and target_index > len(self._panels):
                target_index = len(self._panels)

        if target_index is None:
            target_index = len(self._panels)

        # Respect pinned panels.  Non pinned panels cannot be inserted before
        # the pinned segment while pinned panels must stay within it.
        pinned_count = self._pinned_count()
        if panel.pinned:
            target_index = min(target_index, pinned_count)
        else:
            target_index = max(target_index, pinned_count)

        # Final bounds check in case the requested index is outside the valid
        # range after removing the panel from the layout.
        target_index = max(0, min(target_index, len(self._panels)))

        self._panels.insert(target_index, panel)
        panel.column = self

        self._layout.insertWidget(target_index, panel)

        # Restore the placeholder to its original position (or right before the
        # stretch if the original spot is no longer valid).
        if placeholder_item is not None:
            stretch_index = self._layout.indexOf(self._stretch_item)
            if stretch_index == -1:
                stretch_index = self._layout.count()
                self._stretch_item = self._layout.addStretch()
                stretch_index = self._layout.indexOf(self._stretch_item)
            reinsertion_index = min(placeholder_index, stretch_index)
            self._layout.insertWidget(reinsertion_index, placeholder_item)
            # ``takeAt`` removed the object from the layout, so make sure our
            # placeholder reference still points to the restored instance.
            if isinstance(placeholder_item, DropPlaceholder):
                self._placeholder = placeholder_item

        return target_index

    def remove_panel(self, panel: ReorderablePanel) -> None:
        """Remove ``panel`` from the column if present."""

        index = self._panel_index(panel)
        if index == -1:
            return
        self._panels.pop(index)
        self._layout.removeWidget(panel)
        panel.column = None

    # -- internal helpers -----------------------------------------------
    def _panel_index(self, panel: ReorderablePanel) -> int:
        try:
            return self._panels.index(panel)
        except ValueError:
            return -1

    def _pinned_count(self) -> int:
        return sum(1 for panel in self._panels if panel.pinned)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"ReorderableColumn({self.name!r}, panels={self._panels})"


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    column = ReorderableColumn("Demo")
    column.set_placeholder(DropPlaceholder())
    panels = [
        ReorderablePanel("Panel 1", pinned=True),
        ReorderablePanel("Panel 2", pinned=True),
        ReorderablePanel("Panel 3"),
        ReorderablePanel("Panel 4"),
    ]

    for idx, panel in enumerate(panels):
        column.add_panel(panel, idx)

    # Simulate dragging the final panel to the bottom again – this should not
    # change the ordering or place the panel underneath the stretch item.
    final_index = column.add_panel(panels[-1], target_index=len(column.panels()) + 1)
    print(f"Final index: {final_index}")
    print(f"Order: {column.panels()}")
