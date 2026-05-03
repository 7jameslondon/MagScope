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