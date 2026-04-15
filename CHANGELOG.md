# Changelog

All notable user-facing changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.0] - Unreleased

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

[Unreleased]: https://github.com/7jameslondon/MagScope/compare/v0.2.1...HEAD
[0.3.0]: https://github.com/7jameslondon/MagScope/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/7jameslondon/MagScope/compare/v0.1.4...v0.2.1
[0.1.4]: https://github.com/7jameslondon/MagScope/releases/tag/v0.1.4
