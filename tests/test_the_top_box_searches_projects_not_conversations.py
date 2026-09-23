"""最上面那個框搜**專案**，下面兩欄各自搜**對話**。

老闆 2026-08-27：

> 最上面的對話框，我希望做的是搜尋專案，邏輯上比較接近 AICC、codex、claude
> 的專案資料夾。下面選對話的是在搜尋對話框，這個概念比較不一樣。

## 他抓到的是真的

`conversation_search.score_session()` 比對**兩樣**——對話標題與**專案路徑**
——而那個框的提示字就寫著「搜尋對話名稱或專案路徑…」。

> **一個框回答兩個問題。**而他是從使用者那一端看出來的：
> 「我要找哪個專案」跟「我要接哪個對話」在他腦中是分開的兩件事。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402


class _Entry:
    """一筆假的專案登記。只要有 `name` 與 `path`，跟真的那一個同形。"""

    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path


@pytest.fixture
def panel(monkeypatch):
    """**自己造專案，不要靠這台機器上剛好有幾個。**

    第一版沒有這一段，於是在沙箱裡 `ProjectRegistry().load()` 回空清單，
    而「比不到就回空」跟「本來就是空的」在斷言上一模一樣——
    負向驗證當場把那兩條判成「綠 ✗ 沒有牙齒」。

    > **一個永遠拿不到資料的測試，跟一個通過的測試長得一樣。**
    """
    QApplication.instance() or QApplication([])
    made = LineupWebPanel(Path(tempfile.mkdtemp()))
    from app.services import project_registry

    monkeypatch.setattr(
        project_registry.ProjectRegistry,
        "load",
        lambda self: (
            [
                _Entry("AI Control Center", r"C:\work\AICC"),
                _Entry("剪片專案", r"D:ideos\cut"),
                _Entry("客戶開戶", r"D:\clients\kyc"),
            ],
            "",
        ),
    )
    return made


def _projects(panel) -> dict:
    return panel.collect_state()["lineup"]["projects"]


def test_the_page_is_given_a_project_list_of_its_own(panel) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它 2026-08-27 之前的樣子：畫面上只有一個框，
    而它搜的是對話（順便比對路徑）。他要找專案的時候，
    打進去的字會比中哪一種，**畫面上看不出來**。
    """
    entry = _projects(panel)
    assert "found" in entry and "typed" in entry, entry
    assert isinstance(entry["found"], list)


def test_a_query_that_matches_nothing_returns_nothing(panel) -> None:
    """比不到就是空的，**不准回全部**。

    一個「查不到就給你全部」的搜尋框，跟一個壞掉的搜尋框長得一模一樣
    ——而他會以為那些就是比中的。
    """
    assert len(_projects(panel)["found"]) == 3, "前提壞了：這一輪根本沒有專案可以濾"
    panel.handle_action("find_projects", "這幾個字不可能出現在任何專案名字裡")
    assert _projects(panel)["found"] == []
    # 而比得到的時候要真的只給比得到的那幾筆
    panel.handle_action("find_projects", "剪片")
    assert [x["name"] for x in _projects(panel)["found"]] == ["剪片專案"]


def test_the_two_boxes_do_not_share_one_word(panel) -> None:
    """兩個框的字**分開存**。

    共用一個的話，他在上面打「AICC」會把下面那兩欄的對話也濾掉，
    而畫面上看不出是誰濾的。
    """
    panel.handle_action("find_projects", "只給上面那個框的字")
    assert _projects(panel)["typed"] == "只給上面那個框的字"
    # 下面那兩欄的字是另一個面板在管（`conversation_web_panel._query`），
    # 這裡守的是「陣容頁沒有把自己的字寫到對話那一側」。
    assert getattr(panel, "_query", "") != "只給上面那個框的字"


def test_it_says_which_one_he_has_open(panel) -> None:
    """哪一個是他現在打開的，**引擎算好**。

    畫面拿名字自己比的話會出錯——同名的資料夾是存在的。
    """
    found = _projects(panel)["found"]
    assert found, "前提壞了：一個專案都沒有的話，底下這條是恆真的"
    # 三筆都不是他打開的那一個（他打開的是暫存目錄），所以三筆都要是 False。
    assert [x["current"] for x in found] == [False, False, False], (
        "有一筆被標成「你正在這裡」，而他根本不在那裡——"
        "同名的資料夾是存在的，拿名字比會出錯"
    )
