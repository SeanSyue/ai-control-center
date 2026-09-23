from __future__ import annotations

from app.core.lineup import shown_name
from app.i18n import t

import functools
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, Slot
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.bound_slot import BoundSlot
from app.core.line_notify_worker import LineNotifyWorker
from app.core.orchestrator import Orchestrator
from app.core.outcomes import UnknownOutcome
from app.core.outcomes import describe as describe_outcome
from app.core.run_session import RunSession
from app.core.workflow_worker import WorkflowWorker
from app.services.ai_settings import AIExecutionSettings, AISettingsStore
from app.services.context_ledger import ContextLedger
from app.services.cloud_sync import (
    GoogleDriveFolderSync,
    SyncResult,
)
from app.services.handover import handover_steps, outstanding
from app.services.project_knowledge import ProjectKnowledgeStore
from app.services.line_notifier import LineNotifier, LineSendResult
from app.services.project_dashboard import ProjectDashboardService
from app.services import queue_scheduler
from app.services.project_registry import (
    sensitivity_label,
    SENSITIVITY_LEVELS,
    ProjectEntry,
    ProjectRegistry,
)
from app.services.timeline_store import TimelineStore
from app.services.workspace_portability import WorkspacePortabilityService
from app.ui import shutdown
from app.ui.ai_settings_panel import AISettingsPanel
from app.ui.cloud_sync_dialog import CloudSettingsDialog, CloudSyncWorker
from app.ui.session_panel import SessionTimelinePanel
# 三個原生面板保留著，回退只要改回建立那一行（見 `_build_pages`）。
#
# `tests/test_the_rollback_path_still_exists.py` 從**這個檔案**推出回退需要
# 哪些 surface，然後對原生面板斷言。少了那條測試，這句話會安靜地變成假的：
# 2026-08-11 查證時儀表板那一條就已經是假的了——`push_state()` 是網頁版才有的，
# 改回原生面板會在第一次同步結束時 AttributeError。
#
# `ConversationLibraryPanel` 不在這裡：它的回退目標是**對話庫頁**，而那一頁
# 2026-08-09 整頁退役刪除了，不是被取代。留著那個 import 等於宣稱一條回不去的路。
from app.ui.dashboard_web_panel import DashboardWebPanel
from app.ui.documents_web_panel import DocumentsWebPanel
from app.ui.knowledge_web_panel import KnowledgeWebPanel
from app.ui.lineup_web_panel import LineupWebPanel
from app.ui.roadmap_web_panel import RoadmapWebPanel
from app.ui.settings_web_panel import SettingsWebPanel
from app.ui.audit_web_panel import AuditWebPanel
# 登入與安裝。**不進側邊欄**（2026-08-26 老闆拍板 A），掛成一個屬性——
# `tools/page_wiring.py` 認得這個形狀（`shell` 就是這樣掛的），所以
# `credentials_v1.html` 既不在側邊欄、也不會被算成孤兒。
from app.ui.credentials_web_panel import CredentialsWebPanel
from app.ui.shell_panel import (
    PAGE_GROUPS as SHELL_PAGE_GROUPS,
    PAGES as SHELL_PAGES,
    ShellPanel,
)


def _group_of(key: str) -> dict:
    group = SHELL_PAGE_GROUPS.get(key)
    return {"group": group} if group else {}
from app.ui.native_theme import stylesheet as native_stylesheet
from app.ui.text_view_dialog import TextViewDialog, reveal
from app.ui.web_panel import THEME
from app.services import relative_time

APP_VERSION = "v0.9.4-dev"

# Which of set_status's kinds are 「結局」 - a state a run ended in, as opposed
# to one it is passing through. These, and only these, are remembered on the
# RunSession as `last_outcome`, which is what the execution bar is rebuilt from
# on every refresh and what the attention strip reads.
#
# It is a module constant because there are two writers and they must never
# disagree: set_status (the focused project) and on_completed (which also runs
# for a project the user is not looking at, where set_status is never reached).
# Two copies of this list would be two chances for a new ending to be
# remembered on one path and painted over as 「尚未執行」 on the other - which
# is the exact bug 「尚未設定驗證」 shipped with.
OUTCOME_KINDS = ("done", "error", "partial")


class MainWindow(QMainWindow):
    def __init__(self, project_path: Path) -> None:
        super().__init__()

        self.project_registry = ProjectRegistry()
        self.projects, last_project = self.project_registry.load()
        self.project_path = project_path.resolve()
        self._register_initial_project(self.project_path)

        if last_project:
            saved_path = Path(last_project)
            if any(
                entry.resolved_path == saved_path.resolve()
                for entry in self.projects
            ):
                self.project_path = saved_path.resolve()

        # Stage 1 (ROADMAP.md P4): per-project background-work state lives in
        # RunSession, keyed by resolved project path. The properties below
        # (thread/worker/started_at/pending_ask_result/etc.) redirect reads
        # and writes to the RunSession for whichever project is currently
        # focused (self.project_path), so every existing call site keeps
        # working unchanged for now - this phase only relocates storage,
        # it does not yet change launch/completion behavior (that's later
        # Stage 1 phases).
        self.run_sessions: dict[Path, RunSession] = {}
        self._answer_dialog_open = False
        # LINE-notify thread/worker pairs (Phase 10) are one-shot,
        # fire-and-forget background calls that don't belong on RunSession
        # (they're not "this project is busy" in the is_active() sense) -
        # kept alive here via a real Python reference. Without this, the
        # worker (which has no QObject parent - it can't, moveToThread
        # requires an unparented object) gets garbage collected before the
        # deferred started->run() dispatch ever fires, silently dropping
        # the notification entirely.
        self._line_notify_threads: list[tuple[QThread, LineNotifyWorker]] = []
        self.line_notifier = LineNotifier()
        self.portability = WorkspacePortabilityService()
        self.cloud_sync = GoogleDriveFolderSync()

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.update_elapsed_time)

        # The run controls used to be Qt widgets, so their text and enabled
        # flags were stored by being written onto a widget. They are drawn by
        # shell.html now, so the state lives here and _shell_state() renders it.
        #
        # `_status_kind` is deliberately the shell's own vocabulary
        # (idle/running/gated/error), not the old QSS object names. Those had
        # drifted from meaning: 「執行失敗」 was raised with `statusAsk`, which
        # translated to the gold 「等你決策」 dot - a failed run claiming to be
        # waiting for an answer.
        self._status_text = t("run.status.not_started")
        self._status_kind = "idle"
        self._stage_text = t("run.status.waiting_to_start")
        self._elapsed_text = ""
        self._cloud_status_text = t("cloud.status.not_configured")
        # Projects whose decision banner the user has pushed aside with 「稍後」.
        # Purely a banner-level defer: pending_ask_result is untouched, the
        # Dashboard still lists the project as waiting, and the banner returns
        # on the next state change.
        self._gate_deferred: set[Path] = set()

        # The version used to be in the window's subtitle line, which the shell
        # replaced. The shell has no slot for it, so it moved to the title bar
        # rather than being dropped - "which build am I running" is the first
        # question of every bug report.
        self.setWindowTitle(f"AI Control Center {APP_VERSION}")
        self.resize(*self.FACTORY_SIZE)
        # 900, not 820: the central widget's own minimum is 876px (the tab bar
        # alone needs 828) and the QScrollArea around it has horizontal
        # scrolling off, so at 820 the layout was squeezed below what it asked
        # for with no way to scroll to the rest. Measured, not guessed.
        self.setMinimumSize(900, 600)

        self.build_ui()
        # Collapsed by default: the tab area is now six whole-window designs,
        # and the first thing a new user should see is one of them, not 525px
        # of chrome above a third of a page.
        self.apply_styles()
        # The web panels' day/night button writes to the shared bus. Without
        # this the frame around them stayed light while they went dark.
        THEME.changed.connect(self._on_theme_changed)
        self._refresh_project_list()
        self._update_cloud_status()
        QTimer.singleShot(750, self._auto_sync_on_start)

    def _register_initial_project(self, project_path: Path) -> None:
        resolved = project_path.resolve()

        if not resolved.is_dir():
            return

        if any(
            entry.resolved_path == resolved
            for entry in self.projects
        ):
            return

        self.projects.append(
            ProjectEntry(
                name=resolved.name,
                path=str(resolved),
            )
        )
        self.project_registry.save(
            self.projects,
            str(resolved),
        )

    def _session_for(self, path: Path) -> RunSession:
        """Get-or-create the RunSession for `path`.

        A session is created the first time anything reads or writes
        per-project state for that path - including a plain read via the
        compatibility properties below - so merely focusing a project can
        create an (idle) session entry. That is intentional and cheap: it
        keeps every property uniformly "always returns a real RunSession"
        with no None-vs-session special-casing at call sites.
        """
        session = self.run_sessions.get(path)
        if session is None:
            session = RunSession(path, parent=self)
            # functools.partial is safe here specifically because these are
            # QTimer.timeout connections: the timer is never moved off the
            # GUI thread, so sender and receiver are always the same thread
            # regardless of connection type - unlike the cross-thread
            # worker/QThread connections in _launch_worker/_start_sync/
            # _run_line_notify_worker, which must use BoundSlot instead
            # (see app/core/bound_slot.py).
            session.ask_reminder_timer.timeout.connect(
                functools.partial(self.raise_pending_question, path)
            )
            session.line_notify_timer.timeout.connect(
                functools.partial(self.send_pending_line_notification, path)
            )
            self.run_sessions[path] = session
        return session

    @property
    def _focused_session(self) -> RunSession:
        return self._session_for(self.project_path)

    @property
    def thread(self) -> QThread | None:
        return self._focused_session.thread

    @thread.setter
    def thread(self, value: QThread | None) -> None:
        self._focused_session.thread = value

    @property
    def worker(self) -> WorkflowWorker | None:
        return self._focused_session.worker

    @worker.setter
    def worker(self, value: WorkflowWorker | None) -> None:
        self._focused_session.worker = value

    @property
    def started_at(self) -> float | None:
        return self._focused_session.started_at

    @started_at.setter
    def started_at(self, value: float | None) -> None:
        self._focused_session.started_at = value

    @property
    def pending_ask_result(self) -> dict | None:
        return self._focused_session.pending_ask_result

    @pending_ask_result.setter
    def pending_ask_result(self, value: dict | None) -> None:
        self._focused_session.pending_ask_result = value

    @property
    def line_notification_sent(self) -> bool:
        return self._focused_session.line_notification_sent

    @line_notification_sent.setter
    def line_notification_sent(self, value: bool) -> None:
        self._focused_session.line_notification_sent = value

    @property
    def sync_thread(self) -> QThread | None:
        return self._focused_session.sync_thread

    @sync_thread.setter
    def sync_thread(self, value: QThread | None) -> None:
        self._focused_session.sync_thread = value

    @property
    def sync_worker(self) -> CloudSyncWorker | None:
        return self._focused_session.sync_worker

    @sync_worker.setter
    def sync_worker(self, value: CloudSyncWorker | None) -> None:
        self._focused_session.sync_worker = value

    @property
    def sync_after_workflow_pending(self) -> bool:
        return self._focused_session.sync_after_workflow_pending

    @sync_after_workflow_pending.setter
    def sync_after_workflow_pending(self, value: bool) -> None:
        self._focused_session.sync_after_workflow_pending = value

    @property
    def ask_reminder_timer(self) -> QTimer:
        return self._focused_session.ask_reminder_timer

    @property
    def line_notify_timer(self) -> QTimer:
        return self._focused_session.line_notify_timer

    def _save_project_registry(self) -> None:
        self.project_registry.save(
            self.projects,
            str(self.project_path),
        )

    def _refresh_project_list(self) -> None:
        """Repaint everything that depends on which projects exist.

        This was `_refresh_project_selector`, which rebuilt a QComboBox. The
        project list is `self.projects` and always was - the combo was a second
        copy of it that had to be kept in step. The shell renders the list
        straight from the real one.
        """
        self._update_project_display()

    def _update_project_display(self) -> None:
        self._has_result_file = (
            self.project_path / ".aicc" / "workflow-result.json"
        ).is_file()
        self._refresh_action_buttons()

    # Which controls the execution bar may offer. Derived on read rather than
    # stored, because stored copies of live state are how the stale-stage bug
    # (94fd30a) happened: something changed the session without going through
    # the one method that refreshed the copy, and the screen kept showing the
    # old answer with nothing to indicate it was old. There is nothing to keep
    # in step here - the session is the answer.
    @property
    def _workflow_busy(self) -> bool:
        return self._focused_session.thread is not None

    @property
    def _any_syncing(self) -> bool:
        return any(
            other.sync_thread is not None
            for other in self.run_sessions.values()
        )

    def _actions_blocked(self) -> dict[str, str]:
        """Which shell actions would do nothing right now, and why.

        Keyed by **the action name the page already sends**, so the page can
        disable a control by lookup instead of keeping a second mapping.
        An action that is allowed has no entry - absent means yes.

        2026-08-11. There used to be six `_can_*` properties here
        (`_can_start`, `_can_remove_project`, `_can_sync_now`,
        `_can_open_cloud_settings`, `_can_link_cloud`,
        `_is_waiting_for_answer`) and **nothing read any of them** - found by
        an AST sweep for definitions with zero references in `app/`.

        They were clearly written to disable controls. What actually shipped is
        that each handler guards itself with the same condition and then
        `return`s silently: the menu item is enabled, the user clicks it, and
        the screen does not change. This project has already named that failure
        three times - 「一顆按了什麼都不會發生的按鈕，比一顆明著停用的更糟」 -
        because the user cannot tell it from a crash.

        The worst of the five is 開始執行 **while the focused project is
        syncing**: the bar's busy state only reads `thread`, not `sync_thread`,
        so the button says 開始執行 rather than 停止, and clicking it hits
        `start_workflow`'s first guard and returns.

        **The reasons are derived from the handlers' own conditions, not from
        those properties.** `_can_link_cloud` had already drifted stricter than
        `link_cloud_project` (it also required `not _workflow_busy`), and
        surfacing the stricter copy would disable a control the app would have
        accepted - the same mistake the 陣容 page made with the seat cap.
        The handler is the authority because the handler is what runs; these
        strings only have to agree with it, and the tests pin that they do.
        """
        session = self._focused_session
        blocked: dict[str, str] = {}

        # start_workflow: returns early on either thread. A pending question is
        # NOT blocked - it opens the question instead, which is a real action.
        if session.thread is not None:
            blocked["run"] = t("project.busy.running")
        elif session.sync_thread is not None:
            blocked["run"] = t("project.busy.syncing")
        elif not (self.project_path / ".aicc" / "task.md").is_file():
            # Not a handler guard - `start_workflow` would go on to
            # `save_task()`. Kept because it is the one that costs money to
            # discover, and it says what to do about it.
            blocked["run"] = t("start.no_task_yet")

        # remove_current_project / _start_sync: same guard, both threads.
        if session.is_active():
            busy = t("common.run") if session.thread is not None else t("common.sync")
            blocked["remove_project"] = f"這個專案正在{busy}"
            blocked["sync_now"] = f"這個專案正在{busy}"

        # configure_cloud_sync / link_cloud_project: blocked while ANY project
        # is syncing, because the settings are global - changing root_path
        # mid-sync could make an unrelated in-flight sync write somewhere else.
        if self._any_syncing:
            why = t("cloud.warn.sync_in_flight")
            blocked["cloud_settings"] = why
            blocked["link_cloud_project"] = why

        return blocked

    def _refresh_action_buttons(self) -> None:
        """Resync the stage/status/elapsed state to the FOCUSED project's own
        RunSession, and tell the panels whether it is busy.

        Phase 8 lets focus move onto an already-busy background project
        (switch_to_project no longer refuses that), so this can no longer be
        something only set once, at launch/finish time, for whichever
        project happened to be focused back then - it has to be
        recomputed from live session state every time focus changes too.
        Called from _update_project_display (every focus change) and
        directly from the workflow/sync launch/finish handlers.
        """
        session = self._focused_session
        workflow_busy = self._workflow_busy

        self.session_panel.set_busy(workflow_busy)
        self.ai_settings_panel.set_busy(workflow_busy)

        # 陣容頁要知道**哪一格在跑**（呼吸特效）與**哪一格在等他回話**
        # （那顆亮起來的回覆鈕）。老闆 2026-08-27 兩件都提了，而在這之前
        # 那個面板**完全不知道**——執行狀態長在這一側，從來沒有人告訴它。
        #
        # 在這裡告訴它，因為這一支就是「執行狀態變了就重算一次」的那個點；
        # 找別的地方接，會漏掉焦點切到另一個專案的那一種。
        pending = session.pending_ask_result or {}
        self.lineup_panel.set_run_state(
            running=session.active_role if workflow_busy else "",
            # **答不出是誰就留空，不准猜。**空的時候畫面寫「回覆」，
            # 不是編一個名字——編出來的名字跟對的名字長得一模一樣。
            waiting_role=str(pending.get("review_asked_by", "")),
            question=str(pending.get("review_question", "")),
        )

        self._stage_text = session.current_stage or t("run.status.waiting_to_start")
        if workflow_busy:
            self.set_status(t("run.status.running"), "running")
            self.timer.start()
            self.update_elapsed_time()
        else:
            self._elapsed_text = ""
            if session.pending_ask_result is not None:
                self.set_status(t("outcome.group.ask"), "gated")
                # The banner says 「已等待 X 分 Y 秒」, computed from
                # ask_started_at at render time - so it only advances if
                # something repaints. Stopping the timer here left it frozen at
                # whatever it said when the question arrived, which is a
                # measured-looking number that stops being true one second
                # later. Worse than no number: it reads as "just now" for as
                # long as the wait lasts.
                self.timer.start()
            else:
                self.timer.stop()
                # The last run's outcome, if there was one. Recomputing
                # 尚未執行 unconditionally is what erased it.
                outcome = session.last_outcome
                if outcome is not None:
                    self.set_status(outcome[0], outcome[1])
                else:
                    self.set_status(t("run.status.not_started"), "idle")
        self._push_shell()

    @Slot(str)
    def _on_sensitivity_changed(self, level: str) -> None:
        if not level or level not in SENSITIVITY_LEVELS:
            return
        ProjectRegistry.set_sensitivity(self.project_path, str(level))
        self.append_log(
            f"專案敏感度已設定為：{sensitivity_label(str(level))}"
        )
        self._push_shell()

    @Slot(str)
    def _select_project_from_dashboard(self, raw_path: str) -> None:
        self.switch_to_project(raw_path)

    def _focus_project(self, path: Path) -> None:
        """Apply `path` as the focused project and refresh every dependent widget.

        This only covers the refresh sequence that's identical across
        switch_project/import_workspace/create_project_from_conversation_pair/
        link_cloud_project. Callers remain responsible for anything specific
        to their own flow (registering `path` in self.projects first,
        resetting pending_ask_result/timers before calling this, clearing
        output text, etc.).
        """
        self.project_path = path
        self._save_project_registry()
        self._refresh_project_list()
        self.dashboard_panel.refresh(self.projects)
        self.session_panel.set_project(self.project_path)
        self.ai_settings_panel.set_project(self.project_path)
        self.knowledge_panel.set_project(self.project_path)
        self.roadmap_panel.set_project(self.project_path)
        for panel in (
            self.lineup_panel,
            self.documents_panel,
            self.settings_web_panel,
            # The decision record is per project - showing another project's
            # audit trail under this project's name would be the worst possible
            # thing for this particular page to get wrong.
            self.audit_panel,
        ):
            panel.set_project(self.project_path)
        # Repaint the live-output pane from THIS project's own buffered
        # log (RunSession.log_lines), not whatever the previously-focused
        # project happened to leave in the shared widget - without this,
        # switching away from a running project and back showed nothing
        # it logged while unfocused (append_log calls were simply skipped
        # entirely for a non-focused project, not just hidden).
        self.output.setPlainText("\n".join(self._focused_session.log_lines))

    @Slot(str)
    def switch_to_project(self, raw_path: str) -> None:
        """Focus the project at `raw_path`. The id the shell sends is the path.

        This took a combo-box index until the run controls moved into the
        shell. An index only means anything next to the list it indexes, which
        is why every caller had to go through the combo to use it; the path
        identifies the project on its own.

        Phase 8: focus-switching is decoupled from any background work -
        switching to a busy project is safe (its own RunSession keeps
        running; _update_project_display/_refresh_action_buttons resync
        every widget from that project's own state), and switching AWAY
        from a busy project no longer needs to be blocked either, since
        nothing in this method touches the project being left behind.
        """
        if not raw_path:
            return

        next_path = Path(str(raw_path)).resolve()

        if next_path == self.project_path:
            return

        if not next_path.is_dir():
            QMessageBox.warning(
                self,
                t("project.path_missing.title"),
                f"找不到專案資料夾：\n{next_path}",
            )
            self._refresh_project_list()
            return

        # Do NOT clear pending_ask_result/timers here: since Phase 4/5,
        # those are per-project RunSession state, and self.project_path
        # still points at the OLD project at this point - clearing them
        # would wipe the project we're switching AWAY from's Decision Gate
        # state and orphan its workflow lock (found by Phase 5's adversarial
        # review). The newly-focused project's own state is untouched and
        # correctly isolated via its own RunSession.
        # elapsed_label/stage/status/button states are resynced from the
        # newly-focused project's own RunSession by _focus_project ->
        # _update_project_display -> _refresh_action_buttons below - no
        # need to force a "00:00"/idle reset here, since that resync
        # correctly shows a running project's real elapsed time instead.
        # _focus_project also repaints self.output from next_path's own
        # log buffer - no need to clear it here first.
        self._focus_project(next_path)
        QTimer.singleShot(100, self._auto_sync_on_switch)

    @Slot()
    def add_project(self) -> None:
        # Phase 8: doesn't touch any existing project's run state - always
        # available, matching import_workspace/create_project_from_
        # conversation_pair (same category of operation).
        selected = QFileDialog.getExistingDirectory(
            self,
            t("project.pick_folder"),
            str(self.project_path.parent),
        )

        if not selected:
            return

        path = Path(selected).resolve()
        initialized_project = False
        valid, message = self.project_registry.validate_project(path)

        if not valid:
            answer = QMessageBox.question(
                self,
                t("project.initialise.title"),
                f"{message}\n\n是否建立 .aicc\\task.md？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )

            if answer != QMessageBox.Yes:
                return

            try:
                path = self.project_registry.initialize_project(path)
                initialized_project = True
            except (OSError, ValueError) as exc:
                QMessageBox.critical(
                    self,
                    t("project.initialise.failed"),
                    str(exc),
                )
                return

        existing_index = next(
            (
                index
                for index, entry in enumerate(self.projects)
                if entry.resolved_path == path
            ),
            -1,
        )

        if existing_index < 0:
            self.projects.append(
                ProjectEntry(
                    name=path.name,
                    path=str(path),
                )
            )

        # Phase 12 adversarial review found a real bug here: this used to
        # do self.project_path = path; self.pending_ask_result = None;
        # self.stop_ask_timers() inline - since project_path is reassigned
        # FIRST, those two calls (focus-scoped properties) would wipe
        # whatever project `path` resolves to's OWN pending Decision Gate
        # state if it already had one (e.g. re-selecting an
        # already-tracked project, existing_index >= 0, that has a real
        # unanswered question) - and never released its lock either.
        # _focus_project deliberately does not touch pending_ask_result/
        # timers at all, matching import_workspace/
        # create_project_from_conversation_pair/link_cloud_project. It
        # does repaint self.output from `path`'s own log buffer - for a
        # freshly-initialized project that buffer is empty, so the
        # guidance text below still lands on a clean pane; for a
        # re-selected already-tracked project, its real buffered log is
        # what shows instead of being wiped.
        self._focus_project(path)
        if initialized_project:
            self._show_page("knowledge")
            self._append_project_log(
                path,
                t("project.imported.next_steps")
            )
            self.page_scroll.verticalScrollBar().setValue(
                self.page_stack.y()
            )

    @Slot()
    def remove_current_project(self) -> None:
        # This targets self.project_path (the focused project) for
        # removal, so this guard - reading self.thread/self.sync_thread,
        # which resolve to the focused project's own session - already
        # means exactly "don't remove a project that's still busy",
        # regardless of any OTHER project's state. No change needed here
        # for Phase 8's per-project philosophy; it was already correctly
        # scoped.
        if self.thread is not None or self.sync_thread is not None:
            return

        if len(self.projects) <= 1:
            QMessageBox.information(
                self,
                t("project.remove.refused_title"),
                t("project.remove.need_one"),
            )
            return

        answer = QMessageBox.question(
            self,
            t("project.remove.title"),
            f"從清單移除「{self.project_path.name}」？\n"
            "不會刪除硬碟中的專案檔案。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        self.projects = [
            entry
            for entry in self.projects
            if entry.resolved_path != self.project_path
        ]
        # Phase 12 adversarial review found a real bug here: focus falls
        # back to an arbitrary remaining project (self.projects[0]),
        # unrelated to the one just removed. The old inline
        # self.pending_ask_result = None / self.stop_ask_timers() (focus-
        # scoped properties) ran AFTER that reassignment, so they would
        # wipe THAT fallback project's own real pending Decision Gate
        # state if it happened to have one - and never released its lock
        # either. _focus_project deliberately does not touch
        # pending_ask_result/timers at all - it does repaint self.output
        # from the fallback project's own log buffer instead of wiping it.
        self._focus_project(self.projects[0].resolved_path)

    @Slot()
    def open_project_folder(self) -> None:
        if not self.project_path.is_dir():
            QMessageBox.warning(
                self,
                t("path.missing.title"),
                str(self.project_path),
            )
            return

        QGuiApplication.clipboard().setText(
            str(self.project_path)
        )
        QMessageBox.information(
            self,
            t("project.path.label"),
            "專案路徑已複製到剪貼簿：\n"
            f"{self.project_path}",
        )

    @Slot()
    def export_workspace(self) -> None:
        preview = self.portability.preview_export(self.project_path)
        if not preview.success:
            QMessageBox.warning(self, t("export.preview_failed"), preview.summary)
            return

        preview_text = preview.summary
        if preview.security_report is not None:
            preview_text += "\n\n" + preview.security_report.format_markdown()
        answer = QMessageBox.question(
            self,
            t("export.preview.title"),
            preview_text[:6000] + t("export.confirm_continue"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        selected, _ = QFileDialog.getSaveFileName(
            self,
            t("export.title"),
            str(
                self.project_path.parent
                / f"{self.project_path.name}.aicc-workspace.zip"
            ),
            "AI Workspace (*.zip)",
        )
        if not selected:
            return

        result = self.portability.export_project(
            self.project_path,
            Path(selected),
        )
        if not result.success:
            QMessageBox.warning(self, t("export.failed"), result.summary)
            return

        QMessageBox.information(
            self,
            t("export.done"),
            result.summary + "\n\n" + result.archive_path,
        )

    @Slot()
    def import_workspace(self) -> None:
        # Phase 8: doesn't touch any existing project's run state - always
        # available, same category as add_project.
        archive, _ = QFileDialog.getOpenFileName(
            self,
            t("import.pick_package"),
            str(self.project_path.parent),
            "AI Workspace (*.zip)",
        )
        if not archive:
            return

        destination = QFileDialog.getExistingDirectory(
            self,
            t("import.pick_destination"),
            str(self.project_path.parent),
        )
        if not destination:
            return

        result = self.portability.import_project(
            Path(archive),
            Path(destination),
        )
        if not result.success:
            QMessageBox.warning(self, t("import.failed"), result.summary)
            return

        imported_path = Path(result.project_path).resolve()
        if not any(
            entry.resolved_path == imported_path for entry in self.projects
        ):
            self.projects.append(
                ProjectEntry(
                    name=imported_path.name,
                    path=str(imported_path),
                )
            )

        # Do NOT clear the OLD (still-focused) project's pending_ask_result
        # /timers here - self.project_path hasn't changed yet, so those
        # focus-scoped properties would target the project we're switching
        # AWAY from, wiping its Decision Gate state and orphaning its lock
        # (found by Phase 5's adversarial review). The imported project's
        # own state is separate and untouched.
        self._focus_project(imported_path)
        self._append_project_log(imported_path, result.summary)

        # 「匯入完成」單講檔案數量，會被讀成「都好了」。而搬過來之後還缺的
        # 那幾件事沒有一件會自己出聲（ROADMAP P5）。這裡講一次是因為這是使用者
        # 唯一必然在看的時刻；儀表板那張卡片會一直留著，直到真的做完。
        pending = outstanding(handover_steps(imported_path))
        lines = [result.summary]
        if pending:
            lines.append("")
            lines.append(t("import.still_missing"))
            lines.extend(f"　· {step.title}\n　　{step.detail}" for step in pending)
        for line in lines[1:]:
            self._append_project_log(imported_path, line)
        QMessageBox.information(self, t("import.done"), "\n".join(lines))
        self._offer_reindex_after_import(imported_path, pending)

    def _offer_reindex_after_import(self, project_path: Path, pending) -> None:
        """索引沒跟過來的話，就在這裡重建，不要叫人自己去別頁找按鈕。

        一份說得出問題、卻要你自己去別的地方解決的清單，只做完了一半。而這件事
        剛好是四項裡唯一 AICC 自己做得完的——git 要遠端、LINE 憑證只有人能輸入、
        新對話則是講一件已經發生的事。

        只在真的缺的時候問。索引正常跟過來時多跳一個「要不要重建」，會把這個
        問句本身變成匯入流程的一部分，然後下一次真的缺的時候沒有人會讀它。
        """
        if not any(step.key == "documents" for step in pending):
            return
        answer = QMessageBox.question(
            self,
            t("index.missing.title"),
            t("index.rebuild_prompt"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            result = ProjectKnowledgeStore(project_path).rebuild_project_index()
        except Exception as error:  # pragma: no cover - 防護，不是預期路徑
            self._append_project_log(project_path, f"重建索引失敗：{error}")
            QMessageBox.warning(self, t("index.rebuild_failed"), str(error))
            return
        self._append_project_log(project_path, result.summary)
        self.documents_panel.push_state()
        self.dashboard_panel.push_state()

    @Slot(str)
    def create_project_from_conversation_pair(self, suggested_name: str) -> None:
        # Phase 8: doesn't touch any existing project's run state - always
        # available, same category as add_project.
        parent = QFileDialog.getExistingDirectory(
            self,
            t("project.new.pick_parent"),
            str(self.project_path.parent),
        )
        if not parent:
            return

        raw_name, accepted = QInputDialog.getText(
            self,
            t("project.new.title"),
            t("project.new.name_label"),
            text=suggested_name or "AICC Paired Project",
        )
        if not accepted:
            return

        safe_name = "".join(
            character
            for character in raw_name.strip()
            if character not in '<>:"/\\|?*'
        ).strip()
        if not safe_name:
            QMessageBox.warning(self, t("project.new.failed"), t("project.new.name_required"))
            return

        target = (Path(parent) / safe_name).resolve()
        try:
            target.mkdir(parents=True, exist_ok=False)
            ProjectRegistry.initialize_project(target)
        except FileExistsError:
            QMessageBox.warning(self, t("project.new.failed"), f"資料夾已存在：\n{target}")
            return
        except Exception as exc:
            QMessageBox.warning(self, t("project.new.failed"), str(exc))
            return

        self.projects.append(ProjectEntry(name=target.name, path=str(target)))
        # See import_workspace's comment: do not clear the OLD project's
        # pending_ask_result/timers here - project_path hasn't changed yet.
        self._focus_project(target)
        # "conversations" until P10 renamed the key to "lineup" and this call
        # was missed. _show_page returns silently on an unknown key, so a new
        # project simply stayed on whichever page you happened to be on.
        self._show_page("lineup")
        QMessageBox.information(self, t("project.new.done"), f"已建立專案：\n{target}")

    def _update_cloud_status(self, text: str = "") -> None:
        settings = self.cloud_sync.load_settings()

        if text:
            self._set_cloud_status(text)
            return

        if not settings.enabled:
            self._set_cloud_status(t("cloud.status.disabled"))
            return

        if not settings.root_path:
            self._set_cloud_status(t("cloud.status.no_folder"))
            return

        root = Path(settings.root_path).expanduser()
        if not root.is_dir():
            self._set_cloud_status(t("cloud.status.folder_missing"))
            return

        self._set_cloud_status(
            f"Google Drive：已設定｜{root}"
        )

    @Slot()
    def configure_cloud_sync(self) -> None:
        # Google Drive sync settings are global (one shared root folder/
        # catalog for every project), not per-project - block editing them
        # while ANY project's sync is in flight, not just the focused
        # one's, since changing root_path mid-sync could make an unrelated
        # in-flight sync write to a stale/changed location.
        if any(
            session.sync_thread is not None
            for session in self.run_sessions.values()
        ):
            return

        current = self.cloud_sync.load_settings()
        dialog = CloudSettingsDialog(current, self)
        if dialog.exec() != QDialog.Accepted:
            return

        settings = dialog.settings(current.device_id)
        if settings.enabled:
            if not settings.root_path:
                QMessageBox.warning(
                    self,
                    t("cloud.settings.title"),
                    t("cloud.settings.folder_required"),
                )
                return

            root = Path(settings.root_path).expanduser().resolve()
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                QMessageBox.critical(
                    self,
                    t("cloud.settings.failed"),
                    str(exc),
                )
                return
            settings.root_path = str(root)

        self.cloud_sync.save_settings(settings)
        self._update_cloud_status()

        if settings.enabled:
            self.sync_current_project()

    @Slot()
    def sync_current_project(self) -> None:
        """UI-facing entry point: sync whichever project is currently
        focused. Direct callers that need to target a specific (possibly
        non-focused) project - e.g. on_thread_finished syncing the project
        whose workflow just finished - should call _start_sync(project_path)
        directly instead.
        """
        self._start_sync(self.project_path)

    def _start_sync(self, project_path: Path) -> None:
        session = self._session_for(project_path)
        if session.thread is not None or session.sync_thread is not None:
            return

        settings = self.cloud_sync.load_settings()
        if not settings.enabled:
            if project_path == self.project_path:
                QMessageBox.information(
                    self,
                    t("cloud.sync.title"),
                    t("cloud.sync.enable_first"),
                )
            return

        is_focused = project_path == self.project_path
        if is_focused:
            self._update_cloud_status(t("cloud.status.syncing"))

        try:
            session.sync_thread = QThread(self)
            shutdown.adopt(session.sync_thread)  # **先登記再 start()。**沒有登記的話，關閉那條路上沒有人等它，
            # 而一條還在跑的 QThread 活過 `~QApplication` 會讓 Qt 
            # 直接中止行程（0xC0000409），視窗已經消失、離開碼已經印出來
            # ——使用者記得的是「這個產品關掉會當」。
            session.sync_worker = CloudSyncWorker(
                self.cloud_sync,
                project_path,
            )
            session.sync_worker.moveToThread(session.sync_thread)
            session.sync_thread.started.connect(session.sync_worker.run)
            # See app/core/bound_slot.py - functools.partial is not safe
            # for a cross-thread signal connection under PySide6.
            session.sync_worker.completed.connect(
                BoundSlot(self.on_sync_completed, project_path, parent=session).run
            )
            session.sync_worker.failed.connect(
                BoundSlot(self.on_sync_failed, project_path, parent=session).run
            )
            session.sync_worker.finished.connect(session.sync_thread.quit)
            session.sync_worker.finished.connect(
                session.sync_worker.deleteLater
            )
            session.sync_thread.finished.connect(
                BoundSlot(
                    self.on_sync_thread_finished, project_path, parent=session
                ).run
            )
            session.sync_thread.finished.connect(
                session.sync_thread.deleteLater
            )
            session.sync_thread.start()
        except Exception:
            # Same reasoning as _launch_worker's try/except (Phase 5
            # bugfix round): without this, a setup failure here leaves
            # session.sync_thread permanently non-None - which, since
            # Phase 7's configure_cloud_sync/link_cloud_project guards
            # check any(session.sync_thread is not None for ...), would
            # silently disable Google Drive settings/linking app-wide for
            # every project until restart (Phase 12 adversarial review
            # finding).
            session.sync_thread = None
            session.sync_worker = None
            if is_focused:
                self._update_cloud_status()
            raise

        # Unconditional, not is_focused-gated: cloud_settings_button/
        # link_cloud_button depend on whether ANY session is syncing, so
        # starting a sync for a non-focused project can still change the
        # focused project's own button states.
        self._refresh_action_buttons()

    def on_sync_completed(self, project_path: Path, result: SyncResult) -> None:
        self._append_project_log(project_path, "")
        self._append_project_log(project_path, result.summary)

        if result.cloud_path:
            self._append_project_log(project_path, f"雲端位置：{result.cloud_path}")

        if result.conflicts:
            self._append_project_log(project_path, t("cloud.conflict.files_label"))
            for item in result.conflicts:
                self._append_project_log(project_path, f"- {item}")

        if result.blocked:
            self._append_project_log(project_path, t("cloud.blocked.files_label"))
            for item in result.blocked:
                self._append_project_log(project_path, f"- {item}")

        if project_path != self.project_path:
            # Sync produces no state that other code depends on later
            # (unlike Decision Gate's pending_ask_result) - a background
            # project's sync outcome is safe to skip entirely when not
            # focused (now logged into its own buffer above), matching
            # on_failed's precedent from Phase 5.
            return

        if result.conflicts:
            QMessageBox.warning(
                self,
                t("cloud.conflict.title"),
                t("cloud.conflict.body"),
            )

        if result.blocked:
            QMessageBox.warning(
                self,
                t("cloud.blocked.title"),
                t("cloud.blocked.body"),
            )

        if result.success:
            self._update_cloud_status(
                "Google Drive：同步完成｜"
                f"上傳 {len(result.uploaded)}、"
                f"下載 {len(result.downloaded)}、"
                f"衝突 {len(result.conflicts)}、"
                f"已擋下 {len(result.blocked)}"
            )
            self._update_project_display()
            self.session_panel.refresh_from_disk()
        else:
            self._update_cloud_status(t("cloud.status.sync_failed"))
            QMessageBox.warning(
                self,
                t("cloud.sync.failed_title"),
                "\n".join(result.errors) or t("error.unknown"),
            )

    def on_sync_failed(self, project_path: Path, error: str) -> None:
        self._append_project_log(project_path, "")
        self._append_project_log(project_path, error)
        if project_path != self.project_path:
            return
        self._update_cloud_status(t("cloud.status.sync_failed"))

    def on_sync_thread_finished(self, project_path: Path) -> None:
        session = self._session_for(project_path)
        session.sync_thread = None
        session.sync_worker = None

        # Unconditional, same reasoning as _start_sync: this sync ending
        # may be what makes "any session syncing" turn False, which can
        # re-enable the FOCUSED project's cloud_settings_button/
        # link_cloud_button even if project_path itself isn't focused.
        self._refresh_action_buttons()

    def _auto_sync_on_start(self) -> None:
        settings = self.cloud_sync.load_settings()
        if settings.enabled and settings.auto_sync_on_start:
            self._start_sync(self.project_path)

    def _auto_sync_on_switch(self) -> None:
        settings = self.cloud_sync.load_settings()
        if settings.enabled and settings.auto_sync_on_switch:
            self._start_sync(self.project_path)

    @Slot()
    def link_cloud_project(self) -> None:
        # Phase 8: doesn't touch any existing project's execution state,
        # same category as add_project - the only real constraint is the
        # shared cloud catalog read/write, blocked below while ANY
        # project's sync is in flight (same reasoning as
        # configure_cloud_sync).
        if any(
            session.sync_thread is not None
            for session in self.run_sessions.values()
        ):
            return

        settings = self.cloud_sync.load_settings()
        if not settings.enabled:
            QMessageBox.information(
                self,
                t("cloud.link.title"),
                t("cloud.settings_first"),
            )
            return

        cloud_projects = self.cloud_sync.list_cloud_projects()
        if not cloud_projects:
            QMessageBox.information(
                self,
                t("cloud.link.title"),
                t("cloud.link.none_available"),
            )
            return

        labels = [
            f"{item.name}｜{item.project_id}"
            for item in cloud_projects
        ]
        selected, accepted = QInputDialog.getItem(
            self,
            t("cloud.link.title"),
            t("cloud.link.pick_label"),
            labels,
            0,
            False,
        )
        if not accepted or not selected:
            return

        index = labels.index(selected)
        cloud_project = cloud_projects[index]
        local_folder = QFileDialog.getExistingDirectory(
            self,
            t("cloud.link.pick_local"),
            str(self.project_path.parent),
        )
        if not local_folder:
            return

        answer = QMessageBox.question(
            self,
            t("cloud.link.confirm"),
            "雲端同步只包含 .aicc 工作資料，不包含原始碼。\n"
            "請確認所選資料夾已經是正確的本機 Git clone。\n\n"
            f"雲端：{cloud_project.name}\n"
            f"本機：{local_folder}\n\n"
            "確定要連結並同步嗎？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        result = self.cloud_sync.link_cloud_project(
            cloud_project.project_id,
            Path(local_folder),
        )
        if not result.success:
            QMessageBox.warning(
                self,
                t("cloud.link.failed"),
                "\n".join(result.errors),
            )
            return

        path = Path(local_folder).resolve()
        if not any(entry.resolved_path == path for entry in self.projects):
            self.projects.append(
                ProjectEntry(name=path.name, path=str(path))
            )

        # See import_workspace's comment: do not clear the OLD project's
        # pending_ask_result/timers here - project_path hasn't changed yet.
        self._focus_project(path)
        self._append_project_log(path, result.summary)
        self._update_cloud_status()

    def build_ui(self) -> None:
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )
        self.page_scroll.setFrameShape(QFrame.NoFrame)
        self.setCentralWidget(self.page_scroll)

        central = QWidget()
        # The window's own background never reaches the page: this widget fills
        # the scroll area, and its 24px layout margins are the only place you
        # see "behind" the cards - which was Qt's default #EFEFEF, subtly wrong
        # in day mode and a pale border framing a dark window at night. Named
        # so the stylesheet can reach it by id rather than by a QWidget type
        # selector, which would also match QCheckBox (see native_theme.py).
        # WA_StyledBackground because a bare QWidget otherwise ignores a QSS
        # background entirely - measured: without it the rule changes nothing.
        central.setObjectName("pageContent")
        central.setAttribute(Qt.WA_StyledBackground, True)
        central.setMinimumWidth(760)
        self.page_scroll.setWidget(central)

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(24, 22, 24, 22)
        root_layout.setSpacing(16)


        # The pages are keyed, not indexed. The sidebar sends back
        # `navigate:<key>`, and a key is also the only thing that survives a
        # page being inserted above another one - `setCurrentIndex(1)` in
        # show_result_file had already drifted onto the wrong page as tabs were
        # added around it (see _show_page's callers).
        self.page_stack = QStackedWidget()
        # key → **堆疊裡那個 widget**。跟 `_pages`（key → 面板本身）分開，
        # 因為太高的那幾頁在中間多包了一層自己的捲軸（見 `_within_budget`）。
        self._page_holder: dict[str, QWidget] = {}
        # **不設最小高度。**
        #
        # 這裡本來是 760，而出廠視窗是 980×720（`self.resize(980, 720)`）——
        # 扣掉標題列與邊距，內容區高度大約 640。一個 760 的最小高度在那個
        # 視窗裡永遠塞不下，於是整個內容欄連同決策橫幅都活在外層捲軸裡。
        #
        # 頁面設計 2026-08-24 量到的兩個後果：
        #
        # 一、**出廠視窗大小的使用者從來沒看過設定頁那顆「儲存變更」**
        #     ——它釘在摺線以下 142px。
        # 二、**決策橫幅「看得到」不成立。**它不會被蓋住（那是真的），
        #     但使用者一捲頁面，那條「AI 停下來等你」就被捲出畫面上緣。
        #     而 app 自己就有兩處在程式裡直接捲那個捲軸。
        #
        # 「一個看不到的停下來等於沒有停下來」——那條規矩在這裡有一個現成
        # 的破口，而它不是被誰改壞的，是一個從來沒有在出廠大小底下量過的
        # 數字。
        #
        # 網頁面板自己會處理內容太長的情況（每一頁都有自己的捲軸），
        # 所以這裡不需要替它們撐高度。
        self._pages: dict[str, QWidget] = {}

        self.dashboard_panel = DashboardWebPanel()
        self.dashboard_panel.refresh_button.clicked.connect(
            lambda: self.dashboard_panel.refresh(self.projects)
        )
        self.dashboard_panel.project_selected.connect(
            self._select_project_from_dashboard
        )
        self.dashboard_panel.queue_capacity_available.connect(
            self._maybe_start_next_queued_project
        )
        self.dashboard_panel.refresh(self.projects)

        # The 陣容 page, and still the screen you pick conversations on: scan,
        # select, bind and create-project are all on it, and the binding itself
        # lives in app/services/session_binding.py so this and the native
        # ConversationLibraryPanel write identical session entries.
        #
        # It was called conversation_library_panel until the name outlived what
        # it held. P10 kept the name on the argument that the job had not
        # changed, but the class, the page, the sidebar key and the label had
        # all become 陣容, and the attribute was the only thing left saying
        # otherwise - which is how the retired page kept looking mounted.
        self.lineup_panel = LineupWebPanel(self.project_path)
        self.lineup_panel.sessions_bound.connect(self._refresh_bound_sessions)
        self.lineup_panel.create_project_requested.connect(
            self.create_project_from_conversation_pair
        )
        self.lineup_panel.run_requested.connect(self.start_workflow)
        # 他在某一格上回話。走的是**同一條**送答案的路（`submit_user_answer`），
        # 只是多帶一個「他講給誰」——兩條路的話，兩邊的收尾遲早會漂。
        self.lineup_panel.answer_sent.connect(self._answer_from_lineup)
        self.lineup_panel.open_documents_requested.connect(
            lambda: self._show_page("documents")
        )
        self._add_page("lineup", self.lineup_panel)
        self._add_page("dashboard", self.dashboard_panel)

        self.session_panel = SessionTimelinePanel(self.project_path)
        self._add_page("session", self.session_panel)

        # The HTML pages, not the native panel. `KnowledgePanel` was one tab
        # covering both shared memory and documents; the redesign splits them,
        # and its 21 buttons are now 9 controls on 共享記憶 and 12 on 文件 -
        # reconciled against the native panel by name, nothing dropped.
        # The attribute keeps its old name because prepare_handoff_task brings
        # this tab to the front by it.
        self.knowledge_panel = KnowledgeWebPanel(self.project_path)
        self.knowledge_panel.create_handoff_requested.connect(
            self.prepare_handoff_task
        )
        self._wire_panel_feedback(self.knowledge_panel)
        self._add_page("knowledge", self.knowledge_panel)

        self.documents_panel = DocumentsWebPanel(self.project_path)
        self._wire_panel_feedback(self.documents_panel)
        self._add_page("documents", self.documents_panel)

        self.roadmap_panel = RoadmapWebPanel()
        self._add_page("roadmap", self.roadmap_panel)

        # No longer labelled 預覽. It carried that label because two of its
        # controls (Decision Gate triggers, long task mode) had no backing
        # store, so editing them there would have changed nothing. ROADMAP P3
        # gave both a real one and this panel writes through to
        # AISettingsStore, so the label would now be the misleading half.
        # It does not replace the native AISettingsPanel below - that one still
        # owns the task editor and the per-run execution settings.
        self.settings_web_panel = SettingsWebPanel(self.project_path)
        self._add_page("settings", self.settings_web_panel)

        live_tab = QWidget()
        live_layout = QVBoxLayout(live_tab)
        live_layout.setContentsMargins(0, 10, 0, 0)
        output_title = QLabel(t("run.live_log.title"))
        output_title.setObjectName("sectionLabel")
        live_layout.addWidget(output_title)

        self.output = QPlainTextEdit()
        self.output.setObjectName("liveLog")
        self.output.setReadOnly(True)
        self.output.setPlaceholderText(t("run.live_log.placeholder"))
        self.output.setFont(QFont("Consolas", 10))
        live_layout.addWidget(self.output, 1)
        self._add_page("live", live_tab)

        # Last, behind a divider (see shell_panel.PAGE_GROUPS): the eight above
        # are for doing the work, this one is for proving it was done.
        self.audit_panel = AuditWebPanel(self.project_path)
        self._add_page("audit", self.audit_panel)

        # 登入與安裝：**不進 `_add_page`**（2026-08-26 拍板 A，不進側邊欄），
        # 所以它也不經過那個「每一個面板都一定會經過的路口」——
        # `action_failed` 因此在這裡明著接。漏掉的話，「帶我去登入」按下去
        # 開不起來的時候會什麼都不發生，然後他再按一次。
        #
        # 建出來就先藏起來：它是視窗的子 widget（跟著視窗一起被回收），
        # 但沒有被放進任何版面，不明著 hide 的話 Qt 會在視窗顯示的時候把它
        # 畫在左上角。啟動時那個原生檢查視窗**留著**（拍板 B）。
        self.credentials_panel = CredentialsWebPanel(self)
        self.credentials_panel.hide()
        self._wire_failures(self.credentials_panel)

        # The redesigned pages are whole-window designs - each one has its own
        # header and lays itself out against the viewport height, putting things
        # like the context bar at the *bottom of the window*. Measured at
        # 1456x819: the native chrome above the tab bar was 525px, leaving the
        # page about 254px, so roughly a third of the design was visible and the
        # rest was below a scrollbar the design knew nothing about. Collapsing
        # the cards recovered most of it; moving them into the shell recovers
        # the rest, and removes the second copy of every control - the pages
        # were drawing their own header on top of the native one anyway.
        #
        # Neither half of the shell is a Qt widget styled to look like the
        # design. The sidebar's active item is a gradient fill under two
        # stacked box-shadows, and QSS has no box-shadow at all (one
        # QGraphicsDropShadowEffect per widget, and it cannot do the 1px edge
        # light), so a Qt rebuild would have been visibly not it.
        #
        # side runs the full height of the content - beside the pages *and* the
        # settings panel - and top spans the column to its right, which is the
        # L the design draws and the reason it is two views instead of one.
        self.shell_side = ShellPanel("side", self._shell_state)
        self.shell_side.action.connect(self._on_shell_action)
        self.shell_top = ShellPanel("top", self._shell_state)
        self.shell_top.action.connect(self._on_shell_action)

        self.ai_settings_panel = AISettingsPanel(self.project_path)

        content_column = QVBoxLayout()
        content_column.setContentsMargins(0, 0, 0, 0)
        content_column.setSpacing(16)
        content_column.addWidget(self.shell_top)
        content_column.addWidget(self.page_stack, 1)
        # ⛔ 2026-09-01 之前這裡是 `content_column.addWidget(self.ai_settings_panel)`。
        #
        # 老闆 2026-08-27：「AI 執行設定那邊一樣吃到頁面，不能按照我剛剛講的
        # 去改嗎?」——那一條釘在最底下的原生面板，把每一頁的高度都吃掉一截。
        # 那 22 個控制項與任務編輯器現在長在陣容頁裡（跟著整頁一起捲）。
        #
        # **物件留著、只是不在版面上。** 執行路徑仍然跟它拿設定與任務
        # （`current_settings()`／`save_task()`），而它現在是一個**跟著硬碟走的
        # 影子**：跑之前一律 `refresh_from_disk()`，因為網頁那一個入口寫的是硬碟。
        # 直接刪掉它要一次改動五個呼叫點，而那是在一個他還沒驗收的版本上
        # 動執行路徑——留成影子，行為一模一樣。
        self.ai_settings_panel.setParent(self)
        self.ai_settings_panel.hide()

        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(16)
        body_row.addWidget(self.shell_side)
        body_row.addLayout(content_column, 1)
        root_layout.addLayout(body_row, 1)

    def apply_styles(self) -> None:
        """Apply the Quiet Celestial visual language in the current mode.

        Source: the "AI Control Center 改版 Quiet Celestial" design doc.
        Deliberately QSS-only - no var(), transition or box-shadow, because Qt
        supports none of them. Depth comes from borders plus background steps
        instead.

        Selector/widget pairing was verified against the real widget tree
        before adopting this: #card really is a QFrame (9 instances), every
        #xxxLabel really is a QLabel, and set_status() already calls
        unpolish/polish so the runtime objectName swap between statusIdle/
        Running/Pass/Ask/Fix actually restyles.

        The rules themselves live in native_theme.py, once, with colours as
        roles - see that module for why there is no second dark copy.
        """
        self.setStyleSheet(native_stylesheet(THEME.mode))


    # ---- pages ----------------------------------------------------------
    # 出廠視窗（`resize(980, 720)`）扣掉外框剩下給內容的高度。
    #
    # 一頁的最小高度超過這個數字，**外層那個捲軸就會動**——而外層一捲，
    # 決策橫幅（「AI 停下來等你」唯一的視覺訊號）會被推出畫面上緣。
    # 頁面設計 2026-08-24 的原話：
    #
    # > 外層捲軸與內層捲軸捲掉的東西不一樣。A 不是「捲軸搬個位置」，
    # > 是把捲動的代價從**全域的安全訊號**換成**局部的內容**。
    # 出廠視窗大小。**一個地方寫，兩邊讀**——
    # `tests/test_the_window_fits_what_it_ships_with.py` 用它算預算。
    # 寫成常數而不是讓測試去原始碼裡撈 `resize(...)`：撈的那一版
    # 2026-08-24 之前撈得到，而它量錯了東西（見那支測試的說明）。
    FACTORY_SIZE = (980, 720)

    PAGE_HEIGHT_BUDGET = 640

    # 自己帶捲軸的那幾頁。**指名，不是量出來的**（理由見 `_within_budget`）。
    #
    # 這份清單少一頁會怎樣：那一頁把外層撐開，決策橫幅一捲就不見。
    # 那件事由 `test_the_window_fits_what_it_ships_with.py` 量真視窗來抓。
    PAGES_WITH_THEIR_OWN_SCROLL = frozenset({"SessionTimelinePanel"})

    def _within_budget(self, widget: QWidget) -> QWidget:
        """一頁太高就給它自己的捲軸，**不要讓它撐開外層**。

        2026-08-24 實測：`SessionTimelinePanel` 的最小高度是 658，而它是
        `session_panel.py` 四處 `setMinimumHeight`（150／120／120／120）
        **加起來**的——**沒有任何單一數字超標**。所以「把守衛的掃描範圍
        擴大到那 8 處」那個修法找不到它（那是我 2026-08-24 上一輪給的建議，
        錯的）。

        包一層而不是把那四個數字調小：調小要決定「哪一格可以變多矮」，
        那是四個獨立的版面判斷；而包一層之後那一頁**自己**捲，外層不動。

        頁面設計 2026-08-24 選 A，理由是外層一捲決策橫幅會被推出畫面上緣。

        **沒超過的不包**——包了會多一層永遠不會出現的捲軸，而每一層
        `QScrollArea` 都會吃掉一點邊距。

        ## 為什麼是指名，不是量高度

        第一版寫的是 `if widget.minimumSizeHint().height() <= 預算`，
        而**在建構的當下量不到**：版面還沒算過，那時候每一頁都回 0。
        （網頁面板永遠回 0，所以量高度對它們也永遠沒有意義。）

        改成指名，而「有沒有漏掉一頁」由
        `tests/test_the_window_fits_what_it_ships_with.py` 量**真的組起來的
        視窗**來回答——那支守衛 2026-08-24 之前掃的是原始碼字面，
        所以它一直是綠的，而視窗早就塞不下了。
        """
        if type(widget).__name__ not in self.PAGES_WITH_THEIR_OWN_SCROLL:
            return widget
        holder = QScrollArea()
        holder.setWidgetResizable(True)
        holder.setFrameShape(QFrame.NoFrame)
        holder.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder.setWidget(widget)
        # 這一層是**技術上的容器**，不是一頁。`_pages` 存的還是原本那個
        # 面板，所以 `_show_page`／`action_failed`／每一支測試拿到的都是
        # 它自己——**包一層不准改變任何人拿得到的東西**。
        return holder

    def _add_page(self, key: str, widget: QWidget) -> None:
        self._pages[key] = widget
        # 堆疊裡放的可能是**包了一層捲軸的容器**，而 `_pages` 存的永遠是
        # 那個面板本身。兩者要對得起來，`_show_page` 才切得過去。
        holder = self._within_budget(widget)
        self._page_holder[key] = holder
        self.page_stack.addWidget(holder)
        # **每一個面板的失敗都要有地方去。**
        #
        # 這件事本來是各接各的（`_wire_panel_feedback`，只有兩個面板叫得到），
        # 結果陣容頁六個 `action_failed` 一個都沒接上——使用者按了「改名字」
        # 失敗，畫面上什麼都不會發生。
        #
        # 接在這裡而不是在每個面板建出來的地方：**這是唯一一個每個面板都
        # 一定會經過的路口**，漏接一個的唯一方法是不把它加進畫面。
        #
        # 而「不把它加進畫面」現在真的有一個（憑證入口不進側邊欄），所以
        # 接法本身抽成一支——兩個地方各寫一份 lambda 的那天，兩份會漂開。
        self._wire_failures(widget)

    def _wire_failures(self, widget: QWidget) -> None:
        """這個面板的失敗有地方去嗎。

        `_add_page` 會替每一頁叫一次；**不進 `_add_page` 的面板要自己叫**
        （目前只有憑證入口）。一個沒有人接的訊號，跟一個沒有發的訊號，
        在畫面上一模一樣。
        """
        signal = getattr(widget, "action_failed", None)
        if signal is not None:
            signal.connect(
                lambda message: QMessageBox.warning(self, t("action.failed.title"), message)
            )

    def _show_page(self, key: str) -> None:
        """Bring a page to the front by key, and tell the sidebar."""
        widget = self._pages.get(key)
        if widget is None:
            return
        # **切的是容器**（可能就是面板自己）。切面板的話，被包起來的那一頁
        # 永遠切不過去——而 `setCurrentWidget` 對一個不在堆疊裡的 widget
        # 是靜靜地什麼都不做，沒有錯誤訊息。
        self.page_stack.setCurrentWidget(self._page_holder.get(key, widget))
        # The active item is pushed, never remembered by the page - a page that
        # decided for itself would light up whichever entry it happens to be,
        # which is right only until something else navigates.
        self.shell_side.push_state()

    def _current_page_key(self) -> str:
        current = self.page_stack.currentWidget()
        for key, holder in self._page_holder.items():
            if holder is current:
                return key
        return ""

    # ---- shell state ----------------------------------------------------
    def _push_shell(self) -> None:
        """Repaint both halves of the shell from current state."""
        for panel in (
            getattr(self, "shell_side", None),
            getattr(self, "shell_top", None),
        ):
            if panel is not None:
                panel.push_state()

    def _set_stage(self, text: str) -> None:
        self._stage_text = text
        self._push_shell()

    def _set_cloud_status(self, text: str) -> None:
        self._cloud_status_text = text
        self._push_shell()

    def _set_has_result(self, value: bool) -> None:
        self._has_result_file = value
        self._push_shell()

    def _shell_state(self) -> dict:
        session = self._focused_session
        return {
            "mode": THEME.mode,
            "shell": {
                "active_page": self._current_page_key(),
                "pages": [
                    # `group` only where there is one - the sidebar draws a
                    # divider above the first entry of a new group, and the
                    # page cannot work out where that line goes on its own.
                    # `label` 是 key（`app/i18n.py`）——那份清單在模組層，
                    # 存字的話會在 import 的當下凍住。用到才取。
                    {"key": key, "label": t(label), **_group_of(key)}
                    for key, label in SHELL_PAGES
                    if key in self._pages
                ],
                "project": {
                    "name": self.project_path.name,
                    "path": str(self.project_path),
                    "sensitivity": ProjectRegistry.get_sensitivity(
                        self.project_path
                    ),
                    # **那個值是怎麼來的。**三種：他自己選的／他還沒選過／
                    # 設定檔讀不到所以用最嚴的那一級頂著。
                    #
                    # 不推的話畫面上三種一模一樣，而**那不是體驗問題**：
                    # 一個設定檔壞掉的專案看起來跟他親手設成 KYC 的一樣，
                    # 他最合理的反應是「這個又不是 KYC，改低一點」——
                    # 改下去那個專案的逐字紀錄就開始往外同步。
                    #
                    # 引擎 2026-08-24 就算得出這件事，而畫面那一半一直沒接
                    # （`tests/test_the_screen_knows_why_a_value_is_what_it_is.py`
                    # 釘的是引擎那一半）。
                    "sensitivity_source": ProjectRegistry.sensitivity_source(
                        self.project_path
                    ),
                    # 標籤也一起推。畫面自己維護一份的下場已經發生了——
                    # 引擎寫「高敏感度／KYC」，`shell.js` 寫「KYC／高敏感」，
                    # 兩邊的字**已經不一樣**。
                    "sensitivity_label": sensitivity_label(
                        ProjectRegistry.get_sensitivity(self.project_path)
                    ),
                },
                # The path is the id, so the shell can send back something
                # that identifies a project without depending on list order.
                "projects": [
                    {
                        "id": str(entry.resolved_path),
                        "name": entry.name
                        + ("" if entry.resolved_path.exists() else t("path.missing.inline")),
                    }
                    for entry in self.projects
                ],
                "run": {
                    "state": self._status_kind,
                    "label": self._status_text,
                    # Only while a run is timing. The bar hides the clock when
                    # this is empty rather than showing a frozen 00:00, which
                    # reads as "running, and stuck".
                    "elapsed": self._elapsed_text,
                    # The stage detail, kept out of `label`: label is a closed
                    # set bound to a colour, this is free text. Deliberately
                    # `session.current_stage` raw and not `_stage_text` - that
                    # one falls back to 「等待開始」 for the native label, and
                    # in the bar the status pill already says as much. Empty
                    # means the bar draws no stage element at all.
                    "stage": session.current_stage,
                    # 這一次收場要使用者動哪一樣東西（五組之一），空字串
                    # ＝還沒有收場。顏色只回答「要不要你動」，這一欄回答
                    # 「動哪一樣」——**由一個函式算出來，不是每個畫面各自
                    # 維護一張十一格的表**。
                    "group": session.last_group,
                    # Which line-up seat is running, as an index into `lineup`.
                    # Absent when nothing is - the page then lights no seat
                    # rather than defaulting to the first one.
                    **self._active_slot(session),
                },
                "gate": self._gate_state(session),
                # Every project with something going on, focused or not
                # (ROADMAP P4). Empty list when nothing is - the page draws no
                # strip at all rather than an empty bar saying 「沒事」, which
                # would take a line of screen to report the normal case.
                "activity": self._activity_strip(),
                "drive": {"label": self._cloud_status_text},
                # Read-only summary of who will run this, shown beside 開始執行.
                # That button is the only one that starts spending money, and
                # "who is running it" is the last thing still changeable at
                # that moment - clicking the summary goes to the 陣容 page.
                "lineup": self.lineup_panel.provider_sequence(),
                # Token usage. A constant today, on purpose: ContextLedger is
                # already storing what each CLI reported, but two things about
                # those numbers are unverified and need opposite arithmetic
                # (ROADMAP P12「用量與花費怎麼呈現」), so a total here would be
                # a plausible-looking guess. The page turns this into the one
                # line 「用量統計開發中」.
                #
                # What replaces it: `state` becomes "at_least"/"exact" with
                # `tokens`/`input`/`output`, plus `complete` (whether every CLI
                # call in the run reported). Both of those are already in the
                # ledger - `usage_reported_calls` vs `usage_total_calls` is the
                # `complete` flag. The page merges `complete: false` and
                # `state != "exact"` into one 「至少」, so nothing here has to
                # pre-compute that.
                #
                # `spike` is the exception that does NOT wait for that
                # verification: it is a ratio against this project's own recent
                # runs, and the unknown cancels out of a ratio. So the one thing
                # AICC can honestly say today is 「這次比平常兇」 - which is also
                # the thing users actually fear, since 「貴」 they can look up and
                # 「不知不覺地貴」 they cannot.
                "usage": self._usage_state(session),
                # Which of the bar's actions would do nothing right now, and
                # why. Absent action = allowed. See `_actions_blocked`.
                "blocked": self._actions_blocked(),
            },
        }

    def _usage_state(self, session) -> dict:
        """What the bar says about tokens.

        `state` stays "developing" until the two semantics in ROADMAP P12 are
        verified - no number here is trustworthy before then. `spike` is added
        when there is one, because it is a ratio and survives the ambiguity.
        """
        usage: dict = {"state": "developing"}
        if session.usage_spike:
            usage["spike"] = session.usage_spike
        return usage

    def _active_slot(self, session) -> dict:
        """`{"active_slot": n}` while a seat is running, `{}` otherwise.

        The index comes from **the project's own line-up** - the same object
        `provider_sequence()` draws the seats from. It used to come from a
        fixed `Orchestrator.EXECUTED_ROLES`, which agreed with the drawn seats
        only while every project had the same two: seating a planner in front
        shifts the author from 0 to 1, and the fixed list would have lit the
        planner while the author worked.

        It is deliberately NOT derived from the workflow mode: the duties swap
        between sequences (planning has the reviewer drafting) while the seats
        do not, so an index computed from the mode would light the wrong one
        exactly when the two disagree - and a lit seat is a confident-looking
        claim.

        Absent rather than -1 or 0 when nothing is running: the page treats a
        missing key as "light nothing", and 0 would be indistinguishable from
        "the first seat is working".
        """
        role = session.active_role
        if not role:
            return {}
        index = self.lineup_panel.slot_index(role)
        if index is None:
            # A role the engine dispatches but this line-up does not seat.
            # Better to light nothing than to guess an index.
            return {}
        return {"active_slot": index}

    def _activity_strip(self) -> list[dict]:
        """Every project with something going on, for the shell's top strip.

        ROADMAP P4. This is what replaced 「同時看兩個專案的即時輸出」 after the
        user said what they actually wanted to know: **有沒有出事**.

        The distinction that decided the shape: live output is what a project
        is saying *this second* and it scrolls away, so it is worthless to
        somebody who was not watching. State is what has happened since they
        last looked, and it waits. A person who starts a long run and goes to
        sleep needs the second one. A split screen spends screen width on the
        first, and stops working at three projects.

        Idle projects are left out on purpose - that is the whole reason this
        stays readable at five projects while two panes do not. Left in, the
        strip would grow with the project list rather than with the work.

        Four states, in the order a person cares about them:

        * `waiting` - stopped, needs an answer. **Included even after 稍後**:
          deferring means "later", not "resolved", and this is exactly the case
          where the strip earns its keep, because nothing else remembers.
        * `error` - the last run ended badly and nothing has run since.
        * `partial` - the run ended, nothing is coming back for it, and it needs a
          person: 「尚未設定驗證」, 「驗證逾時」, 「已達修正上限」, 「需要修正」,
          「共同記憶不完整」.
          Left out, this was the one family of endings that finished a run and
          then vanished. The office user this is for starts a run and walks away,
          so 「這個專案停在那裡等你寫一行設定」 has to survive being not-watched
          exactly as much as a failure does. Taken from the shell's own status
          vocabulary rather than by listing workflow_status strings here, so a
          future ending that is 「做了一半」 joins the strip by being painted
          `partial`, not by somebody remembering to add it in a second place.
        * `running` - working, nothing needed.

        (`partial` has no dot colour in aicc.css yet, so the page draws it with the
        neutral dot - the entry, the project name and the reason are all on screen
        and clickable, only the tint is missing. Adding the tint means rebuilding
        app/ui/web, which is out of bounds this round; the state name already is
        the vocabulary set_status uses, so the page picks the colour up the day the
        design folder gains that one rule.)
        """
        entries = []
        for path, session in self.run_sessions.items():
            if session.pending_ask_result is not None:
                state = "waiting"
            elif session.thread is not None:
                state = "running"
            elif session.last_outcome is not None and session.last_outcome[1] in (
                "error",
                "partial",
            ):
                state = session.last_outcome[1]
            else:
                continue
            entries.append(
                {
                    "id": str(path),
                    "name": path.name,
                    "state": state,
                    # Free text, and short: the strip has one line per project.
                    # The full stage stays in the execution bar of whichever
                    # project is focused.
                    # `error` shows the failure's own words: its current_stage
                    # is whatever step it died on, and 「正在執行測試」 beside a
                    # stopped project reads as still running. `partial` is the
                    # other way round - on_completed writes the actionable half
                    # into current_stage (which file, which field), while the
                    # label alone (「尚未設定驗證」) tells the user nothing they
                    # can act on.
                    "stage": (
                        session.last_outcome[0]
                        if state == "error" and session.last_outcome
                        else session.current_stage
                    ),
                    # 使用者要動哪一樣東西。條子上一個專案一行，而「要再跑
                    # 一次」跟「有一項要先修」在那一行上長得一模一樣——
                    # 併在一起就是叫人去按一顆一定會失敗的按鈕。
                    "group": session.last_group,
                    # Deliberately no seat index. `_active_slot` reads the
                    # *focused* project's line-up, so for every other entry in
                    # this strip it would be an index into the wrong list -
                    # the exact mistake the fixed-role-list version made, and
                    # a lit seat is a confident-looking claim. Reading each
                    # project's own line-up off disk is not an option either:
                    # this state is pushed every second for the clock.
                    #
                    # The strip's question is 「哪個要我」, not 「哪一格」. That
                    # one is answered by switching to the project, which is
                    # what clicking an entry does.
                    #
                    # So the page can avoid shouting about the project already
                    # on screen. Whether it should is the page's call; this is
                    # the fact it needs to make it.
                    "focused": path == self.project_path,
                }
            )
        # Cost of missing it, not name and not start time. `partial` sits
        # below `error` (nothing is broken) and above `running` (a running
        # project needs nobody).
        order = {"waiting": 0, "error": 1, "partial": 2, "running": 3}
        entries.sort(key=lambda entry: (order[entry["state"]], entry["name"]))
        return entries

    def _gate_state(self, session) -> dict | None:
        """The decision banner, or None when there is nothing to answer.

        A-2: this is the one thing that has to be visible from every page, so
        it is drawn by the shell rather than by the Dashboard. It is suppressed
        only by an explicit 「稍後」 on this project - never by which page is
        open, and never by the run finishing something else.
        """
        if session.pending_ask_result is None:
            return None
        if self.project_path in self._gate_deferred:
            return None
        question = (
            session.pending_ask_result.get("review_question", "")
            or t("run.needs_decision")
        )
        return {
            "project": self.project_path.name,
            "question": question,
            "waiting": self._ask_waiting_text(session),
        }

    def _ask_waiting_text(self, session) -> str:
        started = getattr(session, "ask_started_at", None)
        if started is None:
            return t("time.just_now")
        return relative_time.describe_clock(time.monotonic() - started)

    # ---- shell actions --------------------------------------------------
    # Every entry is a control the native chrome used to carry, wired to the
    # same handler its button was wired to. Two had no handler to wire to and
    # are new: `stop` and `gate_later` - see their methods.
    def _on_shell_action(self, kind: str, arg: str) -> None:
        if kind.startswith("navigate:"):
            self._show_page(kind.split(":", 1)[1])
            return
        if kind == "set:project":
            self.switch_to_project(arg)
            return
        if kind == "set:sensitivity":
            self._on_sensitivity_changed(arg)
            return

        handlers = {
            "run": self.start_workflow,
            "stop": self.stop_workflow,
            "open_results": self.show_result_file,
            "new_project": self.add_project,
            "remove_project": self.remove_current_project,
            "copy_path": self.open_project_folder,
            "export_workspace": self.export_workspace,
            "import_workspace": self.import_workspace,
            "line_settings": self.configure_line,
            "cloud_settings": self.configure_cloud_sync,
            "sync_now": self.sync_current_project,
            "link_cloud_project": self.link_cloud_project,
            "gate_answer": self.start_workflow,
            "gate_later": self.defer_gate_banner,
            # 「稍後」只收起橫幅，問題還在等，鎖也還握著。**在這之前沒有任何
            # 一條路可以「不回答就結束」**——`dismiss_pending_question` 早就寫好
            # 了，只是沒有人呼叫它。少了它的後果不是不方便：等待中的專案握著
            # workflow lock，排程器不會啟動它，於是一個使用者已經不打算回答的
            # 問題，會讓那個專案永遠停在「等待決策」。
            "gate_dismiss": self.dismiss_pending_question,
        }
        handler = handlers.get(kind)
        if handler is not None:
            handler()

    def _wire_panel_feedback(self, panel) -> None:
        """Give a web panel's results somewhere to go.

        The HTML pages have no general-purpose text pane - their layouts are
        fixed by the design - so a readiness report or an indexed excerpt would
        otherwise be produced and dropped, which looks exactly like a button
        that does nothing.
        """
        panel.report_ready.connect(self._show_panel_text)
        panel.file_opened.connect(self._open_panel_path)
        # `action_failed` **不在這裡接**。這個函式只有兩個面板叫得到
        # （共享記憶、文件），而陣容頁那六個「失敗了要跟使用者說」因此
        # 從頭到尾掉進虛空——包括改名字與選職責失敗的那兩個。
        # 現在接在 `_add_page`：每一個面板都會經過那裡。

    @Slot(str)
    def _show_panel_text(self, text: str) -> None:
        TextViewDialog(t("common.content"), text, parent=self).exec()

    @Slot(str)
    def _open_panel_path(self, raw_path: str) -> None:
        """Reveal a generated file, or show it if the shell will not.

        Falling back to showing the contents matters more than it looks: these
        paths come from actions the user just triggered (產生交接包, 產生啟動
        提示), and "nothing happened" is the worst possible answer to a button
        that did in fact write a file.
        """
        path = Path(raw_path)
        if reveal(path):
            return
        if path.is_file():
            try:
                TextViewDialog(
                    path.name, path.read_text(encoding="utf-8"),
                    subtitle=str(path), parent=self,
                ).exec()
                return
            except OSError:
                pass
        QMessageBox.information(self, t("file.created.title"), str(path))

    def _on_theme_changed(self, _mode: str) -> None:
        """Repaint the native chrome when any panel flips day/night.

        setStyleSheet on the window re-polishes its whole child tree, so
        nothing needs to be told individually - but dialogs already on screen
        are separate top-level widgets and keep the old sheet until reopened.
        """
        self.apply_styles()

    @Slot(str)
    def prepare_handoff_task(self, task: str) -> None:
        self.ai_settings_panel.set_planning_task(
            task,
            planning_rounds=1,
        )
        # 那個面板 2026-09-01 起不在版面上了，所以要帶他去**看得到那份任務**
        # 的地方——陣容頁上那顆〔📄 專案內容與目標〕入口。
        # 停在 knowledge 頁的話，他會被告知「已切換成計畫模式」，
        # 然後看不到任何切換過的痕跡。
        self.lineup_panel.push_state()
        self._show_page("lineup")
        QMessageBox.information(
            self,
            t("handoff.created.title"),
            t("handoff.mode_switched"),
        )
        self.page_scroll.verticalScrollBar().setValue(
            self.workspace_tabs.y()
        )

    @Slot()
    def defer_gate_banner(self) -> None:
        """「稍後」 - hide the decision banner without answering it.

        Banner-level only, deliberately: `pending_ask_result` is untouched, the
        project stays in the Dashboard's 等待決策 column, its reminder and LINE
        timers keep running, and the banner comes back on the next state change
        (see on_completed, which clears the defer). This is not 「結束等待」 -
        that is a separate, confirmed, permanent action.
        """
        if self._focused_session.pending_ask_result is None:
            return
        self._gate_deferred.add(self.project_path)
        self.append_log(t("gate.dismissed_note"))
        self._push_shell()

    @Slot()
    def stop_workflow(self) -> None:
        """Terminate the focused project's in-flight claude/codex subprocess.

        There was no way to stop a run from inside the app before this: the
        only caller of `terminate_current()` was closeEvent's force-close. The
        shell draws a 停止 button whenever a run is busy and Python cannot
        suppress it, so the choice was to make it real or ship a dead control.

        It reuses the force-close mechanism rather than inventing a second one,
        and deliberately does not fake a completion: killing the subprocess
        makes the CLI call fail, the worker takes its existing failure path,
        and on_failed/on_thread_finished do the cleanup (lock release, timers,
        button state) they already do for any other failure. Nothing here
        touches another project's session.

        The lock really is released, though `on_thread_finished` only releases
        it when `pending_ask_result is None`. That holds here rather than by
        luck: `start_workflow` refuses to launch while a question is pending
        (it opens the answer dialog instead), so a session with a live thread
        never has one - which is also why this method's guard is on
        `session.thread`.
        """
        session = self._focused_session
        if session.thread is None:
            return

        answer = QMessageBox.question(
            self,
            t("run.stop.title"),
            f"確定要停止「{self.project_path.name}」目前的執行嗎？\n\n"
            "正在執行的 Claude／Codex 會被中止，本次執行會記為失敗。\n"
            "已經寫入的檔案不會被還原，其他專案不受影響。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        worker = session.worker
        orchestrator = getattr(worker, "orchestrator", None) if worker else None
        if orchestrator is None:
            # The thread is between steps and has no live subprocess to kill.
            # Saying so is better than a button that appears to do nothing.
            self.append_log(t("run.stop.nothing_running"))
            return

        stopped = orchestrator.runner.terminate_current()
        self.append_log(
            t("run.stop.signal_sent")
            if stopped
            else t("run.stop.nothing_found")
        )
        self._set_stage(t("run.stop.stopping"))

    @Slot()
    def start_workflow(self) -> None:
        if self.thread is not None or self.sync_thread is not None:
            return

        if self.pending_ask_result is not None:
            self.prompt_user_answer()
            return

        # **先從硬碟重讀。** 設定與任務現在有兩個入口，而網頁那一個寫的是
        # 硬碟。不重讀的話：他在陣容頁把模型改成 opus、把任務打好，按下開始
        # 執行——而這裡拿到的是那個看不見的面板上沒動過的舊值，
        # `save_task()` 還會拿它那份空的任務**蓋掉他剛剛寫的**。
        self.ai_settings_panel.refresh_from_disk()

        if not self.ai_settings_panel.save_task():
            return

        settings = self.ai_settings_panel.current_settings()
        if self._is_high_usage(settings):
            answer = QMessageBox.question(
                self,
                t("run.heavy.confirm_title"),
                "本次設定可能消耗較多額度：\n\n"
                f"Claude：{settings.claude_model} / {settings.claude_effort}\n"
                f"Codex：{settings.codex_model} / {settings.codex_effort}\n"
                f"預計模型呼叫：{settings.expected_model_calls} 次\n"
                f"Claude 權限：{settings.claude_permission_mode}\n"
                f"Claude 最長時間："
                f"{'不限' if settings.claude_timeout_minutes == 0 else str(settings.claude_timeout_minutes) + ' 分鐘'}\n"
                f"Claude 最大回合："
                f"{'不限' if settings.claude_max_turns == 0 else settings.claude_max_turns}\n\n"
                "是否繼續？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        if not self._try_acquire_project_lock(self.project_path):
            QMessageBox.warning(
                self,
                t("run.already_running.title"),
                t("run.already_running.body"),
            )
            return

        self._launch_worker(
            self.project_path,
            clear_output=True,
            settings=settings,
        )

    @staticmethod
    def _is_high_usage(settings: AIExecutionSettings) -> bool:
        return queue_scheduler.is_high_usage(settings)

    def _try_acquire_project_lock(self, project_path: Path) -> bool:
        if ProjectDashboardService.is_stale_lock(project_path):
            # project_path may not be the focused project (e.g. the queue
            # scheduler acquiring a candidate's lock) - log to ITS own
            # buffer, not whichever project happens to be on screen.
            self._append_project_log(
                project_path, t("run.stale_lock_cleared")
            )
            ProjectDashboardService.release_lock(project_path)
        return ProjectDashboardService.acquire_lock(project_path, "")

    @Slot()
    def _maybe_start_next_queued_project(self) -> None:
        # Stage 1 Phase 11: concurrency_limit becomes a real multi-launch
        # gate instead of the Phase 6-era "any active session blocks
        # everything" placeholder. project_dashboard.py's running_projects/
        # concurrency_limit math was already correct (based on on-disk
        # TimelineStore status, not in-memory RunSession state) - it just
        # was never actually consulted to decide "can we launch another
        # one", since the old design could only ever run one project at a
        # time in the first place. A project merely awaiting an answer
        # (pending_ask_result set, but its workflow already finished) does
        # NOT count against running_projects/available_capacity - it isn't
        # consuming an active execution slot.
        # running_projects only counts active WORKFLOW runs, not Google
        # Drive syncs (RunSession.is_active() checks both, but this
        # capacity math intentionally doesn't) - sync capacity has its own
        # separate global guard (configure_cloud_sync/link_cloud_project),
        # a genuinely different resource from workflow execution slots.
        summary, _ = self.dashboard_panel.service.build(self.projects)
        available_capacity = summary.concurrency_limit - summary.running_projects
        if available_capacity <= 0:
            # Deliberate: lowering concurrency_limit below the number of
            # projects already running does NOT preempt/kill them - it
            # only blocks NEW auto-launches until running_projects drops
            # back under the new limit on its own. Forcibly killing an
            # in-flight AI/CLI workflow mid-turn would waste API spend and
            # risks leaving .aicc run state or partial file writes
            # half-written, so "let existing runs finish" is intentional,
            # not a missing feature.
            return

        candidates = self.dashboard_panel.service.queued_start_candidates(
            self.projects
        )
        if not candidates:
            return

        settings_store = AISettingsStore()

        def load_settings(
            candidate: ProjectEntry,
        ) -> AIExecutionSettings:
            settings_store.set_project(candidate.resolved_path)
            return settings_store.load_project()

        # Why nothing started, if nothing starts. The closing message used to
        # have one explanation for every case - 「用量設定較高」 - and a lock
        # was the other case: the log printed 「偵測到執行中鎖定，先略過」 and
        # then, one line later, told the user to go look at usage settings.
        # **The wrong explanation was the last line**, which is the one that
        # gets read, and it sends the reader to the wrong screen - the real
        # problem may be a stale lock, which the dashboard can already show.
        skipped_high_usage: list[str] = []
        skipped_locked: list[str] = []

        eligible = queue_scheduler.select_eligible_candidates(
            candidates,
            load_settings,
            on_skip=lambda candidate: skipped_high_usage.append(candidate.name),
        )

        # Gate the pull itself (started < available_capacity BEFORE
        # calling next()), not just the loop body: select_eligible_candidates
        # is a lazy generator specifically so that a candidate's settings
        # aren't loaded (load_settings has a real side effect - it creates
        # the candidate's .aicc directory) until it's actually about to be
        # considered. A `for ... : if started >= available_capacity: break`
        # would already have pulled - and side-effected - the first
        # over-capacity candidate before the break ever runs (Phase 12
        # adversarial review finding; the same class of eagerness
        # regression this laziness was originally added to prevent, see
        # commit f25b10f).
        started = 0
        while started < available_capacity:
            try:
                candidate, settings = next(eligible)
            except StopIteration:
                break

            candidate_path = candidate.resolved_path

            if not self._try_acquire_project_lock(candidate_path):
                skipped_locked.append(candidate.name)
                self.append_log(
                    f"佇列排程：「{candidate.name}」偵測到執行中鎖定，先略過。"
                )
                continue

            self.dashboard_panel.service.queue_store.remove(candidate_path)
            self.append_log("")
            self.append_log(
                f"佇列排程：容量足夠，自動開始下一個專案「{candidate.name}」。"
            )
            self._launch_worker(
                candidate_path, clear_output=True, settings=settings
            )
            started += 1

        if started == 0:
            self.append_log("")
            for line in queue_scheduler.nothing_started_reason(
                skipped_high_usage, skipped_locked
            ):
                self.append_log(line)

    def _launch_worker(
        self,
        project_path: Path,
        clear_output: bool,
        user_question: str = "",
        user_answer: str = "",
        starting_fix_attempts: int = 0,
        run_id: str = "",
        claude_session_id: str | None = None,
        codex_session_id: str | None = None,
        settings: AIExecutionSettings | None = None,
    ) -> None:
        session = self._session_for(project_path)
        if session.thread is not None:
            return

        is_focused = project_path == self.project_path

        if settings is not None:
            effective_settings = settings.normalized()
        elif session.pending_ask_result and session.pending_ask_result.get(
            "ai_settings"
        ):
            effective_settings = AIExecutionSettings.from_dict(
                session.pending_ask_result.get("ai_settings", {})
            )
        else:
            # Dormant for every call site as of Phase 6: start_workflow,
            # prompt_user_answer, and the queue scheduler all pass settings
            # explicitly (the queue scheduler loads them per-candidate via
            # AISettingsStore, not from this shared widget). If a future
            # caller ever launches a non-focused project without settings
            # and without a prior ai_settings-bearing pending_ask_result,
            # this would incorrectly fall back to the FOCUSED project's
            # panel state - not fixed here since nothing exercises it yet.
            effective_settings = (
                self.ai_settings_panel.current_settings().normalized()
            )

        if clear_output:
            session.started_at = time.monotonic()
            # A fresh run for project_path - start its log buffer over
            # rather than appending onto whatever the previous run left,
            # and clear the visible pane in sync with it if focused
            # (_append_project_log below only appends, it doesn't replace).
            session.log_lines = []
            if is_focused:
                self.output.clear()
            self._append_project_log(project_path, t("run.started"))
            self._append_project_log(project_path, f"專案：{project_path}")
        else:
            self._append_project_log(project_path, "")
            self._append_project_log(
                project_path, t("run.answer_received")
            )

        session.current_stage = t("run.initialising")
        if is_focused:
            self._set_has_result(False)

        try:
            session.thread = QThread(self)
            shutdown.adopt(session.thread)
            if is_focused:
                selected_claude, selected_codex = (
                    self.session_panel.selected_session_ids()
                )
            else:
                # session_panel is a single shared widget that always
                # reflects whichever project is currently focused - a
                # non-focused auto-launch (queue scheduler, Phase 6+) has
                # no UI-selected session to read there. Start fresh rather
                # than risk silently resuming a DIFFERENT project's
                # Claude/Codex session (explicit claude_session_id/
                # codex_session_id arguments, if given, still override
                # this below either way).
                selected_claude, selected_codex = "", ""
            is_resume = bool(user_answer or run_id)
            if effective_settings.force_new_sessions and not is_resume:
                selected_claude = ""
                selected_codex = ""

            effective_claude = (
                selected_claude
                if claude_session_id is None
                else claude_session_id
            )
            effective_codex = (
                selected_codex
                if codex_session_id is None
                else codex_session_id
            )
            worker = WorkflowWorker(
                project_path,
                user_question=user_question,
                user_answer=user_answer,
                starting_fix_attempts=starting_fix_attempts,
                claude_session_id=effective_claude,
                codex_session_id=effective_codex,
                run_id=run_id,
                settings=effective_settings,
            )
            session.worker = worker
            worker.moveToThread(session.thread)

            session.thread.started.connect(worker.run)
            # functools.partial does NOT get auto-queued across threads by
            # PySide6 (see app/core/bound_slot.py) - use BoundSlot, parented
            # to `session` (always GUI-thread-affinitized, never moved), so
            # these handlers actually run on the GUI thread instead of
            # synchronously on this worker's own background thread.
            worker.stage_changed.connect(
                BoundSlot(self.on_stage_changed, project_path, parent=session).run
            )
            worker.active_role_changed.connect(
                BoundSlot(self.on_active_role_changed, project_path, parent=session).run
            )
            worker.completed.connect(
                BoundSlot(self.on_completed, project_path, parent=session).run
            )
            worker.failed.connect(
                BoundSlot(self.on_failed, project_path, parent=session).run
            )
            worker.finished.connect(session.thread.quit)
            worker.finished.connect(worker.deleteLater)
            session.thread.finished.connect(
                BoundSlot(self.on_thread_finished, project_path, parent=session).run
            )
            session.thread.finished.connect(session.thread.deleteLater)

            session.thread.start()
            if is_focused:
                self._refresh_action_buttons()
        except Exception:
            # Without this, a failure anywhere in setup above (before
            # thread.start() actually runs) would leave session.thread set
            # to a QThread that never started and never emits finished -
            # permanently blocking all future launches for this project
            # (the "if session.thread is not None: return" guard above) and
            # leaking its already-acquired dashboard lock, since release
            # only happens in on_thread_finished.
            session.thread = None
            session.worker = None
            session.current_stage = ""
            ProjectDashboardService.release_lock(project_path)
            self._append_project_log(project_path, "")
            self._append_project_log(
                project_path, t("run.start_failed")
            )
            if is_focused:
                self._refresh_action_buttons()
            raise

    @Slot()
    def prompt_user_answer(self) -> None:
        if (
            self.pending_ask_result is None
            or self.thread is not None
            or self._answer_dialog_open
        ):
            return

        # This method is now only ever reachable via an explicit user
        # click (Stage 1 Phase 9 removed the timer-triggered auto-popup
        # that originally motivated this flag), but it's kept as cheap
        # defense-in-depth against any other double-invocation path
        # (e.g. a fast double-click) stacking a second QInputDialog on
        # top of the first while this one is still open and blocking.
        self._answer_dialog_open = True
        try:
            question = self.pending_ask_result.get(
                "review_question",
                "",
            ) or t("run.needs_decision")

            answer, accepted = QInputDialog.getMultiLineText(
                self,
                t("outcome.ASK_USER.stage"),
                question + t("run.answer_prompt"),
                "",
            )
        finally:
            self._answer_dialog_open = False

        answer = answer.strip()

        if not accepted or not answer:
            # Every one of these was a direct write onto a control: "回答問題",
            # enabled, the dismiss button visible. All of it is derivable from
            # session state, which still says the project is waiting - so
            # recompute instead. Writing controls directly and leaving the
            # state behind them alone is exactly how 94fd30a's stale stage text
            # happened: the next refresh painted over the nice message.
            self._focused_session.current_stage = t("run.waiting_for_answer")
            self._refresh_action_buttons()
            return

        # **兩個入口共用同一支**（陣容頁那一顆走 `_answer_from_lineup`）。
        self._send_user_answer(question, answer, self.pending_ask_result or {})

    @Slot(str, str)
    def _answer_from_lineup(self, role: str, answer: str) -> None:
        """他在陣容頁某一格上回話。

        老闆 2026-08-27：

        > 也要能夠讓使用者在該回覆的 node 上面有一個按鍵去回覆

        **走的是同一條路**——`prompt_user_answer` 那條。兩條路的話，
        兩邊的收尾（清掉 pending、停掉提醒計時器、帶著 run_id 續跑）
        遲早會漂，而漂掉的那一次他會看到一個永遠在等答案的專案。

        ## 講給一個沒有在等的格子

        老闆同一則也說了那要允許（「專案內容隨時可能會去更改」）。
        **而今天引擎沒有那條路**：一段話要進到一個還沒被派工的格子裡，
        得先有地方存著等它開跑，而那個地方不存在。

        所以這裡**照實說**，不假裝送出去了——
        「按了沒反應」跟「送出去了」在畫面上長得一模一樣，
        而他會等一個永遠不會來的回應。
        """
        pending = self.pending_ask_result
        if pending is None or self.thread is not None:
            self.lineup_panel.reject(t("answer.nobody_waiting"))
            return

        waiting = str(pending.get("review_asked_by", ""))
        if waiting and role and role != waiting:
            self.lineup_panel.reject(
                t("answer.not_the_waiting_seat", waiting=shown_name(waiting))
            )
            return

        question = str(
            pending.get("review_question", "")
        ) or t("run.needs_decision")
        self._send_user_answer(question, answer, pending)

    def _send_user_answer(
        self, question: str, answer: str, previous_result: dict
    ) -> None:
        """把答案送上去並續跑。**兩個入口共用這一支。**"""
        self.pending_ask_result = None
        self.stop_ask_timers()

        self.append_log("")
        self.append_log(f"使用者問題：{question}")
        self.append_log(f"使用者答案：{answer}")

        self._launch_worker(
            self.project_path,
            clear_output=False,
            user_question=question,
            user_answer=answer,
            starting_fix_attempts=int(previous_result.get("fix_attempts", 0)),
            run_id=str(previous_result.get("run_id", "")),
            claude_session_id=str(previous_result.get("claude_session_id", "")),
            codex_session_id=str(previous_result.get("codex_session_id", "")),
        )

    @Slot()
    def dismiss_pending_question(self) -> None:
        if self.pending_ask_result is None or self.thread is not None:
            return

        question = self.pending_ask_result.get(
            "review_question",
            "",
        ) or t("run.needs_decision")

        answer = QMessageBox.question(
            self,
            t("run.end_wait.title"),
            "確定要結束這次等待嗎？\n\n"
            f"問題：{question}\n\n"
            "這不會刪除任務或專案檔案，"
            "也不會繼續執行 Claude／Codex。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if answer != QMessageBox.Yes:
            return

        result = dict(self.pending_ask_result)
        result["workflow_status"] = "CANCELLED"
        result["review_status"] = "CANCELLED"
        result["resolution"] = {
            "action": "DISMISSED",
            "resolved_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }

        result_path = (
            self.project_path
            / ".aicc"
            / "workflow-result.json"
        )

        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.critical(
                self,
                t("run.end_wait.failed"),
                f"寫入結果檔失敗：\n{exc}",
            )
            return

        self.pending_ask_result = None
        self.stop_ask_timers()
        ProjectDashboardService.release_lock(self.project_path)
        self.line_notification_sent = False
        # Same reasoning as on_completed/on_failed: keep current_stage in
        # sync, so a later _refresh_action_buttons() call doesn't overwrite
        # this with stale "still running" text.
        self._focused_session.current_stage = t("run.can_rerun")
        self._refresh_action_buttons()
        # After the recompute, not before: the refresh derives 「尚未執行」 from
        # the now-cleared pending state, and this is the more specific truth.
        self.set_status(t("run.wait_ended.title"), "idle")
        self._set_has_result(True)
        self.append_log("")
        self.append_log(t("run.wait_ended.body"))
        run_id = str(result.get("run_id", ""))
        if run_id:
            timeline = TimelineStore(self.project_path)
            timeline.append(
                run_id,
                "user",
                "dismissed",
                t("run.wait_ended.body"),
            )
            timeline.finalize_run(run_id, result)
        self.session_panel.refresh_from_disk()

        settings = self.cloud_sync.load_settings()
        if settings.enabled and settings.auto_sync_after_run:
            QTimer.singleShot(250, self.sync_current_project)
        QTimer.singleShot(0, self._maybe_start_next_queued_project)

    def on_stage_changed(self, project_path: Path, stage: str) -> None:
        self._session_for(project_path).current_stage = stage
        self._append_project_log(project_path, stage)
        if project_path != self.project_path:
            return
        self._set_stage(stage)

    def on_active_role_changed(self, project_path: Path, role: str) -> None:
        """Remember which line-up seat the engine just dispatched to."""
        self._session_for(project_path).active_role = role
        if project_path != self.project_path:
            return
        self._push_shell()

    def on_completed(self, project_path: Path, result: dict) -> None:
        session = self._session_for(project_path)

        workflow_status = result.get(
            "workflow_status",
            "UNKNOWN",
        )
        review_status = result.get(
            "review_status",
            "UNKNOWN",
        )
        fix_attempts = int(result.get("fix_attempts", 0))

        session.sync_after_workflow_pending = True
        if review_status != "ASK_USER":
            session.pending_ask_result = None
            session.stop_ask_timers()
        else:
            session.pending_ask_result = result
            session.line_notification_sent = False

        self._append_project_log(project_path, "")
        self._append_project_log(project_path, t("log.workflow_result_banner"))
        self._append_project_log(
            project_path, json.dumps(result, ensure_ascii=False, indent=2)
        )

        # Resolve the final stage text once and write it into
        # session.current_stage (not just the widget below) - otherwise
        # this nice "工作流程已完成"-style message is immediately
        # overwritten by whatever on_stage_changed last set
        # current_stage to (e.g. "正在執行測試") the NEXT time anything
        # calls _refresh_action_buttons(), which happens on every focus
        # switch and button click. Without this, the status pill
        # correctly settles to idle while the stage line underneath it
        # keeps showing stale "still running" text forever - a real,
        # confusing inconsistency a live run surfaced (not just a
        # cosmetic one-off).
        # 這一次收場長什麼樣，**只有 app/core/outcomes.py 知道**。
        #
        # 這裡本來是一條一百多行的 elif 鏈，最後掛一個 `else` 把沒列到的
        # 收場一律畫成玫瑰色、旁邊印英文碼。2026-08-23 查出來的結果是：
        # 二十幾種收場裡有十種掉進那個 else（畫成紅燈，而它們都不是失敗），
        # 還有一種掉進 `review_status == "PASS"`——**章不齊的那一種，被畫成
        # 薄荷色寫著「工作流程已完成」**。
        #
        # 那條 else 的問題不是它畫錯，是**它讓「漏掉一種」跟「處理過了」長得
        # 一模一樣**，所以沒有任何一條測試變紅過。現在漏掉一種會讓
        # test_every_ending_knows_what_the_screen_should_say 變紅。
        try:
            outcome = describe_outcome(result)
        except UnknownOutcome:
            # 畫面上不炸開——但也不裝作沒事。守衛測試才是那個紅燈；使用者
            # 這一側看到的是一句誠實的「我不認得這個」，而不是一句假的完成。
            self._append_project_log(
                project_path,
                f"[BUG] 這個結局沒有人替它寫過畫面要說什麼：{workflow_status}",
            )
            status_text, status_style = t("outcome.unrecognised.title"), "partial"
            stage_text = t("outcome.unrecognised.body")
            group = ""
        else:
            status_text = outcome.label
            status_style = outcome.style
            stage_text = outcome.stage
            group = outcome.group
        # （`result["outcome_group"]` 由引擎在 `_complete` 蓋，那裡才是唯一的
        # 出口。在這裡蓋等於落地的紀錄沒有它——而 `tools/zero_callers.py` 就是
        # 為了抓這種「算出來了、沒有人讀」的值而寫的。）

        session.current_stage = stage_text
        # The run is over, so no seat is working. Cleared here rather than only
        # on the focused path, because the background project's bar is drawn
        # from this same session the moment the user switches to it.
        session.active_role = ""
        # 「這次比平常兇」. Computed here, once, because the bar re-renders every
        # second and this reads the ledger off disk. None when there is nothing
        # to say - including a project without enough history to have a
        # 「平常」 yet, which is a real state and not a quiet zero.
        run_id = str(result.get("run_id", ""))
        session.usage_spike = (
            ContextLedger(project_path).usage_spike(run_id) if run_id else None
        )

        # How it ended, remembered on the session that actually ran. set_status
        # below also writes last_outcome, but it writes it onto
        # `self._focused_session` and it is never reached for a background
        # project - so a run the user was not watching ended with
        # last_outcome = None, which is what 「從來沒跑過」 looks like, and the
        # attention strip (which reads exactly this field) never showed it.
        # Same whitelist as set_status, for the same reason: 「等待決策」 is
        # derivable from pending_ask_result and would outlive the question.
        if status_style in OUTCOME_KINDS:
            session.last_outcome = (status_text, status_style)
            session.last_group = group

        if project_path != self.project_path:
            # Background project's state is fully recorded on its own
            # RunSession above (and now logged into its own buffer too), so
            # nothing on the focused project's bar changes - but the attention
            # strip is drawn from every session, and this project just entered
            # or left it. Without this push the strip keeps showing 「執行中」
            # for a project that finished minutes ago, until something else
            # happens to repaint the shell. 「有事的時候看得到」 cannot depend
            # on the user clicking something first.
            self._push_shell()
            return

        self.set_status(status_text, status_style)
        self._set_stage(stage_text)

        self._set_has_result(True)
        self.session_panel.refresh_from_disk()

        # Whether this project is waiting on the user is `pending_ask_result`,
        # which the ASK_USER branch above has already set or cleared, and the
        # answer/dismiss controls read straight off it - so there is nothing to
        # assign here, where three separate direct writes used to have to agree.
        #
        # A new decision to make means the banner is worth showing again, even
        # if the user pushed the last one aside with 「稍後」.
        self._gate_deferred.discard(project_path)
        self._push_shell()

        if review_status != "ASK_USER" and workflow_status == "FIX_LIMIT_REACHED":
            QMessageBox.warning(
                self,
                t("run.fix_limit.title"),
                t("run.fix_limit.body"),
            )

    def on_failed(self, project_path: Path, error: str) -> None:
        self._append_project_log(project_path, "")
        self._append_project_log(project_path, error)

        # Same reasoning as on_completed: write the final stage text into
        # session.current_stage, not just the widget, so a later
        # _refresh_action_buttons() call (any focus switch, any button
        # click) doesn't overwrite it with on_stage_changed's last
        # "still running" text.
        session = self._session_for(project_path)
        session.current_stage = t("run.error.title")
        # Same as on_completed: the run is over, so no seat is working.
        session.active_role = ""
        # Same as on_completed: recorded before the focus check, because
        # set_status is below it and only ever writes the focused session.
        # A crash in a project the user had switched away from used to leave
        # no outcome at all - and 「有沒有出事」 is the one question the
        # attention strip exists to answer.
        session.last_outcome = (t("run.failed"), "error")

        if project_path != self.project_path:
            # Same as on_completed: the strip is drawn from every session, so
            # it has to be repainted when a background project ends.
            self._push_shell()
            return
        self.set_status(t("run.failed"), "error")
        self._set_stage(t("run.error.title"))

        QMessageBox.critical(
            self,
            t("run.failed.title"),
            t("run.failed.body"),
        )

    def on_thread_finished(self, project_path: Path) -> None:
        session = self._session_for(project_path)
        session.thread = None
        session.worker = None
        # The safety net for the lit seat. on_completed and on_failed clear it
        # too, but this is the one place every ending goes through - including
        # a 停止 that kills the subprocess, and any future path that ends a run
        # without either handler. A seat left lit says something is working.
        session.active_role = ""

        # Flagship Stage 1 fix: release_lock/start_ask_timers must use the
        # bound project_path (the project this finished thread actually
        # belonged to), not self.project_path read at call time - focus
        # may have moved to a different project since this thread started.
        if session.pending_ask_result is not None:
            session.start_ask_timers()
        else:
            ProjectDashboardService.release_lock(project_path)

        if session.sync_after_workflow_pending:
            session.sync_after_workflow_pending = False
            settings = self.cloud_sync.load_settings()
            if settings.enabled and settings.auto_sync_after_run:
                # _start_sync is per-project since Phase 7 - no longer
                # restricted to the focused project.
                QTimer.singleShot(
                    250, functools.partial(self._start_sync, project_path)
                )

        if project_path == self.project_path:
            self._refresh_action_buttons()
            self.session_panel.refresh_from_disk()
            # Stage 1 Phase 9: no auto-popup, even when focused - the
            # start_button already reads "回答問題" and
            # dismiss_pending_button is visible (both set by
            # _refresh_action_buttons above) as soon as pending_ask_result
            # is set. The user answers by clicking it, never automatically.

        # Only worth checking the queue when THIS project's own lock was
        # actually released (pending_ask_result is None) - otherwise
        # nothing changed about available capacity. Also avoids
        # _maybe_start_next_queued_project (itself still focus-scoped,
        # not yet aware of the bound project_path) reacting to a
        # background project's completion by switching the user's focus
        # and auto-launching an unrelated queued project (found by Phase 5's
        # adversarial review) - matches this method's pre-Stage-1 behavior.
        if session.pending_ask_result is None:
            QTimer.singleShot(0, self._maybe_start_next_queued_project)

    def start_ask_timers(self) -> None:
        self.stop_ask_timers()
        self.ask_reminder_timer.start()
        self.line_notify_timer.start()

    @staticmethod
    def decision_notification_policy() -> dict[str, int | bool]:
        """Documents the current Decision Gate notification design
        (Stage 1 Phase 9): no in-app popup, ever - visibility is via the
        Dashboard's 等待決策 column and this project's own start_button
        text, both driven continuously by session state rather than by
        timers. The only timer-driven external notification left is the
        opt-in LINE push at T+10, since the user explicitly configured
        that channel themselves (unlike an in-app popup, it doesn't
        forcibly interrupt whatever they're doing on this machine).
        """
        return {
            "popup_ever": False,
            "reminder_log_after_minutes": 5,
            "line_after_minutes": 10,
            "sound_enabled": False,
            "line_repeats": 1,
        }

    def stop_ask_timers(self) -> None:
        self.ask_reminder_timer.stop()
        self.line_notify_timer.stop()

    def raise_pending_question(self, project_path: Path) -> None:
        # Stage 1 Phase 9: the 5-minute reminder no longer raises the
        # window, steals OS focus, or auto-opens the answer dialog for
        # ANY project, focused or not - the user explicitly asked for no
        # auto-popup interrupting whatever they're doing. This is now a
        # purely passive nudge: a log line, recorded into this project's
        # own buffer regardless of focus so it's there if the user checks
        # in on it later. The Dashboard's "等待決策" row and this
        # project's own start_button ("回答問題", set continuously by
        # _refresh_action_buttons, not by this timer) are the only
        # visibility mechanisms now - answering always requires an
        # explicit click, never a timer callback.
        session = self._session_for(project_path)
        if session.pending_ask_result is None:
            return

        self._append_project_log(project_path, "")
        self._append_project_log(project_path, t("run.waiting_five_minutes"))

    def _run_line_notify_worker(self, message: str, on_completed, on_failed) -> None:
        """Build a QThread/LineNotifyWorker pair, wire it to on_completed/
        on_failed, and start it - tracked in self._line_notify_threads for
        the reason explained where that list is declared.

        Wraps the whole setup in try/except (Phase 12 adversarial review
        finding): without it, a setup-time exception here - anywhere
        between construction and thread.start() - would leave the
        (thread, worker) tuple permanently stuck in
        self._line_notify_threads, since thread.finished would never fire
        to untrack it, silently leaking the entry and dropping the
        notification with no error surfaced anywhere.
        """
        thread = QThread(self)
        shutdown.adopt(thread)
        worker = LineNotifyWorker(self.line_notifier, message)
        entry = (thread, worker)
        try:
            self._line_notify_threads.append(entry)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            # See app/core/bound_slot.py - functools.partial is not safe
            # for a cross-thread signal connection under PySide6. on_completed/
            # on_failed may themselves be functools.partial objects (from the
            # callers below) - that's fine, they're only ever *called* here
            # (inside BoundSlot.run, already dispatched onto the GUI thread),
            # never passed straight to .connect().
            worker.completed.connect(BoundSlot(on_completed, parent=self).run)
            worker.failed.connect(BoundSlot(on_failed, parent=self).run)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(
                BoundSlot(
                    self._untrack_line_notify_worker, thread, worker, parent=self
                ).run
            )
            thread.finished.connect(thread.deleteLater)
            thread.start()
        except Exception:
            if entry in self._line_notify_threads:
                self._line_notify_threads.remove(entry)
            raise

    def _untrack_line_notify_worker(
        self, thread: QThread, worker: LineNotifyWorker
    ) -> None:
        entry = (thread, worker)
        if entry in self._line_notify_threads:
            self._line_notify_threads.remove(entry)

    @staticmethod
    def _line_notification_text(project_path: Path, session) -> str:
        """The T+10 reminder, with the question withheld on KYC projects.

        LINE is an external service, and the Decision Gate question is written
        by Codex about whatever it is currently handling - on a KYC case that
        is very likely to quote case or client data. SECURITY_MODEL's contract
        for `sensitive_kyc` is that it must stay local: portable export fails
        closed and Drive sync refuses before touching a file. This path was
        sending the question text out anyway, which made the notification the
        one hole in an otherwise closed tier.

        `client` is deliberately not gated here. That tier already syncs to
        Drive under the same contract, so withholding it from LINE alone would
        be inconsistent rather than safer.

        `get_sensitivity` fails closed, so a corrupt or unrecognised
        project.json lands here as sensitive_kyc and the question is withheld.
        """
        header = (
            "【AI Control Center】\n"
            f"專案：{project_path.name}\n"
            "狀態：等待使用者決策已超過 10 分鐘\n"
        )
        if ProjectRegistry.get_sensitivity(project_path) == "sensitive_kyc":
            # The project name is already on the Drive-blocked side of the
            # line, but it is what makes the message actionable, and the user
            # chose it. The generated question is what has to stay in.
            return header + t("notify.redacted_question")

        question = session.pending_ask_result.get(
            "review_question",
            "",
        ) or t("run.needs_decision")
        return header + f"問題：{question}"

    def send_pending_line_notification(self, project_path: Path) -> None:
        session = self._session_for(project_path)
        if session.pending_ask_result is None or session.line_notification_sent:
            return

        # Bind the specific question's run_id so a stale completion
        # arriving after the user has already answered this question AND
        # a new one has since arisen (both within the ~20s send_push
        # round trip - narrow, but real: Phase 12 adversarial review
        # finding) can be recognized as stale and ignored, instead of
        # incorrectly marking the NEW question's notification as already
        # sent.
        run_id = str(session.pending_ask_result.get("run_id", ""))

        message = self._line_notification_text(project_path, session)

        # Stage 1 Phase 10: send_push is a blocking network call (up to a
        # 20s timeout) - run it off the GUI thread so one project's T+10
        # notification can't freeze the whole window (or stack up behind
        # another project's own in-flight notification).
        self._run_line_notify_worker(
            message,
            functools.partial(
                self._on_line_notify_completed, project_path, run_id
            ),
            functools.partial(self._on_line_notify_failed, project_path),
        )

    def _on_line_notify_completed(
        self, project_path: Path, run_id: str, result: LineSendResult
    ) -> None:
        session = self._session_for(project_path)

        current_run_id = str((session.pending_ask_result or {}).get("run_id", ""))
        if current_run_id != run_id:
            # This completion is about a question that's since been
            # answered and superseded by a different one on the same
            # project - applying it now would wrongly mark the NEW
            # question's notification as already sent.
            return

        session.line_notification_sent = result.success

        self._append_project_log(project_path, "")
        self._append_project_log(project_path, result.message)
        if not result.success:
            session.current_stage = t("notify.line_failed_but_waiting")
            if project_path == self.project_path:
                self._set_stage(t("notify.line_failed_but_waiting"))

    def _on_line_notify_failed(self, project_path: Path, error: str) -> None:
        self._append_project_log(project_path, "")
        self._append_project_log(
            project_path, f"LINE 通知發送時發生未預期錯誤：\n{error}"
        )
        self._session_for(project_path).current_stage = t("notify.line_failed_but_waiting")
        if project_path == self.project_path:
            self._set_stage(t("notify.line_failed_but_waiting"))

    @Slot()
    def configure_line(self) -> None:
        # 已經設定過的話，先問要改還是要移除。**在這之前只有設定，沒有解除**：
        # `LineNotifier.clear_credentials()` 一直都在，只是沒有任何地方呼叫它。
        #
        # 少了這條路的後果不是不方便而已——換掉 token 之後舊的那把還留在這台
        # 機器上，而 AICC 會拿著它每十分鐘失敗一次；不想再用 LINE 的人也沒有
        # 辦法把憑證從應用程式裡拿掉。**一個存得進去、拿不出來的憑證，
        # 是使用者對這個 app 少一分信任的地方。**
        if self.line_notifier.is_configured():
            choice = QMessageBox.question(
                self,
                t("line.setup.title"),
                t("line.already_configured"),
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if choice == QMessageBox.Cancel:
                return
            if choice == QMessageBox.Discard:
                self._forget_line_credentials()
                return

        token, accepted = QInputDialog.getText(
            self,
            t("line.setup.title"),
            "Channel Access Token：",
            QLineEdit.Password,
            "",
        )

        if not accepted:
            return

        token = token.strip()
        if not token:
            QMessageBox.warning(
                self,
                t("line.setup.title"),
                t("line.token_required"),
            )
            return

        user_id, accepted = QInputDialog.getText(
            self,
            t("line.setup.title"),
            t("line.user_id_label"),
        )

        if not accepted:
            return

        user_id = user_id.strip()
        if not user_id:
            QMessageBox.warning(
                self,
                t("line.setup.title"),
                t("line.user_id_required"),
            )
            return

        try:
            self.line_notifier.save_credentials(token, user_id)
        except Exception as exc:
            QMessageBox.critical(
                self,
                t("line.setup.failed"),
                str(exc),
            )
            return

        # Stage 1 Phase 10: send_push blocks for up to 20s - run the test
        # send off the GUI thread too, matching
        # send_pending_line_notification's reasoning.
        self._run_line_notify_worker(
            t("line.test_message"),
            self._on_line_test_send_completed,
            self._on_line_test_send_failed,
        )

    def _forget_line_credentials(self) -> None:
        """把憑證從這台機器上刪掉，並且說清楚刪掉的是什麼。

        講明「只刪本機這一份」很重要：**AICC 沒有能力、也不該有能力去撤銷
        LINE 那邊的 token**。讓人以為按了這裡就等於註銷，會讓一把還有效的
        鑰匙留在外面而當事人以為處理完了。
        """
        try:
            self.line_notifier.clear_credentials()
        except OSError as error:
            QMessageBox.critical(self, t("line.remove.failed"), str(error))
            return
        self.settings_web_panel.push_state()
        QMessageBox.information(
            self,
            t("line.remove.done_title"),
            t("line.remove.done_body"),
        )

    def _on_line_test_send_completed(self, result: LineSendResult) -> None:
        if result.success:
            QMessageBox.information(
                self,
                t("line.setup.done_title"),
                t("line.setup.done_body"),
            )
        else:
            QMessageBox.warning(
                self,
                t("line.test_failed"),
                result.message,
            )

    def _on_line_test_send_failed(self, error: str) -> None:
        QMessageBox.warning(
            self,
            t("line.test_failed"),
            f"發送測試訊息時發生未預期錯誤：\n{error}",
        )

    @Slot()
    def update_elapsed_time(self) -> None:
        """One-second tick: the run clock, and the decision banner's wait time.

        Repaints only the execution bar. The sidebar has nothing that changes
        with time, and re-rendering it once a second would rebuild its DOM
        sixty times a minute for no visible difference.
        """
        if self.started_at is not None:
            elapsed = int(time.monotonic() - self.started_at)
            minutes, seconds = divmod(elapsed, 60)
            self._elapsed_text = f"{minutes:02d}:{seconds:02d}"
        if getattr(self, "shell_top", None) is not None:
            self.shell_top.push_state()

    def set_status(self, text: str, kind: str) -> None:
        """Set the run status shown in the shell's execution bar.

        `kind` is the shell's own vocabulary - idle / waiting / running /
        gated / partial / done / error - and it decides the colour of the dot
        beside the text. `done` is mint and exists because a finished run and a
        run that never started were both showing the grey idle dot, which is the
        one distinction the user most wants to read at a glance.
        It used to be a QSS object name on a QLabel, and those had stopped
        matching the words next to them: five different failures
        (「Claude 執行失敗」, 「Codex 回覆無效」, 「執行失敗」, 「無法判定結果」,
        「已達修正上限」) were all raised as `statusAsk`, which is the
        waiting-for-you style. On the old label that was a subtle mis-tint; in
        the shell it is a gold 「等你決策」 dot on a run that failed and is
        waiting for nobody.

        **There are two golds, and the difference is whether anything is
        waiting.** `gated` means AICC is blocked: answer it and the run
        continues. `partial` means the run ended - nothing resumes - but the
        outcome is "half done, look at it" rather than a failure. Reusing
        `gated` for a terminal outcome is the bug described above; that is why
        `partial` is its own kind rather than a colour reused from `gated`.
        """
        if kind not in (
            "idle",
            "waiting",
            "running",
            "gated",
            "partial",
            "done",
            "error",
        ):
            kind = "idle"
        self._status_text = text
        self._status_kind = kind
        # An outcome is remembered on the session so the next refresh can
        # recompute it; a transient state (running, or waiting for an answer)
        # is not, because both are already derivable from the session and
        # storing them would outlive the thing they describe.
        #
        # `partial` belongs here for the same reason `done` and `error` do: it
        # is an outcome. Leaving it out would let _refresh_action_buttons paint
        # 「尚未執行」 over it on the next focus switch - the exact erasure the
        # comment at that call site is about.
        if kind in OUTCOME_KINDS:
            self._focused_session.last_outcome = (text, kind)
        elif kind == "running":
            self._focused_session.last_outcome = None
        self._push_shell()

    def append_log(self, text: str) -> None:
        """Append to the FOCUSED project's log.

        For a handler bound to a specific project_path (which may not be
        the focused one - most workflow/sync/LINE-notify callbacks), use
        _append_project_log instead. This convenience form is for actions
        that only ever concern whichever project is currently on screen
        (settings changes, add/remove-project outcomes, etc.), where
        self.project_path IS the right project by construction.
        """
        self._append_project_log(self.project_path, text)

    def _append_project_log(self, project_path: Path, text: str) -> None:
        # Recorded on the BOUND project's own session regardless of UI
        # focus (see RunSession.log_lines) - only mirrored into the
        # visible widget when that project happens to be the focused one.
        self._session_for(project_path).append_log(text)
        if project_path == self.project_path:
            self.output.appendPlainText(text)

    @Slot()
    def _refresh_bound_sessions(self) -> None:
        self.session_panel.refresh_from_disk()

    @Slot()
    def show_result_file(self) -> None:
        result_path = (
            self.project_path / ".aicc" / "workflow-result.json"
        )

        if not result_path.exists():
            QMessageBox.information(
                self,
                t("result.none_yet.title"),
                t("result.none_yet.body"),
            )
            return

        # The result is written into self.output, which lives on the 即時紀錄
        # page - so that is the page to bring forward. This was
        # `setCurrentIndex(1)`, which has pointed at some other page ever since
        # it was written (今天是儀表板): the user got a page switch that was not
        # the one holding what they had just asked to see.
        self._show_page("live")
        self.output.setPlainText(
            result_path.read_text(encoding="utf-8")
        )

    def closeEvent(self, event) -> None:
        # Phase 8: must check every session, not just the focused
        # project's - closing the window while a background project's
        # workflow/sync is still running would otherwise skip this
        # warning entirely and let Qt tear down its still-running QThread
        # (parented to this MainWindow) with no confirmation, orphaning
        # its lock and losing in-flight work.
        if any(
            session.is_active() for session in self.run_sessions.values()
        ):
            answer = QMessageBox.question(
                self,
                t("close.busy.title"),
                t("close.busy.body"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )

            if answer == QMessageBox.No:
                event.ignore()
                return

            # User confirmed force-close: best-effort kill every in-flight
            # claude/codex CLI subprocess so it doesn't keep running (and
            # burning API/token cost) as an orphan with no window left to
            # observe or stop it. Can't do anything equivalent for Google
            # Drive sync - that's plain Python file I/O on the QThread, not
            # an external process CliRunner.terminate_current() can reach.
            for session in self.run_sessions.values():
                worker = session.worker
                if worker is None:
                    continue
                orchestrator = worker.orchestrator
                if orchestrator is None:
                    continue
                orchestrator.runner.terminate_current()

        self._save_project_registry()
        event.accept()


def create_application() -> QApplication:
    app = QApplication.instance()

    if app is None:
        app = QApplication([])

    app.setApplicationName("AI Control Center")
    app.setOrganizationName("Sean")
    # Sets the icon on every window and dialog. The .exe's own icon is baked in
    # by PyInstaller and covers Explorer and the taskbar's pinned entry, but
    # without this the running window still shows Qt's default.
    icon_path = _icon_path()
    if icon_path is not None:
        app.setWindowIcon(QIcon(str(icon_path)))
    return app


def _icon_path() -> Path | None:
    """packaging/aicc.ico, in both a source tree and a frozen build."""
    base = getattr(sys, "_MEIPASS", None)
    candidate = (
        Path(base) / "aicc.ico"
        if base
        else Path(__file__).resolve().parents[2] / "packaging" / "aicc.ico"
    )
    return candidate if candidate.is_file() else None
