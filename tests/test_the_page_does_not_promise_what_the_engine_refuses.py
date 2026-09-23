r"""畫面上那句話，引擎要真的做得到。

## 2026-08-25 量到的那一句

改名字那個輸入框的 title 寫著：

> 清空就回到出廠名字，沒有另外的還原按鈕。

而 `Lineup.renamed()` 對空字串是 `raise LineupError`——使用者清空、離開
輸入框，拿到的是一個跳出來的拒絕訊息，不是還原。

而且「出廠名字」**在引擎那邊根本不存在**：新的一格叫 `node3`
（`_add_slot()` 就是這樣編號的），不叫「第三位」。頁面那個
`ORD=['第一位',…]` 是畫面自己編的一組名字，而 `s.role` 永遠不是空的，
所以那個 fallback 一次都不會跑到。

> **判準**：一句畫面上說了、按下去卻不會發生的話，跟一個壞掉的功能
> 一樣貴——使用者會照著那句話做事，然後在**別的地方**發現對不上，
> 而那時候他不會知道要回頭看這一句。

## 這一支怎麼避免自己過期

**先問引擎，再看畫面。**第一條實際呼叫 `renamed(role, "")` 確認它還是
拒絕的；哪一天引擎真的做了「清空＝還原」，那一條會紅，而**那時候該改的
是這一支**，不是畫面。

反過來只掃畫面的話，這支測試會在引擎改行為之後**繼續是綠的**，
然後把一句已經變成真話的警告永遠留在那裡。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.lineup import AgentSlot, Lineup, LineupError

PAGE = Path("app/ui/web/lineup_v1.html")
SOURCE = Path("design-pages/lineup_v1.html")

# 改名字那個輸入框。抓的是它送出去的動作（`data-role=`），
# 不是它的 class——class 是樣式，改樣式不該讓這條紅。
RENAME_BOX = re.compile(r'<input[^>]*data-role="\$\{i\}"[^>]*>', re.S)


def _rename_title() -> str:
    match = RENAME_BOX.search(PAGE.read_text(encoding="utf-8"))
    assert match, (
        "在頁面產物裡找不到改名字那個輸入框——它可能改寫法了，"
        "這條檢查要跟著改，不是刪掉"
    )
    title = re.search(r'title="([^"]*)"', match.group(0))
    assert title, "改名字那個輸入框沒有 title——那句話是唯一告訴他規則的地方"
    return title.group(1)


def test_the_engine_really_does_refuse_an_empty_name() -> None:
    """**前提檢查，而且它是這一整支的錨。**

    這一條紅了的意思是「引擎改了行為」——那時候要改的是這支測試與畫面上
    那句話，不是把它調鬆。
    """
    lineup = Lineup.of([AgentSlot("node1", "claude"), AgentSlot("node2", "codex")])
    with pytest.raises(LineupError):
        lineup.renamed("node1", "")
    with pytest.raises(LineupError):
        lineup.renamed("node1", "   ")


def test_a_seat_cannot_be_built_without_a_name_either() -> None:
    """**同一條規則的內層那一道。**

    破壞驗證 E4b 第一次是綠的，而它說對了一件事：`renamed()` 先攔一次
    （為了給一句講得出理由的話），`AgentSlot.__post_init__` 再攔一次。
    只測外層的話，內層那一道**壞掉也不會有人發現**——直到有一條不經過
    `renamed()` 的路把空名字送進來。

    > 兩層防線只測外層，等於只有一層。
    """
    with pytest.raises(LineupError):
        AgentSlot("", "claude")
    with pytest.raises(LineupError):
        AgentSlot("   ", "claude")


def test_a_seat_lowercases_its_own_name_too() -> None:
    """小寫那一件也是兩層，理由同上。

    這一條走的是**不經過** `renamed()` 的路：套用模式、讀檔、精靈寫進去的
    那幾條都直接建 `AgentSlot`。
    """
    assert AgentSlot("NODE3", "claude").role == "node3"


def test_the_page_does_not_say_clearing_the_box_restores_anything() -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它 2026-08-25 之前的樣子：
    `title="清空就回到出廠名字，沒有另外的還原按鈕。"`
    """
    title = _rename_title()
    for lie in ("回到出廠", "還原", "回復", "恢復"):
        assert lie not in title, (
            f"改名字那句話裡寫著「{lie}」，而引擎對空字串是直接拒絕的。\n"
            f"現在那句話是：{title}\n"
            "**一句畫面上說了、按下去卻不會發生的話，跟一個壞掉的功能一樣貴。**"
        )


def test_the_page_says_the_two_rules_that_really_exist() -> None:
    """**另一半：不准把那句話清空了事。**

    只做上面那條的話，一個空的 title 是綠的——而那等於把兩條真的規則
    （不能空、不能同名）也一起拿掉，使用者要按下去被拒絕才知道。
    """
    title = _rename_title()
    assert "空" in title, "沒有告訴他名字不能是空的——而引擎會擋"
    assert "同名" in title or "重複" in title, (
        "沒有告訴他名字不能跟別格一樣——而引擎會擋，"
        "理由是整條線靠名字指人，同名的話「退給它」有兩個答案"
    )


def test_the_page_mentions_that_english_gets_lowercased() -> None:
    """`renamed()` 會 `.lower()`——打「NODE3」存進去是「node3」。

    這不是新行為，是改名字那顆控制項 2026-08-25 才按得到，
    所以以前沒有人看得見它。**新做一顆控制項，會把它背後那條路上所有的
    舊行為第一次攤在使用者面前。**
    """
    lineup = Lineup.of([AgentSlot("node1", "claude")])
    assert lineup.renamed("node1", "NODE3").roles == ("node3",), (
        "引擎不再把名字轉小寫了——那樣的話畫面上那句話要跟著拿掉"
    )
    assert "小寫" in _rename_title(), (
        "沒有告訴他英文會存成小寫。他打 NODE3、畫面顯示 node3，"
        "而他會以為自己打錯了"
    )


def test_the_built_page_matches_the_design_source() -> None:
    """產物跟設計原始檔要一致——改了設計檔忘記重建不會被雜湊守衛擋下來。"""
    if not SOURCE.is_file():
        pytest.skip(f"這台機器上沒有 {SOURCE}（設計資料夾不在旁邊）")
    match = RENAME_BOX.search(SOURCE.read_text(encoding="utf-8"))
    assert match, f"{SOURCE} 裡找不到改名字那個輸入框"
    assert re.search(r'title="([^"]*)"', match.group(0)).group(1) == _rename_title(), (
        "設計原始檔跟頁面產物的那句話對不上——"
        "改完設計要跑 `app/ui/web/build_pages.py`"
    )
