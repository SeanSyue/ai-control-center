"""中文檢索找不找得齊（ROADMAP P0.5）。

SQLite FTS5 的預設 tokenizer 把一整串沒有空白隔開的中文當成**一個** token。
所以「開戶討論」比得到「⋯ 開戶討論」，比不到「開戶討論紀錄」——兩者對人來說
都是明顯的命中。

原本的程式只有在 FTS 一筆都沒比到時才改用字串比對，所以只要 FTS 湊巧比到一筆，
字串比對就整個不跑，**其他本來找得到的文件會安靜地消失**。
量到的：三份文件都含「開戶討論」，LIKE 找得到三份，search 只回兩份。

**FTS 部分成功比完全失敗更糟**——完全失敗至少會落到那條會動的路。
"""
from __future__ import annotations

from pathlib import Path

from app.services.project_knowledge import ProjectKnowledgeStore

DOCS = {
    # The term sits inside a longer CJK run - FTS tokenises the whole run as
    # one token, so this one is invisible to the index.
    "a.md": "客戶陳先生 開戶討論紀錄",
    # Delimited by spaces, so FTS finds it.
    "b.md": "一般的專案筆記 開戶討論",
    "c.md": "第三份也提到 開戶討論 的東西",
    "d.md": "完全無關的內容",
}


def make_store(tmp_path: Path, files: dict[str, str] | None = None):
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / "notes").mkdir()
    for name, body in (files or DOCS).items():
        (project / "notes" / name).write_text(body, encoding="utf-8")
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()
    return store


def test_a_chinese_term_finds_every_document_that_contains_it(
    tmp_path: Path,
) -> None:
    """The one that was broken. Three contained it; two came back."""
    store = make_store(tmp_path)

    found = sorted(chunk.display_name for chunk in store.search("開戶討論"))

    assert found == ["a.md", "b.md", "c.md"]


def test_the_one_the_index_cannot_see_is_still_returned(
    tmp_path: Path,
) -> None:
    # Named separately because it is the specific document the old code lost:
    # the term is inside a longer run, so FTS will never match it, and it was
    # only reachable through the LIKE path that used to be skipped.
    store = make_store(tmp_path)

    names = {chunk.display_name for chunk in store.search("開戶討論")}

    assert "a.md" in names


def test_documents_that_do_not_contain_it_stay_out(tmp_path: Path) -> None:
    """The other direction. Running LIKE as well as FTS must widen recall,
    not turn the search into 「回傳全部」."""
    store = make_store(tmp_path)

    names = {chunk.display_name for chunk in store.search("開戶討論")}

    assert "d.md" not in names


def test_the_reason_is_per_chunk_not_per_query(tmp_path: Path) -> None:
    """One search now produces both kinds at once.

    「這一段是索引比到的還是字串比到的」 is only useful if it is true of that
    chunk rather than of the batch - and after this change a single query
    routinely returns some of each.
    """
    store = make_store(tmp_path)

    reasons = {
        chunk.display_name: chunk.reason for chunk in store.search("開戶討論")
    }

    assert reasons["a.md"] == "substring"
    assert reasons["b.md"] == "keyword"


def test_an_english_term_still_uses_the_index(tmp_path: Path) -> None:
    # English tokenises per word, so FTS works and bm25 ordering still
    # applies. The fix must not throw that away by making everything LIKE.
    store = make_store(
        tmp_path,
        {"e.md": "deployment installer pytest notes", "f.md": "unrelated"},
    )

    hits = store.search("installer pytest")

    assert [chunk.display_name for chunk in hits] == ["e.md"]
    assert hits[0].reason == "keyword"


def test_the_index_still_decides_the_order_when_it_can(tmp_path: Path) -> None:
    """Running LIKE as well must not throw bm25 away.

    Found by negative control: replacing the merge with 「只跑 LIKE」 left every
    other test green, because none of them had two matches whose best order
    differed. LIKE orders by 「最近索引的先」, which is not relevance - so
    without this, the fix for recall would have quietly cost ranking.
    """
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / "notes").mkdir()
    relevant = project / "notes" / "relevant.md"
    passing = project / "notes" / "passing.md"
    relevant.write_text(
        "installer installer installer packaging the installer",
        encoding="utf-8",
    )
    passing.write_text(
        "a long note about many unrelated things that mentions installer once "
        + "and then goes on about something else entirely " * 6,
        encoding="utf-8",
    )
    store = ProjectKnowledgeStore(project)
    # Indexed second, so 「最近索引的先」 would put the weaker match first.
    store.index_file(relevant, source_type="project")
    store.index_file(passing, source_type="project")

    hits = store.search("installer")

    assert [chunk.display_name for chunk in hits][0] == "relevant.md", (
        "比較相關的那一份沒有排在前面——bm25 排序被丟掉了"
    )
