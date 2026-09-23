"""文件預覽是證據清單，不是資料庫表格（ROADMAP P1）。

一列寫著「片段 3」加 220 個字，是一張表。證據要回答的是**為什麼是這一段**。

而在這之前，這一頁連比對都沒做過：`_hits_by_document` 用 `search("")` 取片段，
空字串什麼都比對不到，所以 store 回的是**最近索引到的**那幾段——然後畫面
把它們掛在「AI 實際會讀到的片段」底下。在有真實任務的專案上那根本是另一組
資料，而且它跟一次挑得很準的結果長得一模一樣：一樣的版面、一樣的數量、
沒有標記、沒有任何一句話說它其實什麼都沒比對。

查詢一直都在，就在 `.aicc/task.md`——執行本身讀的就是那個檔案。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.documents_web_panel import DocumentsWebPanel, _highlight  # noqa: E402

DOCS = {
    "notes/開戶.md": "境外開戶需要護照與地址證明，另外要準備委任合約。",
    "notes/部署.md": "部署流程：先跑 pytest，再打包 installer。",
}


def _panel(tmp_path: Path, task: str) -> DocumentsWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    for name, body in DOCS.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (project / ".aicc" / "task.md").write_text(task, encoding="utf-8")
    panel = DocumentsWebPanel(project)
    panel.store.rebuild_project_index()
    return panel


def _hits(panel: DocumentsWebPanel) -> list[dict]:
    return [h for doc in panel.collect_state()["docs"] for h in doc["hits"]]


def test_the_excerpts_are_matched_against_the_real_task(tmp_path: Path) -> None:
    """The bug: the preview never saw the task."""
    panel = _panel(tmp_path, "installer 打包")

    hits = _hits(panel)

    assert hits, "沒有任何片段，這條會空轉"
    assert all(h["why"] != "沒有比對到關鍵字，帶的是最近更新的" for h in hits), hits
    assert any("installer" in h["text"] for h in hits)


def test_each_excerpt_says_why_it_is_here(tmp_path: Path) -> None:
    panel = _panel(tmp_path, "installer 打包")

    for hit in _hits(panel):
        assert hit["why"], hit
        assert "命中" in hit["why"] or "文字包含" in hit["why"], hit
        # 「為什麼」要講得出是哪個字，不然它只是一個分類標籤。
        assert "：" in hit["why"], hit


def test_the_matched_words_are_marked_in_the_text(tmp_path: Path) -> None:
    """設計端的稿子上就是標起來的。之前做不到的理由是「沒有查詢」。"""
    panel = _panel(tmp_path, "installer 打包")

    marked = [h for h in _hits(panel) if "<mark>" in h["text"]]

    assert marked, [h["text"] for h in _hits(panel)]
    assert "<mark>installer</mark>" in marked[0]["text"]


def test_a_task_with_nothing_to_match_says_so_instead_of_pretending(
    tmp_path: Path,
) -> None:
    """唯一一種誠實答案是「沒有為什麼」的情況。

    這些片段照樣會被送進 prompt，所以不能不顯示；能做的是不要讓它看起來
    跟有根據的一樣。
    """
    panel = _panel(tmp_path, "!!! ???")

    state = panel.collect_state()
    hits = [h for doc in state["docs"] for h in doc["hits"]]

    assert hits, "沒有比對到也還是會帶資料，這正是問題所在"
    assert all(h["why"] == "沒有比對到關鍵字，帶的是最近更新的" for h in hits), hits
    assert all(h["weak"] for h in hits), hits
    assert all("<mark>" not in h["text"] for h in hits), "沒比對到卻標了字"
    assert "不一定相關" in state["note"]


def test_an_ordinary_run_says_nothing_extra(tmp_path: Path) -> None:
    """每次都出現的提示，等於在真的需要它的那次沒有人看見。"""
    panel = _panel(tmp_path, "installer 打包")

    state = panel.collect_state()

    assert state["note"] == ""
    assert not any(
        h["weak"] for doc in state["docs"] for h in doc["hits"]
    )


def test_the_page_renders_why_and_the_note(tmp_path: Path) -> None:
    # 先剝註解：這一頁上兩個 bug 都是斷言打在提到欄位的文字上。
    import re

    page = Path("app/ui/web/documents_v1.html").read_text(encoding="utf-8")
    code = re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", page, flags=re.S))

    assert "h.why" in code, "頁面沒有畫出「為什麼是這一段」"
    assert "h.weak" in code, "沒有比對到的片段跟有根據的長得一樣"
    # `DATA.note?`，帶問號。第一版只找 `DATA.note`，而樣板裡的 `${DATA.note}`
    # 讓那個字串永遠在——把外面的條件換成 `false` 照樣是綠的。要守的是
    # 「有話才說」這個條件本身，不是這個名字出現過。
    assert "DATA.note?" in code, "挑選說明不是有話才說"


# --- _highlight 本身 --------------------------------------------------------


def test_highlighting_escapes_before_it_marks() -> None:
    """順序就是全部：先標再跳脫會把 <mark> 也跳脫掉，反過來則會讓
    含 `<` 的關鍵字把標記夾帶進頁面。"""
    out = _highlight("值是 <script>alert(1)</script> 這樣", ("script",))

    assert "<script>" not in out
    assert "&lt;<mark>script</mark>&gt;" in out


def test_one_term_inside_another_marks_the_longer_one_once(tmp_path: Path) -> None:
    """中文的日常情況，不是刻意刁難：任務裡同時出現「開戶」和「境外開戶」。

    **第一版用的是 `("開", "開戶")`，負向驗證判它沒有牙齒**——「開」只有一個字，
    會先被最小長度那道過濾掉，所以不管標記怎麼寫都不可能巢狀，這條分不出
    任何差別。兩個關鍵字都要夠長，重疊才是真的。

    一次過、一個 alternation，所以這條同時守住兩種寫法：
    逐個關鍵字標會變成 `<mark>境外<mark>開戶</mark></mark>`（數量變 2），
    短的先標會只框住「開戶」、把長的那個切斷。
    """
    out = _highlight("境外開戶需要護照", ("開戶", "境外開戶"))

    assert out.count("<mark>") == 1, out
    assert "<mark>境外開戶</mark>" in out, out


def test_the_longer_of_two_terms_starting_at_the_same_place_wins() -> None:
    """「最長的先排」守的其實只有這一種情況，而上一條守不到它。

    `re.sub` 取的是**最左邊**的匹配，所以兩個關鍵字從不同位置開始時，
    誰先排都一樣——負向驗證把「最短的先標」判成沒有牙齒，就是因為
    「開戶」和「境外開戶」起點不同。要分得出來，其中一個必須是另一個的
    **前綴**：這時兩個候選從同一格開始，alternation 的順序才決定誰贏。
    """
    out = _highlight("境外開戶文件清單", ("開戶", "開戶文件"))

    assert "<mark>開戶文件</mark>" in out, out
    assert out.count("<mark>") == 1, out


def test_nothing_matched_means_nothing_marked() -> None:
    assert "<mark>" not in _highlight("一段普通的內容", ())


def test_a_single_character_term_is_not_marked() -> None:
    # 一個字的關鍵字會把整段打成馬賽克，標記就失去意義了。
    assert "<mark>" not in _highlight("境外開戶需要護照", ("開",))
