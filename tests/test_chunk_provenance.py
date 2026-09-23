"""匯入的舊對話，在 context 裡要看得出來是舊對話（ROADMAP P7）。

一段匯入的 transcript 是別人幾個月前的對話。沒有標記的話，它跟目前專案的原始碼
在 prompt 裡長得一模一樣——都只是「Relevant Project Documents」底下的一段文字。

**模型會把 transcript 裡的「我們決定用 X」當成這個專案現在的事實。** 那不是檢索
出錯，是檢索對了而標籤沒給，所以這一條的修法是加一句話，不是改排序。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services.cloud_transcript_import import CloudTranscriptImporter
from app.services.project_knowledge import ProjectKnowledgeStore

MARK = "Imported past conversation"


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / "notes").mkdir()
    (project / "notes" / "settings.md").write_text(
        "settings toolbar sticky positioning notes", encoding="utf-8"
    )
    return project


def import_transcript(project: Path, tmp_path: Path, said: str) -> None:
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                {
                    "title": "settings toolbar discussion",
                    "mapping": {
                        "1": {
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": [said]},
                            }
                        }
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    CloudTranscriptImporter(project).import_paths([export])


def test_an_imported_chunk_is_labelled_in_the_context(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    import_transcript(
        project, tmp_path, "settings toolbar sticky positioning decision"
    )
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()

    context = store.build_context("settings toolbar sticky")

    assert "## Transcript" in context, "沒有選到那段 transcript"
    assert MARK in context
    assert "may be out of date" in context


def test_a_project_file_is_not_labelled(tmp_path: Path) -> None:
    """The other half. A label on everything is a label on nothing, and it
    would also be false: a source file is not a past conversation."""
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()

    context = store.build_context("settings toolbar sticky")

    assert "settings.md" in context
    assert MARK not in context


def test_the_chunk_carries_where_it_came_from(tmp_path: Path) -> None:
    # Read off the document row rather than guessed from the path. A path
    # check would say "imported" for any project file that happened to live
    # under a folder with that word in it.
    project = make_project(tmp_path)
    import_transcript(
        project, tmp_path, "settings toolbar sticky positioning decision"
    )
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()

    kinds = {c.source_type for c in store.search("settings toolbar sticky")}

    assert kinds == {"project", "imported"}


def test_the_label_survives_the_no_keyword_fallback(tmp_path: Path) -> None:
    """The fallback path builds its chunks separately.

    Two construction sites, one of which is easy to forget - and it is the
    path taken when the task has no usable search terms, which is exactly when
    the context is least related to the task and most in need of labels.
    """
    project = make_project(tmp_path)
    import_transcript(
        project, tmp_path, "settings toolbar sticky positioning decision"
    )
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()

    chunks = store.search("!!! ???")

    assert chunks
    assert {c.reason for c in chunks} == {"recent"}
    assert any(c.source_type == "imported" for c in chunks)
