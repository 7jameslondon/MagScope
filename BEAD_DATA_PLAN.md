# Bead ROI Shared-Memory Plan

## Goal

Replace the current bead ROI synchronization approach, which broadcasts a Python
`dict[int, tuple[int, int, int, int]]` over IPC, with a shared-memory-backed ROI
store plus lightweight change notifications.

The new design should:

- keep bead updates fast
- keep ROI reads fast enough for video processing and UI consumers
- avoid repeated pickling and copying of ROI dictionaries between processes
- allocate the full shared-memory ROI buffer once at startup
- support up to 10000 beads without resizing shared memory
- preserve monotonic bead ID allocation unless the UI explicitly compacts IDs
- make one process the sole writer to bead state
- keep ROI validation and move acceptance centralized


## Current State

Today bead ROIs are stored per process as a dictionary and synchronized through
`SetBeadRoisCommand` broadcasts.

Important current usage patterns:

- the UI builds the ROI dictionary in `magscope/ui/ui.py`
- managers receive that dictionary via `ManagerProcessBase.set_bead_rois()` in
  `magscope/processes.py`
- `magscope/videoprocessing.py` immediately converts the dict keys and values to
  NumPy arrays before cropping and tracking
- `magscope/beadlock.py` iterates bead IDs and ROIs during XY lock updates
- `magscope/ui/controls.py` uses the ROI values for histogram cropping

This means the existing dict is convenient, but not a good fit for the
performance-sensitive tracking path.


## Recommended Design

Use a new shared-memory object named `BeadRoiBuffer` in `magscope/datatypes.py`.

The recommended implementation is phased:

### Phase 1: shared-memory read optimization

- add `BeadRoiBuffer`
- keep the UI as the sole writer for bead ROI changes
- replace full-dictionary ROI broadcasts with lightweight change
  notifications
- have consumers refresh process-local cached `(ids, rois)` snapshots from the
  shared buffer

This phase captures the main performance win with the least architectural
change, because the current program already treats the UI as the authoritative
owner of bead graphics and ROI edits.

### Phase 2: optional write-side centralization

Only if later work shows it is necessary, add a dedicated `BeadManager` that
becomes the sole writer to `BeadRoiBuffer`.

That second phase should be treated as a separate project because it requires a
real ownership inversion: the UI would stop being the bead authority and would
instead reconcile its graphics from canonical shared state.

The buffer is allocated once during MagScope startup, like the existing shared
memory buffers, and then attached by other processes with `create=False`.

### Canonical Data Layout

Store ROIs by bead ID using row-indexed shared arrays with a fixed capacity of
10000 bead rows:

- ROI matrix: `uint32[:, 4]`
- occupancy flags: `uint8[:]`
- row index equals bead ID
- valid bead IDs are `0..9999`

The four ROI columns are:

- column 0: `x0`
- column 1: `x1`
- column 2: `y0`
- column 3: `y1`

Occupancy is stored separately from the ROI matrix rather than as a fifth
column. This keeps ROI slicing simple and avoids mixing coordinate and state
data in one dtype.


## Why This Structure

This design is a compromise between fast updates and simple reads.

### Fast updates

Because row number equals bead ID:

- `add_beads()` writes directly to known rows
- `update_beads()` is direct indexed overwrite
- `remove_beads()` only clears occupancy and row contents

These operations are effectively O(number of changed beads).

### Fast reads

`get_beads()` can be implemented as:

```python
occupied = occupancy[:max_id_plus_one]
ids = np.flatnonzero(occupied)
rois = roi_matrix[ids].copy()
return ids, rois
```

That is efficient because:

- the occupancy scan is compact and vectorized
- only active ROI rows are copied
- the returned shape directly matches what video processing wants

### Why not a dict or Manager dict

- Python dicts create high per-entry overhead and require pickling across IPC
- `multiprocessing.Manager().dict()` adds proxy/server overhead and is not well
  suited to hot paths

### Why not a more complex sparse+dense dual structure initially

A structure that keeps both a sparse id-indexed table and a dense active table
could make reads even faster, but it adds significantly more implementation and
consistency complexity. The selected design is simpler and should be fast enough
when paired with process-local caching.


## Shared Memory Layout

`BeadRoiBuffer` should use deterministic shared-memory segment names in the same
style as the existing buffers.

### Metadata segment

Store at least these values in a small shared metadata block:

- `capacity`
- `max_id_plus_one`
- `active_count`
- `version`

All metadata values can be stored as 64-bit unsigned integers for simplicity.

### Data segments

Allocate exactly these shared-memory segments at startup:

- one ROI data segment for `(10000, 4)` `uint32`
- one occupancy segment for `(10000,)` `uint8`

Suggested naming scheme:

- `BeadRoiBuffer Info`
- `BeadRoiBuffer Data`
- `BeadRoiBuffer Occupancy`

### Capacity and memory size

- ROI data uses `10000 * 4 * 4 = 160000` bytes
- occupancy uses `10000 * 1 = 10000` bytes
- total buffer payload is about `170 KB`, plus a small metadata segment

This fixed allocation is small enough that dynamic growth is unnecessary.


## Ownership Model

`BeadRoiBuffer` is the canonical shared bead store, but writer ownership should
follow the migration phase.

### Phase 1 writer ownership

- `UIManager` remains the only process that mutates `BeadRoiBuffer`
- UI, video processing, bead lock, and other managers may call `get_beads()` or
  other read helpers on the buffer
- non-UI processes continue to request visual bead moves through existing UI
  flows

This matches the current architecture, where bead graphics live in the UI and
the UI already ignores inbound ROI snapshots.

### Phase 2 writer ownership

If a later refactor introduces `BeadManager`, then:

- `BeadManager` becomes the only process allowed to mutate `BeadRoiBuffer`
- all bead write requests must be sent to `BeadManager` over IPC
- the UI must rebuild and reconcile graphics from canonical shared state

### Responsibilities if `BeadManager` is added later

- add beads
- update bead ROIs
- remove beads
- clear beads
- reorder beads
- determine the next bead ID
- validate whether a requested bead move is allowed
- enforce ROI/image bounds
- send bead update notifications after successful changes

That is a valid long-term direction, but it should not be conflated with the
shared-memory read optimization itself.


## Locking Model

Use a single multiprocessing lock for the whole `BeadRoiBuffer`, matching the
existing repository pattern used by `VideoBuffer` and `MatrixBuffer`.

### Mutating operations

These should hold the lock for the full operation:

- `add_beads()`
- `update_beads()`
- `remove_beads()`
- `reorder_beads()`
- `clear_beads()`

### Read operations

`get_beads()` should also hold the lock while constructing its returned copied
snapshot, then release the lock before the caller uses the arrays.

This avoids handing out live references into mutable shared memory.


## Public API

`BeadRoiBuffer` should expose the following methods.

### `add_beads(value: dict[int, tuple[int, int, int, int]]) -> None`

- accepts multiple bead ROIs at once
- requires every bead ID to be within the fixed capacity `0..9999`
- writes all rows in one lock section
- sets occupancy for all added rows
- updates `active_count`
- updates `max_id_plus_one` if needed
- increments `version` once

This method should not send IPC itself; the caller responsible for the logical
batch should send one notification after the write completes.

If any bead ID is outside the fixed capacity, raise a clear error.

### `update_beads(value: dict[int, tuple[int, int, int, int]]) -> None`

- accepts multiple bead ROI updates at once
- requires that all bead IDs already exist and are occupied
- writes all rows in one lock section
- increments `version` once

If any bead ID is invalid or not occupied, raise an error.

### `remove_beads(ids) -> None`

- accepts an iterable of bead IDs
- clears occupancy for those IDs
- zeros the ROI rows for cleanliness
- decrements `active_count`
- leaves `max_id_plus_one` unchanged so IDs remain monotonic
- increments `version` once

### `reorder_beads() -> dict[int, int]`

This method takes no arguments.

Purpose:

- compact all active bead IDs so there are no gaps
- preserve the existing bead order
- reduce IDs only as needed to fill gaps

Behavior:

- iterate active bead IDs in ascending order
- assign new IDs `0..n-1` in that same order
- move ROI rows and occupancy accordingly
- clear any rows that are no longer occupied
- set `max_id_plus_one = active_count`
- increment `version` once
- return an `old_id -> new_id` mapping

In phase 1 this may be called by the UI's existing `Reassign IDs` flow. In
phase 2 it should move behind a `BeadManager` request.

### `get_next_available_bead_id() -> int`

- return `max_id_plus_one`
- do not search for gaps
- preserve monotonic ID allocation

If `max_id_plus_one >= 10000`, callers must treat the buffer as full until IDs
are compacted or beads are removed.

### `get_beads() -> tuple[np.ndarray, np.ndarray]`

Return:

- `ids`: shape `(n,)`, dtype `uint32`
- `rois`: shape `(n, 4)`, dtype `uint32`

Properties:

- includes only occupied bead IDs
- sorted in ascending bead ID order
- returns copied arrays, not live shared-memory views

This is the preferred consumer format for performance-sensitive code.

### `clear_beads() -> None`

- clear occupancy for all active rows
- zero ROI storage
- reset `active_count` to zero
- reset `max_id_plus_one` to zero
- increment `version` once

The fixed shared-memory allocation remains in place after clear.


## Consumer Data Shape

The selected consumer interface is:

```python
ids, rois = bead_roi_buffer.get_beads()
```

This is preferred over a dict because it matches current usage in
`magscope/videoprocessing.py`, where the dict is immediately turned into arrays.

### Why this is the best fit

In `videoprocessing`, the current code does all of the following:

- `np.array(list(bead_rois.values()))`
- `len(bead_rois)`
- `np.array(list(bead_rois.keys()))`
- `list(bead_rois.values())` for ROI cropping

Returning `(ids, rois)` directly removes that Python conversion overhead.


## Notification Model

The shared-memory buffer is the source of truth. IPC should only notify other
processes that ROI data changed.

### New IPC command

Add a new payload-free command:

```python
@dataclass(frozen=True)
class UpdateBeadRoisCommand(Command):
    pass
```

This replaces the current pattern of broadcasting the full ROI dictionary.

### Phase 1 command model

In phase 1, do not introduce a new writer-targeted command surface yet.

- the UI writes to `BeadRoiBuffer`
- the UI sends one `UpdateBeadRoisCommand()` after each logical batch
- existing command flows such as `MoveBeadsCommand` keep their current meaning

This keeps the migration focused on transport and caching rather than changing
ownership semantics at the same time.

### Phase 2 command model if `BeadManager` is added

If a later phase adds `BeadManager`, add direct commands targeted to
`BeadManager` for mutation requests.

Suggested commands:

- `AddBeadsCommand`
- `UpdateBeadsCommand`
- `RemoveBeadsCommand`
- `ClearBeadsCommand`
- `ReorderBeadsCommand`
- `RequestMoveBeadsCommand`

Important:

- do not reuse `MoveBeadsCommand` for a different routing meaning
- request and notification/result messages must use distinct command classes
  because the current IPC registry stores one route per command type
- if move batches can be rejected, clamped, or partially applied, add an
  explicit result command rather than relying on `UpdateBeadRoisCommand()`
  alone

### Notification semantics

- the current phase writer writes ROI changes into `BeadRoiBuffer`
- after the logical batch completes, that writer sends one
  `UpdateBeadRoisCommand()`
- recipients refresh their local cached `(ids, rois)` snapshot

No ROI payload should be carried in the command.


## Process-Local Caching

The most important runtime optimization is to avoid calling `get_beads()` in hot
loops.

### Strategy

Each process that needs bead ROIs should cache the latest snapshot locally.

Recommended cached fields:

- `self._bead_roi_ids`
- `self._bead_roi_values`

When `UpdateBeadRoisCommand()` is received:

- call `ids, rois = self.bead_roi_buffer.get_beads()`
- replace the cached arrays

### Why this matters

This keeps video processing out of shared ROI scans during its hot path.

The intended flow is:

- UI requests a bead change from `BeadManager`
- `BeadManager` updates shared memory
- `BeadManager` sends one notification
- `VideoProcessorManager` refreshes its cache once
- worker tasks use that cached snapshot until the next notification


## Batching Rules

Any component that requests changes to multiple beads should use bulk mutation
commands so `BeadManager` can apply one batch and send only one notification.

### Required behavior

- if multiple beads are added, request one `AddBeadsCommand` with the full dict
- if multiple beads are updated, request one `UpdateBeadsCommand` with the full
  dict
- if multiple beads are removed, request one `RemoveBeadsCommand` with all IDs
- after each logical batch, `BeadManager` sends one `UpdateBeadRoisCommand()`

### Explicit goal

Avoid loops that perform:

- one bead change
- one `UpdateBeadRoisCommand()`
- repeat many times

That pattern would create unnecessary IPC overhead.


## UI Integration Plan

### `magscope/ui/ui.py`

Update the UI manager to treat the shared ROI buffer as the source of truth for
reads.

### Phase 1

In the first phase, the UI still owns writes.

Changes:

- `add_bead()`, `remove_bead()`, `clear_beads()`, and `reset_bead_ids()` should
  write `BeadRoiBuffer` and then send one `UpdateBeadRoisCommand()`
- the UI should keep its current responsibility for assigning IDs and updating
  bead graphics
- the UI should maintain its own graphics state and only use shared memory as
  the transport to other processes

When the next monotonic ID would be `10000`, the UI should reject the add
request in phase 1. The UI should surface a clear user-facing message stating that the
maximum number of beads is 10000 and that the user should remove beads or use
`Reassign IDs`.

### Phase 2

If `BeadManager` is added later, move UI writes behind request commands and make
the UI reconcile graphics from canonical shared state.

### Drag and move behavior

Dragging should stay responsive in the UI.

### Phase 1 drag behavior

In phase 1, the UI remains authoritative:

- while the user drags, the `BeadGraphic` moves locally in the UI for smooth
  interaction
- the local drag path should still clamp to obvious scene bounds for immediate
  visual feedback
- on mouse release, the UI writes the canonical ROI into `BeadRoiBuffer`
- the UI then sends one update notification

### Phase 2 drag behavior

If `BeadManager` is added later, dragging can be optimistic in the UI but
authoritative in `BeadManager`.

Behavior:

- while the user drags, the `BeadGraphic` moves locally in the UI for smooth
  interaction
- the local drag path should still clamp to obvious scene bounds for immediate
  visual feedback
- on mouse release, the UI sends a move request to `BeadManager`
- `BeadManager` validates the requested move against bead constraints and image
  bounds
- if accepted, `BeadManager` writes the new ROI to `BeadRoiBuffer` and sends one
  update notification
- if rejected or adjusted, the UI reconciles the graphic to the canonical ROI
  from the buffer after notification

This gives responsive dragging without making the UI the authority on valid ROI
placement.

### Reassign IDs button

The current `reset_bead_ids()` logic in the UI should remain in phase 1.

If `BeadManager` is added later, then the current `reset_bead_ids()` logic in
the UI should be replaced.

New behavior:

- UI sends a `ReorderBeadsCommand` to `BeadManager`
- `BeadManager` calls `mapping = bead_roi_buffer.reorder_beads()`
- `BeadManager` sends one update notification
- UI applies the returned mapping or reconciles from the canonical buffer state
- UI updates bead labels
- UI updates `selected_bead`
- UI updates `reference_bead`

This keeps the compaction operation centralized in the shared buffer.


## Manager and Process Integration Plan

### Phase 1

No new manager process is required for the initial migration.

Changes:

- add `BeadRoiBuffer` to shared buffer creation in `magscope/scope.py`
- add a corresponding lock name in `self.lock_names`
- keep a handle on the scope object, similar to the existing buffers
- attach to `BeadRoiBuffer` when each process starts
- replace the current `self.bead_rois` dict transport with:
  - a `self.bead_roi_buffer` handle
  - cached `self._bead_roi_ids`
  - cached `self._bead_roi_values`
- add a handler for `UpdateBeadRoisCommand()` that refreshes cached arrays

### New `magscope/beadmanager.py`

Add a `BeadManager` process subclassing `ManagerProcessBase`.

Responsibilities:

- attach to `BeadRoiBuffer`
- handle all bead mutation IPC commands
- validate requested add/move/update/remove operations
- maintain single-writer semantics for bead state
- broadcast `UpdateBeadRoisCommand()` after successful logical batches
- optionally include compact/reorder metadata when consumers need ID remapping

This section is phase 2 only.

### `magscope/scope.py`

Changes:

- create and start `BeadManager`
- add `BeadRoiBuffer` to shared buffer creation in `_create_shared_buffers()`
- add a corresponding lock name in `self.lock_names`
- keep a handle on the scope object, similar to the existing buffers
- create the buffer once at startup with `capacity = 10000`

### `magscope/processes.py`

Changes:

- attach to `BeadRoiBuffer` when each process starts
- replace the current `self.bead_rois` dict storage with:
  - a `self.bead_roi_buffer` handle
  - cached `self._bead_roi_ids`
  - cached `self._bead_roi_values`
- add a handler for `UpdateBeadRoisCommand()` that refreshes cached arrays
- if phase 2 is adopted, only `BeadManager` should expose bead write handlers;
  other managers should use direct commands to request mutations

The buffer should be considered the canonical state. The cached arrays are local
read snapshots only.

If phase 1 is used, `UIManager` remains the writer. If phase 2 is used,
`BeadManager` becomes the writer.


## Video Processing Integration Plan

`magscope/videoprocessing.py` is the main performance-sensitive consumer.

### Current issue

The current worker path receives a ROI dict and immediately converts it to NumPy
arrays before doing the actual work.

### New behavior

- `VideoProcessorManager` keeps a cached `(ids, rois)` snapshot
- on `UpdateBeadRoisCommand()`, refresh that cache once
- when enqueuing a worker task, include the cached arrays instead of a dict
- workers consume arrays directly

### Expected worker-side shape

- `bead_ids`: `(n,)`
- `bead_rois`: `(n, 4)`

### Worker path updates

Replace dict-specific logic such as:

- `len(bead_rois)`
- `list(bead_rois.values())`
- `list(bead_rois.keys())`
- `for bead_key, bead_value in bead_rois.items()`

with direct array operations based on `bead_ids` and `bead_rois`.

Cropping should use:

```python
crop_stack_to_rois(stack, [tuple(row) for row in bead_rois])
```

or ideally a small helper update so `crop_stack_to_rois()` accepts a NumPy array
directly.


## Bead Lock Integration Plan

`magscope/beadlock.py` should be updated to use cached arrays rather than a
dictionary.

### Iteration pattern

Use:

```python
for bead_id, roi in zip(ids, rois, strict=False):
    ...
```

### Membership checks

Where membership testing is needed, create a temporary set only when necessary:

```python
active_ids = set(ids.tolist())
```

This keeps the normal iteration path array-based.


## UI Controls Integration Plan

`magscope/ui/controls.py` currently uses ROI dict values for the histogram crop
preview.

Update it to use the cached ROI array instead.

Example pattern:

```python
ids, rois = self.manager.get_cached_bead_rois()
if len(rois) > 0:
    image = crop_stack_to_rois(stack, [tuple(row) for row in rois])
```


## Error Handling and Validation

Authoritative validation depends on the chosen phase.

For phase 1, replace that rule with:

- `UIManager` remains the authoritative validator for direct user edits
- UI-side validation continues to clamp to scene/image bounds during direct
  interaction
- `BeadLockManager` should continue to use the current UI-mediated move flow

If phase 2 is adopted, `BeadManager` becomes the authoritative validator.

`BeadRoiBuffer` should validate inputs carefully.

### `add_beads()`

- validate that ROI tuples have exactly four integer-like values
- validate that bead IDs are non-negative integers
- reject bead IDs greater than or equal to `10000`

### `update_beads()`

- reject bead IDs that are out of range
- reject bead IDs that are not occupied

### Move validation in `BeadManager`

- reject or clamp moves that would place an ROI outside valid image bounds
- ensure ROI width/height remain consistent with the configured ROI size
- validate requested moves against the current canonical bead state, not stale
  UI state
- apply the full move batch atomically when possible
- if only part of a batch is invalid, define and document whether the full batch
  fails or invalid moves are filtered out; keep this behavior consistent

This decision must be finalized before a phase 2 implementation starts.

### `remove_beads()`

- ignore missing IDs or raise a clear error; implementation choice should be
  consistent across the API

### `reorder_beads()`

- must preserve bead order
- must remove gaps
- must clear stale rows after compaction


## Testing Plan

Add dedicated `BeadRoiBuffer` tests, likely alongside the existing shared memory
buffer tests.

### Buffer tests

- create/attach metadata round-trip
- fixed-capacity allocation with `capacity == 10000`
- monotonic `get_next_available_bead_id()`
- removals leave gaps but do not affect monotonic allocation
- `get_beads()` returns compact sorted snapshots
- `update_beads()` rejects invalid or removed IDs
- `add_beads()` rejects bead IDs outside `0..9999`
- `clear_beads()` resets state without reallocating shared memory

### Reorder tests

- active bead order is preserved
- gaps are removed
- returned `old_id -> new_id` mapping is correct
- `max_id_plus_one` becomes `active_count`

### Integration-oriented tests

Phase 1:

- `UIManager` is the only process that writes to `BeadRoiBuffer`
- UI `Reassign IDs` updates the buffer and sends one notification per logical
  compaction
- consumers refresh their cached arrays on `UpdateBeadRoisCommand()`
- video processing consumes array snapshots instead of dicts
- UI drag moves locally and updates canonical ROI state on release
- UI shows a clear error when the monotonic next bead ID reaches `10000`

Phase 2, if implemented later:

- `BeadManager` is the only process that writes to `BeadRoiBuffer`
- bulk add/update/remove sends one request and one notification per logical
  batch
- UI write requests reconcile correctly from canonical shared state
- `BeadManager` rejects or clamps out-of-bounds ROI moves consistently


## Implementation Order

Recommended implementation sequence:

### Phase 1

1. Add `BeadRoiBuffer` to `magscope/datatypes.py`
2. Add unit tests for the new buffer
3. Add `UpdateBeadRoisCommand` in `magscope/ipc_commands.py`
4. Wire the new buffer into `magscope/scope.py`
5. Update `magscope/processes.py` to attach the buffer and cache snapshots
6. Update `UIManager` to write the buffer and broadcast one notification per
   logical batch
7. Convert `magscope/videoprocessing.py` to cached `(ids, rois)` snapshots
8. Convert `magscope/beadlock.py` to cached array snapshots where practical,
   while preserving its existing move-request flow
9. Convert any remaining dict-based ROI consumers
10. Run tests and adjust integration details

### Phase 2, only if needed later

1. Add separate BeadManager request/result command classes
2. Add `BeadManager`
3. Move write-side validation and ID allocation into `BeadManager`
4. Convert UI write paths to request commands plus reconciliation from
   canonical shared state
5. Finalize reorder/remap behavior for selection, reference, live profile, and
   pending XY-lock state
6. Run tests and adjust integration details

## Open Decisions Before Phase 2

These decisions should be made explicitly before implementing `BeadManager`:

- whether move batches are all-or-nothing or partially applied
- whether invalid moves are rejected or clamped
- what result command reports accepted, adjusted, or rejected move batches
- how reorder operations report ID remapping to UI and lock consumers
- how `selected_bead`, `reference_bead`, and live profile bead selection are
  remapped after compaction
- whether deleted-bead removes are ignored or treated as errors
- what image-bounds source is authoritative for non-UI validation


## Deferred Optimization

If profiling later shows that `get_beads()` is still too expensive, the next step
would be to maintain a dense active snapshot inside the shared buffer itself.

That is intentionally deferred.

The initial implementation should stay with:

- sparse id-indexed ROI rows
- separate occupancy flags
- copied compact snapshots from `get_beads()`
- process-local caching refreshed only on notification

Dynamic shared-memory growth is intentionally out of scope.

This is expected to give a good balance of performance, simplicity, and safety.
