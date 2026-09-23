from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.core.run_session import RunSession


def test_run_session_defaults_are_idle(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])

    session = RunSession(tmp_path)

    assert session.project_path == tmp_path
    assert session.thread is None
    assert session.worker is None
    assert session.started_at is None
    assert session.pending_ask_result is None
    assert session.line_notification_sent is False
    assert session.sync_thread is None
    assert session.sync_worker is None
    assert session.sync_after_workflow_pending is False
    assert session.is_active() is False


def test_run_session_is_active_reflects_workflow_thread(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    session = RunSession(tmp_path)

    session.thread = object()

    assert session.is_active() is True


def test_run_session_is_active_reflects_sync_thread(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    session = RunSession(tmp_path)

    session.sync_thread = object()

    assert session.is_active() is True


def test_run_session_ask_timers_are_singleshot_with_expected_intervals(
    tmp_path: Path,
) -> None:
    QApplication.instance() or QApplication([])
    session = RunSession(tmp_path)

    assert session.ask_reminder_timer.isSingleShot() is True
    assert session.ask_reminder_timer.interval() == 5 * 60 * 1000
    assert session.line_notify_timer.isSingleShot() is True
    assert session.line_notify_timer.interval() == 10 * 60 * 1000


def test_run_session_start_stop_ask_timers(tmp_path: Path) -> None:
    QApplication.instance() or QApplication([])
    session = RunSession(tmp_path)

    session.start_ask_timers()
    assert session.ask_reminder_timer.isActive() is True
    assert session.line_notify_timer.isActive() is True

    session.stop_ask_timers()
    assert session.ask_reminder_timer.isActive() is False
    assert session.line_notify_timer.isActive() is False


def test_run_session_timers_are_independent_per_instance(tmp_path: Path) -> None:
    # Each project's Decision Gate reminder clock must be independent -
    # starting one project's timers must not start another's.
    QApplication.instance() or QApplication([])
    session_a = RunSession(tmp_path / "a")
    session_b = RunSession(tmp_path / "b")

    session_a.start_ask_timers()

    assert session_a.ask_reminder_timer.isActive() is True
    assert session_b.ask_reminder_timer.isActive() is False
