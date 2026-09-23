"""Per-project background-work state for MainWindow (Stage 1, ROADMAP.md P4).

Before Stage 1, MainWindow held this as a single set of instance
attributes (self.thread, self.pending_ask_result, self.sync_thread, ...)
that implicitly assumed only one project could ever be running or waiting
for a Decision Gate answer at a time. RunSession is the per-project
container that replaces that singleton state, one instance per project
path that has ever had a workflow run, a Google Drive sync, or a pending
question against it.

Phase 4 introduces this container and wires MainWindow's old attribute
names to it via properties scoped to the currently *focused* project, with
no change to launch/completion logic yet - that rewiring is Phase 5+.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, QTimer

if TYPE_CHECKING:
    from app.core.workflow_worker import WorkflowWorker
    from app.ui.cloud_sync_dialog import CloudSyncWorker

ASK_REMINDER_INTERVAL_MS = 5 * 60 * 1000
LINE_NOTIFY_INTERVAL_MS = 10 * 60 * 1000
MAX_LOG_LINES = 5000


class RunSession(QObject):
    """Background-work state for a single project.

    Bundles both kinds of background work a project can have in flight -
    an AI workflow run and a Google Drive sync - plus the Decision Gate
    reminder timers, which are per-project because each project's pending
    question has its own independent 5-/10-minute clock.
    """

    def __init__(self, project_path: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.project_path = project_path

        self.thread: QThread | None = None
        self.worker: WorkflowWorker | None = None
        self.started_at: float | None = None
        self.ask_started_at: float | None = None
        # How this project's last run ended, as (text, kind) for the execution
        # bar - or None if it has not run. Kept here for the same reason
        # `current_stage` is: _refresh_action_buttons() recomputes the bar from
        # session state on every focus change and every button click, so an
        # outcome written only onto the screen is painted over by the next
        # refresh. Measured live: a run that failed with CODEX_ERROR went back
        # to 尚未執行 with a grey dot, leaving no sign it had ever run.
        self.last_outcome: tuple[str, str] | None = None
        # 這一次收場屬於哪一組（app/core/outcomes.py 的五組）。顏色只說
        # 「要不要你動」，這一欄說「動哪一樣東西」——沒有它，畫面只能
        # 把「少一項設定」跟「要再跑一次」畫成同一格。
        self.last_group: str = ""
        self.pending_ask_result: dict | None = None
        self.line_notification_sent = False
        # Last stage_changed text for this run, persisted regardless of UI
        # focus so switching focus TO this project (Phase 8 lets that
        # happen while it's still running) can redisplay where it's at,
        # rather than showing stale/blank text.
        self.current_stage = ""
        # Which line-up seat the engine is dispatching to right now, by role
        # name - "" between steps and once the run ends. Same reason as
        # `current_stage`: it has to survive a focus switch, and it has to be
        # cleared on the way out. A seat left lit after the run is a claim that
        # something is still working.
        self.active_role = ""
        # `{"factor": 3.0, "window": 5}` when the last run used unusually many
        # tokens for this project, else None. Computed once when the run ends
        # rather than in _shell_state(): the bar re-renders every second for the
        # clock, and this reads the ledger off disk.
        self.usage_spike: dict[str, float] | None = None

        self.sync_thread: QThread | None = None
        self.sync_worker: CloudSyncWorker | None = None
        self.sync_after_workflow_pending = False

        # Recorded regardless of UI focus (unlike self.output, the single
        # shared QPlainTextEdit MainWindow mirrors the FOCUSED project's
        # log into) so switching focus away and back doesn't lose whatever
        # this project logged while it wasn't the one on screen. Capped to
        # bound memory for a project that runs for a very long time across
        # a long-lived AICC session; the durable record of everything is
        # still TimelineStore on disk regardless of this cap.
        self.log_lines: list[str] = []

        self.ask_reminder_timer = self._make_singleshot_timer(
            ASK_REMINDER_INTERVAL_MS
        )
        self.line_notify_timer = self._make_singleshot_timer(
            LINE_NOTIFY_INTERVAL_MS
        )

    def _make_singleshot_timer(self, interval_ms: int) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(interval_ms)
        return timer

    def append_log(self, text: str) -> None:
        self.log_lines.append(text)
        if len(self.log_lines) > MAX_LOG_LINES:
            del self.log_lines[: len(self.log_lines) - MAX_LOG_LINES]

    def is_active(self) -> bool:
        """True if this project has any live thread (workflow or sync)."""
        return self.thread is not None or self.sync_thread is not None

    def start_ask_timers(self) -> None:
        self.stop_ask_timers()
        # When the wait began. The shell's decision banner says 「已等待 …」 and
        # had no source for it - this is the moment the project actually
        # started waiting, which is a fact the code already had and was
        # throwing away. Recorded here rather than estimated at render time,
        # because a rendered guess is indistinguishable from a measurement.
        self.ask_started_at = time.monotonic()
        self.ask_reminder_timer.start()
        self.line_notify_timer.start()

    def stop_ask_timers(self) -> None:
        self.ask_started_at = None
        self.ask_reminder_timer.stop()
        self.line_notify_timer.stop()
