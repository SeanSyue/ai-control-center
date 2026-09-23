from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from app.core.workflow_worker import WorkflowWorker
from app.services.ai_settings import AIExecutionSettings
from tests.fakes import (
    calls_after_intake,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
    with_intake,
)
from tests.qt_helpers import (
    SignalTimeoutError,
    running_worker_thread,
    wait_for_signal,
)


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# Task\n\nDo the thing.\n", encoding="utf-8"
    )
    declare_verify_command(project)
    return project


def _settings(**overrides) -> AIExecutionSettings:
    base = AIExecutionSettings(
        workflow_mode="development",
        auto_memory_maintenance=False,
        auto_session_bootstrap=False,
        max_fix_attempts=1,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class _Emitter(QObject):
    ping = Signal(str)


def test_wait_for_signal_returns_once_emitted() -> None:
    application = QApplication.instance() or QApplication([])
    emitter = _Emitter()

    from PySide6.QtCore import QTimer

    QTimer.singleShot(10, lambda: emitter.ping.emit("hello"))

    spy = wait_for_signal(emitter.ping, timeout_ms=2000)

    assert spy.count() == 1
    assert spy.at(0)[0] == "hello"
    application.processEvents()


def test_wait_for_signal_times_out_when_never_emitted() -> None:
    QApplication.instance() or QApplication([])
    emitter = _Emitter()

    with pytest.raises(SignalTimeoutError):
        wait_for_signal(emitter.ping, timeout_ms=200)


def test_workflow_worker_completes_via_real_qthread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Proves the qt_helpers infrastructure itself is correct before any
    # Stage 1 production code relies on it: the same WorkflowWorker +
    # ScriptedCliRunner combination used throughout
    # test_workflow_worker_runtime.py, but driven through a real
    # moveToThread()+thread.start() instead of a synchronous worker.run()
    # call - exercising actual cross-thread signal delivery for the first
    # time in this test suite.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = make_project(tmp_path)

    script = [
        cli_result('{"result": "done, no questions"}'),  # claude
        cli_result("1 passed"),  # pytest
        cli_result(
            '{"status": "PASS", "summary": "looks good", '
            '"issues": [], "instructions": [], "question": ""}'
        ),  # codex review
    ]
    runner_class = scripted_cli_runner_class(with_intake(script))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(project, settings=_settings())
    completed_results = []
    failed_messages = []
    worker.completed.connect(completed_results.append)
    worker.failed.connect(failed_messages.append)

    with running_worker_thread(worker) as thread:
        wait_for_signal(worker.finished, timeout_ms=10000)
        thread.wait(2000)

    application.processEvents()

    assert not failed_messages
    assert len(completed_results) == 1
    assert completed_results[0]["workflow_status"] == "COMPLETED"
    assert completed_results[0]["review_status"] == "PASS"
    assert len(calls_after_intake(runner_class.calls)) == 3


def test_running_worker_thread_tears_down_on_assertion_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # A raised assertion inside the `with` body must not leak a running
    # QThread into later tests - the context manager's finally block has
    # to run regardless of how the body exits.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = make_project(tmp_path)

    script = [
        cli_result('{"result": "done, no questions"}'),
        cli_result("1 passed"),
        cli_result(
            '{"status": "PASS", "summary": "ok", '
            '"issues": [], "instructions": [], "question": ""}'
        ),
    ]
    runner_class = scripted_cli_runner_class(with_intake(script))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(project, settings=_settings())

    thread_ref: list[QThread] = []
    with pytest.raises(AssertionError):
        with running_worker_thread(worker) as thread:
            thread_ref.append(thread)
            wait_for_signal(worker.finished, timeout_ms=10000)
            raise AssertionError("simulated test failure mid-body")

    assert thread_ref[0].isRunning() is False
