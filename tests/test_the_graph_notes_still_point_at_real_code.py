r"""`docs/Graph-Engineering-對照AICC-2026-08-25.md` 裡引的行號，要真的在。

## 為什麼這一支值得存在

那一份文件的**唯一價值**是「別人講的概念」跟「我們手上真的有的東西」之間
那條線畫在哪。而那條線是用**檔名行號**畫的：

> `AgentSlot`（`app/core/lineup.py:285`）只有五個欄位

程式一搬家，那個行號就指到別的東西——而**一份指錯地方的對照表，讀起來
跟一份對的一模一樣**。這個專案 2026-08-24 才為同一個病付過學費
（30 條假綠燈裡 29 條在記憶裡有前身，其中 9 條的紀錄寫著「已修」）。

## 它守什麼

每一個 `檔名:行號`：檔案要在、行號不能超過檔案長度、**而且那一行要真的
是文件說的那個東西**（一個函式定義、一個類別、或文件自己引的那句話）。

## 它擋不住什麼

**擋不住「行號對得上但那一行的意思變了」**——例如 `criteria_seat` 還在
第 150 行，但它的規則被改掉了。那一半沒有機制，只有讀。

寫出來是因為【每一條檢查都講得出改壞的樣子】：只寫擋得住的部分，
下一個人會以為那一塊已經安全了。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "Graph-Engineering-對照AICC-2026-08-25.md"

# 文件裡寫的路徑有長有短（`lineup.py:181`、`app/core/lineup.py:285`），
# 因為在一段講 `lineup.py` 的文字裡寫全路徑很吵。這裡按順序試。
SEARCH_ROOTS = ("", "app", "app/core", "app/core/steps", "app/services", "app/ui", "tools")

CITATION = re.compile(r"`([A-Za-z0-9_/]+\.py):(\d+)")

# **檔名不帶行號的那些也要查。**
#
# 2026-08-25：這份文件引了 `app/services/rule_layers.py` 兩次，而那個檔案
# 在 `app/core/`。上面那條樣式**只認帶行號的**，所以它一次都沒看過那兩行。
#
# 這一整支的檔頭寫著「每一個 `檔名:行號`」——而它守的範圍比那句話小。
# 同一個形狀 `docs/WHY.md` 已經記過一次（【全稱句的檢查，掃的範圍比它說的
# 小】），**而這一次犯的人是寫那條紀錄的我**。
BARE_PATH = re.compile(r"`((?:app|tools|tests|docs)/[A-Za-z0-9_/]+\.py)`")

# 那一行至少要**像**一個定義。文件引的全部是類別或函式，
# 唯一的例外是 `consulting.py:54`（引的是那一行取全部座位的程式）。
LOOKS_LIKE_AN_ANCHOR = re.compile(r"^\s*(def |class |[A-Za-z_][A-Za-z0-9_]*\s*=)")


def _resolve(relative: str) -> Path | None:
    for prefix in SEARCH_ROOTS:
        candidate = ROOT / prefix / relative if prefix else ROOT / relative
        if candidate.is_file():
            return candidate
    return None


def _citations() -> list[tuple[str, int]]:
    return [(m.group(1), int(m.group(2))) for m in CITATION.finditer(DOC.read_text(encoding="utf-8"))]


def test_the_document_is_still_there() -> None:
    """前提檢查：文件不在的話，底下每一條都是恆真的。"""
    assert DOC.is_file(), f"{DOC.name} 不見了——整支測試該跟著刪"
    assert _citations(), "文件裡一個行號都沒有，這條檢查什麼都沒檢查到"


@pytest.mark.parametrize("relative,line", _citations())
def test_every_cited_line_exists(relative: str, line: int) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：`app/core/lineup.py` 往上搬了 20 行，於是文件裡那句
    「`AgentSlot` 只有五個欄位」指到了別的東西——而讀的人不會發現。
    """
    path = _resolve(relative)
    assert path is not None, (
        f"文件引了 `{relative}`，而這個 repo 裡沒有那個檔案。"
        "**一份指錯地方的對照表，讀起來跟一份對的一模一樣。**"
    )
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    assert line <= len(lines), (
        f"`{relative}:{line}` 超過檔案長度（{len(lines)} 行）"
    )


@pytest.mark.parametrize("relative,line", _citations())
def test_every_cited_line_still_looks_like_what_it_claims(
    relative: str, line: int
) -> None:
    """那一行要**還是一個定義**，不是搬家之後剛好落在的空白行。

    只檢查「行號沒有超過檔案長度」的話，一個往下搬了 30 行的函式
    照樣是綠的——那個檢查證明的是「檔案夠長」，不是「指對了」。
    """
    path = _resolve(relative)
    assert path is not None
    text = path.read_text(encoding="utf-8-sig").splitlines()[line - 1]
    # **不准寫成 `... or text.strip()`。**第一版是那樣寫的，而文件引的
    # 五行全部非空，所以那個 `or` 讓整條斷言恆真——破壞驗證 Q3 當場抓到。
    assert LOOKS_LIKE_AN_ANCHOR.match(text), (
        f"`{relative}:{line}` 現在是一行空的：{text!r}\n"
        "程式搬家之後那個行號指到別的地方了。"
        "**一份指錯地方的對照表，讀起來跟一份對的一模一樣。**"
    )


def _bare_paths() -> list[str]:
    return sorted(set(BARE_PATH.findall(DOC.read_text(encoding="utf-8"))))


def test_the_scan_sees_the_paths_without_line_numbers() -> None:
    """前提檢查：抓不到的話底下那一條是恆真的。"""
    assert _bare_paths(), (
        "文件裡一個不帶行號的路徑都沒抓到——樣式可能壞了，"
        "而壞掉的樣式讀起來跟「全部都對」一模一樣"
    )


@pytest.mark.parametrize("relative", _bare_paths())
def test_every_bare_path_exists(relative: str) -> None:
    """**這一條是 2026-08-25 補的，因為上面那幾條漏掉了它守的東西。**

    改壞的樣子：文件寫 `app/services/rule_layers.py`，而那個檔案在
    `app/core/`。讀的人照著去找，找不到，然後以為那個東西不存在——
    **而它存在，只是文件指錯地方**。
    """
    assert (ROOT / relative).is_file(), (
        f"文件引了 `{relative}`，而這個 repo 裡沒有那個檔案。"
        "**一份指錯地方的對照表，讀起來跟一份對的一模一樣。**"
    )
