# MagScope User Guide Asset Map

Generated documentation assets live under:

```text
assets/doc_capture/
```

This keeps the new documentation captures grouped while preserving the repository's existing
top-level `assets/` convention.

## Beginner Guide Outline

1. **Before You Start**
   - Purpose: explain what the guide covers, what the demo mode is, and what does not require hardware.
   - Assets: none required.

2. **Install and Launch the Demo**
   - Purpose: get a new user from Python import to the first MagScope window.
   - Assets:
     - `screenshots/startup/main-window.png`

3. **First Look at the Main Window**
   - Purpose: orient the user to the top bar, controls area, Live Camera, and Live Plots.
   - Assets:
     - `screenshots/startup/main-window.png`
     - `screenshots/controls/run-tab.png`
     - `screenshots/controls/analysis-tab.png`
     - `screenshots/controls/locking-tab.png`
     - `screenshots/controls/motors-tab.png`

4. **Live Camera and Bead ROIs**
   - Purpose: explain the simulated camera view, bead boxes, selected/reference colors, ROI IDs, and auto bead selection.
   - Assets:
     - `screenshots/startup/live-camera-with-rois.png`
     - `screenshots/workflows/bead-selection-workflow.png`
     - `gifs/workflows/bead-selection-workflow.gif`
     - `screenshots/workflows/auto-bead-selection-dialog.png`
     - `screenshots/workflows/auto-bead-selection-accepted.png`

5. **Control Panels and Navigation**
   - Purpose: explain workflow tabs, titled panels, scrolling, search, and the basic panel workflow.
   - Assets:
     - `screenshots/navigation/search-box-roi.png`

6. **Status and Camera Settings**
   - Purpose: explain display rate, video processors, video buffer, and simulated camera settings.
   - Assets:
     - `screenshots/panels/status-panel.png`
     - `screenshots/panels/camera-settings-panel.png`

7. **Recording and Saving Data**
   - Purpose: explain Acquire, Saving, folder selection, and data mode choices.
   - Assets:
     - `screenshots/panels/acquisition-panel.png`
     - `screenshots/workflows/save-folder-picker.png`
     - `screenshots/workflows/saving-enabled.png`

8. **Live Plots and Analysis Panels**
   - Purpose: explain Live Plots, plot settings, histogram, and radial profile monitor.
   - Assets:
     - `screenshots/live-view/live-plots.png`
     - `screenshots/live-view/live-plots-with-data.png`
     - `screenshots/workflows/analysis-tab-with-plots.png`
     - `screenshots/panels/plot-settings-panel.png`
     - `screenshots/panels/histogram-panel.png`
     - `screenshots/panels/radial-profile-panel.png`

9. **Locking and Hardware-Aware Panels**
   - Purpose: introduce XY-Lock, Z-Lock, Z-LUT setup, and the hardware manager placeholder.
   - Assets:
     - `screenshots/panels/xy-lock-panel.png`
     - `screenshots/panels/z-lock-panel.png`
     - `screenshots/zlut/simulated-focus-motor-panel.png`
     - `screenshots/zlut/simulated-focus-motor-plot.png`
     - `screenshots/zlut/new-zlut-dialog.png`
     - `screenshots/zlut/zlut-generation-dialog.png`
     - `screenshots/panels/hardware-managers-panel.png`

10. **Preferences and Settings**
    - Purpose: explain where persistent MagScope, tracking, and appearance settings live.
    - Assets:
      - `screenshots/preferences/preferences-dialog.png`

11. **Scripting**
    - Purpose: introduce the scripting panel and point to the scripting guide.
    - Assets:
      - `screenshots/panels/scripting-panel.png`

12. **Troubleshooting and Shutdown**
    - Purpose: describe common beginner issues, safe shutdown, and when to restart Python.
    - Assets: none required for the first pass.

## Current Capture Scenarios

| Scenario | Asset | Guide Section |
| --- | --- | --- |
| Main window overview | `screenshots/startup/main-window.png` | Install and Launch; First Look |
| Live camera with ROIs | `screenshots/startup/live-camera-with-rois.png` | Live Camera and Bead ROIs |
| Live plots dock, no data | `screenshots/live-view/live-plots.png` | Live Plots and Analysis Panels |
| Live plots dock, populated data | `screenshots/live-view/live-plots-with-data.png` | Live Plots and Analysis Panels |
| Analysis workflow with live plots | `screenshots/workflows/analysis-tab-with-plots.png` | Live Plots and Analysis Panels |
| Full Run controls | `screenshots/controls/run-tab.png` | First Look |
| Full Analysis controls | `screenshots/controls/analysis-tab.png` | First Look |
| Full Locking controls | `screenshots/controls/locking-tab.png` | First Look; Locking |
| Full Motors controls | `screenshots/controls/motors-tab.png` | First Look; Locking |
| Status panel | `screenshots/panels/status-panel.png` | Status and Camera Settings |
| Acquisition panel | `screenshots/panels/acquisition-panel.png` | Recording and Saving Data |
| Camera settings panel | `screenshots/panels/camera-settings-panel.png` | Status and Camera Settings |
| Scripting panel | `screenshots/panels/scripting-panel.png` | Scripting |
| Plot settings panel | `screenshots/panels/plot-settings-panel.png` | Live Plots and Analysis Panels |
| Histogram panel | `screenshots/panels/histogram-panel.png` | Live Plots and Analysis Panels |
| Radial profile panel | `screenshots/panels/radial-profile-panel.png` | Live Plots and Analysis Panels |
| XY-Lock panel | `screenshots/panels/xy-lock-panel.png` | Locking and Hardware-Aware Panels |
| Z-Lock panel | `screenshots/panels/z-lock-panel.png` | Locking and Hardware-Aware Panels |
| Hardware manager placeholder | `screenshots/panels/hardware-managers-panel.png` | Locking and Hardware-Aware Panels |
| Search box with ROI results dropdown | `screenshots/navigation/search-box-roi.png` | Control Panels and Navigation |
| Preferences dialog | `screenshots/preferences/preferences-dialog.png` | Preferences and Settings |
| Bead selection workflow | `screenshots/workflows/bead-selection-workflow.png` | Live Camera and Bead ROIs |
| Bead selection cursor frames | `gif_frames/workflows/bead-selection-workflow/frame-01.png` through `frame-41.png` | Local intermediate artifacts, not committed |
| Bead selection workflow GIF | `gifs/workflows/bead-selection-workflow.gif` | Live Camera and Bead ROIs |
| Auto Bead Selection dialog | `screenshots/workflows/auto-bead-selection-dialog.png` | Live Camera and Bead ROIs |
| Accepted auto-selected beads | `screenshots/workflows/auto-bead-selection-accepted.png` | Live Camera and Bead ROIs |
| Save-folder picker | `screenshots/workflows/save-folder-picker.png` | Recording and Saving Data |
| Saving enabled workflow | `screenshots/workflows/saving-enabled.png` | Recording and Saving Data |
| Simulated focus motor panel | `screenshots/zlut/simulated-focus-motor-panel.png` | Locking and Hardware-Aware Panels |
| Simulated focus motor plot | `screenshots/zlut/simulated-focus-motor-plot.png` | Locking and Hardware-Aware Panels |
| New Z-LUT dialog | `screenshots/zlut/new-zlut-dialog.png` | Locking and Hardware-Aware Panels |
| Generated Z-LUT review dialog | `screenshots/zlut/zlut-generation-dialog.png` | Locking and Hardware-Aware Panels |

The bead-selection cursor frames are produced from real Qt click, drag, and right-click events in
the documentation `VideoViewer`, then annotated with a synthetic cursor so the GIF shows where the
user action happened. The frame folder is ignored by Git because the committed GIF and capture
script are enough for the guide and for regeneration.

## Later Capture Candidates

- A full multi-process live demo capture if the in-process documentation scene cannot represent a needed state.
