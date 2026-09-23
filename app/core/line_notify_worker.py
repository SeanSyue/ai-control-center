"""Background worker for LineNotifier.send_push (Stage 1 Phase 10).

LineNotifier.send_push makes a synchronous urllib.request.urlopen call
with a 20-second timeout. Called directly on the GUI thread (as it was
before this phase), this blocks the whole UI for up to 20 seconds - a
bigger risk once N concurrent projects can each fire their own T+10
notification than it was when only one project's timer could ever be
running at a time. Mirrors app/ui/cloud_sync_dialog.py's CloudSyncWorker,
the same pattern already proven in this codebase for exactly this kind
of "one blocking call, run it off the GUI thread" case.
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal, Slot

from app.services.line_notifier import LineNotifier


class LineNotifyWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, notifier: LineNotifier, message: str) -> None:
        super().__init__()
        self.notifier = notifier
        self.message = message

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(self.notifier.send_push(self.message))
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            self.finished.emit()
