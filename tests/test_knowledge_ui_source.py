from __future__ import annotations

import re
from pathlib import Path


def test_main_window_includes_knowledge_tab() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    # 2026-08-23 砍掉原生面板之後只剩一個類別，所以這裡不釘類別名。
    # 要守的是：這一頁存在，而且「建立交接任務」還是走它。
    assert "self.knowledge_panel = " in source
    assert "prepare_handoff_task" in source
    # Match the shape, not the number: pinning the literal version made
    # every bump break tests in files that have nothing to do with it.
    assert re.search(r"v\d+\.\d+\.\d+-dev", source)


# （原本這裡還有一條 `test_knowledge_panel_has_document_management_controls`，
# 釘住原生面板上四十幾個功能。2026-08-23 那個面板砍掉了，而那些斷言**沒有跟著
# 刪**——搬到 `tests/test_the_retired_panels_features_all_have_a_home.py`，
# 打在它們現在真正的家：文件頁與共享記憶頁。）
