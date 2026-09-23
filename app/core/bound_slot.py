"""Cross-thread-safe replacement for functools.partial-bound Qt slots.

Stage 1 (ROADMAP.md P4) binds a project's identity into signal connections
from per-project background QThreads (WorkflowWorker/CloudSyncWorker/
LineNotifyWorker/QThread.finished) so a handler always acts on the project
it was actually connected for, not whichever project happens to be focused
when the signal later fires.

functools.partial cannot do this safely: PySide6 (verified against 6.11.1)
cannot resolve a functools.partial-wrapped callable back to a QObject bound
method for its auto-connection-type detection. A signal connected to
`functools.partial(self.on_x, project_path)` across threads is dispatched
SYNCHRONOUSLY on the EMITTING (worker) thread instead of being queued to the
receiver's own thread - even when Qt.QueuedConnection is forced explicitly.
Every handler wired this way was therefore mutating GUI widgets from a
background thread whenever a real QThread was involved, which is how this
was found: a real two-project concurrency run raised
"QObject: Cannot create children for a parent that is in a different
thread" and crashed the process.

A genuine bound METHOD of a QObject (not its __call__, and not a
functools.partial) IS correctly recognized by PySide6's auto-connection
detection, so BoundSlot.run below gets auto-queued exactly like any other
cross-thread signal connection.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject


class BoundSlot(QObject):
    """Binds extra leading arguments to `func` for a signal connection.

    Must be parented to a QObject that (a) never moves to another thread
    and (b) outlives the connection - `parent` is required, not optional,
    since an unparented instance with no surviving Python reference would
    be garbage collected before it ever fires (the same class of bug Phase
    10 hit with LineNotifyWorker's thread/worker pair).
    """

    def __init__(
        self,
        func: Callable[..., None],
        *bound_args: object,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._func = func
        self._bound_args = bound_args

    def run(self, *signal_args: object) -> None:
        self._func(*self._bound_args, *signal_args)
