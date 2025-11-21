# MagScope lifecycle quick reference

MagScope is a singleton. Attempting to construct a second instance raises a
`TypeError`; the application lifetime is bound to the first instance you
create. Call `MagScope.start()` once to launch the GUI and manager processes. A
second `start()` while the instance is running only emits a warning because
startup is already in progress. Once the instance receives a quit or completes
`stop()`, it is permanently terminated and cannot be restarted.

Use `MagScope.stop()` to request the same orderly shutdown that would occur if a
manager broadcasts `quit`. The call blocks until all managers acknowledge the
quit request and their processes have joined.
