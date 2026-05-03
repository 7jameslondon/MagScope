# About this file
This file is for human generated notes not for AI agents. Edits should only be made to this file when specified.


# Notes
The current design has some issues. The dividers between controls plots and camera dont really make sense/serve a purpose. It would be nice to have as many controls visable as possible and a special pop up modal window for things like settings/prefrences. It would probably be best for settings to not be collapsable but instead all visable. However, the histogram and live perveiw should probably be hidden when not enabled and otherwise visable.

## Preferences Modal

Move infrequent/global panels out of the main desktop control rail into a modal Preferences window. Add compact icon-only buttons at the top of the controls area: one opens Preferences, one opens Help/User Guide.

Panels removed from the main control rail:
- MagScope Settings
- Tracking Options
- Reset the GUI
- Need help?

Preferences modal:
- Tab 1: MagScope
- Tab 2: Tracking
- Include a “Reset GUI Layout” button/action in the Preferences window.
- Settings and tracking controls should be fully visible in their tabs, not wrapped in collapsible panels.

Help/User Guide:
- Replace the current “Need help?” panel with an icon-only Help button.
- The Help button should open the MagScope user guide/documentation link.

## Tabbed Control Rail

The preferred redesign is a typical desktop layout with a stable left control rail and a large main viewer area. The current splitter/divider system should be removed or greatly reduced because it allows users to create blank space, shrink controls until columns are hidden, and generally makes the desktop UI harder to understand.

Use a fixed-width left control rail with workflow tabs instead of draggable/resizable control columns. The main area should remain dedicated to the live camera view and plots, with the camera/viewer taking most available space.

Control rail tabs:
- Run: Status, Acquisition, Camera Settings, Bead Selection, Scripting
- Analysis: Plot Settings, Histogram, Radial Profile Monitor, Allan Deviation if available
- Z-LUT: Z-LUT, Z-LUT Generation
- Locking: XY-Lock, Z-Lock
- Custom: optional tab for user-added/custom controls that do not map cleanly to an existing tab

Live/optional panels:
- Histogram and Radial Profile Monitor should keep their enable controls visible.
- Their plot/preview content should be hidden when disabled and shown only when enabled.
- This keeps the control rail shorter and reduces visual clutter.

Layout behavior:
- Avoid user-resizable dividers for the controls rail.
- Do not allow users to drag panels into arbitrary extra columns in the default desktop UI.
- The control rail width should be stable enough that controls are not clipped.
- The camera/video viewer should receive the flexible expanding space.

## Dockable Live Viewer Redesign

The live camera viewer and live plots should be implemented as dockable desktop panes rather than fixed widgets inside splitter layouts or separate hard-coded windows.

Use Qt's native `QDockWidget` system for:
- Live Camera
- Live Plots

Behavior:
- Both viewer panes should be dockable, floatable, movable, and restorable.
- Users should be able to undock either pane into its own floating window.
- Users should be able to drag floating panes back into the main MagScope window.
- Users should be able to restore a hidden/closed viewer pane from a View/Viewers toolbar or menu.
- A `Reset Viewer Layout` action should restore the default camera/plot layout.

Startup behavior should preserve the current `n_windows` intent:
- `n_windows == 1`: controls, camera, and plots start in one main window, with camera and plots docked.
- `n_windows == 2`: controls and camera start in the main window, live plots start floating on the second screen.
- `n_windows == 3`: controls start in the main window, camera and plots start floating on separate screens when available.
- Even when a pane starts floating, it must still be a dock widget owned by the main window so it can be re-docked.

Implementation guidance:
- Refactor `UIManager.create_central_widgets()` so the central widget hosts only the controls area.
- Create `QDockWidget("Live Camera")` wrapping `self.video_viewer`.
- Create `QDockWidget("Live Plots")` wrapping `self.plots_widget`.
- Add both docks to the primary `QMainWindow`.
- Add a small toolbar or menu with `Live Camera`, `Live Plots`, and `Reset Viewer Layout`.
- Use each dock's `toggleViewAction()` for show/hide restore behavior.
- Persist viewer layout separately from the controls layout using `QMainWindow.saveState()` / `restoreState()` in `QSettings`.
- Suggested key: `viewer/layout_state`.
- Do not couple this to the existing `controls/layout` settings.

Important constraints:
- Do not create duplicate camera or plot widgets. The live viewer and plot label should remain single widgets moved into dock containers.
- Do not change video acquisition, plotting, buffers, IPC, or tracking behavior.
- Avoid custom docking/window-management code unless Qt's native dock system cannot satisfy a requirement.