# Scope
   These guidelines apply to every file within this repository unless overridden by a more specific `AGENTS.md` file in a subdirectory.

# Import Guidelines
   To ensure consistency across the repository, please order and format imports as follows:
   1. **Grouping**
      - Group imports into three sections, separated by a single blank line:
      1. Standard library imports
      2. Third-party library imports
      3. Local package or relative imports
   2. **Ordering**
      - Within each group, sort imports alphabetically by module path.
      - Use explicit module paths instead of wildcard imports. Except for "from magscope.ipc_commands import *".
   3. **Formatting**
      - Combine imports from the same module on a single line when possible (e.g., `from module import A, B`).
      - If the imported names do not fit within 100 characters, use parentheses with one import per line and a trailing comma.
      - Keep import statements at the top of the file, after any module-level docstring and before other code.

# Circular Import Safety
   - When adjusting imports, double-check that reordering does not change when modules are first executed. Many packages (notably `magscope.ui`) rely on specific initialization order, so moving an import between groups can introduce circular dependencies.
   - Prefer importing from a module that defines the symbol directly instead of going through package-level re-exports when there is any risk of a cycle (e.g., import `AcquisitionMode` from `magscope.utils`, not from `magscope`).
   - If you must refactor imports across modules, run `python -c "import magscope"` locally to confirm no circular import errors are introduced.

# Project overview
   You are working on a scientific Python application called MagScope. MagScope is a GUI-driven microscope control and acquisition framework for magnetic-tweezers experiments. It uses multiple manager processes (e.g., CameraManager, BeadLockManager, VideoProcessorManager, ScriptManager, UIManager and hardware managers) that subclass ManagerProcessBase and run in separate multiprocessing.Process instances.

   Shared state is stored in ring buffers and matrices backed by shared memory (VideoBuffer, MatrixBuffer, InterprocessValues) with per-buffer locks. Processes exchange commands and status via Message objects sent over multiprocessing.Pipe connections created by create_pipes. The MagScope class in scope.py is the top-level orchestrator: it constructs managers, shared buffers, locks, and pipes, calls configure_shared_resources(...) on each manager, runs the main IPC loop, and supervises shutdown.

   The GUI lives in UIManager, which runs a Qt event loop and uses timers to pull images from VideoBuffer, overlay bead tracks from MatrixBuffer, and update plots and control panels. Cameras implement CameraBase (e.g., DummyCameraBeads) and are managed by CameraManager. Video processing is handled by VideoProcessorManager and VideoWorker, which read stacks from VideoBuffer, call MagTrack to compute bead positions/profiles (CPU/GPU), and write into MatrixBuffer.

# Global constraints for all changes
   - Preserve all existing user-visible behavior, public APIs, configuration formats, and on-disk file formats unless explicitly instructed otherwise.
   - Treat the acquisition, buffer, tracking, and IPC paths as performance-critical. Do not add unnecessary copies, allocations, or extra layers of abstraction on hot paths.
   - Keep the basic architecture intact: MagScope as orchestrator, manager classes as singletons (SingletonABCMeta), shared-memory buffers as the data path, and Message-based IPC as the control path.
   - Do not change shared buffer names, shapes, or indexing behavior (VideoBuffer, ProfilesBuffer, TracksBuffer, hardware matrices) unless explicitly requested.
   - Prefer small, incremental refactors over large rewrites. If you see more work to do, describe follow-up steps instead of doing everything at once.
   - Do not add new runtime dependencies without being asked.
   - Reduce unnecessary complexity and confusing control flow while preserving semantics.
   - Make naming consistent and descriptive across modules.
   - Improve docstrings and comments where they clarify responsibilities, invariants, performance constraints, or cross-process interactions.
   - Keep formatting consistent and easy to read. Prefer to follow the existing style; do not introduce a different formatter style unless explicitly requested.

# Tests and validation:
   - If the environment allows, run pytest after making changes and report the results. Do not treat the tests as exhaustive; also reason about correctness and performance.

# Tecnical infomration
   - MagScope class instances are a singleton. They can only be started once.
   