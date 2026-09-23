"""共享記憶那一頁講的「這次會送出什麼」，要跟真的送出去的一致。

那一頁本來自己算一遍：照 `MEMORY_FILES` 順序填、12,000 字填滿就停。註解寫著
「derived from the sending code's own rule so the two cannot drift apart」——
而它**已經漂了**：送出端在 2026-08-11 就換成公平配額了，頁面這邊還停在
「填滿就停」，所以畫面上說「已達字數上限、沒送出」的那幾份，實際上是被切成
節錄送出去的。一份寫錯的摘要，比一份不完整的摘要糟。

2026-08-21 截斷整個拿掉之後，這一頁只剩一件事要講對：**每一份都全文送出**。
這一組釘的就是那件事，而且是拿真的組裝函式去對，不是再抄一次規則。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_knowledge import MEMORY_FILES  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.knowledge_web_panel import KnowledgeWebPanel  # noqa: E402


def _panel(tmp_path: Path, each: int = 4_000) -> KnowledgeWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    panel = KnowledgeWebPanel(project)
    panel.store.ensure_memory_files()
    # 八份各 4,000 字＝32,000，比當年最大的上限（22,000）還多，所以舊規則下
    # 這一頁一定會畫出「截斷」與「已達字數上限」。
    for file_name in MEMORY_FILES:
        (panel.store.knowledge_path / file_name).write_text(
            f"{file_name}\n" + "字" * each, encoding="utf-8"
        )
    return panel


def test_the_panel_does_not_promise_more_than_is_sent(tmp_path: Path) -> None:
    """頁面說的送出字數，要等於送出端真的組出來的字數。

    比的是總量與逐份的量，兩邊都要對——只比總量的話，兩份互相抵銷的錯誤會
    對得起來。
    """
    panel = _panel(tmp_path)

    plan = panel.collect_state()["context"]
    sent = panel.store.build_memory_context()

    assert sum(item["sent_chars"] for item in plan) + 2 * (len(plan) - 1) == len(sent)
    for item in plan:
        assert item["chars"] == item["sent_chars"], item["name"]


def test_no_file_is_drawn_as_truncated_or_dropped(tmp_path: Path) -> None:
    """`truncated` 與「已達字數上限」不准再出現在這一頁上。

    畫得出一個引擎永遠到不了的狀態，就是畫面開始描述一個已經不存在的行為。
    """
    panel = _panel(tmp_path)

    plan = panel.collect_state()["context"]

    assert [item["state"] for item in plan] == ["full"] * len(MEMORY_FILES)
    assert not [item for item in plan if item.get("reason") == "已達字數上限"]


def test_the_page_is_told_there_is_no_limit(tmp_path: Path) -> None:
    """`context_limit = 0` 是頁面用來決定畫「共 N 字」還是畫一條量表的旗標。

    非 0 的話它會畫「已用 N／M 字上限」——一條對著不存在的上限量出來的進度條，
    就是同一個謊換一個地方講。
    """
    panel = _panel(tmp_path)

    assert panel.collect_state()["context_limit"] == 0


def test_a_file_with_nothing_in_it_is_still_reported_as_skipped(tmp_path: Path) -> None:
    """空檔案不是「被切掉」，它是真的沒有東西可送——這兩件事要分得開。"""
    panel = _panel(tmp_path)
    (panel.store.knowledge_path / "NEXT_STEPS.md").write_text("  \n", encoding="utf-8")
    (panel.store.knowledge_path / "PRODUCT_REVIEW.md").unlink()

    plan = {item["name"]: item for item in panel.collect_state()["context"]}

    assert plan["NEXT_STEPS.md"]["state"] == "skipped"
    assert plan["NEXT_STEPS.md"]["reason"] == "檔案是空的"
    assert plan["PRODUCT_REVIEW.md"]["state"] == "skipped"
    assert plan["PRODUCT_REVIEW.md"]["reason"] == "檔案不存在"
