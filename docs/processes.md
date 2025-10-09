# Process infrastructure

The `magscope.processes` module implements the base classes and helpers that all
MagScope manager processes inherit from. It ensures that every subsystem runs in
its own process, coordinates inter-process communication (IPC), and shares
resources such as locks and buffers.

## Singleton support

Most managers should exist only once. The :class:`~magscope.processes.SingletonMeta`
and :class:`~magscope.processes.SingletonABCMeta` metaclasses enforce this rule by
raising an error if code attempts to instantiate a second copy. The base process
class uses the ABC variant so that abstract methods continue to work.

## `ManagerProcessBase`

:class:`~magscope.processes.ManagerProcessBase` wraps
:class:`multiprocessing.Process` and defines the common contract for MagScope's
manager processes:

* **Shared resources** &ndash; Upon entering :meth:`~magscope.processes.ManagerProcessBase.run`
the base class attaches to the shared :class:`~magscope.datatypes.VideoBuffer`,
``TracksBuffer``, and ``ProfilesBuffer`` objects as well as the shared locks and
coordination events.
* **Main loop** &ndash; Subclasses implement :meth:`setup` and :meth:`do_main_loop`.
  The base ``run`` method calls these and then repeatedly invokes
  :meth:`do_main_loop` followed by :meth:`receive_ipc` while ``self._running`` is
  true.
* **IPC** &ndash; :meth:`send_ipc` publishes :class:`~magscope.utils.Message`
  instances back to the central :class:`~magscope.scope.MagScope` router. Incoming
  messages are processed by :meth:`receive_ipc`, which looks up the named method
  on ``self`` and executes it. Scriptable setters such as
  :meth:`set_acquisition_mode` use the :func:`~magscope.utils.registerwithscript`
  decorator so they are available to automation scripts.
* **Shutdown** &ndash; :meth:`quit` sets the quitting flag, relays a broadcast quit
  message to other managers, drains the IPC pipe until MagScope acknowledges the
  shutdown, and finally prints a status message.

## Shared values

The small :class:`~magscope.processes.InterprocessValues` container exposes two
multiprocessing ``Value`` instances that video processing uses to report worker
state: ``video_process_flag`` stores the current
:class:`~magscope.utils.PoolVideoFlag` while ``video_process_busy_count`` tracks
active workers.
