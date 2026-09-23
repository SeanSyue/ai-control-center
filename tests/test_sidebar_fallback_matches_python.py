"""側邊欄那份 fallback 清單，跟 Python 的那份要一樣。

`shell.js` 的 `DEFAULT_PAGES` 是側邊欄清單的**第二份副本**。真正的清單由
Python 推（只有它知道哪些面板真的掛上了），fallback 只在沒有後端的時候出現
——也就是**設計端單獨用瀏覽器開檔案的時候**。

所以它過期沒有人會發現：app 裡看不到它，測試也不會碰到它，唯一會讀到它的人
是照著它畫版面的設計端。2026-08-11 校對，它已經漂了兩項：

* 列著「對話庫」——那一頁 2026-08-09 就退役刪除了。
* 少了「即時紀錄」。

兩個方向都是會造成實際浪費的：照著它做，設計端會替一個不存在的頁面畫入口，
而真的存在的那一頁沒有人替它畫。ROADMAP P11 甚至寫過同一件事的另一半
（「即時紀錄在 `DEFAULT_PAGES` 裡沒有對應項目，用頁面自己的預設清單那一頁
會直接沒有入口」）——**寫下來了，然後就沒有下文**。散文不會變紅。

這個檔案就是讓它變紅的東西。它不驗版面、不驗樣式，只驗**兩份清單講同一件事**。
"""
from __future__ import annotations

import re
from pathlib import Path

from app.ui.shell_panel import PAGES, PAGE_GROUPS

SHELL_JS = Path("app/ui/web/shell.js")


def _fallback() -> list[dict[str, str]]:
    """把 `DEFAULT_PAGES` 那一段字面值讀出來。

    切法本身會斷言成功。這種解析最糟的失敗方式是**安靜地讀到零筆**，
    那樣底下每一條比對都會拿空清單去比空清單，永遠通過。
    """
    source = SHELL_JS.read_text(encoding="utf-8")
    assert "DEFAULT_PAGES = [" in source, "找不到 DEFAULT_PAGES，這裡的切法要跟著改"
    block = source.split("DEFAULT_PAGES = [", 1)[1].split("];", 1)[0]

    entries = []
    for line in block.splitlines():
        item = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", line))
        if "key" in item:
            entries.append(item)
    assert entries, "DEFAULT_PAGES 解析到零筆——切法壞了，不是清單空了"
    return entries


def test_the_fallback_lists_the_same_pages_in_the_same_order() -> None:
    assert [item["key"] for item in _fallback()] == [key for key, _ in PAGES], (
        "側邊欄的 fallback 清單跟 Python 的對不上。\n"
        "它只在沒有後端時出現，也就是設計端唯一看得到的版面——多一頁，"
        "他們會替一個不存在的入口畫圖；少一頁，真的存在的那一頁沒有人畫。"
    )


def test_the_fallback_uses_the_same_labels() -> None:
    """字也要一樣。

    預覽上寫著另一個名字，設計端就會照那個名字排版與命名圖層。

    2026-08-24：`PAGES` 那一欄存的**變成 key 了**（介面文字搬進
    `app/i18n.py`，而那張表在模組層——存字會在 import 的當下凍住）。
    所以這裡比的是**取出來的那一句**，不是那張表裡的值。
    **要守的事沒有變。**
    """
    from app.i18n import t

    assert [item["label"] for item in _fallback()] == [
        t(key) for _, key in PAGES
    ]


def test_the_divider_marks_the_same_entry() -> None:
    """分隔線畫在哪一項，兩邊要一致。

    這條線是這一頁少數帶著意思的樣式：上面八項是「做事」，決策紀錄是
    「證明做過的事」。畫錯位置比沒有線更糟——它會宣稱一個不存在的分類。
    """
    grouped = {
        item["key"]: item["group"] for item in _fallback() if "group" in item
    }

    assert grouped == PAGE_GROUPS
