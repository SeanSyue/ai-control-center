r"""按陣容頁上任何一顆按鈕，**都不准弄丟那一格的職責**。

## 這一條是 2026-08-25 量出來的，不是想出來的

老闆的五節點測試第一步是「加一格 → 改名 → 設職責 → 挑對話」。實測：

    出廠        [('author', 'work'),   ('reviewer', 'review')]
    改名 NODE1  [('node1',  'work'),   ('reviewer', 'review')]
    設職責 work [('node1',  'work'),   ('reviewer', 'review')]
    挑對話      [('node1',  'review'), ('reviewer', 'review')]   ← 這裡

按下「挑對話」的那一刻，那一格從**執行**變成**複查**，畫面上一個字都不說。
之後 `app/core/steps/building.py` 找不到任何 `duty == work` 的位子，整輪收在
「沒有人做事」——**在他還沒看到任何產出之前**。

## 為什麼它躲了這麼久

三個地方（`_set_slot_provider`／`_set_slot_session`／`_move_slot`）是
**逐個位置參數重建 `AgentSlot`**，而 `duty` 是第六格、沒有傳。（`_move_slot`
2026-08-27 之後不再自己組 `AgentSlot`——老闆拍板箭頭搬的是整個 node，順序
那件事整支交給 `Lineup.moved()`。另外兩支還是在這一層組。）空的 `duty`
會由 `AgentSlot.__post_init__` 照舊角色名回推（`_DUTY_BY_LEGACY_ROLE`）：

| 那一格叫什麼 | 回推的結果 |
|---|---|
| `author`（出廠名字）| `work` ——**剛好推回原值** |
| `node1`／`實作`（他自己取的）| `review` |

**所以掉了跟沒掉長得一模一樣**，只要你不改名字。而
`tests/test_lineup_panel.py` 每一條都用出廠名字。

## 同一個病的第五次

`tests/test_a_seat_does_not_quietly_lose_a_field.py` 的檔頭記了 2026-08-22
的四次，還寫著「所以這裡不再靠『記得把新欄位加進去』」——**而它只守
`Lineup` 那一層**。畫面這一層自己組 `AgentSlot`，繞過了它守的每一個方法。

`app/core/workflow_worker.py` 那一處的註解甚至逐字寫著這次的症狀
（「使用者排的『實作』那一格職責會退回預設，引擎因此找不到任何會產出東西的
位子，而畫面上他的陣容看起來一切正常」）。**修好了一個，另外三個還活著。**

## 它守什麼

**每一顆改得動陣容的按鈕，逐欄比對。**用的是 `dataclasses.fields()`，
所以之後再加欄位，這一條會自己涵蓋它。名字刻意用他自己取的
（`node1`），因為出廠名字會把這個病藏起來。
"""
from __future__ import annotations

import os
from dataclasses import fields
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import DUTY_WORK, AgentSlot  # noqa: E402
from app.ui.lineup_web_panel import LineupWebPanel  # noqa: E402

# 他自己取的名字。**不准換成 `author`**——出廠名字剛好會被
# `_DUTY_BY_LEGACY_ROLE` 推回原值，這一整支就變成恆綠的。
HIS_NAME = "node1"
# 第二格。職責刻意用 `approve` 而不是 `review`——見 `_panel` 裡的理由。
HIS_OTHER_NAME = "node2"


def _panel(tmp_path: Path, monkeypatch) -> LineupWebPanel:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    panel = LineupWebPanel(project)
    panel._scanned = True
    panel._hits = {"claude": [], "codex": []}
    # 五節點測試的第一步：改名，然後設職責。
    panel.handle_action("set_slot_role", f"0:{HIS_NAME}")
    panel.handle_action("set_slot_duty", "0:work")
    panel.handle_action("set_slot_identity", "0:你是熟悉境外開戶法規的資深顧問。")
    # 第二格排成**核准**，也是他自己取的名字。
    #
    # 為什麼要有這一格：「換廠牌」那顆按鈕**不動一個會寫檔案的位子**
    # （`app/ui/lineup_web_panel.py` 擋著：會寫檔案的只能由 Claude 擔任），
    # 所以拿第一格去測它，那一次呼叫整個是空的——破壞驗證 D2 當場判它
    # 「綠 ✗ 沒有牙齒」，而它說對了：**案例太順**。
    #
    # 職責也不能用 `review`：掉了之後回推的預設值**剛好就是** `review`，
    # 兩邊一樣，一樣看不出來。`approve` 是這一格唯一測得出東西的選擇。
    panel.handle_action("set_slot_role", f"1:{HIS_OTHER_NAME}")
    panel.handle_action("set_slot_duty", "1:approve")
    panel.handle_action("set_slot_identity", "1:你是最後拍板的人。")
    return panel


# 每一顆改得動陣容的按鈕，以及它**該**改哪一欄。
# 「該改哪一欄」列出來是為了讓比對能說「其餘全部都要一樣」——
# 只斷言 duty 沒掉的話，下一個欄位掉了不會有人發現。
BUTTONS = [
    (0, "set_slot_session", "0:chat-abc", {"session_id"}),
    (1, "set_slot_provider", "1:codex", {"provider", "session_id", "credential"}),
    (0, "set_slot_identity", "0:你是很嚴格的審查員。", {"identity"}),
    (0, "set_slot_duty", "0:review", {"duty"}),
]


@pytest.mark.parametrize("seat,kind,arg,may_change", BUTTONS)
def test_a_button_changes_only_what_it_says_it_changes(
    seat: int, kind: str, arg: str, may_change: set[str], tmp_path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它 2026-08-25 之前的樣子：那三個地方逐個位置參數重建
    `AgentSlot`，第六格 `duty` 沒有傳。
    """
    panel = _panel(tmp_path, monkeypatch)
    before = list(panel._lineup)[seat]
    # 前提：這一格的職責**不是**回推會給的那個預設值，不然掉了跟沒掉一樣。
    assert before.duty != "review", "案例太順：這一格掉了職責也看不出來"

    panel.handle_action(kind, arg)

    after = list(panel._lineup)[seat]
    for field in fields(AgentSlot):
        if field.name in may_change:
            continue
        assert getattr(after, field.name) == getattr(before, field.name), (
            f"按了「{kind}」，而 `{field.name}` 也跟著變了："
            f"{getattr(before, field.name)!r} → {getattr(after, field.name)!r}\n"
            "逐個位置參數重建 `AgentSlot` 的話，後面加的欄位會靜靜消失。"
            "**用 `dataclasses.replace()`。**"
        )


def test_moving_a_seat_carries_the_duty_with_the_seat(
    tmp_path, monkeypatch
) -> None:
    """上下移動：職責跟著**那一格**走（老闆 2026-08-27 拍板搬的是整個 node）。

    改壞的樣子有兩種，而它們都是無聲的：

    * 職責從名字回推（2026-08-25 之前）——移動一次，他自己取名的那一格就從
      「執行」變成「複查」，**做事的那一格沒有了**；
    * 職責被釘在位置上（2026-08-27 之前）——移動一次，做事的變成另一格，
      而卡片上的名字沒有動。

    所以這裡斷言的是**名字與職責的配對**，不是任何一個位置上的值：
    `node1` 走到哪裡都還是做事的那一格。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("set_slot_session", f"0:{HIS_NAME}-chat")
    pairs_before = {s.role: s.duty for s in panel._lineup}
    assert pairs_before[HIS_NAME] == DUTY_WORK, "前提壞了"

    panel.handle_action("move_slot", "0:1")

    assert list(panel._lineup)[1].session_id == f"{HIS_NAME}-chat", (
        "移動根本沒有發生——下面兩條會因此變成恆綠的"
    )
    assert [s.role for s in panel._lineup] == [HIS_OTHER_NAME, HIS_NAME], (
        "整個 node 沒有跟著走——名字留在原位的話，他按下去卡片看起來沒有動"
    )
    assert {s.role: s.duty for s in panel._lineup} == pairs_before, (
        "上下移動之後職責跟名字配錯了。職責是「這一格做哪一種事」，"
        "它跟著那一格走，不跟著位置走"
    )


def test_the_seat_can_still_be_dispatched_after_the_whole_sequence(
    tmp_path, monkeypatch
) -> None:
    """**整條路走完，引擎還找得到一格會做事的。**

    上面每一條分開都可能是綠的，而使用者是**連著按**的。這一條走的就是
    五節點測試的第一步，斷言的是最終那件事：`steps/building.py` 挑得到人。
    挑不到的話那一輪收在「沒有人做事」，而他還沒看到任何產出。
    """
    panel = _panel(tmp_path, monkeypatch)
    panel.handle_action("set_slot_session", "0:chat-abc")
    panel.handle_action("set_slot_provider", "0:claude")
    # **中間那顆按鈕真的要動到東西**，不然這一條走的是一條被擋在門口的路，
    # 而底下那條斷言在那種情況下照樣是綠的（什麼都沒動，做事的那一格當然
    # 還在）。所以移完之後先問一次「他真的搬過去了嗎」。
    panel.handle_action("move_slot", "0:1")
    assert list(panel._lineup)[1].session_id == "chat-abc", (
        "移動沒有發生，這一條就不是「整條路走完」了"
    )
    panel.handle_action("move_slot", "1:-1")

    workers = [s.role for s in panel._lineup if s.duty == DUTY_WORK]
    assert workers == [HIS_NAME], (
        f"排完之後沒有一格會做事（{[(s.role, s.duty) for s in panel._lineup]}）——"
        "引擎會收在「沒有人做事」，而畫面上他的陣容看起來一切正常"
    )


def test_the_page_is_told_what_each_of_his_seats_does_this_round(
    tmp_path, monkeypatch
) -> None:
    """他自己取的名字，**每一格都拿得到這一輪的工作**。

    改壞的樣子：陣容頁把座位的**名字**送給引擎那張工作表，而那張表
    2026-08-25 之前用出廠名字當鍵。於是 `jobs` 是空的，這一頁把**每一格**
    畫成虛線紅框加「不會被執行」——**同一時間引擎照跑照付錢**。

    這一條刻意用他自己取的名字：出廠名字走相容路徑會推回原值，
    **掉了跟沒掉長得一模一樣**（破壞驗證 F8 第一版就是綠的，因為那條
    測試用的是 author/reviewer/finisher）。
    """
    from app.i18n import t  # noqa: PLC0415

    panel = _panel(tmp_path, monkeypatch)
    sequence = panel.collect_state()["sequence"]

    assert sequence["executed_slots"] == len(list(panel._lineup)), (
        f"他自訂名字的陣容有一格拿不到工作：{sequence}——"
        "那一格會被畫成「不會被執行」，而引擎會派工給它"
    )
    # **比字，不只比格數。**只比格數的話，「名字推不出職責一律當複查」那條
    # 相容路徑照樣湊得出同樣的格數——每一格都拿到工作，只是**拿到別人的**。
    # 破壞驗證 F8 第二版就是這樣綠的。
    mode = panel._workflow_mode()
    expected = {
        "development": {"0": t("job.build"), "1": t("job.approve")},
        "planning": {"0": t("job.read_plan"), "1": t("job.approve")},
    }[mode]
    assert sequence["jobs"] == expected, (
        f"這一輪是{mode}，而每一格拿到的工作是 {sequence['jobs']}——"
        "職責是 work／approve，拿到的卻不是它們該有的那一句"
    )
