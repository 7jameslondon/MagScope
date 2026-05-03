# About this file
This file is for human generated notes not for AI agents. Edits should only be made to this file when specified.


# Notes
The current design has some issues. The dividers between controls plots and camera dont really make sense/serve a purpose. It would be nice to have as many controls visable as possible and a special pop up modal window for things like settings/prefrences. It would probably be best for settings to not be collapsable but instead all visable. However, the histogram and live perveiw should probably be hidden when not enabled and otherwise visable.

## Preferences Modal Idea

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