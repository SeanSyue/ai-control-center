"""佇列排程沒有自動開始任何專案時，講的是真正的原因。

`_maybe_start_next_queued_project()` 結尾原本只有一句話：

> 佇列排程：佇列中下一個專案用量設定較高，需要手動確認後才會開始。

而「一個都沒開始」有兩種原因，鎖定是另一種。實測（把
`_try_acquire_project_lock` 蓋成永遠失敗）日誌長這樣：

```
佇列排程：「b」偵測到執行中鎖定，先略過。
佇列排程：佇列中下一個專案用量設定較高，需要手動確認後才會開始。
```

**兩句互相矛盾，而錯的那句在最後**——最後一句是會被讀的那句。它把人送去翻
用量設定，而真正的問題是鎖，甚至可能是**殘留的鎖**（儀表板本來就會標）。

這個形狀在這個專案裡出現過很多次：畫面給出一個看起來很合理、而且**在多數
情況下剛好正確**的答案。高用量確實是最常見的原因，所以那句話大部分時候是對的
——那正是它活下來的方式。

理由的組裝放在 `queue_scheduler.nothing_started_reason()`（純函式），
在那裡驗，不用每次起一個 MainWindow。最後一條例外：真的跑一次 MainWindow，
因為「那兩個 list 有沒有被填對」是接線，不是純邏輯。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services import queue_scheduler  # noqa: E402


# --- 理由本身（純函式）------------------------------------------------------


def test_a_lock_is_not_reported_as_high_usage() -> None:
    """這一組存在的理由。"""
    lines = queue_scheduler.nothing_started_reason([], ["b"])

    assert not any("用量" in line for line in lines), lines
    assert any("鎖定" in line for line in lines), lines


def test_high_usage_is_still_reported_as_high_usage() -> None:
    lines = queue_scheduler.nothing_started_reason(["b"], [])

    assert any("用量設定較高" in line for line in lines), lines
    assert not any("鎖定" in line for line in lines), lines
    # 指名是哪一個。「下一個專案」在佇列有三個的時候等於沒說。
    assert any("「b」" in line for line in lines), lines


def test_both_reasons_are_said_when_both_happened() -> None:
    """兩個原因可以同時成立，而只講一個等於把另一個藏起來。

    只講高用量的話，鎖住的那個專案會永遠卡在佇列裡而沒有人知道為什麼。
    """
    lines = queue_scheduler.nothing_started_reason(["a"], ["b"])

    assert any("用量設定較高" in line for line in lines), lines
    assert any("鎖定" in line for line in lines), lines


def test_the_lock_line_points_at_where_to_look() -> None:
    # 鎖定訊息要能帶人到下一步。殘留的鎖定是儀表板已經在標的東西。
    lines = queue_scheduler.nothing_started_reason([], ["b"])

    assert any("儀表板" in line for line in lines), lines


def test_an_unknown_reason_does_not_borrow_an_existing_one() -> None:
    """兩邊都空的時候（候選在中途被別的視窗清掉），要說不知道。

    套一個現成的理由上去，就是這一整條 bug 的來源。
    """
    lines = queue_scheduler.nothing_started_reason([], [])

    assert lines, "什麼都不說，使用者只會看到佇列不動"
    assert not any("用量" in line or "鎖定" in line for line in lines), lines


# --- 惰性不能因為這次改動而消失 ---------------------------------------------


def test_reporting_skips_does_not_load_settings_eagerly() -> None:
    """`on_skip` 不可以把惰性弄壞。

    `load_settings` 有真的副作用（會建立那個專案的 `.aicc` 目錄），所以
    產生器刻意只在呼叫端真的要下一個時才載入。先數一遍「有幾個高用量」
    就等於替每一個還沒輪到的候選建目錄——那是這個產生器當初存在的理由。
    """
    from app.services.ai_settings import AIExecutionSettings
    from app.services.project_registry import ProjectEntry

    loaded: list[str] = []

    def load(candidate: ProjectEntry) -> AIExecutionSettings:
        loaded.append(candidate.name)
        # **一份便宜的設定**，不是出廠那一份：出廠序列 2026-08-27 換成開發
        # 序列之後，`AIExecutionSettings()` 自己就過了高用量門檻，於是每一個
        # 候選都被略過、產生器一個都不吐——這一支要量的惰性就無從量起。
        return AIExecutionSettings(
            max_fix_attempts=0, auto_memory_maintenance=False
        )

    candidates = [ProjectEntry(name=n, path=f"C:/{n}") for n in ("a", "b", "c")]
    eligible = queue_scheduler.select_eligible_candidates(
        candidates, load, on_skip=lambda c: None
    )

    next(eligible)

    assert loaded == ["a"], f"只要第一個，卻載入了 {loaded}"


# --- 接線：兩個 list 真的有被填對 -------------------------------------------


def _project(root: Path, name: str) -> Path:
    from app.services.ai_settings import AIExecutionSettings, AISettingsStore
    from app.services.project_registry import ProjectRegistry

    path = root / name
    (path / ".aicc").mkdir(parents=True)
    (path / ".aicc" / "task.md").write_text("# 任務\n", encoding="utf-8")
    ProjectRegistry.initialize_project(path)
    # 這個專案**自己選過一份便宜的設定**。沒有這一行的話它跟的是出廠值，
    # 而出廠序列 2026-08-27 換成開發序列之後，出廠值就過了高用量門檻——
    # 候選在「拿不拿得到鎖」之前就被略過，底下那一條要驗的鎖定理由
    # 永遠不會發生，而它會以「講錯話」的形狀失敗。
    AISettingsStore(path).save_project(
        AIExecutionSettings(max_fix_attempts=0, auto_memory_maintenance=False)
    )
    return path


def test_a_locked_candidate_really_produces_the_lock_reason(
    tmp_path: Path, monkeypatch
) -> None:
    """真的跑一次 MainWindow。

    上面那些驗的是「給定兩個 list，話講得對不對」。這一條驗的是**那兩個 list
    有沒有被填對**——而那是接線，純函式測不到。這個專案已經有好幾次是純邏輯
    對、接線沒接上。
    """
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from app.services.project_registry import ProjectEntry
    from app.ui.main_window import MainWindow

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    first = _project(tmp_path, "a")
    second = _project(tmp_path, "b")

    window = MainWindow(first)
    try:
        window.projects.append(ProjectEntry(name="b", path=str(second)))
        window.dashboard_panel.service.queue_store.enqueue(second)
        # 鎖拿不到＝另一個視窗正在跑它，或留下了殘留的鎖定。
        monkeypatch.setattr(window, "_try_acquire_project_lock", lambda path: False)
        logged: list[str] = []
        monkeypatch.setattr(window, "append_log", lambda text="": logged.append(text))

        window._maybe_start_next_queued_project()

        # **比對總結那一句，不是「日誌裡有沒有出現『鎖定』兩個字」。**
        # 每個被略過的候選在當下就各自記了一行「偵測到執行中鎖定」，所以那兩個
        # 字一定在——第一版就是這樣寫的，而負向驗證直接判它沒有牙齒：
        # 把 `skipped_locked.append(...)` 拿掉，總結退回「不知道原因」，
        # 而測試照樣綠，因為它看到的是另一行裡的同一個詞。
        #
        # 「儀表板」只有總結那一句會講，所以它才是這條要問的東西。
        assert any("儀表板" in line for line in logged), logged
        assert not any("用量設定較高" in line for line in logged), logged
    finally:
        window.deleteLater()
