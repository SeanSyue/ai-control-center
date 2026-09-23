"""佇列那五個控制項，畫面按得到。

N8（2026-08-24 頁面設計那一輪查到的）：

> `queue_store` 的五個操作**引擎一直都在**，接線（`queue_capacity_available`）
> 也接好在等，**而畫面上是一份唯讀名單**。

也就是：能力做出來了、線也接好了，而**使用者按不到**——跟沒做一樣。
這一份守的是引擎那一半（`handle_action` 收得到那四種），畫面那一半在
`AICC-視覺概念/交接給頁面設計-2026-08-24.md` §六。

## 為什麼每一種都要發 `queue_capacity_available`

**包含「加進佇列」。**佇列空著而且有位子的時候，加進去那一刻就該開始跑，
不是等下一次有別的事情發生。

漏掉那個訊號的症狀很具體：他按了「恢復」，畫面上佇列還在那裡，
而**什麼都沒有開始跑**，直到他下一次做別的事。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.dashboard_web_panel import QUEUE_ACTIONS, DashboardWebPanel  # noqa: E402


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    made = DashboardWebPanel()
    yield made
    made.deleteLater()
    app = QApplication.instance()
    if app is not None:
        app.processEvents()


def _queue(panel):
    return panel.service.queue_store.load()


def test_the_four_actions_are_listed_in_one_place() -> None:
    """畫面那一側要照著這份做，而一份對得上的清單比五個散落的字串好對。"""
    assert QUEUE_ACTIONS == (
        "queue_add",
        "queue_remove",
        "queue_pause",
        "queue_limit",
    )


def test_adding_and_removing_reach_the_store(panel, tmp_path) -> None:
    project = tmp_path / "案子"
    project.mkdir()

    panel.handle_action("queue_add", str(project))
    assert len(_queue(panel).queued_paths) == 1, "加進佇列按不到"

    panel.handle_action("queue_remove", str(project))
    assert _queue(panel).queued_paths == (), "移除按不到"


def test_pausing_and_resuming_reach_the_store(panel) -> None:
    panel.handle_action("queue_pause", "1")
    assert _queue(panel).paused is True

    panel.handle_action("queue_pause", "0")
    assert _queue(panel).paused is False


def test_the_concurrency_limit_reaches_the_store(panel) -> None:
    panel.handle_action("queue_limit", "3")
    assert _queue(panel).concurrency_limit == 3


def test_a_limit_that_is_not_a_number_does_not_blow_up(panel) -> None:
    """**送過來的不是數字，不准丟例外。**

    第一版的說明寫著「存一個 0 進去的話那個清單永遠沒有位子」——
    **那句話是錯的**：`set_concurrency_limit` 自己夾在 1~5，存不進 0
    （負向驗證 N8-3 是綠的才發現）。

    真正的風險是另一件：`int("很多")` 會丟 `ValueError`，而那是在一個
    **動作處理器**裡面。一個從畫面按下去、然後在 Python 這一側炸掉的動作，
    使用者看到的是什麼沒有人保證——可能什麼都沒發生，可能整個面板停住。

    所以這裡守的是「**不丟例外，而且不亂動那個值**」，不是「擋住 0」。
    """
    before = _queue(panel).concurrency_limit
    panel.handle_action("queue_limit", "很多")   # 不准在這裡炸
    assert _queue(panel).concurrency_limit == before


def test_every_queue_action_tells_the_scheduler(panel, tmp_path) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：他按了「恢復」，佇列還在那裡，而什麼都沒有開始跑
    ——直到他下一次做別的事。
    """
    project = tmp_path / "案子"
    project.mkdir()

    fired: list[int] = []
    panel.queue_capacity_available.connect(lambda: fired.append(1))

    for kind, arg in (
        ("queue_add", str(project)),
        ("queue_pause", "0"),
        ("queue_limit", "2"),
        ("queue_remove", str(project)),
    ):
        before = len(fired)
        panel.handle_action(kind, arg)
        assert len(fired) > before, f"{kind} 沒有告訴排程器有位子了"


def test_the_paths_stay_lined_up_with_the_names(panel, tmp_path) -> None:
    """`queue` 是名字，`queue_paths` 是路徑，**逐格對齊**。

    畫面上顯示名字，但「把這一列移除」要送得回一個認得出來的東西——
    而名字會撞（兩個專案資料夾同名是很平常的事）。

    對不齊的樣子：他按第二列的移除，被移掉的是第三個。
    """
    for name in ("甲", "乙", "丙"):
        folder = tmp_path / name
        folder.mkdir()
        panel.handle_action("queue_add", str(folder))

    state = panel.collect_state()
    assert len(state["queue"]) == len(state["queue_paths"]) == 3
    assert state["queue_paths"] == list(_queue(panel).queued_paths)


def test_the_page_is_told_whether_the_queue_is_paused(panel) -> None:
    """暫停是一個**狀態**，不是一個動作。

    畫面上那顆按鈕要顯示「恢復」，而佇列那幾列要看得出來它們不會自己開始。
    """
    panel.handle_action("queue_pause", "1")
    assert panel.collect_state()["queue_paused"] is True
