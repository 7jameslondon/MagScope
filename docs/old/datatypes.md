# Shared data buffers

`magscope.datatypes` contains the shared-memory buffer implementations that glue
MagScope's multiprocess architecture together. They let independent processes
exchange video stacks and numeric telemetry without copying large arrays between
process boundaries.

## `VideoBuffer`

:class:`~magscope.datatypes.VideoBuffer` is a ring buffer that stores 3D image
stacks plus their timestamps. A creator process initialises the underlying
:meth:`multiprocessing.shared_memory.SharedMemory` segments with ``create=True``
and passes the buffer's locks and geometry. Subsequent processes attach with
``create=False`` and read the metadata from the "Info" block to reconstruct the
shape and dtype. Key features include:

* Stack-aware read/write helpers that guard against underflow and overflow by
  tracking write, read, and count indices in a dedicated shared block.
* Convenience methods such as :meth:`~magscope.datatypes.VideoBuffer.get_level`
  and :meth:`~magscope.datatypes.VideoBuffer.check_read_stack` for monitoring
  buffer occupancy.
* Peek operations that expose the newest frame without locking, enabling live
  previews at the expense of occasional partially written frames.

## `MatrixBuffer`

:class:`~magscope.datatypes.MatrixBuffer` generalises the same design for
arbitrary two-dimensional data (``n_rows`` × ``n_cols``). It stores timestamped
rows of float64 data alongside an optional metadata block that records the
number of columns. Manager processes use it to share bead tracking results,
profiles, and other tabular telemetry.

## Exceptions and helpers

Two custom exceptions, :class:`~magscope.datatypes.BufferUnderflow` and
:class:`~magscope.datatypes.BufferOverflow`, differentiate empty and full buffer
conditions. Utility functions such as :func:`~magscope.datatypes.uint_dtype_to_int`
and :func:`~magscope.datatypes.int_to_uint_dtype` convert between dtype objects
and bit depths when serialising metadata into the shared memory headers.
