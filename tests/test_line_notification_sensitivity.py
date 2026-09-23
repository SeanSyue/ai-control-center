"""A KYC project's Decision Gate question must not leave the machine.

SECURITY_MODEL's contract for `sensitive_kyc` is that it stays local: portable
export fails closed, Drive sync refuses before touching a file. The T+10 LINE
reminder was sending the question text to an external service anyway - and that
text is written by Codex about whatever it is handling, so on a KYC case it is
very likely to quote case or client data.

The notification body is assembled by a static method precisely so it can be
tested without a running window or a real LINE credential.
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

QUESTION = "是否要把王小明的護照掃描件匯出到共用資料夾？"


def _project(tmp_path: Path, sensitivity: str | None) -> Path:
    project = tmp_path / "case"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    if sensitivity is not None:
        ProjectRegistry.set_sensitivity(project, sensitivity)
    return project


def _session():
    return SimpleNamespace(pending_ask_result={"review_question": QUESTION})


def test_a_kyc_project_does_not_send_the_question_text(tmp_path: Path) -> None:
    project = _project(tmp_path, "sensitive_kyc")

    message = MainWindow._line_notification_text(project, _session())

    assert QUESTION not in message
    assert "王小明" not in message
    assert "護照" not in message
    # Still actionable: it has to say which project, or the reminder is useless.
    assert project.name in message
    assert "不外送" in message


def test_an_ordinary_project_still_sends_the_question(tmp_path: Path) -> None:
    # The whole value of the T+10 reminder is knowing what is being asked
    # without going back to the machine. Withholding it everywhere would be
    # "safe" and useless.
    project = _project(tmp_path, "personal")

    message = MainWindow._line_notification_text(project, _session())

    assert QUESTION in message


def test_a_client_project_still_sends_the_question(tmp_path: Path) -> None:
    # Deliberate: `client` already syncs to Drive under the same contract, so
    # withholding it from LINE alone would be inconsistent, not safer. This
    # pins the decision so a future change is made on purpose.
    project = _project(tmp_path, "client")

    message = MainWindow._line_notification_text(project, _session())

    assert QUESTION in message


def test_a_corrupt_project_file_withholds_the_question(tmp_path: Path) -> None:
    # get_sensitivity fails closed, and this asserts the notification inherits
    # that: an unreadable project.json must land on the restrictive side.
    project = _project(tmp_path, None)
    (project / ".aicc" / "project.json").write_text("{ broken", encoding="utf-8")

    message = MainWindow._line_notification_text(project, _session())

    assert QUESTION not in message


def test_a_missing_question_still_produces_a_message(tmp_path: Path) -> None:
    project = _project(tmp_path, "personal")
    session = SimpleNamespace(pending_ask_result={})

    message = MainWindow._line_notification_text(project, session)

    assert "此任務需要使用者決策" in message
