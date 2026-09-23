from __future__ import annotations

from pathlib import Path


def test_main_window_exposes_workspace_import_export() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "WorkspacePortabilityService" in source
    # Were the two buttons' objectNames. Both moved into the shell's overflow
    # menu, so the thing to assert is that the actions it sends are wired.
    assert '"export_workspace": self.export_workspace' in source
    assert '"import_workspace": self.import_workspace' in source
    assert "def export_workspace" in source
    assert "def import_workspace" in source
    assert "preview_export" in source
    # 那句話 2026-08-24 搬進 `app/i18n.py` 了（介面文字全部往那裡集中）。
    # **守的東西沒有變**：主視窗要畫得出那個預覽對話框的標題。
    # 改成比 key，是因為比中文的話它會在每一次搬家的時候變紅，而搬家不是壞掉。
    assert '"export.preview.title"' in source

    from app.i18n import STRINGS

    assert STRINGS["export.preview.title"] == "匯出 Workspace 預覽"
