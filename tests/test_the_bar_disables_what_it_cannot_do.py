"""外框上按了不會有事的動作，要停用並講原因（不是靜靜地什麼都不做）。

這一組的起點是一次 AST 掃描：`app/` 裡定義了、而 `app/` 裡沒有任何地方讀的
名字。撈到六個 property——`_can_start`、`_can_remove_project`、`_can_sync_now`、
`_can_open_cloud_settings`、`_can_link_cloud`、`_is_waiting_for_answer`。

**它們不是死碼，是只有測試在讀的產品程式碼。** 那個差別重要：死碼刪掉就好，
而「只有測試在讀」代表**有人寫下了一條規則、也寫了測試證明它成立，然後那條
規則從來沒有到達畫面**。測試是綠的，功能不存在。

實際跑起來是這樣：每個 handler 自己在開頭擋自己（同樣的條件），條件不符就
`return`。選單項目是亮的、按下去畫面完全沒變。這個專案已經三次指名這個形狀
——**一顆按了什麼都不會發生的按鈕，比一顆明著停用的更糟**，因為使用者分不出
它跟壞掉的差別。

最貴的一個是同步中的「開始執行」：執行列的 busy 只看 `thread` 不看
`sync_thread`，所以那顆按鈕還寫著「開始執行」而不是「停止」。

所以這個檔案守的不是那六個 property，是**畫面收到的那份 `blocked` 跟 handler
真的會不會動作一致**。方向刻意是雙向的：
說擋住卻其實做得到，跟說可以卻什麼都不會發生，一樣糟。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.main_window import MainWindow  # noqa: E402

# 外框上每一個「條件不符就靜靜 return」的動作，對到它自己的 handler。
# 動作名就是頁面送回來的那個字串，所以這張表跟 `data-shell-act` 是同一組詞。
GUARDED = ("run", "remove_project", "sync_now", "cloud_settings",
           "link_cloud_project")


def _window(tmp_path: Path, monkeypatch, with_task: bool = True) -> MainWindow:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    if with_task:
        (project / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    return MainWindow(project)


def _blocked(window) -> dict[str, str]:
    return window._shell_state()["shell"]["blocked"]


# --- 什麼都不忙的時候 --------------------------------------------------------


def test_an_idle_project_blocks_nothing(tmp_path, monkeypatch) -> None:
    # 反方向的前提檢查：一張永遠說「都不能按」的表，會讓整個外框變成裝飾。
    window = _window(tmp_path, monkeypatch)

    assert _blocked(window) == {}

    window.deleteLater()


def test_a_project_with_no_task_cannot_start(tmp_path, monkeypatch) -> None:
    """這一條不是 handler 的守衛，是刻意多出來的。

    `start_workflow` 在沒有 task.md 時會走到 `save_task()`，不是靜靜 return。
    但這是唯一一個**要花錢才會發現**的失敗，所以寧可先在按鈕上說清楚。
    """
    window = _window(tmp_path, monkeypatch, with_task=False)

    assert "task.md" in _blocked(window)["run"]

    window.deleteLater()


# --- 忙的時候 ---------------------------------------------------------------


def test_a_running_project_blocks_the_three_it_should(tmp_path, monkeypatch) -> None:
    window = _window(tmp_path, monkeypatch)
    window._focused_session.thread = object()

    blocked = _blocked(window)

    assert set(blocked) == {"run", "remove_project", "sync_now"}
    # 雲端設定跟連結雲端專案只被「有專案在同步」擋，不被執行擋——那是 handler
    # 的條件。跟著擋下去會停用一個 app 其實會接受的動作，那是陣容頁座位上限
    # 犯過的同一個錯（畫面比引擎嚴）。
    assert "cloud_settings" not in blocked
    assert "link_cloud_project" not in blocked

    window.deleteLater()


def test_syncing_blocks_run_which_is_the_one_the_bar_hid(
    tmp_path, monkeypatch
) -> None:
    """這是這一整組的理由。

    執行列的 busy 只看 `thread`，所以同步中那顆按鈕寫的是「開始執行」而不是
    「停止」。按下去 `start_workflow` 第一道守衛就 return——**畫面完全沒變**。
    """
    window = _window(tmp_path, monkeypatch)
    window._focused_session.sync_thread = object()

    blocked = _blocked(window)

    assert "run" in blocked, "同步中按「開始執行」還是什麼都不會發生"
    assert "同步" in blocked["run"]
    # 雲端那兩個是全域設定，任何專案在同步都要擋。
    assert "cloud_settings" in blocked
    assert "link_cloud_project" in blocked

    window.deleteLater()


def test_another_projects_sync_blocks_only_the_global_settings(
    tmp_path, monkeypatch
) -> None:
    # 雲端設定是全域的（一個共用根資料夾），所以別的專案在同步也要擋；
    # 但這個專案自己還是可以跑。
    window = _window(tmp_path, monkeypatch)
    other = tmp_path / "other"
    (other / ".aicc").mkdir(parents=True)
    window._session_for(other.resolve()).sync_thread = object()

    blocked = _blocked(window)

    assert set(blocked) == {"cloud_settings", "link_cloud_project"}

    window.deleteLater()


# --- 兩邊要講同一件事 --------------------------------------------------------


@pytest.mark.parametrize("action", GUARDED)
def test_a_blocked_action_really_does_nothing(
    tmp_path, monkeypatch, action: str
) -> None:
    """說擋住的，handler 真的不會動作。

    用「呼叫下去、看它有沒有走到第一步」來驗，不是比對條件式——比對條件式
    等於把 handler 的守衛抄第二遍，而那正是這一整組要消滅的東西。
    """
    window = _window(tmp_path, monkeypatch)
    # 同時滿足兩種忙：自己在執行，而且有專案在同步。
    window._focused_session.thread = object()
    window._focused_session.sync_thread = object()
    assert action in _blocked(window), "這個案例沒有擋到這個動作，測不到東西"

    # 每個 handler 過了守衛之後**第一件會做的事**。監看它，就是在問
    # 「守衛有沒有真的擋下來」，而不是把守衛的條件抄第二遍。
    reached: list[str] = []

    def spy(name):
        def recorded(*args, **kwargs):
            reached.append(name)
            raise AssertionError(f"走到 {name} 了——這個動作應該被擋住")
        return recorded

    monkeypatch.setattr(window.ai_settings_panel, "save_task", spy("save_task"))
    monkeypatch.setattr(window.cloud_sync, "load_settings", spy("load_settings"))
    projects_before = list(window.projects)

    window._on_shell_action(action, "")

    assert reached == [], f"{action} 說被擋住了，實際上還是動作了：{reached}"
    # `remove_project` 過了守衛之後不呼叫上面任何一個，所以它要自己的觀測點。
    assert list(window.projects) == projects_before

    window.deleteLater()


@pytest.mark.parametrize("action", GUARDED)
def test_every_guarded_action_can_be_blocked_at_all(
    tmp_path, monkeypatch, action: str
) -> None:
    """每一個列進 GUARDED 的動作，都要真的有辦法被擋住。

    少了這條，上面那條可以因為「這個動作永遠不在 blocked 裡」而空轉通過
    ——而它的 assert 訊息會說「沒有測到東西」，但那要有人去讀。
    """
    window = _window(tmp_path, monkeypatch)
    window._focused_session.thread = object()
    window._focused_session.sync_thread = object()

    assert action in _blocked(window)

    window.deleteLater()


def test_the_reasons_say_something(tmp_path, monkeypatch) -> None:
    """停用而不說為什麼是另一種比較小的謊。

    使用者會以為功能壞了，而不是知道「等同步跑完就可以」。
    """
    window = _window(tmp_path, monkeypatch)
    window._focused_session.sync_thread = object()

    for action, why in _blocked(window).items():
        assert len(why) >= 6, f"{action} 的原因太短講不出事情：{why!r}"

    window.deleteLater()
