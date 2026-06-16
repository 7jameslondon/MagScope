# Changelog

All notable user-facing changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.4.0] - 2026-06-16

### Added
- Dockable Live Camera and Live Plots panes with a Layout menu for showing, redocking, and resetting viewer panes.
- Persistent single-window viewer layouts, including floating and maximized dock states.
- Guide-only UI control search with fuzzy suggestions, keyboard shortcuts, and navigation to panels, preferences, and menu items.
- Preferences tabs for MagScope, Tracking, Appearance, and layout reset options, including a configurable UI accent color.
- Top-level Z-LUT menu actions for creating, loading, unloading, and previewing the current Z-LUT.
- Live camera bead toolbar controls for bead editing, ROI settings, ID reassignment, and bead counts.
- Adaptive workflow tabs for Run, Analysis, Locking, and Custom controls.
- Packaged Material Symbols icons and refreshed SVG/logo assets for the UI and startup splash.
- Unified main window top bar with custom title label, window controls, compact mode, and reorganized menus.
- Redesigned XY-Lock and Z-Lock panels with compact two-column card layout, activity indicators, and focus motor detection.
- Desktop app icons for the taskbar and main window.
- Motors tab hardware placeholder panel shown when no hardware managers are registered.
- Unified preferences import and export for appearance and layout settings.
- Live plot loading bar preference and ring indicator for loading state.
- Expanded user guide screenshots and workflow GIFs for the redesigned UI.

### Changed
- MagScope now uses one main window with dockable viewer panes instead of the previous multi-window viewer layout.
- Bead selection, Z-LUT, layout reset, and settings controls were moved from the main control rail into toolbar, menu, and Preferences workflows.
- Z-Lock corrections are now damped for smoother focus adjustments.
- Acquisition labels, acquisition mode display names, histogram/profile panels, dock styling, and live zoom display were refined for clarity and consistency.
- **Breaking**: AcquisitionMode enum values are renamed to match display labels (e.g. `"Video (Full)"` instead of `"video (full)"`).
  Old member names (`FULL_VIDEO`, `TRACK_AND_CROP_VIDEO`, `CROP_VIDEO`) remain as aliases so
  `AcquisitionMode.FULL_VIDEO` still resolves correctly, but any code that compares against or
  passes raw strings like `"video (full)"` must be updated to the new values like `"Video (Full)"`.
- PyPI and TestPyPI publish workflows now use newer artifact actions to avoid GitHub Actions Node 20 deprecation warnings.
- Dock viewer controls now use Material Symbols icons (pin, float, close).
- Live plot rendering and layout tightened with reduced padding.
- Preferences dialog redesigned with sidebar navigation and grouped settings.
- Workflow columns now use drag-and-drop movable tabs with responsive layout.

### Fixed
- Invalid saved viewer dock geometry is cleared and replaced with the default layout instead of restoring a broken layout.
- UI control search now handles empty and missing queries without stale status feedback or unintended menu action execution.
- Minimap zoom labels now round consistently as one-decimal multipliers.
- Restored native window caption buttons to fix Aero Snap on Windows.
- Bead ROI dragging no longer breaks under CustomizeWindowHint window flags.
- Help button hover state behaves correctly across themes.
- Startup splash renders correctly across display configurations.
- Windows taskbar icon displays correctly.

## [0.3.0] - 2026-04-14

### Added
- Python microscope integration for cameras and focus motor support.
- Z-LUT generation workflow, preview, and related UI.
- Auto bead selection workflow with progress reporting and cancellation support.
- New hardware integrations including simulated linear motors, Zaber NMS, and PI E-709 focus motor support.
- Allan deviation plotting and data saving support.

### Changed
- GPU installation guidance and optional dependencies now target CuPy v14 with CUDA 12 and 13 extras.
- Startup flow and splash handling were improved to provide clearer progress and timeout behavior.
- Several UI panels, labels, and plotting behaviors were refined for usability and responsiveness.
- Cross-platform CI diagnostics were expanded, especially for Python 3.13.

### Fixed
- Video processing reservation and completion tracking issues.
- Camera health monitoring, initialization, and error handling in several hardware paths.
- Auto bead selection false positives, stale search state, and overlay refresh issues.
- Multiple Z-LUT generation edge cases around validation, cancellation, preview ordering, and failure handling.

## [0.2.1] - 2025-12-05

### Added
- Z-LUT selection panel and loading support.
- Camera connection guide and GPU setup guide.
- GUI reset panel and global lock overlay in the video viewer.
- Reusable IPC and scripting command infrastructure shared across processes.

### Changed
- MagScope orchestrator structure and lifecycle documentation were clarified.
- Bead ROI caching and XY-lock batching were optimized.
- The README and user documentation were simplified and expanded.

### Fixed
- Empty settings files are handled safely.
- IPC polling and quit handling were made more robust.
- Camera teardown and several GUI edge cases were corrected.

## [0.1.4] - 2025-11-15

### Added
- Getting started and user guide documentation.
- Configurable verbose logging.
- Additional GUI tests and panel demos.

### Changed
- Dummy camera configuration and several UI interactions were simplified.
- Internal buffer handling and validation feedback were improved.

### Fixed
- Camera teardown safety issues.
- Dummy camera frame sequencing issues.
- Duplicate starts of a `MagScope` instance are now blocked.

[Unreleased]: https://github.com/7jameslondon/MagScope/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/7jameslondon/MagScope/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/7jameslondon/MagScope/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/7jameslondon/MagScope/compare/v0.1.4...v0.2.1
[0.1.4]: https://github.com/7jameslondon/MagScope/releases/tag/v0.1.4
