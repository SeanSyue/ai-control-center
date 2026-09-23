"""四種職責一律灰底，「會寫檔案」是**另一個標籤**。

頁面設計 2026-08-23 提的，我回「接受，我們改」，而那一句後面還有：

> **那條會紅的檢查我們寫**（職責標籤的 class 不得含 `aqua`）——
> 它咬的是頁面產物，但守的是引擎這邊的決定，**放在我們這裡才不會隨頁面
> 改版消失**。

## 為什麼一律灰底

`回覆給系統開發-19.md` 第 4 節：

> 四種職責裡，使用者實際需要一眼分辨的**只有一件事**：
> **這一格會不會真的動我的檔案。**
>
> **把四種畫成四種同等的樣子，等於宣稱它們同等重要，
> 而那件真正要緊的事會消失在其他三個裡面。**

所以分別靠**有沒有那個標籤**，不靠顏色：`work` 那一格多掛一個
「會寫檔案」，另外三種什麼都沒有。零新增視覺語彙。

## 他們立論裡我驗過的那兩點

- `can_write` 就是 `duty == work`——**一個欄位、一個視覺**，class 與字面同源
- 同一頁上 `.bdg.aqua` 已經有另一個用處（「可續接」），`.job` 也是水藍

也就是說：職責用 aqua 的話，同一個顏色在同一頁上講三件不同的事。
"""
from __future__ import annotations

import re
from pathlib import Path

BUILT = Path("app/ui/web/lineup_v1.html")
SOURCE = Path("design-pages/lineup_v1.html")

# 那個標籤的字。**寫在這裡一份**：只比 class 不比字的話，有人把
# 「會寫檔案」改成別的說法，這條照樣是綠的。
THE_WORDS = "會寫檔案"


# 職責在畫面上長什麼樣，2026-08-25 改過一次：從一個 badge 變成一個下拉
# （那是 `set_slot_duty`，擋著五節點測試的兩顆控制項之一）。
#
# **這條檢查的理由沒有變，形狀變了。**所以兩種寫法都認：
# 舊的 `${duty?`（badge）與新的 `data-duty=`（下拉）。
# 舊的留著不是為了相容，是為了**改回去的那一天它照樣咬得到**。
DUTY_MARKUP = ("${duty?", "data-duty=")


def _duty_badge_lines(text: str) -> list[str]:
    """畫職責的那幾行。

    比對範圍窄是刻意的：整份檔案裡 `aqua` 有七八個合法用處
    （聚焦外框、進度條、「可續接」⋯⋯），**全檔掃 aqua 會誤報**，
    而一條會誤報的檢查比沒有更貴。
    """
    lines = text.splitlines()
    found: list[str] = []
    for i, line in enumerate(lines):
        if not any(mark in line for mark in DUTY_MARKUP):
            continue
        # 一個 badge 的寫法會跨行（字串用 + 接下去），往下多收兩行
        found.extend(lines[i : i + 3])
    return found


def test_the_scan_finds_the_badge() -> None:
    """前提檢查：抓不到的話底下每一條都是恆真的。

    2026-08-25 它真的紅了一次，而**它自己的訊息寫對了下一步**：
    職責從 badge 改成下拉之後 `${duty?` 抓不到任何東西。
    改的是這條檢查認得的形狀，不是把它刪掉。
    """
    assert BUILT.is_file(), f"{BUILT} 不見了"
    assert _duty_badge_lines(BUILT.read_text(encoding="utf-8")), (
        "在頁面產物裡找不到畫職責的那一段——"
        f"它可能又改寫法了（現在認得 {DUTY_MARKUP}），"
        "這條檢查要跟著改，不是刪掉"
    )


def test_the_duty_badge_is_never_aqua() -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它 2026-08-23 之前的樣子：
    `class="bdg ${s.can_write?'aqua':'grey'}"`。
    """
    offenders = [
        line.strip()
        for line in _duty_badge_lines(BUILT.read_text(encoding="utf-8"))
        if "aqua" in line
    ]
    assert not offenders, (
        "職責標籤又用了 aqua：\n  "
        + "\n  ".join(offenders)
        + "\n\n四種職責一律灰底。把四種畫成四種同等的樣子，等於宣稱它們"
        "同等重要，而唯一真正要緊的那件事（這一格會不會動我的檔案）"
        "會消失在其他三個裡面。\n"
        "而且同一頁上 .bdg.aqua 已經有另一個用處（「可續接」）——"
        "同一個顏色講三件事。"
    )


def test_the_writing_seat_gets_its_own_badge() -> None:
    """**分別靠「有沒有那個標籤」。**

    拿掉顏色而不加這個標籤的話，畫面上四種職責變成完全一樣——
    使用者再也看不出哪一格會動他的檔案，而那是**唯一**他需要一眼分辨的事。
    這條跟上面那條要一起在：只做上面那條是把資訊拿掉。
    """
    text = BUILT.read_text(encoding="utf-8")
    assert THE_WORDS in text, (
        f"頁面上找不到「{THE_WORDS}」那個標籤。"
        "職責標籤拿掉顏色之後，這是唯一還看得出「這一格會動我的檔案」的東西。"
    )
    # **它要掛在 `writes_files` 上，不是 `can_write`。**
    #
    # 2026-08-26 從 `can_write` 改過來，而改的人是對的：
    # `can_write` 是「職責**准不准**動檔案」，`writes_files` 是「這一輪
    # **會不會**動」。使用者把「不編輯專案檔案」那顆框勾起來之後，
    # 職責還是「執行」（准），但這一輪不會動（不會）。
    #
    # **掛在 `can_write` 上的話，這顆標籤會跟那顆勾選框講相反的話**——
    # 一個寫著「會寫檔案」，一個打著勾寫著「不要動」。
    assert re.search(
        r"writes_files\?`?<span class=\"bdg[^\"]*\"", text
    ), f"「{THE_WORDS}」沒有掛在 writes_files 上"


def test_the_extra_badge_is_grey_too() -> None:
    """連它自己也是灰的。

    給它一個顏色會讓「零新增視覺語彙」那句話失效——而這一頁上每一個
    顏色都已經有主人了。
    """
    text = BUILT.read_text(encoding="utf-8")
    match = re.search(r"writes_files\?`?<span class=\"bdg ([a-z]+)\"", text)
    assert match, "找不到那個標籤的 class"
    assert match.group(1) == "grey", (
        f"「{THE_WORDS}」用了 {match.group(1)}，而它應該跟職責標籤一樣灰"
    )


def test_the_built_page_matches_the_design_source() -> None:
    """產物跟設計原始檔要一致。

    `app/ui/web/*` 是建置產物——直接改它會被雜湊守衛擋下來，但**「改了
    設計檔忘記重建」不會被擋**，而那時候畫面上完全看不出差別。
    """
    if not SOURCE.is_file():
        import pytest

        pytest.skip(f"這台機器上沒有 {SOURCE}（設計資料夾不在旁邊）")

    source_badge = _duty_badge_lines(SOURCE.read_text(encoding="utf-8"))
    built_badge = _duty_badge_lines(BUILT.read_text(encoding="utf-8"))
    assert source_badge == built_badge, (
        "設計原始檔跟頁面產物的職責標籤對不上——"
        "改完設計要跑 `app/ui/web/build_pages.py`。"
    )
