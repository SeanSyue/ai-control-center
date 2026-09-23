"""「不回答，結束這次等待」（掃描「算出來沒人讀」時挖到）。

`MainWindow.dismiss_pending_question` 是一個 `@Slot()`，**沒有任何地方呼叫它**。
外框上只有「查看並回答」和「稍後」，而「稍後」只收起橫幅——問題還在等，鎖也
還握著。

少了這條路的後果不是不方便：**等待中的專案握著 workflow lock，排程器不會啟動
它**。所以一個使用者已經不打算回答的問題（任務改了、問題過時了），會讓那個
專案永遠停在「等待決策」，而唯一的出路是去回答一個沒有意義的問題。

這個檔案驗那條路真的走得通，而且**不會順手做多餘的事**：結束等待不執行任何
CLI、不刪任何檔案。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.services.project_dashboard import ProjectDashboardService  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

ASK = {"review_status": "ASK_USER", "review_question": "要改成月結嗎？", "run_id": "r1"}


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    # 同 test_line_credentials：一條沒 patch 到的出口就足以讓負向驗證整輪掛住。
    for name, answer in (
        ("question", QMessageBox.Yes),
        ("information", QMessageBox.Ok),
        ("warning", QMessageBox.Ok),
        ("critical", QMessageBox.Ok),
    ):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, _r=answer, **k: _r)
        )


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# Task\n", encoding="utf-8")
    ProjectRegistry.initialize_project(project)
    made = MainWindow(project)
    yield made
    made.deleteLater()


def _waiting(window) -> None:
    window.pending_ask_result = dict(ASK)
    window._focused_session.start_ask_timers()
    ProjectDashboardService.acquire_lock(window.project_path, "r1")


# --- 那條路走得通 ------------------------------------------------------------


def test_the_shell_action_is_wired(window) -> None:
    """驗接線，不是驗方法。

    今天已經有三次「方法對了但呼叫點沒接上」，所以這條直接走
    `_on_shell_action`。
    """
    _waiting(window)

    window._on_shell_action("gate_dismiss", "")

    assert window.pending_ask_result is None, "gate_dismiss 沒有接到任何東西"


def test_ending_the_wait_stops_the_reminders(window) -> None:
    _waiting(window)
    session = window._focused_session
    assert session.ask_reminder_timer.isActive()

    window._on_shell_action("gate_dismiss", "")

    assert not session.ask_reminder_timer.isActive()
    assert not session.line_notify_timer.isActive()


def test_ending_the_wait_releases_the_lock(window) -> None:
    """這才是真正的後果。

    鎖沒放掉的話，專案在排程器眼裡還是「有人在跑」，於是它永遠不會被啟動
    ——而畫面上已經沒有任何東西在等了。
    """
    _waiting(window)
    assert ProjectDashboardService.lock_path(window.project_path).exists()

    window._on_shell_action("gate_dismiss", "")

    assert not ProjectDashboardService.lock_path(window.project_path).exists()


def test_the_banner_goes_away(window) -> None:
    _waiting(window)
    assert window._shell_state()["shell"]["gate"] is not None

    window._on_shell_action("gate_dismiss", "")

    assert window._shell_state()["shell"]["gate"] is None


# --- 不會順手做多餘的事 ------------------------------------------------------


def test_declining_the_confirmation_keeps_the_wait(window, monkeypatch) -> None:
    _waiting(window)
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No)
    )

    window._on_shell_action("gate_dismiss", "")

    assert window.pending_ask_result is not None
    assert window._focused_session.ask_reminder_timer.isActive()


def test_the_confirmation_says_what_it_does_not_do(window, monkeypatch) -> None:
    """「結束等待」聽起來可能像「取消任務」。

    使用者要能分辨這是「我不回答了」還是「把東西刪掉」，所以那句話要明講。
    """
    _waiting(window)
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: (asked.append(a[2]), QMessageBox.No)[1]),
    )

    window._on_shell_action("gate_dismiss", "")

    assert asked, "破壞性動作沒有先問"
    assert "不會刪除" in asked[0], asked[0]
    assert "要改成月結嗎？" in asked[0], "沒有把要放棄的問題本身講出來"


def test_ending_the_wait_starts_nothing(window, monkeypatch) -> None:
    # 結束等待不是「繼續執行」。跑起 CLI 會花錢，而使用者剛表達的是相反的意思。
    _waiting(window)
    started: list[int] = []
    monkeypatch.setattr(
        MainWindow, "_launch_worker", lambda self, *a, **k: started.append(1)
    )

    window._on_shell_action("gate_dismiss", "")

    assert not started


def test_nothing_happens_when_there_is_no_question(window) -> None:
    assert window.pending_ask_result is None

    window._on_shell_action("gate_dismiss", "")

    assert window.pending_ask_result is None


# --- 選單上看得到 ------------------------------------------------------------


def test_the_menu_offers_it_only_while_something_is_waiting() -> None:
    """有問題才出現。

    一個常駐的「結束這次等待」在沒有等待的時候是個沒有意義的選項，
    而使用者學會忽略它之後，真的在等的那次它就不存在了。
    """
    import re

    source = Path("app/ui/web/shell.js").read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))

    assert 'data-shell-act="gate_dismiss"' in code, "選單上沒有這個動作"
    menu = code.split('data-shell-act="link_cloud_project"')[1].split("</div>")[0]
    assert "s.gate ?" in menu, "沒有等待的時候也照樣顯示"
