r"""頁面自己喊的話，要有人聽得到。

## FG27：一個裝了警報、而警報接到牆裡的產品

每一頁都有偵測「Python 推下來的東西跟我畫出來的對不上」的機制
（`settings_v1.html` 的 `checkEcho`、其他頁的欄位比對），而它們喊的方式是
`console.warn`。

**而 App 的 `QWebEnginePage` 沒有覆寫 `javaScriptConsoleMessage`，
所以那句話連 stderr 都到不了。**

2026-08-25 量到三種樣子，**畫面上都像正常運作**：

| 使用者看到 | 實際 |
|---|---|
| 決策紀錄頁把已經回答過的決策畫成「尚未回答」，頂部節點數翻倍 | 頁面喊了，沒有人聽 |
| 設定頁把他剛關掉的開關彈回開著 | 同上 |
| 設定頁少掉幾列（例如權限模式那一列）| 他以為那個設定不存在 |

> **一個沒有人在聽的警報，跟沒有警報一樣**——而它更貴，
> 因為寫它的人以為那件事已經被守住了。

## 為什麼端到 stderr 而不是彈視窗

這些是**開發期的對不上**，不是使用者做錯事。彈視窗會讓一個正在用產品的人
被一句他看不懂的話打斷。

## 為什麼 `info` 不端

頁面自己的除錯輸出會把真的警報淹掉——**一個永遠在響的警報等於沒有警報**。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: E402

from app.ui.web_panel import _PageThatIsHeard  # noqa: E402

LEVELS = QWebEnginePage.JavaScriptConsoleMessageLevel


class _Spy(_PageThatIsHeard):
    """不建立真的 QWebEnginePage——這一支測的是**那個決定**，不是 Qt。"""

    def __init__(self) -> None:  # noqa: D107  故意不呼叫 super()
        pass


def _heard(level, message: str, capsys) -> str:
    _Spy().javaScriptConsoleMessage(level, message, 42, "file:///x/settings_v1.html")
    return capsys.readouterr().err


def test_a_warning_from_the_page_reaches_somebody(capsys) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它 2026-08-26 之前的樣子：頁面喊 `console.warn`，
    而那句話連 stderr 都到不了。
    """
    out = _heard(LEVELS.WarningMessageLevel, "推下來的值跟畫出來的對不上", capsys)
    assert "推下來的值跟畫出來的對不上" in out, (
        "頁面喊的話沒有到 stderr——那個警報接在牆裡"
    )


def test_it_says_which_page_and_which_line(capsys) -> None:
    """喊出來要講得出**哪一頁、哪一行**。

    「有東西不對」寫進 stderr 等於沒寫——看到的人要知道去哪裡找。
    """
    out = _heard(LEVELS.ErrorMessageLevel, "壞了", capsys)
    assert "settings_v1.html" in out, out
    assert "42" in out, out


def test_an_error_is_heard_too(capsys) -> None:
    """error 比 warn 更該端出來。"""
    assert "炸了" in _heard(LEVELS.ErrorMessageLevel, "炸了", capsys)


def test_the_page_chatter_does_not_drown_the_alarm(capsys) -> None:
    """**另一半：`info` 不准端。**

    頁面自己的除錯輸出會把真的警報淹掉——**一個永遠在響的警報等於沒有警報**。
    只做上面那幾條的話，把所有等級都端出來是綠的。
    """
    assert _heard(LEVELS.InfoMessageLevel, "我畫完了", capsys) == "", (
        "連 info 都端出來了——真的警報會被淹掉"
    )


def test_every_panel_gets_the_page_that_listens() -> None:
    """**每一頁都要裝，不是只有設定頁。**

    只有一頁裝的話，其他頁的警報照樣接在牆裡，
    而**那幾頁的作者以為自己裝了警報**。
    """
    source = Path("app/ui/web_panel.py").read_text(encoding="utf-8-sig")
    assert "_PageThatIsHeard" in source
    assert "setPage(_PageThatIsHeard(" in source.replace(" ", ""), (
        "共用的那個面板基底沒有裝上會聽的頁面——"
        "每一頁的警報都還接在牆裡"
    )
