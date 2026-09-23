"""三個原生面板砍掉了，它們身上每一個功能都要有一個網頁那一側的家。

2026-08-23 老闆拍板砍掉 `dashboard_panel.py`／`knowledge_panel.py`／
`roadmap_panel.py`（1,222 行）。他先反問的是：

> **新版本有可能壞嗎？壞了不能版本更新嗎？**

查證之後他是對的：那個回退路徑覆蓋的情境，它自己也活不下來——側邊欄本身
就是網頁面板（`ShellPanel(WebViewPanel)`），QWebEngine 起不來的話使用者
連換頁都換不了。

## 這一份守的是刪除的**代價**，不是刪除本身

被刪掉的 `test_knowledge_panel_has_document_management_controls` 是一條真的
有牙齒的檢查：它釘住了那個面板上四十幾個功能。**把它跟著刪掉，那些功能就
沒有人在守了**——而「跟著刪」正是刪東西最容易犯的錯：連刪掉的東西身上的
保險一起帶走。

所以那些斷言不是被刪掉，是**搬到它們現在真正的家**：
文件那幾項在「文件」頁，共同記憶那幾項在「共享記憶」頁。
"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path("app/ui/web")

# 原本釘在 `knowledge_panel.py` 上的文件管理功能，現在住在「文件」那一頁。
DOCUMENT_ACTIONS = (
    "import_files",
    "import_folder",
    "import_wizard",
    "import_transcript",
    "scan_project",
    "reindex_all",
    "detect_changes",
    "remove",
    "preview_relevant",
    "rescan",
)

# 共同記憶那一半住在「共享記憶」那一頁。
MEMORY_ACTIONS = (
    "edit",
    "save",
    "view_all",
    "readiness_check",
    "make_handoff",
    "make_bootstrap",
    "save_context",
    "create_handoff_task",
)


def _code(page: str) -> str:
    """把註解剝掉再比。

    這一頁上一個 bug 就是斷言打在**提到**那個欄位的註解上——測試綠著，
    而畫面什麼都沒接。同一種洞這個專案犯過三次，共通點都是
    「提到某個東西」被當成「用了某個東西」。
    """
    text = (WEB / page).read_text(encoding="utf-8")
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def test_every_document_action_has_a_button_on_the_documents_page() -> None:
    code = _code("documents_v1.html")
    missing = [act for act in DOCUMENT_ACTIONS if f'data-act="{act}"' not in code]
    assert not missing, (
        "被砍掉的原生面板身上這幾項，文件頁沒有接："
        + "、".join(missing)
        + "。刪東西的時候把它身上的保險一起帶走了。"
    )


def test_every_memory_action_has_a_button_on_the_knowledge_page() -> None:
    code = _code("knowledge_v1.html")
    missing = [act for act in MEMORY_ACTIONS if f'data-act="{act}"' not in code]
    assert not missing, (
        "被砍掉的原生面板身上這幾項，共享記憶頁沒有接：" + "、".join(missing)
    )


def test_the_panels_really_are_gone() -> None:
    """砍了就要真的不在，而且不准悄悄長回來。

    留一份「隨時可以改回去」的副本，就是留一條**在你正因為出事而回退的那個
    時刻才會壞掉的路**——那比沒有退路更糟。
    """
    for name in ("dashboard_panel.py", "knowledge_panel.py", "roadmap_panel.py"):
        assert not (Path("app/ui") / name).exists(), (
            f"{name} 又回來了。它要嘛是活的（那就要有人維護、有測試守）"
            "，要嘛不存在。"
        )


def test_the_main_window_does_not_import_them_back() -> None:
    """連 import 那一行都不留。

    那三行本來掛著 `# noqa: F401`——一個「這個 import 沒有人用，但我故意留著」
    的標記。留著它等於留著一句關於未來的宣稱，而**沒有人會在平常的日子裡去
    走一次回退路徑**。
    """
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")
    for name in ("DashboardPanel", "KnowledgePanel", "RoadmapPanel"):
        assert f"import {name}" not in source, f"{name} 的 import 又回來了"
