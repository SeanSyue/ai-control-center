from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from app.services.ai_settings import AIExecutionSettings, AISettingsStore
from app.services.cloud_sync import CloudProjectInfo, CloudSyncSettings, SyncResult
from app.services.line_notifier import LineNotifier, LineSendResult
from app.services.project_dashboard import ProjectDashboardService
from app.services.project_registry import ProjectEntry, ProjectRegistry
from app.services.timeline_store import TimelineStore
from app.services.workspace_portability import WorkspaceImportResult
from app.ui.main_window import MainWindow
from tests.fakes import (
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)


def _drain_web_engine_teardown(application) -> None:
    """Give Chromium the event loop it needs to finish tearing down a
    MainWindow that was actually shown, instead of letting it take that
    time out of whichever test happens to run next.

    Showing a MainWindow is what makes QtWebEngine build a real GPU
    surface for the seven web panels; QT_QPA_PLATFORM=offscreen does not
    prevent that. When such a window goes away Chromium loses the GL
    context and restarts its GPU process, and that restart is serviced
    inside the Qt event loop - one single processEvents() call blocks the
    GUI thread for about five seconds. Confirmed with
    faulthandler.dump_traceback_later while the freeze was happening: the
    innermost Python frame is processEvents() itself, so no code in app/
    is doing the blocking.

    The restart needs wall-clock time to elapse, not merely event-loop
    passes - 200 back-to-back processEvents() calls do not absorb it,
    100 passes 5ms apart do. Draining here is not a delay added to make
    something pass: the five seconds get spent either way, and this only
    decides whether they are spent by the test that caused them or
    charged at random to a later one. Measured: with this drain the worst
    single processEvents() call in a later test is 0.002s; without it,
    4.97s.
    """
    for _ in range(100):
        application.processEvents()
        time.sleep(0.005)


def _make_task_project(path: Path) -> Path:
    (path / ".aicc").mkdir(parents=True, exist_ok=True)
    (path / ".aicc" / "task.md").write_text(
        "# Task\n\nDo the thing.\n", encoding="utf-8"
    )
    ProjectRegistry.initialize_project(path)
    declare_verify_command(path)
    return path


def _development_settings(**overrides) -> AIExecutionSettings:
    base = AIExecutionSettings(
        workflow_mode="development",
        auto_memory_maintenance=False,
        auto_session_bootstrap=False,
        max_fix_attempts=1,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _happy_path_script() -> list:
    return [
        cli_result('{"result": "done, no questions"}'),
        cli_result("1 passed"),
        cli_result(
            '{"status": "PASS", "summary": "ok", '
            '"issues": [], "instructions": [], "question": ""}'
        ),
    ]


def test_main_window_constructs_without_raising(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    window = MainWindow(project)

    assert window.project_path == project.resolve()
    assert window._shell_state()["shell"]["projects"]
    assert window.dashboard_panel is not None
    assert window.session_panel is not None
    assert window.knowledge_panel is not None
    assert window.roadmap_panel is not None
    assert window.lineup_panel is not None
    assert window.thread is None
    assert window.sync_thread is None

    window.deleteLater()
    application.processEvents()


def test_main_window_project_sensitivity_combo_reflects_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    ProjectRegistry.set_sensitivity(project, "client")

    window = MainWindow(project)

    assert (
        window._shell_state()["shell"]["project"]["sensitivity"] == "client"
    )

    window.deleteLater()
    application.processEvents()


def test_select_project_from_dashboard_switches_without_raising(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Regression test: _select_project_from_dashboard used to reference
    # undefined locals (task_exists/result_path) and raised NameError on
    # every call. It now just forwards to the combo box, which drives
    # switch_project via currentIndexChanged.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = tmp_path / "project_a"
    project_a.mkdir()
    ProjectRegistry.initialize_project(project_a)

    project_b = tmp_path / "project_b"
    project_b.mkdir()
    ProjectRegistry.initialize_project(project_b)

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window._refresh_project_list()

    window._select_project_from_dashboard(str(project_b.resolve()))

    assert window.project_path == project_b.resolve()

    window.deleteLater()
    application.processEvents()


def test_prompt_user_answer_blocks_reentrant_dialog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Regression test: the 5-minute ask_reminder_timer -> raise_pending_question
    # -> prompt_user_answer chain could fire again while the modal QInputDialog
    # from an earlier call was still open (pending_ask_result/self.thread don't
    # change while the dialog blocks), stacking a second dialog on top.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    window = MainWindow(project)
    window.pending_ask_result = {
        "review_question": "A or B?",
        "fix_attempts": 0,
        "run_id": "run-1",
        "claude_session_id": "",
        "codex_session_id": "",
    }

    call_count = 0

    def fake_get_multiline_text(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Simulate the reminder timer firing again while this dialog's
        # nested event loop is still pumping events.
        window.prompt_user_answer()
        return "", False

    monkeypatch.setattr(
        "app.ui.main_window.QInputDialog.getMultiLineText",
        fake_get_multiline_text,
    )

    window.prompt_user_answer()

    assert call_count == 1
    assert window._answer_dialog_open is False

    window.deleteLater()
    application.processEvents()


def _stale_pending_ask_result() -> dict:
    return {
        "review_question": "A's question",
        "run_id": "run-a",
        "claude_session_id": "sess-a",
        "codex_session_id": "",
        "fix_attempts": 0,
    }


def test_import_workspace_resets_stale_pending_ask_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Regression test: import_workspace used to reassign self.project_path
    # with no guard and no pending_ask_result reset. If project A had an
    # unanswered Decision Gate question, its run_id/session ids would
    # survive under the new project_path B, so answering it would resume
    # A's Claude/Codex session against B's working directory.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = tmp_path / "project_a"
    project_a.mkdir()
    ProjectRegistry.initialize_project(project_a)

    window = MainWindow(project_a)
    window.pending_ask_result = _stale_pending_ask_result()

    imported_path = tmp_path / "project_b"
    imported_path.mkdir()
    ProjectRegistry.initialize_project(imported_path)

    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(tmp_path / "export.zip"), ""),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(tmp_path),
    )
    monkeypatch.setattr(
        window.portability,
        "import_project",
        lambda *a, **k: WorkspaceImportResult(
            success=True,
            project_path=str(imported_path),
        ),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )

    window.import_workspace()

    assert window.project_path == imported_path.resolve()
    assert window.pending_ask_result is None

    window.deleteLater()
    application.processEvents()


def test_create_project_from_conversation_pair_resets_stale_pending_ask_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = tmp_path / "project_a"
    project_a.mkdir()
    ProjectRegistry.initialize_project(project_a)

    window = MainWindow(project_a)
    window.pending_ask_result = _stale_pending_ask_result()

    parent_dir = tmp_path / "parent"
    parent_dir.mkdir()

    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(parent_dir),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QInputDialog.getText",
        lambda *a, **k: ("Paired Project", True),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )

    window.create_project_from_conversation_pair("Paired Project")

    expected_target = (parent_dir / "Paired Project").resolve()
    assert window.project_path == expected_target
    assert window.pending_ask_result is None

    window.deleteLater()
    application.processEvents()


def test_link_cloud_project_resets_stale_pending_ask_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = tmp_path / "project_a"
    project_a.mkdir()
    ProjectRegistry.initialize_project(project_a)

    window = MainWindow(project_a)
    window.pending_ask_result = _stale_pending_ask_result()

    local_folder = tmp_path / "project_b"
    local_folder.mkdir()
    ProjectRegistry.initialize_project(local_folder)

    cloud_project = CloudProjectInfo(
        project_id="cloud-1",
        name="Cloud Project B",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    monkeypatch.setattr(
        window.cloud_sync,
        "load_settings",
        lambda: CloudSyncSettings(enabled=True, root_path=str(tmp_path)),
    )
    monkeypatch.setattr(
        window.cloud_sync,
        "list_cloud_projects",
        lambda: [cloud_project],
    )
    monkeypatch.setattr(
        window.cloud_sync,
        "link_cloud_project",
        lambda project_id, local_project: SyncResult(
            success=True,
            project_id=project_id,
            project_name="Cloud Project B",
        ),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QInputDialog.getItem",
        lambda *a, **k: ("Cloud Project B｜cloud-1", True),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(local_folder),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.warning",
        lambda *a, **k: None,
    )

    window.link_cloud_project()

    assert window.project_path == local_folder.resolve()
    assert window.pending_ask_result is None

    window.deleteLater()
    application.processEvents()


def test_singleton_properties_default_to_idle_with_zero_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 4: MainWindow.thread/pending_ask_result/etc. are now
    # properties backed by a per-project RunSession dict rather than plain
    # instance attributes. A freshly constructed window has zero sessions -
    # every property must still report the same idle defaults the old
    # plain attributes had.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)

    window = MainWindow(project)

    assert window.run_sessions == {} or set(window.run_sessions) <= {
        window.project_path
    }
    assert window.thread is None
    assert window.worker is None
    assert window.started_at is None
    assert window.pending_ask_result is None
    assert window.line_notification_sent is False
    assert window.sync_thread is None
    assert window.sync_worker is None
    assert window.sync_after_workflow_pending is False

    window.deleteLater()
    application.processEvents()


def test_singleton_properties_are_isolated_per_focused_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Two sessions, focus pointed at each in turn: writing through the
    # property while project A is focused must not be visible while B is
    # focused, and must still be there when focus returns to A.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = tmp_path / "project_a"
    project_a.mkdir()
    ProjectRegistry.initialize_project(project_a)

    project_b = tmp_path / "project_b"
    project_b.mkdir()
    ProjectRegistry.initialize_project(project_b)

    window = MainWindow(project_a)

    sentinel_a = {"review_question": "A's question"}
    window.pending_ask_result = sentinel_a
    window.line_notification_sent = True

    # Bypass the UI-level switch_project guard/reset logic here on purpose -
    # this test targets the property shim's own scoping in isolation, not
    # switch_project's behavior (covered separately).
    window.project_path = project_b.resolve()

    assert window.pending_ask_result is None
    assert window.line_notification_sent is False

    window.pending_ask_result = {"review_question": "B's question"}

    window.project_path = project_a.resolve()

    assert window.pending_ask_result == sentinel_a
    assert window.line_notification_sent is True
    assert len(window.run_sessions) == 2

    window.deleteLater()
    application.processEvents()


def test_ask_reminder_timer_is_isolated_per_focused_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = tmp_path / "project_a"
    project_a.mkdir()
    ProjectRegistry.initialize_project(project_a)

    project_b = tmp_path / "project_b"
    project_b.mkdir()
    ProjectRegistry.initialize_project(project_b)

    window = MainWindow(project_a)
    window.start_ask_timers()
    assert window.ask_reminder_timer.isActive() is True

    window.project_path = project_b.resolve()

    assert window.ask_reminder_timer.isActive() is False

    window.deleteLater()
    application.processEvents()


def test_on_thread_finished_releases_lock_for_bound_project_not_focus(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Flagship Stage 1 Phase 5 regression test: on_thread_finished used to
    # release ProjectDashboardService's lock for self.project_path (read at
    # call time), not the project the finished thread actually belonged to.
    # If focus had moved to a different project by the time a background
    # thread finished, this released the WRONG project's lock. Drive the
    # handler directly with an explicit bound project_path (as the real
    # functools.partial-wired connection does) while a DIFFERENT project is
    # focused, and assert only the bound project's lock is released.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    ProjectDashboardService.acquire_lock(project_a.resolve(), "run-a")
    ProjectDashboardService.acquire_lock(project_b.resolve(), "run-b")

    # Focus stays on A; the finished thread belonged to B.
    assert window.project_path == project_a.resolve()
    window.on_thread_finished(project_b.resolve())

    assert not ProjectDashboardService.lock_path(project_b.resolve()).exists()
    assert ProjectDashboardService.lock_path(project_a.resolve()).exists()

    window.deleteLater()
    application.processEvents()


def test_on_thread_finished_does_not_touch_focused_widgets_for_other_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    window._status_text = "尚未執行"
    window._stage_text = "等待開始"

    window.on_thread_finished(project_b.resolve())

    # B finishing must not flip A's (the focused project's) visible
    # widgets - the old code, reading self.project_path at call time,
    # would have re-enabled/relabelled these regardless of which project
    # actually finished.
    assert window._stage_text == "等待開始"

    window.deleteLater()
    application.processEvents()


def test_two_concurrent_workers_complete_independently_via_real_qthreads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # End-to-end proof that _launch_worker's BoundSlot-bound signal
    # connections (see app/core/bound_slot.py) carry the right project
    # identity through real cross-thread Qt signal delivery, not just in a
    # synchronous/direct-call test. Two projects are launched directly
    # through _launch_worker (bypassing start_workflow's still-focus-only UI
    # guard, which is unchanged until Phase 8) with B never focused, and
    # both must reach COMPLETED independently with their own correct
    # results.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    runner_class = scripted_cli_runner_class(
        _happy_path_script() + _happy_path_script()
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    settings = _development_settings()

    window._launch_worker(project_a.resolve(), clear_output=True, settings=settings)
    window._launch_worker(project_b.resolve(), clear_output=True, settings=settings)

    session_a = window.run_sessions[project_a.resolve()]
    session_b = window.run_sessions[project_b.resolve()]
    assert session_a.thread is not None
    assert session_b.thread is not None

    # Poll the actual postcondition (on_thread_finished has run and
    # cleared session.thread) rather than the raw thread.finished signal:
    # thread.finished fires before that queued-connection slot necessarily
    # runs, so waiting on the signal alone doesn't guarantee the handler
    # has already executed by the time this loop would exit.
    deadline = time.monotonic() + 15
    while session_a.thread is not None or session_b.thread is not None:
        assert time.monotonic() < deadline, (
            f"timed out waiting for both workers to finish "
            f"(a_thread={session_a.thread}, b_thread={session_b.thread})"
        )
        application.processEvents()
        time.sleep(0.01)
    assert session_a.pending_ask_result is None
    assert session_b.pending_ask_result is None

    window.deleteLater()
    application.processEvents()


def test_launch_worker_handlers_run_on_gui_thread_not_worker_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # This is the actual production bug the manual live test (Phase 12)
    # found: functools.partial-bound cross-thread signal connections are
    # NOT auto-queued by PySide6 (verified against 6.11.1 in
    # tests/test_bound_slot.py) - they dispatch synchronously on the
    # EMITTING (worker) thread instead of the GUI thread, which crashed the
    # real app with "QObject: Cannot create children for a parent that is
    # in a different thread" the moment a handler touched a real widget.
    # Every automated test up to this point only ever asserted resulting
    # state values, never which thread produced them, so this class of bug
    # slipped through 290+ passing tests undetected. This test closes that
    # gap at the real _launch_worker wiring, not just BoundSlot in
    # isolation.
    import threading

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    main_thread_id = threading.get_ident()

    project_a = _make_task_project(tmp_path / "project_a")

    runner_class = scripted_cli_runner_class(_happy_path_script())
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    window = MainWindow(project_a)

    observed_thread_ids: dict[str, int] = {}

    real_on_stage_changed = window.on_stage_changed
    real_on_completed = window.on_completed
    real_on_thread_finished = window.on_thread_finished

    def spy_on_stage_changed(project_path, stage):
        observed_thread_ids.setdefault(
            "on_stage_changed", threading.get_ident()
        )
        return real_on_stage_changed(project_path, stage)

    def spy_on_completed(project_path, result):
        observed_thread_ids.setdefault("on_completed", threading.get_ident())
        return real_on_completed(project_path, result)

    def spy_on_thread_finished(project_path):
        observed_thread_ids.setdefault(
            "on_thread_finished", threading.get_ident()
        )
        return real_on_thread_finished(project_path)

    monkeypatch.setattr(window, "on_stage_changed", spy_on_stage_changed)
    monkeypatch.setattr(window, "on_completed", spy_on_completed)
    monkeypatch.setattr(window, "on_thread_finished", spy_on_thread_finished)

    window._launch_worker(
        project_a.resolve(), clear_output=True, settings=_development_settings()
    )

    session = window.run_sessions[project_a.resolve()]
    deadline = time.monotonic() + 15
    while session.thread is not None:
        assert time.monotonic() < deadline, "timed out waiting for worker"
        application.processEvents()
        time.sleep(0.01)

    assert observed_thread_ids, "no handler ever ran - test setup is broken"
    for name, thread_id in observed_thread_ids.items():
        assert thread_id == main_thread_id, (
            f"{name} ran on thread {thread_id}, expected the GUI thread "
            f"({main_thread_id}) - a background-thread widget touch here "
            f"is exactly what crashed the real app."
        )

    window.deleteLater()
    application.processEvents()


def test_switch_project_preserves_old_projects_pending_ask_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Regression test for a bug found by Phase 5's own adversarial review:
    # switch_project used to clear self.pending_ask_result/self.stop_ask_timers()
    # BEFORE reassigning self.project_path, so those focus-scoped properties
    # targeted the OLD (about-to-be-abandoned) project via _focused_session -
    # wiping its Decision Gate state and orphaning its workflow lock, instead
    # of protecting anything. RunSession already isolates state per-project
    # since Phase 4/5, so switching focus away must leave the old project's
    # own state untouched.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window._refresh_project_list()

    sentinel = {"review_question": "A's question", "run_id": "run-a"}
    window.pending_ask_result = sentinel
    window.start_ask_timers()
    ProjectDashboardService.acquire_lock(project_a.resolve(), "run-a")

    window.switch_to_project(str(project_b.resolve()))

    assert window.project_path == project_b.resolve()

    session_a = window.run_sessions[project_a.resolve()]
    assert session_a.pending_ask_result == sentinel
    assert session_a.ask_reminder_timer.isActive() is True
    assert ProjectDashboardService.lock_path(project_a.resolve()).exists()

    window.deleteLater()
    application.processEvents()


def test_import_workspace_preserves_old_projects_pending_ask_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Same bug as switch_project's, for one of the other three sites that
    # route through _focus_project - import_workspace is representative of
    # create_project_from_conversation_pair/link_cloud_project too, which
    # share the identical fix.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")

    window = MainWindow(project_a)
    sentinel = {"review_question": "A's question", "run_id": "run-a"}
    window.pending_ask_result = sentinel
    window.start_ask_timers()
    ProjectDashboardService.acquire_lock(project_a.resolve(), "run-a")

    imported_path = tmp_path / "project_b"
    imported_path.mkdir()
    ProjectRegistry.initialize_project(imported_path)

    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(tmp_path / "export.zip"), ""),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(tmp_path),
    )
    monkeypatch.setattr(
        window.portability,
        "import_project",
        lambda *a, **k: WorkspaceImportResult(
            success=True,
            project_path=str(imported_path),
        ),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.information",
        lambda *a, **k: None,
    )

    window.import_workspace()

    assert window.project_path == imported_path.resolve()

    session_a = window.run_sessions[project_a.resolve()]
    assert session_a.pending_ask_result == sentinel
    assert session_a.ask_reminder_timer.isActive() is True
    assert ProjectDashboardService.lock_path(project_a.resolve()).exists()

    window.deleteLater()
    application.processEvents()


def test_on_thread_finished_skips_queue_check_when_bound_project_awaits_answer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Regression test for a bug found by Phase 5's own adversarial review:
    # the QTimer.singleShot(0, self._maybe_start_next_queued_project) call
    # at the end of on_thread_finished used to fire unconditionally, even
    # when the just-finished (possibly non-focused) project still has an
    # unanswered question and its lock was deliberately NOT released.
    # _maybe_start_next_queued_project is itself still focus-scoped, so an
    # unrelated background project entering ASK_USER could silently steal
    # UI focus and auto-launch a different queued project.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    call_count = 0

    def fake_maybe_start() -> None:
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(
        window, "_maybe_start_next_queued_project", fake_maybe_start
    )

    session_b = window._session_for(project_b.resolve())
    session_b.pending_ask_result = {"review_question": "B's question"}

    window.on_thread_finished(project_b.resolve())
    application.processEvents()

    assert call_count == 0

    window.deleteLater()
    application.processEvents()


def test_on_thread_finished_still_checks_queue_when_no_pending_answer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Positive-case companion to the test above: the queue check must
    # still fire normally once the finished project has no pending ask
    # (matching pre-Stage-1 behavior for the ordinary completion path).
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")

    window = MainWindow(project_a)

    call_count = 0

    def fake_maybe_start() -> None:
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(
        window, "_maybe_start_next_queued_project", fake_maybe_start
    )

    window.on_thread_finished(project_a.resolve())
    application.processEvents()

    assert call_count == 1

    window.deleteLater()
    application.processEvents()


def test_launch_worker_recovers_session_state_when_setup_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Regression test for a gap found by Phase 5's own adversarial review:
    # a failure anywhere in _launch_worker's setup (between constructing
    # session.thread and actually calling session.thread.start()) used to
    # leave session.thread set to a QThread that never started and never
    # emits finished - permanently blocking all future launches for that
    # project (the "if session.thread is not None: return" guard) and
    # leaking its already-acquired dashboard lock, since release only
    # happens in on_thread_finished.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")

    window = MainWindow(project_a)
    ProjectDashboardService.acquire_lock(project_a.resolve(), "run-a")

    def raising_worker(*args, **kwargs):
        raise RuntimeError("simulated WorkflowWorker construction failure")

    monkeypatch.setattr("app.ui.main_window.WorkflowWorker", raising_worker)

    with pytest.raises(RuntimeError):
        window._launch_worker(
            project_a.resolve(),
            clear_output=True,
            settings=_development_settings(),
        )

    session = window.run_sessions[project_a.resolve()]
    assert session.thread is None
    assert session.worker is None
    assert not ProjectDashboardService.lock_path(project_a.resolve()).exists()
    assert "run" not in window._actions_blocked()

    # The guard must not be permanently stuck - a fresh launch attempt
    # (using the real WorkflowWorker again) must be able to proceed.
    monkeypatch.undo()
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    runner_class = scripted_cli_runner_class(_happy_path_script())
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
    window._launch_worker(
        project_a.resolve(),
        clear_output=True,
        settings=_development_settings(),
    )
    assert session.thread is not None

    window.deleteLater()
    application.processEvents()


def _wait_until_idle(application, session, timeout_seconds: float = 15) -> None:
    deadline = time.monotonic() + timeout_seconds
    while session.thread is not None:
        assert time.monotonic() < deadline, "timed out waiting for worker to finish"
        application.processEvents()
        time.sleep(0.01)


def test_maybe_start_next_queued_project_launches_without_switching_focus(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 6: the queue scheduler used to pick its launch target
    # by calling project_selector.setCurrentIndex(...) - hijacking the
    # user's UI focus as a side channel - and only proceeded if that
    # switch happened to land on the candidate. It must now launch the
    # candidate directly by path, leaving focus untouched.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    runner_class = scripted_cli_runner_class(_happy_path_script())
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window.dashboard_panel.service.queue_store.enqueue(project_b)

    candidate = ProjectEntry(name="project_b", path=str(project_b))
    monkeypatch.setattr(
        "app.ui.main_window.queue_scheduler.select_eligible_candidates",
        # Phase 12 fix pulls via next(eligible) directly (not a `for`
        # loop), matching select_eligible_candidates' real Iterator
        # contract - the mock must return an actual iterator, not a
        # plain list, or next() raises TypeError.
        lambda candidates, load_settings, **_: iter(
            [(candidate, _development_settings())]
        ),
    )

    window._maybe_start_next_queued_project()

    assert window.project_path == project_a.resolve()
    session_b = window.run_sessions[project_b.resolve()]
    assert session_b.thread is not None

    _wait_until_idle(application, session_b)

    window.deleteLater()
    application.processEvents()


def test_maybe_start_next_queued_project_blocked_when_at_concurrency_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 11: capacity is now judged by concurrency_limit vs.
    # running_projects (on-disk TimelineStore state), not by "does any
    # in-memory RunSession happen to be active" - that Phase 6-era
    # placeholder guard is gone precisely because it could never allow
    # more than one project running at a time, defeating the whole point
    # of this phase. Default concurrency_limit is 1, so one genuinely
    # RUNNING project (a real timeline entry, not an in-memory sentinel -
    # running_projects is computed from disk) must still block a second.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_c = _make_task_project(tmp_path / "project_c")
    project_b = _make_task_project(tmp_path / "project_b")
    running_c = TimelineStore(project_c).create_run("執行中任務")
    # **鎖也要拿。**2026-08-27 之前這裡只寫 `create_run()`，而那只是在
    # `run.json` 裡留下一句 `RUNNING` 的宣稱。首頁當時直接信那句話——
    # 而硬碟上有六筆卡在那句話裡六天，佇列因此永遠不排它們。
    # 現在宣稱要有鎖背書才算「真的在跑」（`is_really_running`），
    # 而**這一條要測的是併發上限**，所以這裡要佈一個真的在跑的專案。
    assert ProjectDashboardService.acquire_lock(project_c, running_c) is True

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window.projects.append(ProjectEntry(name="project_c", path=str(project_c)))
    window.dashboard_panel.service.queue_store.enqueue(project_b)

    candidate = ProjectEntry(name="project_b", path=str(project_b))
    monkeypatch.setattr(
        "app.ui.main_window.queue_scheduler.select_eligible_candidates",
        lambda candidates, load_settings, **_: iter(
            [(candidate, _development_settings())]
        ),
    )

    window._maybe_start_next_queued_project()

    session_b = window.run_sessions.get(project_b.resolve())
    assert session_b is None or session_b.thread is None

    window.deleteLater()
    application.processEvents()


def test_maybe_start_next_queued_project_not_blocked_by_focused_pending_ask(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # A pending question on the FOCUSED project has nothing to do with an
    # unrelated queued candidate's ability to start - the old global-ish
    # pending_ask_result pre-filter must be gone.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    runner_class = scripted_cli_runner_class(_happy_path_script())
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window.dashboard_panel.service.queue_store.enqueue(project_b)
    window.pending_ask_result = {"review_question": "A's unrelated question"}

    candidate = ProjectEntry(name="project_b", path=str(project_b))
    monkeypatch.setattr(
        "app.ui.main_window.queue_scheduler.select_eligible_candidates",
        lambda candidates, load_settings, **_: iter(
            [(candidate, _development_settings())]
        ),
    )

    window._maybe_start_next_queued_project()

    session_b = window.run_sessions[project_b.resolve()]
    assert session_b.thread is not None

    _wait_until_idle(application, session_b)

    window.deleteLater()
    application.processEvents()


def test_launch_worker_does_not_read_focused_session_ids_for_other_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 6: session_panel always reflects the FOCUSED project.
    # A non-focused launch (queue scheduler) must not silently inherit the
    # focused project's selected Claude/Codex session ids.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    monkeypatch.setattr(
        window.session_panel,
        "selected_session_ids",
        lambda: ("focused-claude-id", "focused-codex-id"),
    )

    captured_calls = []

    class FakeWorker:
        def __init__(self, project_path, **kwargs):
            captured_calls.append(kwargs)
            self.finished = _NullSignal()
            self.completed = _NullSignal()
            self.failed = _NullSignal()
            self.stage_changed = _NullSignal()
            self.active_role_changed = _NullSignal()

        def moveToThread(self, thread):
            pass

        def run(self):
            pass

        def deleteLater(self):
            pass

    class _NullSignal:
        def connect(self, *_a, **_k):
            pass

    monkeypatch.setattr("app.ui.main_window.WorkflowWorker", FakeWorker)
    monkeypatch.setattr(
        "app.ui.main_window.QThread.start", lambda self: None
    )

    # force_new_sessions defaults to True, which would independently blank
    # out the session ids regardless of this fix - disable it so the test
    # actually exercises the is_focused branch, not that unrelated logic.
    settings = _development_settings(force_new_sessions=False)

    window._launch_worker(
        project_b.resolve(), clear_output=True, settings=settings
    )

    assert captured_calls[-1]["claude_session_id"] == ""
    assert captured_calls[-1]["codex_session_id"] == ""

    window._launch_worker(
        project_a.resolve(), clear_output=True, settings=settings
    )

    assert captured_calls[-1]["claude_session_id"] == "focused-claude-id"
    assert captured_calls[-1]["codex_session_id"] == "focused-codex-id"

    window.deleteLater()
    application.processEvents()


def test_on_sync_completed_does_not_touch_focused_widgets_for_other_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 7: sync handlers must be identity-bound the same way
    # Phase 5 did for workflow handlers - a background project's sync
    # completion must not touch the focused project's cloud status label.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    window._set_cloud_status("Google Drive：未啟用")

    window.on_sync_completed(
        project_b.resolve(),
        SyncResult(success=True, project_id="p", project_name="project_b"),
    )

    assert window._cloud_status_text == "Google Drive：未啟用"

    window.deleteLater()
    application.processEvents()


def test_on_sync_thread_finished_clears_bound_sessions_sync_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    session_a = window._session_for(project_a.resolve())
    session_b = window._session_for(project_b.resolve())
    session_a.sync_thread = object()
    session_b.sync_thread = object()

    # No need to force the control state any more - it is derived from these
    # two sessions, so setting them up *is* setting it up.
    assert "sync_now" in window._actions_blocked()

    window.on_sync_thread_finished(project_b.resolve())

    assert session_b.sync_thread is None
    # B finishing must not re-enable A's (the focused project's) buttons -
    # A is still "syncing" per its own session state.
    assert "sync_now" in window._actions_blocked()
    assert session_a.sync_thread is not None

    window.on_sync_thread_finished(project_a.resolve())

    assert session_a.sync_thread is None
    assert "sync_now" not in window._actions_blocked()

    window.deleteLater()
    application.processEvents()


def test_configure_cloud_sync_blocked_by_any_active_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # The guard used to only check the focused project's own sync_thread -
    # meaningless once a background project's sync can be in flight while
    # an unrelated project is focused. Editing the shared cloud settings
    # mid-sync could make an unrelated in-flight sync write to a
    # stale/changed root_path.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    session_b = window._session_for(project_b.resolve())
    session_b.sync_thread = object()

    dialog_opened = []
    monkeypatch.setattr(
        "app.ui.main_window.CloudSettingsDialog",
        lambda *a, **k: dialog_opened.append(True),
    )

    window.configure_cloud_sync()

    assert dialog_opened == []

    window.deleteLater()
    application.processEvents()


def test_on_thread_finished_starts_sync_for_bound_project_not_focus(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 7: sync-after-workflow used to only fire for the
    # focused project (a Phase 5 limitation explicitly deferred to this
    # phase). It must now fire for whichever project's workflow finished.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    monkeypatch.setattr(
        window.cloud_sync,
        "load_settings",
        lambda: CloudSyncSettings(
            enabled=True, root_path=str(tmp_path), auto_sync_after_run=True
        ),
    )

    started_sync_for = []
    monkeypatch.setattr(
        window,
        "_start_sync",
        lambda project_path: started_sync_for.append(project_path),
    )

    session_b = window._session_for(project_b.resolve())
    session_b.sync_after_workflow_pending = True

    window.on_thread_finished(project_b.resolve())

    # The sync trigger is deferred via QTimer.singleShot(250, ...) - pump
    # the event loop long enough for it to actually fire.
    deadline = time.monotonic() + 5
    while not started_sync_for:
        assert time.monotonic() < deadline, "timed out waiting for _start_sync"
        application.processEvents()
        time.sleep(0.01)

    assert started_sync_for == [project_b.resolve()]

    window.deleteLater()
    application.processEvents()


def test_start_sync_recovers_session_state_when_setup_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Phase 12 adversarial review found the same gap _launch_worker had
    # before its own Phase 5 fix: a failure anywhere in _start_sync's
    # setup (between constructing session.sync_thread and actually
    # calling .start()) used to leave session.sync_thread permanently
    # non-None. Since configure_cloud_sync/link_cloud_project's guards
    # check any(session.sync_thread is not None for all sessions), this
    # would silently disable Google Drive settings/linking app-wide for
    # every project until restart.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")

    window = MainWindow(project_a)
    monkeypatch.setattr(
        window.cloud_sync,
        "load_settings",
        lambda: CloudSyncSettings(
            enabled=True, root_path=str(tmp_path), auto_sync_after_run=True
        ),
    )

    def raising_worker(*args, **kwargs):
        raise RuntimeError("simulated CloudSyncWorker construction failure")

    monkeypatch.setattr("app.ui.main_window.CloudSyncWorker", raising_worker)

    with pytest.raises(RuntimeError):
        window._start_sync(project_a.resolve())

    session = window.run_sessions[project_a.resolve()]
    assert session.sync_thread is None
    assert session.sync_worker is None

    # This is the exact predicate configure_cloud_sync/link_cloud_project
    # check (live, not via button state) to decide whether to block -
    # prove it would NOT block a subsequent attempt.
    assert not any(
        s.sync_thread is not None for s in window.run_sessions.values()
    )

    window.deleteLater()
    application.processEvents()


def test_link_cloud_project_blocked_by_any_active_sync(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    session_b = window._session_for(project_b.resolve())
    session_b.sync_thread = object()

    load_settings_calls = []
    monkeypatch.setattr(
        window.cloud_sync,
        "load_settings",
        lambda: load_settings_calls.append(True),
    )

    window.link_cloud_project()

    assert load_settings_calls == []

    window.deleteLater()
    application.processEvents()


class _FakeCloseEvent:
    def __init__(self) -> None:
        self.accepted = False
        self.ignored = False

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.ignored = True


def test_switch_project_allowed_while_a_project_is_busy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 8: focus-switching is decoupled from any background
    # work entirely - switching focus must not be blocked by, or disturb,
    # an unrelated project's own busy state, in either direction.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window._refresh_project_list()

    session_a = window._session_for(project_a.resolve())
    session_a.thread = object()

    window.switch_to_project(str(project_b.resolve()))

    assert window.project_path == project_b.resolve()
    assert session_a.thread is not None

    window.deleteLater()
    application.processEvents()


def test_switch_project_to_busy_project_resyncs_display(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window._refresh_project_list()

    session_b = window._session_for(project_b.resolve())
    session_b.thread = object()
    session_b.current_stage = "Codex 正在審查"

    window.switch_to_project(str(project_b.resolve()))

    assert window.project_path == project_b.resolve()
    run = window._shell_state()["shell"]["run"]
    assert run["state"] == "running"
    assert run["label"] == "執行中"
    assert "run" in window._actions_blocked()
    assert window._stage_text == "Codex 正在審查"
    # Global-affordance controls do not depend on any project's busy
    # state (Phase 8's "add_project always available" principle). The
    # shell draws the project switcher and 新增專案 unconditionally, so
    # what is asserted now is that the switcher still has something to
    # switch between while a project is busy.
    assert len(window._shell_state()["shell"]["projects"]) >= 2
    # B (the now-focused, busy project) blocks its OWN remove/sync controls.
    assert "remove_project" in window._actions_blocked()
    assert "sync_now" in window._actions_blocked()

    window.deleteLater()
    application.processEvents()


def test_add_project_and_friends_allowed_while_focused_project_is_busy(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")

    window = MainWindow(project_a)
    session_a = window._session_for(project_a.resolve())
    session_a.thread = object()

    # Must exist already, or validate_project() fails and add_project()
    # asks (mocked Yes) to run initialize_project() - which itself
    # requires the directory to already exist, else it raises and pops an
    # unmocked QMessageBox.critical.
    new_project = tmp_path / "project_new"
    new_project.mkdir()
    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(new_project),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )

    window.add_project()

    assert any(
        entry.resolved_path == new_project.resolve()
        for entry in window.projects
    )
    assert window.project_path == new_project.resolve()

    window.deleteLater()
    application.processEvents()


def test_add_project_preserves_target_projects_pending_ask_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Phase 12 adversarial review found a real bug: the old add_project()
    # did `self.project_path = path; self.pending_ask_result = None;
    # self.stop_ask_timers()` inline. Since project_path (a focus-scoped
    # property redirect) was reassigned FIRST, those two calls actually
    # acted on `path`'s own session state, not whatever was focused
    # before. Re-adding an already-tracked project (existing_index >= 0)
    # that has a real unanswered Decision Gate question would silently
    # wipe that question and stop its reminder timers.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    existing_project = _make_task_project(tmp_path / "existing_project")

    window = MainWindow(project_a)
    window.projects.append(
        ProjectEntry(name="existing_project", path=str(existing_project))
    )

    session = window._session_for(existing_project.resolve())
    session.pending_ask_result = {"review_question": "existing question"}
    session.start_ask_timers()

    monkeypatch.setattr(
        "app.ui.main_window.QFileDialog.getExistingDirectory",
        lambda *a, **k: str(existing_project),
    )

    window.add_project()

    assert window.project_path == existing_project.resolve()
    assert session.pending_ask_result == {
        "review_question": "existing question"
    }
    assert session.ask_reminder_timer.isActive()
    assert session.line_notify_timer.isActive()

    window.deleteLater()
    application.processEvents()


def test_remove_current_project_preserves_fallback_projects_pending_ask_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Phase 12 adversarial review found the same class of bug as
    # add_project's: the old remove_current_project() reassigned
    # project_path to the fallback project (self.projects[0]) FIRST, then
    # ran self.pending_ask_result = None / self.stop_ask_timers() - which,
    # being focus-scoped, wiped the FALLBACK project's own real pending
    # Decision Gate state (if it had one) instead of just clearing the
    # removed project's now-irrelevant state.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    fallback_project = _make_task_project(tmp_path / "fallback_project")

    window = MainWindow(project_a)
    window.projects.append(
        ProjectEntry(name="fallback_project", path=str(fallback_project))
    )

    fallback_session = window._session_for(fallback_project.resolve())
    fallback_session.pending_ask_result = {"review_question": "fallback's question"}
    fallback_session.start_ask_timers()

    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )

    window.remove_current_project()

    assert window.project_path == fallback_project.resolve()
    assert fallback_session.pending_ask_result == {
        "review_question": "fallback's question"
    }
    assert fallback_session.ask_reminder_timer.isActive()
    assert fallback_session.line_notify_timer.isActive()

    window.deleteLater()
    application.processEvents()


def test_close_event_warns_when_background_project_is_active(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 8: closeEvent must check every session, not just the
    # focused project's - a background project's live QThread (parented
    # to MainWindow) would otherwise be torn down with no warning at all.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    session_b = window._session_for(project_b.resolve())
    session_b.thread = object()

    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.No,
    )

    event = _FakeCloseEvent()
    window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False

    window.deleteLater()
    application.processEvents()


def test_close_event_does_not_warn_when_nothing_is_active(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project_a)

    question_calls = []
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: question_calls.append(True),
    )

    event = _FakeCloseEvent()
    window.closeEvent(event)

    assert question_calls == []
    assert event.accepted is True

    window.deleteLater()
    application.processEvents()


def test_close_event_terminates_in_flight_cli_subprocess_when_confirmed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # closeEvent used to just warn and, if the user force-closed anyway,
    # let Qt tear down the still-running QThread with no attempt to stop
    # the actual claude/codex CLI subprocess underneath it - that process
    # would keep running (and burning API/token cost) as an orphan with no
    # window left to observe or stop it. Prove the real fix reaches all the
    # way down to a genuinely running subprocess: use a real CliRunner (not
    # the ScriptedCliRunner test double) blocked on a real slow child
    # process, wired up exactly like production
    # (session.worker.orchestrator.runner), and confirm closeEvent's
    # terminate_current() call actually kills it - not just that the
    # method gets called.
    import sys
    import threading

    from app.adapters.cli_runner import CliRunner

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project_a)
    session = window._session_for(project_a.resolve())

    real_runner = CliRunner(project_a.resolve())
    outcome: dict = {}

    def run_long_command() -> None:
        outcome["result"] = real_runner.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=60,
        )

    class _FakeOrchestrator:
        def __init__(self, runner: CliRunner) -> None:
            self.runner = runner

    class _FakeWorker:
        def __init__(self, orchestrator: "_FakeOrchestrator") -> None:
            self.orchestrator = orchestrator

    session.thread = object()  # any non-None value marks the session active
    session.worker = _FakeWorker(_FakeOrchestrator(real_runner))

    thread = threading.Thread(target=run_long_command)
    thread.start()

    deadline = time.monotonic() + 5
    while real_runner._current_process is None:
        assert time.monotonic() < deadline, "subprocess never started"
        time.sleep(0.02)

    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )

    event = _FakeCloseEvent()
    window.closeEvent(event)

    thread.join(timeout=10)

    assert event.accepted is True
    assert not thread.is_alive(), (
        "run() never returned after closeEvent - the subprocess was not "
        "actually terminated"
    )
    assert outcome["result"].success is False

    session.thread = None
    window.deleteLater()
    application.processEvents()


def test_on_thread_finished_never_auto_opens_answer_dialog(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 9: the T+0 auto-popup is gone entirely, even for the
    # focused project - answering is always an explicit user click on
    # start_button ("回答問題"), never a timer/completion callback.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project_a)
    window.show()  # isVisible() below is only meaningful once shown

    prompt_calls = []
    monkeypatch.setattr(
        window, "prompt_user_answer", lambda: prompt_calls.append(True)
    )

    session_a = window._session_for(project_a.resolve())
    session_a.pending_ask_result = {"review_question": "A's question"}

    window.on_thread_finished(project_a.resolve())
    application.processEvents()

    assert prompt_calls == []
    # A-2: a project that is waiting must say so somewhere that cannot be
    # navigated away from. That is the shell's decision banner now, not a
    # button on a card, so this asserts the banner is actually rendered
    # rather than that a widget exists.
    gate = window._shell_state()["shell"]["gate"]
    assert gate is not None
    assert gate["question"] == "A's question"
    assert window._session_for(project_a.resolve()).pending_ask_result is not None

    # This is the only test in the file that show()s a window, so it is
    # the only one that owes the teardown below - see the helper.
    window.close()
    window.deleteLater()
    _drain_web_engine_teardown(application)


def test_raise_pending_question_does_not_open_dialog_or_raise_window(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 9: the T+5 reminder no longer raises the window,
    # steals OS focus, or opens the answer dialog - it's a passive log
    # line only, for whoever happens to already be looking at this
    # project.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project_a)

    prompt_calls = []
    monkeypatch.setattr(
        window, "prompt_user_answer", lambda: prompt_calls.append(True)
    )
    raise_calls = []
    monkeypatch.setattr(window, "raise_", lambda: raise_calls.append(True))
    activate_calls = []
    monkeypatch.setattr(
        window, "activateWindow", lambda: activate_calls.append(True)
    )

    session_a = window._session_for(project_a.resolve())
    session_a.pending_ask_result = {"review_question": "A's question"}

    window.raise_pending_question(project_a.resolve())

    assert prompt_calls == []
    assert raise_calls == []
    assert activate_calls == []

    window.deleteLater()
    application.processEvents()


def test_two_projects_with_pending_questions_answer_independently(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 9's stated verification: two projects both awaiting an
    # answer, focus on each in turn, answer independently, no cross-talk.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window._refresh_project_list()

    session_a = window._session_for(project_a.resolve())
    session_a.pending_ask_result = {
        "review_question": "A's question",
        "run_id": "run-a",
        "claude_session_id": "",
        "codex_session_id": "",
        "fix_attempts": 0,
    }
    session_b = window._session_for(project_b.resolve())
    session_b.pending_ask_result = {
        "review_question": "B's question",
        "run_id": "run-b",
        "claude_session_id": "",
        "codex_session_id": "",
        "fix_attempts": 0,
    }

    launch_calls = []
    monkeypatch.setattr(
        window,
        "_launch_worker",
        lambda project_path, **kwargs: launch_calls.append(
            (project_path, kwargs.get("run_id"))
        ),
    )
    monkeypatch.setattr(
        "app.ui.main_window.QInputDialog.getMultiLineText",
        lambda *a, **k: ("Answer for whichever is focused", True),
    )

    window.switch_to_project(str(project_a.resolve()))
    assert window.project_path == project_a.resolve()
    window.prompt_user_answer()

    window.switch_to_project(str(project_b.resolve()))
    assert window.project_path == project_b.resolve()
    window.prompt_user_answer()

    assert launch_calls == [
        (project_a.resolve(), "run-a"),
        (project_b.resolve(), "run-b"),
    ]
    # Answering A must not have touched B's own pending state before B
    # was answered in turn.
    assert session_a.pending_ask_result is None
    assert session_b.pending_ask_result is None

    window.deleteLater()
    application.processEvents()


# A hang guard, not a timing budget: nothing here is measured in seconds,
# and a healthy round trip clears in a handful of passes. The number only
# exists so a genuinely stuck notification fails the run instead of
# wedging the suite forever.
_LINE_NOTIFY_HANG_GUARD_PASSES = 100_000


def _finish_line_notify(window, application) -> None:
    """Wait out every in-flight LINE-notify QThread and let its queued
    completion reach the GUI thread - without putting a wall clock in the
    path.

    The wall clock had to go, and not because 5 seconds was too tight.
    This suite shares one process with QtWebEngine.
    test_on_thread_finished_never_auto_opens_answer_dialog is the only
    test in the whole file that calls window.show(), and showing a
    MainWindow is what makes Chromium build a real GPU surface for the
    seven web panels. Tearing that window down loses the GL context, and
    the GPU process restart that follows is serviced inside the Qt event
    loop: four tests later, one single processEvents() call freezes the
    GUI thread for about five seconds. Verified with
    faulthandler.dump_traceback_later during the freeze - the innermost
    Python frame is processEvents() itself, with no product code
    underneath it, so nothing in app/ is doing the blocking.

    So `while not done: assert time.monotonic() < deadline` was not
    measuring the LINE notification at all; it was measuring Chromium's
    GPU restart, and losing a coin flip against it. Measured before this
    helper existed: running this file whole failed 11 times out of 20,
    running the same test alone failed 0 times out of 20. Raising the
    budget would only have moved the coin flip, which is why it was not
    the fix.

    What replaces it ends on an event rather than on a clock: the loop
    below stops the moment the window itself reports nothing is in
    flight, which is the last link of the completed -> finished -> quit
    -> untrack chain. A processEvents() call that happens to take five
    seconds costs that loop one iteration and changes nothing about what
    it concludes.
    """
    passes = 0
    while window._line_notify_threads:
        # Pumping is not incidental here, it is the mechanism.
        # worker.finished -> thread.quit is a queued connection into the
        # GUI thread (the QThread is parented to the window), and so is
        # thread.finished -> _untrack_line_notify_worker. A blocking
        # thread.wait() here would deadlock the pair against each other:
        # the thread waits to be told to quit by a slot that only runs
        # when the caller pumps. So the loop drives the whole chain -
        # completed, finished, quit, untrack - and ends exactly when the
        # window says nothing is in flight any more.
        application.processEvents()
        # **這一行不是效能調整。**不 sleep 就不放開 GIL，背景執行緒拿不到
        # 任何 Python 執行時間，而主執行緒在這個迴圈裡不斷重入 Qt 的事件
        # 派送——2026-08-24 量到那種寫法會讓整個行程當掉（290 次 8 次，
        # 加上 sleep 之後 280 次 0 次）。見 `qt_helpers.wait_until`。
        time.sleep(0.01)
        passes += 1
        assert passes < _LINE_NOTIFY_HANG_GUARD_PASSES, (
            "the LINE notify thread never finished after "
            f"{passes} event-loop passes"
        )


def test_send_pending_line_notification_updates_session_via_real_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 10: send_pending_line_notification must go through a
    # real QThread/LineNotifyWorker round trip (not call send_push
    # directly on the GUI thread) and still correctly update the bound
    # project's session state once the background call completes.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")

    captured_messages = []

    def fake_send_push(self, message):
        captured_messages.append(message)
        return LineSendResult(True, "sent ok")

    monkeypatch.setattr(LineNotifier, "send_push", fake_send_push)

    window = MainWindow(project_a)
    session_a = window._session_for(project_a.resolve())
    session_a.pending_ask_result = {"review_question": "A's question"}

    window.send_pending_line_notification(project_a.resolve())

    _finish_line_notify(window, application)

    assert captured_messages, "the worker thread never reached send_push"
    assert "A's question" in captured_messages[0]
    assert project_a.name in captured_messages[0]
    assert session_a.line_notification_sent is True

    window.deleteLater()
    application.processEvents()


def test_send_pending_line_notification_does_not_block_gui_thread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """send_push is a blocking network call (up to a 20s timeout), so it
    must run off the GUI thread AND the GUI thread must keep running its
    own slots while it is in flight.

    That is a claim about interleaving, not about elapsed time, and it is
    asserted as one here. send_push parks until the GUI thread has proved
    it is still alive, so every assertion below is a fact about ordering:
    none of them gets weaker or stronger depending on how long the Qt
    event loop takes. That matters in this process specifically - see
    _finish_line_notify for the five-second freeze that lives in it.

    The two bounded waits that remain are hang guards, not budgets, and
    neither one is what decides pass or fail. Both are on plain-Python
    thread handshakes that the Qt event loop is not part of, so the
    freeze cannot reach them; a healthy run clears both in under a
    millisecond, and if either one ever ran out it would be because the
    worker thread genuinely never started or the GUI thread genuinely
    never came back.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")

    gui_thread_ident = threading.get_ident()
    send_push_ran_on_gui_thread: list[str] = []
    send_push_returned: list[str] = []
    inside_send_push = threading.Event()
    may_return_from_send_push = threading.Event()

    def parked_send_push(self, message):
        if threading.get_ident() == gui_thread_ident:
            # The regression this test exists to catch: send_push back on
            # the GUI thread. Return instead of parking, so it lands on
            # the assert below as a red test rather than as a suite that
            # deadlocks against itself.
            send_push_ran_on_gui_thread.append(message)
            return LineSendResult(True, "sent ok")
        inside_send_push.set()
        may_return_from_send_push.wait(30)
        send_push_returned.append(message)
        return LineSendResult(True, "sent ok")

    monkeypatch.setattr(LineNotifier, "send_push", parked_send_push)

    window = MainWindow(project_a)
    session_a = window._session_for(project_a.resolve())
    session_a.pending_ask_result = {"review_question": "A's question"}

    from PySide6.QtCore import QTimer

    # If send_pending_line_notification itself blocks, nothing below ever
    # runs, so release the worker from a watchdog and record that we had
    # to. Firing this timer is a failure, not a rescue - see the assert.
    call_returned = threading.Event()
    had_to_break_a_deadlock: list[str] = []

    def _break_deadlock() -> None:
        if not call_returned.is_set():
            had_to_break_a_deadlock.append("send_pending_line_notification")
            may_return_from_send_push.set()

    deadlock_watchdog = threading.Timer(30, _break_deadlock)
    deadlock_watchdog.daemon = True
    deadlock_watchdog.start()
    try:
        window.send_pending_line_notification(project_a.resolve())
        call_returned.set()
    finally:
        deadlock_watchdog.cancel()

    assert had_to_break_a_deadlock == [], (
        "send_pending_line_notification did not return while send_push was "
        "still in flight - it is waiting for the notification instead of "
        "handing it to a worker thread"
    )
    assert send_push_returned == [], (
        "send_pending_line_notification only returned after send_push had "
        "finished, so the network call is still on the caller's thread"
    )

    # The worker reaches send_push without needing the GUI thread at all
    # (thread.started -> worker.run is a direct call inside the new
    # thread), so this handshake is not affected by anything the event
    # loop is doing.
    assert inside_send_push.wait(30), "the worker thread never reached send_push"
    assert send_push_ran_on_gui_thread == [], (
        "send_push ran on the GUI thread - the whole point of the "
        "QThread/LineNotifyWorker round trip is that it does not"
    )

    # send_push is provably still parked right now: only the line further
    # down sets may_return_from_send_push. So whatever the GUI thread
    # manages to do here, it does while the notification is in flight -
    # which is exactly the claim.
    #
    # The liveness probe is five slots posted to the GUI thread's own
    # event queue. It replaces a repeating 20ms QTimer, which asked "did
    # 100ms of wall-clock time pass", not "did the GUI thread run". The
    # pump below is bounded in event-loop passes, so one processEvents()
    # call that happens to take five seconds costs this loop a single
    # iteration and neither proves nor disproves anything.
    ran_on_gui_thread: list[int] = []

    def _make_probe(n: int):
        def _probe() -> None:
            ran_on_gui_thread.append(n)

        return _probe

    # Held in a list on purpose: a slot handed to singleShot and then
    # dropped can be collected before it ever fires.
    probes = [_make_probe(n) for n in range(5)]
    for probe in probes:
        QTimer.singleShot(0, probe)

    for _ in range(50):
        if len(ran_on_gui_thread) == len(probes):
            break
        application.processEvents()
        time.sleep(0.01)   # 見上面那段：不放開 GIL 的緊迴圈會讓行程當掉

    assert ran_on_gui_thread == [0, 1, 2, 3, 4], (
        "the GUI thread did not run its own queued slots while send_push "
        f"was in flight - only got {ran_on_gui_thread}"
    )
    assert send_push_returned == [], (
        "send_push escaped its park, so the GUI thread liveness above was "
        "not measured against an in-flight notification"
    )

    may_return_from_send_push.set()
    _finish_line_notify(window, application)

    assert len(send_push_returned) == 1, (
        "send_push was released but never completed exactly once"
    )
    assert session_a.line_notification_sent is True

    window.deleteLater()
    application.processEvents()


def test_line_notify_thread_and_worker_stay_referenced_until_finished(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Regression test for the actual bug found while implementing Phase
    # 10: a QThread/LineNotifyWorker pair built entirely from local
    # variables (worker has no QObject parent - moveToThread forbids one)
    # gets garbage collected before the deferred started->run() dispatch
    # ever fires, silently dropping the notification with no error. Must
    # stay referenced in self._line_notify_threads until truly finished,
    # then be cleaned up (not accumulate forever).
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")

    monkeypatch.setattr(
        LineNotifier,
        "send_push",
        lambda self, message: LineSendResult(True, "sent ok"),
    )

    window = MainWindow(project_a)
    session_a = window._session_for(project_a.resolve())
    session_a.pending_ask_result = {"review_question": "A's question"}

    window.send_pending_line_notification(project_a.resolve())

    assert len(window._line_notify_threads) == 1

    _finish_line_notify(window, application)

    assert session_a.line_notification_sent is True
    assert window._line_notify_threads == []

    window.deleteLater()
    application.processEvents()


def test_run_line_notify_worker_recovers_when_setup_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Phase 12 adversarial review found the same gap _launch_worker/
    # _start_sync had before their own fixes: a failure anywhere in
    # _run_line_notify_worker's setup (between constructing the
    # thread/worker pair and actually calling .start()) used to leave the
    # (thread, worker) tuple permanently stuck in
    # self._line_notify_threads, since thread.finished would never fire
    # to untrack it - silently leaking the entry forever with no error
    # surfaced anywhere.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project_a)

    def raising_worker(*args, **kwargs):
        raise RuntimeError("simulated LineNotifyWorker construction failure")

    monkeypatch.setattr("app.ui.main_window.LineNotifyWorker", raising_worker)

    with pytest.raises(RuntimeError):
        window._run_line_notify_worker(
            "test message", lambda result: None, lambda error: None
        )

    assert window._line_notify_threads == []

    window.deleteLater()
    application.processEvents()


def test_on_line_notify_completed_ignores_stale_completion_for_superseded_question(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Phase 12 adversarial review Finding 3: an in-flight LINE push (up to
    # ~20s round trip) started for one pending question can complete
    # AFTER that question was answered and superseded by a NEW question
    # on the same project. Without run_id-scoping, the stale completion
    # would wrongly mark the NEW question's notification as already sent
    # - silently suppressing its real T+10 LINE alert.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project_a)
    session = window._session_for(project_a.resolve())

    session.pending_ask_result = {
        "review_question": "old question",
        "run_id": "run-1",
    }
    session.line_notification_sent = False

    # The old question gets answered and superseded by a new one before
    # the stale "run-1" completion arrives.
    session.pending_ask_result = {
        "review_question": "new question",
        "run_id": "run-2",
    }

    window._on_line_notify_completed(
        project_a.resolve(), "run-1", LineSendResult(True, "sent ok")
    )

    assert session.line_notification_sent is False

    # A completion for the CURRENT question must still apply normally.
    window._on_line_notify_completed(
        project_a.resolve(), "run-2", LineSendResult(True, "sent ok")
    )

    assert session.line_notification_sent is True

    window.deleteLater()
    application.processEvents()


def test_maybe_start_next_queued_project_respects_available_capacity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Stage 1 Phase 11's stated verification: concurrency_limit=3, 2
    # already running, 3 candidates queued -> only 1 more launches, to
    # exactly reach the limit and no further.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    running_1 = _make_task_project(tmp_path / "running_1")
    running_2 = _make_task_project(tmp_path / "running_2")
    run_1 = TimelineStore(running_1).create_run("執行中任務 1")
    run_2 = TimelineStore(running_2).create_run("執行中任務 2")
    # **鎖也要拿。**2026-08-27 之前這裡只寫 `create_run()`，而那只是在
    # `run.json` 裡留下一句 `RUNNING` 的宣稱。首頁當時直接信那句話——
    # 而硬碟上有六筆卡在那句話裡六天，佇列因此永遠不排它們。
    # 現在宣稱要有鎖背書才算「真的在跑」（`is_really_running`），
    # 而**這一條要測的是併發上限**，所以這裡要佈一個真的在跑的專案。
    assert ProjectDashboardService.acquire_lock(running_1, run_1) is True
    assert ProjectDashboardService.acquire_lock(running_2, run_2) is True

    candidate_paths = [
        _make_task_project(tmp_path / f"candidate_{i}") for i in range(3)
    ]

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="running_1", path=str(running_1)))
    window.projects.append(ProjectEntry(name="running_2", path=str(running_2)))
    for i, path in enumerate(candidate_paths):
        window.projects.append(
            ProjectEntry(name=f"candidate_{i}", path=str(path))
        )
    window.dashboard_panel.service.queue_store.set_concurrency_limit(3)
    for path in candidate_paths:
        window.dashboard_panel.service.queue_store.enqueue(path)

    candidates_settings = [
        (
            ProjectEntry(name=f"candidate_{i}", path=str(path)),
            _development_settings(),
        )
        for i, path in enumerate(candidate_paths)
    ]
    monkeypatch.setattr(
        "app.ui.main_window.queue_scheduler.select_eligible_candidates",
        lambda candidates, load_settings, **_: iter(candidates_settings),
    )

    launched = []
    monkeypatch.setattr(
        window,
        "_launch_worker",
        lambda project_path, **kwargs: launched.append(project_path),
    )

    window._maybe_start_next_queued_project()

    assert launched == [candidate_paths[0].resolve()]

    window.deleteLater()
    application.processEvents()


def test_maybe_start_next_queued_project_launches_multiple_real_workers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Real end-to-end proof, not just capacity arithmetic in isolation:
    # N ScriptedCliRunner-backed projects launched by the scheduler in one
    # pass, driven through actual concurrent QThreads, all independently
    # reach COMPLETED with correct, uncontaminated results.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    candidate_paths = [
        _make_task_project(tmp_path / f"candidate_{i}") for i in range(3)
    ]

    runner_class = scripted_cli_runner_class(_happy_path_script() * 3)
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    window = MainWindow(project_a)
    for i, path in enumerate(candidate_paths):
        window.projects.append(
            ProjectEntry(name=f"candidate_{i}", path=str(path))
        )
    window.dashboard_panel.service.queue_store.set_concurrency_limit(3)
    for path in candidate_paths:
        window.dashboard_panel.service.queue_store.enqueue(path)

    candidates_settings = [
        (
            ProjectEntry(name=f"candidate_{i}", path=str(path)),
            _development_settings(),
        )
        for i, path in enumerate(candidate_paths)
    ]
    monkeypatch.setattr(
        "app.ui.main_window.queue_scheduler.select_eligible_candidates",
        lambda candidates, load_settings, **_: iter(candidates_settings),
    )

    window._maybe_start_next_queued_project()

    sessions = [
        window.run_sessions[path.resolve()] for path in candidate_paths
    ]
    assert all(session.thread is not None for session in sessions)

    deadline = time.monotonic() + 15
    while any(session.thread is not None for session in sessions):
        assert time.monotonic() < deadline, "timed out waiting for workers"
        application.processEvents()
        time.sleep(0.01)

    for session in sessions:
        assert session.pending_ask_result is None

    window.deleteLater()
    application.processEvents()


def test_maybe_start_next_queued_project_does_not_eagerly_load_settings_past_capacity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Phase 12 adversarial review found a regression: the old loop was
    # `for candidate, settings in eligible: if started >= available_capacity:
    # break`. select_eligible_candidates is a lazy generator specifically
    # so a candidate's settings (a real load_settings side effect) are
    # never loaded until the caller actually asks for the next item - but
    # a `for` loop always pulls its next item BEFORE running the body, so
    # the break ran only after the first over-capacity candidate had
    # already been pulled and side-effected. Gating the pull itself
    # (`while started < available_capacity: next(eligible)`) must mean a
    # candidate beyond capacity is never even considered.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")
    project_c = _make_task_project(tmp_path / "project_c")
    # 兩個候選都**自己選過一份便宜的設定**。跟出廠值的話它們都算高用量
    # （出廠序列 2026-08-27 換成開發序列，估算 6 次呼叫 ≥ 門檻 5），
    # 於是佇列一個都不會開始，而這一支要量的是「超過容量的那一個有沒有
    # 被提早載入」——兩個都被略過的話，那件事根本沒有發生過。
    for queued in (project_b, project_c):
        AISettingsStore(queued).save_project(
            AIExecutionSettings(
                max_fix_attempts=0, auto_memory_maintenance=False
            )
        )

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window.projects.append(ProjectEntry(name="project_c", path=str(project_c)))
    window.dashboard_panel.service.queue_store.set_concurrency_limit(1)
    window.dashboard_panel.service.queue_store.enqueue(project_b.resolve())
    window.dashboard_panel.service.queue_store.enqueue(project_c.resolve())

    considered: list[Path] = []
    original_load_project = AISettingsStore.load_project

    def tracking_load_project(self):
        if self.project_path is not None:
            considered.append(self.project_path)
        return original_load_project(self)

    monkeypatch.setattr(AISettingsStore, "load_project", tracking_load_project)

    launched = []
    monkeypatch.setattr(
        window,
        "_launch_worker",
        lambda project_path, **kwargs: launched.append(project_path),
    )

    window._maybe_start_next_queued_project()

    # Only project_b (within capacity) should ever have had its settings
    # loaded - project_c, beyond available_capacity=1, must never be
    # pulled from the generator at all.
    assert considered == [project_b.resolve()]
    assert launched == [project_b.resolve()]

    window.deleteLater()
    application.processEvents()


def test_append_project_log_records_even_when_not_focused(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # This is the actual gap: append_log used to be called only from
    # inside `if project_path == self.project_path:` guards scattered
    # across every workflow/sync/LINE-notify handler, so a non-focused
    # project's log text was never recorded ANYWHERE - not hidden, simply
    # never captured. _append_project_log must always record onto the
    # bound project's own RunSession regardless of focus, and only mirror
    # into the visible widget when that project happens to be focused.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")
    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    window._append_project_log(project_b.resolve(), "background project B log")

    session_b = window.run_sessions[project_b.resolve()]
    assert session_b.log_lines == ["background project B log"]
    assert "background project B log" not in window.output.toPlainText()

    window.deleteLater()
    application.processEvents()


def test_switching_focus_restores_each_projects_own_buffered_log(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")
    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window._refresh_project_list()

    window.append_log("project A's own log line")
    window._append_project_log(project_b.resolve(), "project B's own log line")

    # Focused on A: A's line visible, B's is not.
    assert "project A's own log line" in window.output.toPlainText()
    assert "project B's own log line" not in window.output.toPlainText()

    window.switch_to_project(str(project_b.resolve()))

    # Switched to B: B's line is now visible, A's is gone from view (but
    # not deleted - it's still in A's own RunSession.log_lines).
    assert "project B's own log line" in window.output.toPlainText()
    assert "project A's own log line" not in window.output.toPlainText()
    assert window.run_sessions[project_a.resolve()].log_lines == [
        "project A's own log line"
    ]

    window.switch_to_project(str(project_a.resolve()))

    # Switching back to A restores A's line - this is the actual bug:
    # before this fix, self.output.clear() ran on every switch_project
    # call with nothing to restore it from, so A's line would be gone
    # forever even though A was never touched.
    assert "project A's own log line" in window.output.toPlainText()

    window.deleteLater()
    application.processEvents()


def test_on_completed_and_on_failed_record_log_for_unfocused_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")
    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    # project_a is focused; project_b's handlers fire while unfocused.
    window.on_stage_changed(project_b.resolve(), "B is doing something")
    window.on_completed(
        project_b.resolve(),
        {"workflow_status": "PASS", "review_status": "PASS"},
    )

    session_b = window.run_sessions[project_b.resolve()]
    joined_b = "\n".join(session_b.log_lines)
    assert "B is doing something" in joined_b
    assert "===== 工作流程結果 =====" in joined_b
    assert "B is doing something" not in window.output.toPlainText()
    assert "===== 工作流程結果 =====" not in window.output.toPlainText()

    window.on_failed(project_b.resolve(), "boom")
    joined_b = "\n".join(session_b.log_lines)
    assert "boom" in joined_b
    assert "boom" not in window.output.toPlainText()

    window.deleteLater()
    application.processEvents()


def test_launch_worker_resets_log_buffer_for_a_fresh_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    runner_class = scripted_cli_runner_class(_happy_path_script())
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    window = MainWindow(project_a)
    session = window.run_sessions[project_a.resolve()]
    session.log_lines = ["stale line from a previous run"]

    window._launch_worker(
        project_a.resolve(), clear_output=True, settings=_development_settings()
    )

    assert session.log_lines[0] != "stale line from a previous run"
    assert "工作流程開始。" in session.log_lines
    assert "stale line from a previous run" not in window.output.toPlainText()

    _wait_until_idle(application, session)

    window.deleteLater()
    application.processEvents()


def test_switching_focus_away_and_back_preserves_backgrounded_projects_real_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # End-to-end proof, matching the actual manual live-test scenario that
    # found this gap: launch two real concurrent workers, switch focus
    # away from A to B while A is still running, let A finish entirely
    # while unfocused, then switch back to A and confirm its real output
    # (not just a placeholder) is there - not lost because A wasn't the
    # one on screen while it worked.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    runner_class = scripted_cli_runner_class(_happy_path_script() * 2)
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window._refresh_project_list()

    window._launch_worker(
        project_a.resolve(), clear_output=True, settings=_development_settings()
    )

    window.switch_to_project(str(project_b.resolve()))

    session_a = window.run_sessions[project_a.resolve()]
    _wait_until_idle(application, session_a)

    # A finished entirely while B was focused - its result text must be in
    # its own buffer even though it was never shown on screen.
    joined_a = "\n".join(session_a.log_lines)
    assert "===== 工作流程結果 =====" in joined_a
    assert "===== 工作流程結果 =====" not in window.output.toPlainText()

    window.switch_to_project(str(project_a.resolve()))

    assert "===== 工作流程結果 =====" in window.output.toPlainText()

    window.deleteLater()
    application.processEvents()


def test_on_completed_updates_current_stage_so_it_does_not_go_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Regression test found by a live manual run: _refresh_action_buttons()
    # always displays session.current_stage (it's called on every focus
    # switch and every button click), but on_completed used to only
    # setText() the stage_label widget directly for a nice final message
    # like "工作流程已完成" - it never updated session.current_stage
    # itself. The NEXT _refresh_action_buttons() call would then silently
    # overwrite that nice message with whatever on_stage_changed had last
    # set current_stage to (e.g. "正在執行測試"), even though the status
    # pill correctly showed idle - producing exactly the contradictory
    # "尚未執行" + "正在執行測試" display a live run surfaced.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project_a)
    session = window._session_for(project_a.resolve())

    session.current_stage = "正在執行測試"  # simulate the last stage_changed

    window.on_completed(
        project_a.resolve(),
        {
            # 一種引擎真的吐得出來的收場。以前這裡寫 "UNKNOWN"，而畫面那條
            # 判定鏈有個 else 會把任何東西接住——所以測試也接受了一個引擎
            # 從來不會產生的值。現在沒有 else 了，這裡也要用真的。
            "workflow_status": "COMPLETED",
            "review_status": "PASS",
            "fix_attempts": 0,
        },
    )

    assert session.current_stage == "工作流程已完成"
    assert window._stage_text == "工作流程已完成"

    # The actual bug: a later _refresh_action_buttons() call (as every
    # focus switch and button click triggers) must not revert this.
    window._refresh_action_buttons()
    assert window._stage_text == "工作流程已完成"

    window.deleteLater()
    application.processEvents()


def test_on_failed_updates_current_stage_so_it_does_not_go_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project_a)
    session = window._session_for(project_a.resolve())
    session.current_stage = "正在執行測試"

    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.critical",
        lambda *a, **k: None,
    )

    window.on_failed(project_a.resolve(), "boom")

    assert session.current_stage == "工作流程發生錯誤"
    assert window._stage_text == "工作流程發生錯誤"

    window._refresh_action_buttons()
    assert window._stage_text == "工作流程發生錯誤"

    window.deleteLater()
    application.processEvents()


def test_dismiss_pending_updates_current_stage_so_it_does_not_go_stale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project_a)
    session = window._session_for(project_a.resolve())
    session.current_stage = "正在執行測試"
    session.pending_ask_result = {
        "review_question": "q",
        "run_id": "run-1",
        "workflow_status": "ASK_USER",
    }

    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )

    window.dismiss_pending_question()

    assert session.current_stage == "可重新執行任務"
    assert window._stage_text == "可重新執行任務"

    window._refresh_action_buttons()
    assert window._stage_text == "可重新執行任務"

    window.deleteLater()
    application.processEvents()


# --- stop_workflow -----------------------------------------------------------
#
# The execution bar draws 停止 whenever a run is busy and Python cannot suppress
# it, so this had to be either real or a dead control. It reuses closeEvent's
# terminate_current() rather than inventing a second teardown, and these prove
# it the same way closeEvent's own test does: a real CliRunner blocked on a real
# child process, wired up as production wires it
# (session.worker.orchestrator.runner). Asserting that terminate_current() was
# *called* would pass just as well against a mechanism that killed nothing.


class _FakeOrchestrator:
    def __init__(self, runner) -> None:
        self.runner = runner


class _FakeWorker:
    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator


def _busy_session_with_real_subprocess(window, project: Path):
    """Attach a real CliRunner blocked on a real sleeping child to `project`."""
    import sys
    import threading

    from app.adapters.cli_runner import CliRunner

    session = window._session_for(project.resolve())
    runner = CliRunner(project.resolve())
    outcome: dict = {}

    def run_long_command() -> None:
        outcome["result"] = runner.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=60,
        )

    thread = threading.Thread(target=run_long_command)
    thread.start()

    deadline = time.monotonic() + 5
    while runner._current_process is None:
        assert time.monotonic() < deadline, "subprocess never started"
        time.sleep(0.02)

    session.thread = object()   # any non-None value marks the session active
    session.worker = _FakeWorker(_FakeOrchestrator(runner))
    return session, thread, outcome


def test_stop_workflow_kills_the_running_subprocess_when_confirmed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)
    session, thread, outcome = _busy_session_with_real_subprocess(window, project)

    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )

    window.stop_workflow()
    thread.join(timeout=10)

    assert not thread.is_alive(), (
        "run() never returned after stop_workflow - the subprocess was not "
        "actually terminated"
    )
    assert outcome["result"].success is False

    session.thread = None
    window.deleteLater()
    application.processEvents()


def test_stop_workflow_does_nothing_when_the_user_declines(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)
    session, thread, _outcome = _busy_session_with_real_subprocess(window, project)

    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.No,
    )

    window.stop_workflow()

    # Still running: declining a destructive confirmation has to mean nothing
    # happened, not "happened a moment later".
    assert thread.is_alive()

    session.worker.orchestrator.runner.terminate_current()
    thread.join(timeout=10)
    session.thread = None
    window.deleteLater()
    application.processEvents()


def test_stop_workflow_does_not_prompt_when_nothing_is_running(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    asked: list[str] = []
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: asked.append("asked") or QMessageBox.Yes,
    )

    window.stop_workflow()

    # A confirmation dialog for stopping something that is not running is the
    # kind of prompt that teaches users to click through prompts.
    assert asked == []

    window.deleteLater()
    application.processEvents()


def test_stop_workflow_leaves_another_projects_run_alone(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # The whole point of Stage 1 is that projects run independently. A stop
    # button that reached across sessions would undo that in one click, and
    # nothing about the focused project's own state would look wrong afterwards.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))

    session_a, thread_a, _ = _busy_session_with_real_subprocess(window, project_a)
    session_b, thread_b, _ = _busy_session_with_real_subprocess(window, project_b)

    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )

    window.stop_workflow()          # focused project is A
    thread_a.join(timeout=10)

    assert not thread_a.is_alive()
    assert thread_b.is_alive(), "stopping A also killed B's subprocess"

    session_b.worker.orchestrator.runner.terminate_current()
    thread_b.join(timeout=10)
    session_a.thread = session_b.thread = None
    window.deleteLater()
    application.processEvents()


def test_stop_workflow_says_so_when_there_is_no_subprocess_to_kill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # A thread between steps has no live child process. Saying that is not
    # cosmetic: the alternative is a confirmed destructive action that visibly
    # does nothing, which reads as a broken button.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])

    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)
    session = window._session_for(project.resolve())
    session.thread = object()
    session.worker = None

    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.Yes,
    )

    window.stop_workflow()

    assert any("沒有正在執行的子行程" in line for line in session.log_lines), (
        f"沒有告訴使用者發生了什麼：{session.log_lines}"
    )

    session.thread = None
    window.deleteLater()
    application.processEvents()


def test_the_execution_bar_is_told_the_stage_detail(tmp_path: Path, monkeypatch) -> None:
    # `stage` is computed in Python for both live and terminal outcomes (see
    # _apply_workflow_result: "Codex 未完成審查" is a finished-run stage), but
    # it was never pushed into the shell, so the bar could not draw it at all.
    # 回覆給系統開發-12.md asked which of the two it was; the answer is both.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")

    window = MainWindow(project)
    window._session_for(project.resolve()).current_stage = "Codex 未完成審查"

    assert window._shell_state()["shell"]["run"]["stage"] == "Codex 未完成審查"

    window.deleteLater()
    application.processEvents()


def test_a_project_with_no_stage_pushes_empty_not_a_placeholder(
    tmp_path: Path, monkeypatch
) -> None:
    # `_stage_text` falls back to 「等待開始」 for the native label. Pushing that
    # into the bar would put a second, redundant sentence beside a status pill
    # that already says the same thing - the page draws no stage element when
    # this is empty, which is the intended look.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")

    window = MainWindow(project)

    assert window._shell_state()["shell"]["run"]["stage"] == ""
    assert window._stage_text == "等待開始"

    window.deleteLater()
    application.processEvents()


def test_usage_is_pushed_as_developing_and_carries_no_number(
    tmp_path: Path, monkeypatch
) -> None:
    # The placeholder is a decision, not an unfinished feature: two things about
    # the CLIs' own token figures are unverified and need opposite arithmetic,
    # so any total would be a plausible-looking guess (ROADMAP P12). This
    # asserts the absence, because a number appearing here is exactly the
    # failure being guarded against - the page renders whatever it is given.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")

    window = MainWindow(project)
    usage = window._shell_state()["shell"]["usage"]

    assert usage["state"] == "developing"
    for key in ("tokens", "input", "output"):
        assert key not in usage, f"用量還沒驗證就推了數字：{key}={usage.get(key)}"

    window.deleteLater()
    application.processEvents()


def test_a_half_done_run_is_gold_and_not_a_failure(tmp_path: Path, monkeypatch) -> None:
    # 已達修正上限 and 需要修正 both mean Claude really changed files and the
    # tests really ran - there is work to look at. Rose reads as "nothing came
    # of this". Gold is the attention colour, and `partial` is the terminal
    # member of that family (see set_status on the two golds).
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    # FIX_LIMIT_REACHED pops a modal warning; unpatched it blocks forever.
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.warning", lambda *a, **k: QMessageBox.Ok
    )
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    # 第二個案例本來是 ("COMPLETED", "FIX", "需要修正")——**那是引擎從來不會
    # 產生的組合**（COMPLETED 一定配 review PASS），它能通過只是因為畫面那條
    # 判定鏈有個 else 什麼都接。換成章不齊那一種：它同樣是「做了一半」，
    # 而且它 2026-08-23 之前被畫成薄荷色寫著「工作流程已完成」。
    for workflow_status, review_status, expected_text in (
        ("FIX_LIMIT_REACHED", "", "已達修正上限"),
        ("NOT_DECIDABLE", "PASS", "還不能拍板"),
    ):
        window.on_completed(
            project.resolve(),
            {
                "workflow_status": workflow_status,
                "review_status": review_status,
                "decision_verdict": "MISSING",
                "fix_attempts": 1,
            },
        )
        run = window._shell_state()["shell"]["run"]
        assert run["state"] == "partial", f"{expected_text} 應該是 partial：{run}"
        assert run["label"] == expected_text
        # The reason has to survive alongside the state - it is what the bar
        # shows next to the usage figure.
        assert run["stage"]

    window.deleteLater()
    application.processEvents()


def test_the_bar_says_which_thing_the_user_has_to_touch(
    tmp_path: Path, monkeypatch
) -> None:
    """收場的分組要真的推到畫面上，不然它只是一個沒有人讀的值。

    顏色回答的是「要不要你動」；**這一欄回答「動哪一樣東西」**。少了它，
    「少一項設定」（去改陣容）跟「要再跑一次」（什麼都不用改）在執行列上
    長得一模一樣。
    """
    from app.core import outcomes

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    for workflow_status, expected_group in (
        ("VERIFY_NOT_CONFIGURED", outcomes.GROUP_SETTING),
        ("APPROVAL_REJECTED", outcomes.GROUP_RERUN),
        ("APPROVAL_BLOCKED", outcomes.GROUP_FIX),
    ):
        window.on_completed(
            project.resolve(),
            {
                "workflow_status": workflow_status,
                "review_status": "NOT_RUN",
                "fix_attempts": 0,
            },
        )
        run = window._shell_state()["shell"]["run"]
        assert run["group"] == expected_group, (
            f"{workflow_status} 的分組沒有推到畫面上：{run}"
        )

    window.deleteLater()
    application.processEvents()


def test_nothing_to_verify_with_is_not_painted_as_a_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """VERIFY_NOT_CONFIGURED belongs in the gold family, not beside 「執行失敗」.

    Nothing failed and nothing ran: the project has never said how it should
    be checked. Rose here would tell the user something is broken and send
    them looking for it, when the only thing missing is one line of settings
    - which is the same wrong story the hard-coded pytest command used to
    tell, just repainted.

    Which of the two golds it is, is the other half, and it is not a matter of
    taste: `gated` means an answer resumes the run, and there is no answer to
    give here. That half is measured in
    test_the_stop_and_ask_survives_the_refresh_that_follows_it.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    window.on_completed(
        project.resolve(),
        {
            "workflow_status": "VERIFY_NOT_CONFIGURED",
            "review_status": "NOT_RUN",
            "tests_status": "NOT_DECLARED",
            "verification_problem": "這個專案沒有宣告驗證指令，請先設定：verify_command。",
        },
    )

    run = window._shell_state()["shell"]["run"]
    assert run["state"] != "error", f"沒得驗不是壞了：{run}"
    assert run["state"] == "partial"
    assert run["label"] == "尚未設定驗證"
    # The reason the project gave has to reach the screen - 「去設定哪裡」 is
    # the only actionable part of this ending.
    assert "verify_command" in run["stage"]

    window.deleteLater()
    application.processEvents()


def test_the_stop_and_ask_survives_the_refresh_that_follows_it(
    tmp_path: Path, monkeypatch
) -> None:
    """Read the light again AFTER on_thread_finished, not only at on_completed.

    Every run ends with on_completed and then on_thread_finished, and the
    second one calls _refresh_action_buttons, which rebuilds the execution bar
    from session state alone. Only 「outcome」 kinds are kept there, so the
    first version of this ending - painted `gated` - was repainted 「尚未執行
    / idle」 one call later: a grey dot identical to a project that has never
    run, on a run that stopped on purpose and needs the user to do something.

    Measured before the fix: AFTER on_completed = gated/尚未設定驗證, AFTER
    on_thread_finished = idle/尚未執行. Asserting only the first of those is
    what let it ship, so this test reads both.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    window.on_completed(
        project.resolve(),
        {
            "workflow_status": "VERIFY_NOT_CONFIGURED",
            "review_status": "NOT_RUN",
            "tests_status": "NOT_DECLARED",
            "verification_problem": "這個專案沒有宣告驗證指令，請先設定：verify_command。",
        },
    )
    before = window._shell_state()["shell"]["run"]
    assert (before["state"], before["label"]) == ("partial", "尚未設定驗證")

    window.on_thread_finished(project.resolve())

    after = window._shell_state()["shell"]["run"]
    assert after["state"] == before["state"], f"收工那一步把燈抹掉了：{after}"
    assert after["label"] == before["label"], f"收工那一步把字抹掉了：{after}"
    # 「去哪裡設定」 is the only actionable thing this ending carries, and it
    # is rebuilt from session.current_stage by the same refresh - so it gets
    # read on this side of it too.
    assert "verify_command" in after["stage"]

    # The same refresh runs again on every focus switch and every button
    # click, so once is not proof.
    window._refresh_action_buttons()
    again = window._shell_state()["shell"]["run"]
    assert (again["state"], again["label"]) == (before["state"], before["label"])

    window.deleteLater()
    application.processEvents()


def test_a_timed_out_verification_also_survives_the_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    # The other ending that stops without anything being broken. It reaches the
    # bar through the same two-step ending, so it can be erased the same way -
    # and it was written after the erasure was already known about, which is
    # exactly when a second instance of the same bug gets written.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    window.on_completed(
        project.resolve(),
        {
            "workflow_status": "VERIFY_TIMED_OUT",
            "review_status": "NOT_RUN",
            "verification_problem": "驗證指令超過 900 秒未結束，請調整 verify_timeout_seconds。",
        },
    )
    window.on_thread_finished(project.resolve())

    run = window._shell_state()["shell"]["run"]
    assert run["state"] == "partial", f"逾時的結局被收工抹掉了：{run}"
    assert run["label"] == "驗證逾時"
    assert "verify_timeout_seconds" in run["stage"]

    window.deleteLater()
    application.processEvents()


def test_a_real_failure_stays_rose(tmp_path: Path, monkeypatch) -> None:
    # The negative half of the pair above: a CLI that fell over produced
    # nothing to look at, so it must NOT be promoted into the gold family.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    for workflow_status in ("CODEX_ERROR", "CLAUDE_ERROR", "CODEX_INVALID_RESPONSE"):
        window.on_completed(
            project.resolve(),
            {"workflow_status": workflow_status, "review_status": ""},
        )
        assert window._shell_state()["shell"]["run"]["state"] == "error"

    window.deleteLater()
    application.processEvents()


def test_a_half_done_outcome_survives_switching_project(
    tmp_path: Path, monkeypatch
) -> None:
    # `partial` is an outcome, so it has to be remembered on the session. If it
    # is not, the next _refresh_action_buttons paints 「尚未執行」 over it - the
    # same erasure that once wiped 完成 and 執行失敗.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.warning", lambda *a, **k: QMessageBox.Ok
    )
    application = QApplication.instance() or QApplication([])
    project_a = _make_task_project(tmp_path / "project_a")
    project_b = _make_task_project(tmp_path / "project_b")

    window = MainWindow(project_a)
    window.projects.append(ProjectEntry(name="project_b", path=str(project_b)))
    window._refresh_project_list()

    window.on_completed(
        project_a.resolve(),
        {
            "workflow_status": "FIX_LIMIT_REACHED",
            "review_status": "",
            "fix_attempts": 2,
        },
    )
    assert window._shell_state()["shell"]["run"]["state"] == "partial"

    window.switch_to_project(str(project_b.resolve()))
    window.switch_to_project(str(project_a.resolve()))

    run = window._shell_state()["shell"]["run"]
    assert run["state"] == "partial", f"切走再切回來就忘了：{run}"
    assert run["label"] == "已達修正上限"

    window.deleteLater()
    application.processEvents()


def test_the_running_seat_is_pushed_as_an_index(tmp_path: Path, monkeypatch) -> None:
    # The index is the seat's position in **this project's line-up** - the same
    # object the drawn seats come from - not something derived from the
    # workflow mode. The duties swap between sequences while the seats do not,
    # so a mode-derived index lights the wrong one exactly when the two
    # disagree.
    from app.core.lineup import ROLE_AUTHOR, ROLE_PLANNER, ROLE_REVIEWER

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    def lit() -> object:
        return window._shell_state()["shell"]["run"].get("active_slot")

    window.on_active_role_changed(project.resolve(), ROLE_AUTHOR)
    assert lit() == 0

    window.on_active_role_changed(project.resolve(), ROLE_REVIEWER)
    assert lit() == 1

    # Seat a planner in front and every index shifts by one. Against a fixed
    # role list this is where the wrong seat lights up: the author would still
    # report 0, which is now the planner.
    window.lineup_panel._lineup = window.lineup_panel._lineup.with_planner()

    window.on_active_role_changed(project.resolve(), ROLE_PLANNER)
    assert lit() == 0
    window.on_active_role_changed(project.resolve(), ROLE_AUTHOR)
    assert lit() == 1
    window.on_active_role_changed(project.resolve(), ROLE_REVIEWER)
    assert lit() == 2

    window.deleteLater()
    application.processEvents()


def test_no_seat_is_lit_when_nothing_is_running(tmp_path: Path, monkeypatch) -> None:
    # Absent, not 0 - and this is the half that matters. A seat left lit after
    # the run says something is still working, and 0 is indistinguishable from
    # "the first seat is going".
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    assert "active_slot" not in window._shell_state()["shell"]["run"]

    window.deleteLater()
    application.processEvents()


def test_the_seat_goes_dark_when_the_run_ends(tmp_path: Path, monkeypatch) -> None:
    from app.core.lineup import ROLE_AUTHOR

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(
        "app.ui.main_window.QMessageBox.critical", lambda *a, **k: QMessageBox.Ok
    )
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    for ending in ("completed", "failed", "thread_finished"):
        window.on_active_role_changed(project.resolve(), ROLE_AUTHOR)
        assert "active_slot" in window._shell_state()["shell"]["run"]

        if ending == "completed":
            window.on_completed(
                project.resolve(),
                {"workflow_status": "PLANNING_COMPLETED", "review_status": ""},
            )
        elif ending == "failed":
            window.on_failed(project.resolve(), "爆炸了")
        else:
            window.on_thread_finished(project.resolve())

        assert "active_slot" not in window._shell_state()["shell"]["run"], (
            f"{ending} 之後座位還亮著"
        )

    window.deleteLater()
    application.processEvents()


def test_an_unseated_role_lights_nothing_rather_than_guessing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    window.on_active_role_changed(project.resolve(), "某個引擎會派但陣容沒有的角色")

    assert "active_slot" not in window._shell_state()["shell"]["run"]

    window.deleteLater()
    application.processEvents()


def test_a_verification_timeout_is_not_painted_as_a_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """VERIFY_TIMED_OUT is 「做了一半」, not 「壞了」.

    The command started and the author really did change files - the
    deadline just fired first. Nothing has been shown to be broken, so rose
    would tell the user their code failed when what actually needs raising
    is one number in project.json.

    This also guards the fall-through: a workflow_status with no branch of
    its own drops to the final `else` and is painted `NOT_RUN` in rose,
    which is both wrong and unreadable.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = _make_task_project(tmp_path / "project_a")
    window = MainWindow(project)

    window.on_completed(
        project.resolve(),
        {
            "workflow_status": "VERIFY_TIMED_OUT",
            "review_status": "NOT_RUN",
            "tests_status": "TIMED_OUT",
            "verification_problem": (
                "驗證指令在宣告的 900 秒內沒有結束，已被中止。"
                "請確認 verify_timeout_seconds 是否足夠。"
            ),
        },
    )

    run = window._shell_state()["shell"]["run"]
    assert run["state"] == "partial"
    assert run["label"] == "驗證逾時"
    assert "verify_timeout_seconds" in run["stage"]

    # `partial` is in set_status's last_outcome whitelist, so the ending
    # survives the refresh that always follows a finished thread. A colour
    # that gets repainted to 「尚未執行」 is the same as no colour at all.
    window.on_thread_finished(project.resolve())
    after = window._shell_state()["shell"]["run"]
    assert after["state"] == "partial"
    assert after["label"] == "驗證逾時"

    window.deleteLater()
    application.processEvents()
