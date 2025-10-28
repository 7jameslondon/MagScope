# Video processing pipeline

`magscope.videoprocessing` contains the GPU-enabled video analysis pipeline. It
splits work between a manager process (:class:`~magscope.videoprocessing.VideoProcessorManager`)
and a pool of worker processes (:class:`~magscope.videoprocessing.VideoWorker`).

## Manager responsibilities

The manager inherits from :class:`~magscope.processes.ManagerProcessBase` and
initialises a queue of work items during :meth:`~magscope.videoprocessing.VideoProcessorManager.setup`.
Each queue entry captures the acquisition directory, ROI configuration, current
acquisition mode, magnification, and other flags. The main loop checks whether a
full stack of frames is available in :class:`~magscope.datatypes.VideoBuffer` and
whether the GPU workers are idle. When both are true the manager marks the
shared :class:`~magscope.utils.PoolVideoFlag` as ``RUNNING`` and enqueues a task.

## Worker responsibilities

Every :class:`~magscope.videoprocessing.VideoWorker` attaches to the shared video
and matrix buffers in its :meth:`run` method. Workers pull tasks from the queue,
update the shared ``busy_count`` counter while processing, and handle
exceptions by printing the traceback. The heavy lifting occurs inside
:meth:`~magscope.videoprocessing.VideoWorker.process`, which:

* Reads the latest stack and timestamps from :class:`~magscope.datatypes.VideoBuffer`.
* Runs MagTrack analysis (via the external :mod:`magtrack` package) to produce
  bead trajectories and optional ZLUT profiles.
* Persists results when requested by writing TIFF stacks, cropped ROI movies,
  and text files with bead positions and profiles.
* Sends completion notices back to :class:`~magscope.scripting.ScriptManager`
  when scripts are waiting for acquisition state changes.

The GPU lock shared between workers serialises access to CUDA contexts for
hardware that cannot service multiple clients at once. When all tasks complete
the shared flag is set to ``FINISHED`` so the camera manager can recycle its
buffers.
