"""線上那顆「↓」按下去，新的那一格**真的接在上一格後面**。

老闆 2026-08-27 打開 App 報了三件，這是第三件（「往下擴增第二行」按不出來）。
他定案 ⊕ 與 ↓ 的語意時說的是：

> 我覺得 ＋ 號是在兩個 node 中間加一個 node，
> 但往下一行的話，是要兩個 node 中間**往下拉一行**出來才對。

**分支的起點在那條線上，不是某一格**——他不必先在腦中想像那條線會怎麼接，
按方向就好。所以兩顆按鈕送的是同一個動作，只是 ↓ 多帶一半：

    add_slot | <插在第幾格前面>@<接在誰後面>

橫的 ⊕ 送 `"1"`，線上的 ↓ 送 `"1@author"`。

## 這一支跟 `test_a_seat_inserted_between_two_others_lands_there` 的分工

那一支守 `@` **前面**那一半（插在第幾格）。這一支守 `@` **後面**那一半，
以及一件規格裡沒寫、而天真的實作一定會有的事：**接線被拒的時候不准留半格。**
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


@pytest.fixture
def panel():
    QApplication.instance() or QApplication([])
    return LineupWebPanel(Path(tempfile.mkdtemp()))


def test_the_down_arrow_carries_the_parent_through(panel) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：`_add_slot` 不拆 `@`。那一刻整個字串會被當成位置，
    `int()` 爆掉、收成一句拒絕——他按「往下分一行」，拿到一句
    「看不懂的位置」，而那條線是他**按出來**的，不是他打的。
    """
    before = panel._lineup.roles
    panel.handle_action("add_slot", f"1@{before[0]}")
    slots = panel._lineup.slots
    assert len(slots) == len(before) + 1, (
        f"按了「往下分一行」而格數沒變：{[s.role for s in slots]}"
    )
    fresh = slots[1]
    assert fresh.role not in before, "插進去的不是新的那一格"
    assert fresh.parent == before[0], (
        f"新的那一格接在 {fresh.parent!r}，而他按的那條線是從 {before[0]!r} 出去的"
        "——線是按出來的，不是設定出來的，所以這裡不該有第二種答案。"
    )


def test_the_plain_plus_does_not_wire_anything(panel) -> None:
    """橫的那顆 ⊕ **不接線**。

    兩顆按鈕共用一個動作，所以要有東西守住它們不會變成同一顆。
    這一條紅掉的樣子：⊕ 也自己接一條線——他只是想插一格，
    畫面上多出一條他沒畫過的線，而那張圖看起來會像他畫的。
    """
    panel.handle_action("add_slot", "1")
    fresh = panel._lineup.slots[1]
    assert fresh.parent == "", (
        f"橫的 ⊕ 自己接了一條線到 {fresh.parent!r}——他沒有畫那一條"
    )


def test_a_refused_wiring_leaves_no_half_added_seat(panel) -> None:
    """接線被拒的時候，**那一格也不准留下來**。

    天真的寫法是「先加好、再接線」，而接線丟例外的時候那一格已經在
    `self._lineup` 裡了——他按「往下分一行」，拿到一句錯誤訊息
    **外加一格他沒要的**，而那一格會被 `_persist()` 寫進硬碟。

    收乾淨了跟沒做過，在這裡必須長得一樣。
    """
    before = [s.role for s in panel._lineup.slots]
    said: list[str] = []
    panel.reject = said.append  # type: ignore[method-assign]
    panel.handle_action("add_slot", "1@這一格不存在")
    assert [s.role for s in panel._lineup.slots] == before, (
        f"接線被拒，卻留下了一格：{[s.role for s in panel._lineup.slots]}"
    )
    assert said, "擋下來了卻一個字都沒說——按了沒反應跟接好了長得一樣"


def test_the_flag_answers_for_both_halves() -> None:
    """那顆按鈕畫不畫，看的是**兩半都走得通**。

    `supports_insert_at` 同時決定 ⊕ 與 ↓ 畫不畫，而 ↓ 承諾的是
    **插一格＋接一條線**。只驗前半的話，`@` 那一刀被誰拆掉的那天
    旗標還是說是，而那顆按鈕還是被畫出來——
    頁面那一側的註解寫著它為什麼要問：「**這裡不畫一顆說謊的按鈕**」。
    """
    QApplication.instance() or QApplication([])
    src = Path("app/ui/lineup_web_panel.py").read_text(encoding="utf-8")
    body = src[src.index("def _insert_at_reaches_the_engine") :]
    body = body[: body.index(chr(10) + "    def ")]
    assert "with_parent" in body, (
        "那個旗標沒有驗「接得上線」那一半——它守的兩顆按鈕裡有一顆會說謊"
    )
    assert LineupWebPanel._insert_at_reaches_the_engine() is True, (
        "旗標回 False：⊕ 與 ↓ 兩顆今天都不會被畫出來"
    )
