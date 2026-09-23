"""寫「已修」的那一則，**要說得出同型的其他位置**。

## 為什麼有這條

2026-08-24 五節點測試前掃了一次假綠燈，30 條站得住。**29 條在記憶裡有可以
逐字引用的前身**，只有 1 條是新的。

而真正的訊號在第二層：**16 條「同一件事的同一個實例」裡，有 9 條當時的紀錄
寫著「已經修好了」**——修的是同一個病的**另一個實例**：

* 收場那條 elif 鏈：修了畫面，沒修例外那條路
* 沒有預設值：修了 `outcomes.py`，泳道那張手抄的表回來了
* 驗收標準不是備胎：修了測試那道門，沒修派工前那道
* 稽核完整性：修了匯出對話框，沒修稽核頁

> **一筆寫著「已修」而只修了一個實例的紀錄，跟一筆真的結案的紀錄，
> 在下一個人讀的時候一模一樣。**而它比假綠燈更貴——假綠燈至少還會被掃到，
> **結案的紀錄不會有人回頭掃。**

## 這條為什麼是機制不是叮嚀

老闆拍板要改格式的時候，我大可以只寫進 `DECISIONS.md` 就算做完。
**這個專案同一天才證實過：沒有機制的規則，失敗的時候不會有任何東西變紅。**

所以格式寫進定案的同時，這裡守它。

## 它擋得住什麼、擋不住什麼

**擋得住**：新寫的「已修」那一則整段沒有〈同型的其他位置〉。

**擋不住**：那一段寫了但內容是假的（列了三個位置而其實有五個）。
那一題沒有機制，只有下一次掃描。寫出來是因為
【每一條檢查都講得出改壞的樣子】——**只寫擋得住的部分，
下一個人會以為那一塊已經安全了**。

`BEFORE_THE_RULE` 是這條規則**生效之前**就寫好的那些。它們不補
——回頭替半年前的紀錄補一段，補的是猜的，而猜的比沒有更糟。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHY = ROOT / "docs" / "WHY.md"

# 這一則在講「修好了」。用詞刻意抓寬——漏抓一則的代價是這條檢查白做。
FIX_WORDS = ("已修", "修好了", "修掉了", "做完了", "已經修")

# 那一段的標題。認這幾種寫法，**不強迫用哪一種**——強迫的話下一個人
# 會為了過這條檢查去改標題，而不是去想那件事。
SAME_KIND_HEADINGS = ("同型的其他位置", "同一種的其他位置", "隔壁那幾個")

# 這條規則 2026-08-24 拍板。在那之前寫好的不補。
#
# **不是豁免，是分界**：回頭替半年前的紀錄補一段「同型的其他位置」，
# 補的是猜的——而一段猜出來的清單，讀起來跟一段查過的一模一樣，
# 正好是這條規則要擋的病。
RULE_TOOK_EFFECT = "2026-08-24"


def _sections(text: str) -> list[tuple[str, str]]:
    """把 `WHY.md` 切成一則一則。回 (標題, 內文)。"""
    parts = re.split(r"^## ", text, flags=re.M)
    out = []
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        out.append((head.strip(), body))
    return out


def _is_after_the_rule(heading: str, body: str) -> bool:
    """這一則是規則生效之後寫的嗎。

    `WHY.md` 的標題大多帶日期（`## 某某某 · 2026-08-24 · #標籤` 或
    `## 2026-08-24　某某某　#標籤`）。抓不到日期的**當成規則之前**
    ——寧可漏抓，不要對一則根本沒有日期的舊紀錄誤報。
    """
    found = re.findall(r"20\d\d-\d\d-\d\d", heading)
    if not found:
        return False
    return max(found) >= RULE_TOOK_EFFECT


def test_every_fix_note_written_after_the_rule_says_where_else_it_lives() -> None:
    """**這一條是這份檔案存在的理由。**"""
    text = WHY.read_text(encoding="utf-8")
    missing = []
    for heading, body in _sections(text):
        if not _is_after_the_rule(heading, body):
            continue
        if not any(word in body for word in FIX_WORDS):
            continue
        if any(mark in body for mark in SAME_KIND_HEADINGS):
            continue
        missing.append(heading)

    assert not missing, (
        "這幾則寫了「修好了」，而沒有說同型的其他位置在哪：\n  "
        + "\n  ".join(missing)
        + "\n\n2026-08-24 掃出的 30 條假綠燈裡，16 條是同一件事的同一個實例，"
        "而**其中 9 條當時的紀錄寫著「已經修好了」**。"
        "\n加一段〈同型的其他位置〉，逐一列出那個例外／那條路／那張表在這個 "
        "repo 的其他出現位置，各標「已處理／沒動，理由」。"
        "\n**一個都找不到也要寫「找過了，只有這一處」**"
        "——「沒有那一段」跟「找過了只有一處」要分得出來。"
    )


def test_the_guard_would_actually_catch_something() -> None:
    """前提檢查：規則生效之後真的有「已修」那種紀錄可以檢查。

    一則都沒有的話，上面那一條是恆真的——而恆真的檢查跟沒有檢查一樣。
    """
    text = WHY.read_text(encoding="utf-8")
    checked = [
        heading
        for heading, body in _sections(text)
        if _is_after_the_rule(heading, body)
        and any(word in body for word in FIX_WORDS)
    ]
    assert checked, (
        "規則生效之後沒有任何一則在講「修好了」，所以上面那條檢查什麼都沒檢查到"
    )
