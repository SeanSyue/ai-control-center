"""為什麼這些資料會被帶入（ROADMAP P0.5）。

計數本來就有了：幾份文件、幾個區段、大約幾個 token。**沒有的是「憑什麼」。**

而三種挑法裡有一種的誠實答案是「沒有憑什麼」——任務文字裡抓不到可用的關鍵字時，
帶的是最近更新的資料，可能跟這個任務完全無關。在畫面上它跟一次挑得很準的執行
長得一模一樣，所以這個檔案大半在測那一種。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.project_knowledge import ProjectKnowledgeStore


def make_store(tmp_path: Path, files: dict[str, str]) -> ProjectKnowledgeStore:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    for name, body in files.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()
    return store


DOCS = {
    "notes/settings.md": "設定頁的儲存列使用 sticky 定位，負邊距會讓它浮起來。",
    "notes/deploy.md": "部署流程：先跑 pytest，再打包 installer。",
}


def test_a_real_match_says_nothing(tmp_path: Path) -> None:
    """The ordinary case has to stay silent.

    A line on every run saying 「照關鍵字挑的」 is a line people stop reading -
    and then it is not there on the run where the answer is worse.
    """
    store = make_store(tmp_path, DOCS)

    for query in ("設定頁的儲存列", "installer pytest"):
        chunks = store.search(query)
        assert chunks, query
        assert {c.reason for c in chunks} <= {"keyword", "substring"}, query
        assert store.selection_note(query) == "", query


def test_a_chinese_term_inside_a_longer_run_only_matches_by_substring(
    tmp_path: Path,
) -> None:
    """The accurate version of a claim I first wrote too strongly.

    The first draft of this test was called 「a Chinese task **never** reaches
    the full text index」 and said the tokenizer does not segment CJK at all.
    That is wrong: FTS5's default tokenizer treats a whitespace-delimited run
    of CJK as one token, so a Chinese term matches when it *is* such a run and
    misses when it sits inside a longer one. 「設定頁的儲存列」 here is inside
    「設定頁的儲存列使用 sticky 定位」, which is why it only matches by
    substring - not because Chinese never matches.

    The correction matters beyond wording: 「有時候會中」 is what made the old
    「FTS 有結果就不跑 LIKE」 shortcut lose documents. See `test_search_recall`.

    What this still pins is the design of `selection_note`: a substring match
    says nothing, because 「索引過舊，重新索引會比較準」 would be wrong advice
    on every Chinese task where the term happens to sit inside a longer run.
    """
    store = make_store(tmp_path, DOCS)

    assert {c.reason for c in store.search("設定頁的儲存列")} == {"substring"}
    assert {c.reason for c in store.search("installer pytest")} == {"keyword"}
    assert store.selection_note("設定頁的儲存列") == ""


def test_a_task_with_no_usable_terms_says_so_out_loud(tmp_path: Path) -> None:
    """The case this whole thing exists for.

    No search terms means the most recently indexed chunks were sent - they
    may be about something else entirely. On screen it looks identical to a
    well-matched run, and the model is handed unrelated material as context.
    """
    store = make_store(tmp_path, DOCS)

    chunks = store.search("!!! ???")

    assert chunks, "沒有關鍵字時仍然會帶資料，這正是問題所在"
    assert {c.reason for c in chunks} == {"recent"}

    note = store.selection_note("!!! ???")
    assert "不一定相關" in note
    # And it says what to do about it, not just what happened.
    assert "具體" in note


def test_the_reason_is_recorded_per_chunk_not_guessed_from_the_score(
    tmp_path: Path,
) -> None:
    """A rank of 0.0 means 「LIKE 命中」 and also 「FTS 給了零分」.

    Those are different answers to 「為什麼帶這一段」, so the reason is set at
    the branch that decides it rather than inferred afterwards.
    """
    store = make_store(tmp_path, DOCS)

    for chunk in store.search("!!! ???"):
        assert chunk.reason == "recent"
    for chunk in store.search("設定頁的儲存列"):
        assert chunk.reason != "recent"


def test_the_note_reaches_the_run_timeline(tmp_path: Path) -> None:
    """Otherwise it is a sentence nobody sees.

    The same gap negative control kept finding elsewhere: every assertion in
    the service layer stays green while the finding reaches nobody.
    """
    from app.adapters.cli_runner import CliResult
    from app.core.orchestrator import Orchestrator
    from app.services.ai_settings import AIExecutionSettings
    from app.services.timeline_store import TimelineStore

    store = make_store(tmp_path, DOCS)
    project = store.project_path
    (project / ".aicc" / "task.md").write_text("!!! ???", encoding="utf-8")

    orchestrator = Orchestrator(
        project,
        run_id="run-1",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
    )
    orchestrator.runner.run = lambda *a, **k: CliResult(  # type: ignore[method-assign]
        command="claude", success=True, return_code=0, stdout="{}", stderr=""
    )
    orchestrator.run_claude()

    events = TimelineStore(project).load_events(run_id="run-1")
    lines = [
        event.content
        for event in events
        if event.event_type == "knowledge_context"
    ]

    assert lines, "沒有 knowledge_context 事件，這條就什麼都沒驗到"
    assert "不一定相關" in lines[0]


def test_a_project_with_no_documents_has_no_note(tmp_path: Path) -> None:
    # Nothing was selected, so there is no selection to explain. A note here
    # would be a warning about a thing that did not happen.
    store = make_store(tmp_path, {})

    assert store.selection_note("設定頁") == ""
