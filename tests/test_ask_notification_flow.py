"""T+0 / T+5 / T+10 當成一條流程來驗（ROADMAP P3）。

三段各自都有測試：計時器的間隔、LINE 那條的執行緒往返、跨專案的隔離。
**沒有測到的是它們串起來的樣子**——而這條流程真正會壞掉的地方全都在接縫上：

  T+0   執行結束、狀態是 ASK_USER  → 兩個計時器開始跑
  T+5   `raise_pending_question`  → 在那個專案自己的紀錄裡留一行
  T+10  `send_pending_line_notification` → 推一則 LINE，只推一次

接縫的意思是：**回答之後，後面兩段還會不會動。** 一則在使用者已經回答完
十分鐘後才送到的 LINE，比沒有通知更糟——它會教人不要相信這個通知。

計時器不等真的十分鐘，直接觸發，所以這條流程是可測的而不是「理論上會動」。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.run_session import (  # noqa: E402
    ASK_REMINDER_INTERVAL_MS,
    LINE_NOTIFY_INTERVAL_MS,
)
from app.services.line_notifier import LineNotifier, LineSendResult  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

ASK = {"review_status": "ASK_USER", "review_question": "要不要改成月結？", "run_id": "r1"}
DONE = {"review_status": "APPROVED", "run_id": "r1"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    yield application


def _project(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    (path / ".aicc").mkdir(parents=True)
    (path / ".aicc" / "task.md").write_text("# Task\n做事。\n", encoding="utf-8")
    ProjectRegistry.initialize_project(path)
    return path.resolve()


def _sent(monkeypatch) -> list[str]:
    """Capture LINE pushes without a thread, so the flow stays deterministic."""
    messages: list[str] = []

    def fake(self, message):
        messages.append(message)
        return LineSendResult(True, "sent ok")

    monkeypatch.setattr(LineNotifier, "send_push", fake)

    def run_now(self, message, on_completed, on_failed):
        on_completed(LineNotifier().send_push(message))

    monkeypatch.setattr(MainWindow, "_run_line_notify_worker", run_now)
    return messages


# --- T+0 --------------------------------------------------------------------


def test_a_question_starts_both_timers(app, tmp_path) -> None:
    project = _project(tmp_path, "p")
    window = MainWindow(project)
    session = window._session_for(project)

    session.pending_ask_result = ASK
    window.on_thread_finished(project)

    assert session.ask_reminder_timer.isActive()
    assert session.line_notify_timer.isActive()
    window.deleteLater()


def test_a_run_that_asks_nothing_starts_no_timers(app, tmp_path) -> None:
    # 反方向：不然「一律開計時器」也會通過上面那條。
    project = _project(tmp_path, "p")
    window = MainWindow(project)
    session = window._session_for(project)

    session.pending_ask_result = None
    window.on_thread_finished(project)

    assert not session.ask_reminder_timer.isActive()
    assert not session.line_notify_timer.isActive()
    window.deleteLater()


def test_the_reminder_comes_before_the_notification(app, tmp_path) -> None:
    """順序寫死在兩個常數裡，而順序反了不會有任何測試發現。"""
    assert ASK_REMINDER_INTERVAL_MS < LINE_NOTIFY_INTERVAL_MS
    assert ASK_REMINDER_INTERVAL_MS == 5 * 60 * 1000
    assert LINE_NOTIFY_INTERVAL_MS == 10 * 60 * 1000


# --- T+5 --------------------------------------------------------------------


def test_the_reminder_lands_in_that_projects_own_log(app, tmp_path) -> None:
    """就算焦點已經移到別的專案。

    T+5 刻意不搶視窗焦點、不自動跳答題視窗（使用者要求過）。所以它唯一的
    產物就是這一行；如果它跟著焦點跑，那這一段等於什麼都沒做。
    """
    project = _project(tmp_path, "p")
    other = _project(tmp_path, "other")
    window = MainWindow(project)
    session = window._session_for(project)
    session.pending_ask_result = ASK

    window.project_path = other
    window.raise_pending_question(project)

    assert any("5 分鐘" in line for line in session.log_lines), session.log_lines
    window.deleteLater()


def test_the_reminder_says_nothing_once_it_is_answered(app, tmp_path) -> None:
    project = _project(tmp_path, "p")
    window = MainWindow(project)
    session = window._session_for(project)

    session.pending_ask_result = None
    window.raise_pending_question(project)

    assert not any("5 分鐘" in line for line in session.log_lines), session.log_lines
    window.deleteLater()


# --- T+10 -------------------------------------------------------------------


def test_the_notification_carries_the_question(app, tmp_path, monkeypatch) -> None:
    messages = _sent(monkeypatch)
    project = _project(tmp_path, "p")
    window = MainWindow(project)
    session = window._session_for(project)
    session.pending_ask_result = ASK

    window.send_pending_line_notification(project)

    assert messages, "T+10 什麼都沒送"
    assert "要不要改成月結？" in messages[0]
    assert session.line_notification_sent is True
    window.deleteLater()


def test_the_notification_is_sent_once_not_every_time(
    app, tmp_path, monkeypatch
) -> None:
    messages = _sent(monkeypatch)
    project = _project(tmp_path, "p")
    window = MainWindow(project)
    window._session_for(project).pending_ask_result = ASK

    window.send_pending_line_notification(project)
    window.send_pending_line_notification(project)

    assert len(messages) == 1, messages
    window.deleteLater()


def test_nothing_is_sent_once_it_is_answered(app, tmp_path, monkeypatch) -> None:
    """接縫上最重要的一條。

    十分鐘後才送到、而使用者早就回答完的通知，比沒有通知更糟：它會教人
    不要相信這個通知。
    """
    messages = _sent(monkeypatch)
    project = _project(tmp_path, "p")
    window = MainWindow(project)
    window._session_for(project).pending_ask_result = None

    window.send_pending_line_notification(project)

    assert messages == []
    window.deleteLater()


# --- 接縫 -------------------------------------------------------------------


def test_answering_stops_both_stages_from_firing_later(
    app, tmp_path, monkeypatch
) -> None:
    """回答之後計時器要停，而且就算硬觸發也不能有動作。

    兩件事分開驗是有理由的：計時器停掉是「不會被叫到」，而兩個處理器自己
    也要擋得住——已經排進事件佇列的那一次觸發，是停不掉的。
    """
    messages = _sent(monkeypatch)
    project = _project(tmp_path, "p")
    window = MainWindow(project)
    session = window._session_for(project)
    session.pending_ask_result = ASK
    window.on_thread_finished(project)
    assert session.ask_reminder_timer.isActive()

    # 使用者回答了。
    session.pending_ask_result = None
    session.stop_ask_timers()

    assert not session.ask_reminder_timer.isActive()
    assert not session.line_notify_timer.isActive()

    before = list(session.log_lines)
    window.raise_pending_question(project)
    window.send_pending_line_notification(project)

    assert session.log_lines == before
    assert messages == []
    window.deleteLater()


def test_the_next_question_gets_its_own_notification(
    app, tmp_path, monkeypatch
) -> None:
    """`line_notification_sent` 是「這一題送過了」，不是「這個專案送過了」。

    它沒有在回答的時候歸零——歸零的時機是**下一題出現的時候**（`on_completed`
    收到 ASK_USER）。所以這條走的是那條路徑而不是直接改旗標：直接改就等於
    在測我自己的假設，而漏掉歸零的後果是第二題以後永遠不再通知，而且是安靜的。
    """
    messages = _sent(monkeypatch)
    project = _project(tmp_path, "p")
    window = MainWindow(project)
    session = window._session_for(project)

    session.pending_ask_result = ASK
    window.send_pending_line_notification(project)
    assert len(messages) == 1

    # 回答掉，然後下一輪執行又問了一題。
    session.pending_ask_result = None
    window.on_completed(project, {**ASK, "review_question": "第二題", "run_id": "r2"})

    assert session.line_notification_sent is False, "第二題的通知被第一題擋住了"
    window.send_pending_line_notification(project)

    assert len(messages) == 2, messages
    assert "第二題" in messages[1]
    window.deleteLater()


def test_a_finished_run_with_no_question_clears_the_previous_one(
    app, tmp_path
) -> None:
    # 另一半：不是 ASK_USER 的結果要把上一題收乾淨，否則計時器會為一個
    # 已經不存在的問題繼續跑。
    project = _project(tmp_path, "p")
    window = MainWindow(project)
    session = window._session_for(project)
    session.pending_ask_result = ASK
    session.start_ask_timers()

    window.on_completed(project, DONE)

    assert session.pending_ask_result is None
    assert not session.ask_reminder_timer.isActive()
    assert not session.line_notify_timer.isActive()
    window.deleteLater()
