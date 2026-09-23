from __future__ import annotations

from pathlib import Path

from app.ui.shell_panel import PAGES


def test_main_window_includes_roadmap_tab_before_live_log() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    # 2026-08-23 砍掉原生面板之後只剩一個類別。守的是這一頁存在、
    # 而且排在即時紀錄前面。
    assert "self.roadmap_panel = " in source
    assert "live_tab = QWidget()" in source

    # The live log has no entry in shell.js's own default page list, so if it
    # ever stops being pushed from here it becomes unreachable rather than
    # merely out of order - assert it is present, not just placed.
    keys = [key for key, _label in PAGES]
    assert "live" in keys
    assert keys.index("knowledge") < keys.index("roadmap")
    assert keys.index("roadmap") < keys.index("live")


def test_roadmap_document_tracks_major_next_steps() -> None:
    source = Path("ROADMAP.md").read_text(encoding="utf-8")

    assert "AI Control Center Roadmap" in source
    assert "P0 - Conversation-First UX" in source
    assert "P1 - 文件管理" in source
    assert "P3 - 長任務與 Decision Gate" in source
    assert "P8 - 安全強化" in source
    assert "P9 - 商業驗證" in source
    assert "OAuth token scraping" in source
