"""「這一輪」那個下拉，只給引擎真的會派工的那幾種。

老闆 2026-08-27：

> 現在在 node 命名下面有一個「這一輪：XX」這邊不能手動更改，那我沒有辦法做流程設計吧?
> 能不能把這一輪 XX 的 XX 用下拉式選單就好?

在這之前那張卡片上是兩列：`職責 [執行▾]`（改得動）與 `這一輪：動手做`
（改不動，而它就是上面那一格的結果）。**兩列、同一件事、一個能改一個不能改。**

## 這一支守的那件事

合成一列之後，選項**跟著序列走**。計畫序列裡引擎只跑兩種工作，
所以那個下拉只有兩項——**他選不到一個引擎不會派工的東西**。

今天（2026-08-27 之前）他在計畫序列裡選得到「拍板」，而 `_run_planning`
一次都不會碰核准那一格。

> **一個可以選、而選了不會發生的選項，比沒有那個選項貴。**
> 沒有他會去問；有他不會。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.orchestrator import Orchestrator  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402


@pytest.fixture
def panel():
    QApplication.instance() or QApplication([])
    return LineupWebPanel(Path(tempfile.mkdtemp()))


def _choices(panel) -> list[dict]:
    return panel.collect_state()["lineup"]["duty_choices"]


def test_the_options_are_exactly_what_this_sequence_dispatches(panel) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：下拉裡寫死四種。他在計畫序列裡選得到「拍板」，
    而引擎一次都不會派工給它——一個選了不會發生的選項。
    """
    for mode in ("development", "planning"):
        panel.handle_action("set_run_setting", f"workflow_mode:{mode}")
        offered = [c["id"] for c in _choices(panel)]
        runs = list(Orchestrator.SEQUENCE_JOBS[mode]["jobs"])
        assert offered == runs, (
            f"{mode} 這條序列真的派工給 {runs}，而下拉給了 {offered}——"
            "多的那幾種他選得到，而引擎不會做。"
        )


def test_the_planning_sequence_cannot_offer_approve(panel) -> None:
    """計畫序列裡選不到「拍板」。

    這一條把上面那條的**實際後果**單獨釘住：上面那條比的是兩張表一不一樣，
    改壞的方式之一是**兩張表一起錯**。這一條認的是那個具體的名字。
    """
    panel.handle_action("set_run_setting", "workflow_mode:planning")
    assert "approve" not in [c["id"] for c in _choices(panel)], (
        "計畫序列的下拉裡有「拍板」——而 `_run_planning` 一次都不會碰核准那一格"
    )


def test_every_option_says_what_that_round_does(panel) -> None:
    """每一項的字是**這一輪要做什麼**，不是職責的代號。

    他要的是「這一輪：⋯」那一句，不是 `work`／`review`。
    畫面拿到代號自己去翻的話，那份對照表就是第二個答案。
    """
    for c in _choices(panel):
        assert c["text"].startswith("這一輪"), c
        assert c["text"] != c["id"], f"{c['id']} 沒有翻成人話"
