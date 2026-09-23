"""找死碼的工具本身要能被相信，因為有人會照著它刪東西。

`tools/zero_callers.py` 找「app/ 裡定義了、而 app/ 與 tools/ 都沒有呼叫」的
名字。它的輸出是一張要人去讀的短名單，但**「要人去讀」不是它可以誤報的許可**：
2026-08-11 它把 `native_theme.stylesheet` 列進「只有測試在呼叫」，而
`main_window` 正在用它套整個原生外框的樣式——

    from app.ui.native_theme import stylesheet as native_stylesheet
    ...
    self.setStyleSheet(native_stylesheet(THEME.mode))

改名的 import 讓 `stylesheet` 這個名字在呼叫端一次都不會出現。照著清單刪的話，
**原生視窗會失去全部樣式，而且沒有任何測試會紅**（樣式表沒有斷言）。

所以這個檔案守的是工具的誠實度，方向刻意是**偽陽性**那一邊：一個把活的程式
說成死的工具，比沒有工具糟——它會誘人做出唯一真正不可逆的那種改動。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

TOOL = Path("tools/zero_callers.py")


def _tool():
    spec = importlib.util.spec_from_file_location("zero_callers_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _reported() -> set[str]:
    """工具會列出來的名字（兩欄合起來）。

    呼叫工具自己的 `shortlists()`，**不是在這裡把分類重算一遍**。
    第一版重算了，而負向驗證直接判它沒有牙齒：把工具的 `main()` 改成什麼都
    不報，這個檔案照樣全綠——因為它問的是自己那一份分類，不是工具的。
    """
    tests_only, nowhere, _, _how_many = _tool().shortlists()
    return set(tests_only) | set(nowhere)


def test_the_tool_finds_something() -> None:
    # 前提檢查：解析壞掉時 `_definitions()` 會回空，底下每一條都會恆真。
    assert len(_tool()._definitions()) > 300


@pytest.mark.parametrize(
    "name,why",
    [
        # 這一條就是那次差一點的損害。
        ("stylesheet", "main_window 用 `import stylesheet as native_stylesheet` 套原生外框樣式"),
        # 一般的 attribute 呼叫，確認基本情況沒壞。
        ("push_state", "每個網頁面板都在呼叫"),
        ("collect_state", "WebViewPanel 的核心"),
    ],
)
def test_a_name_that_is_really_used_is_not_reported(name: str, why: str) -> None:
    assert name not in _reported(), (
        f"`{name}` 其實有人在用（{why}），工具卻把它列成沒有呼叫者。\n"
        "偽陽性在這支工具上特別貴：它會誘人刪掉活的程式，"
        "而刪掉之後不一定有測試會紅。"
    )


def test_a_name_that_is_really_unused_is_reported() -> None:
    """反方向。少了這條，一支「什麼都不報」的工具也會全綠。

    挑 `set_role_session`：`session_registry` 自己的註解說組陣容的呼叫者要用
    role-shaped API，而實際上組陣容的地方（`lineup_web_panel`）是照**索引**
    改一格再整份 `save_lineup`——兩個 Claude 座位需要索引層級的定位，
    那正是 ROADMAP P10 修掉的那個 bug。所以這個 setter 沒有呼叫者是事實。
    """
    assert "set_role_session" in _reported()
