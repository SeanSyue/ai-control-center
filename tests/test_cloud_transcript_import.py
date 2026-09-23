from __future__ import annotations

import json
from pathlib import Path

from app.services.cloud_transcript_import import CloudTranscriptImporter
from app.services.project_knowledge import ProjectKnowledgeStore


def test_import_chatgpt_export_as_context_only_document(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    export = tmp_path / "conversations.json"
    export.write_text(
        json.dumps(
            [
                {
                    "title": "KYC 權限討論",
                    "mapping": {
                        "1": {
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["請討論 Root Admin 規則"]},
                                "create_time": 1,
                            }
                        },
                        "2": {
                            "message": {
                                "author": {"role": "assistant"},
                                "content": {"parts": ["應該保留高風險確認"]},
                                "create_time": 2,
                            }
                        },
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    result = CloudTranscriptImporter(project).import_paths([export])

    assert result.success is True
    assert result.imported_files
    documents = ProjectKnowledgeStore(project).list_documents()
    assert len(documents) == 1
    assert documents[0].source_type == "imported"
    assert "KYC" in documents[0].display_name
    preview = ProjectKnowledgeStore(project).document_preview(documents[0].document_id)
    assert "context-only" in preview
    assert "Root Admin" in preview


# ---- 重複匯入不會累積（ROADMAP P7） --------------------------------------


def _export(tmp_path: Path, title: str = "跟客戶討論開戶") -> Path:
    import json as _json

    path = tmp_path / "conversations.json"
    path.write_text(
        _json.dumps(
            [
                {
                    "title": title,
                    "mapping": {
                        "1": {
                            "message": {
                                "author": {"role": "user"},
                                "content": {"parts": ["請問委任書要怎麼寫"]},
                            }
                        }
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    return path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    return project


def test_importing_the_same_export_twice_leaves_one_file(
    tmp_path: Path,
) -> None:
    """It used to leave two, named by the clock.

    Measured before the fix: two markdown files on disk while the index held
    one - so what was on disk and what AICC believed had already drifted apart
    on the second click.
    """
    from app.services.cloud_transcript_import import CloudTranscriptImporter
    from app.services.project_knowledge import ProjectKnowledgeStore

    project = _project(tmp_path)
    export = _export(tmp_path)

    CloudTranscriptImporter(project).import_paths([export])
    second = CloudTranscriptImporter(project).import_paths([export])

    transcripts = list(
        (project / ".aicc" / "imported-context" / "transcripts").glob("*.md")
    )
    assert len(transcripts) == 1
    assert len(ProjectKnowledgeStore(project).list_documents()) == 1
    assert len(second.already_here) == 1


def test_the_second_import_says_nothing_new_came_in(tmp_path: Path) -> None:
    """「匯入了 1 個」 when nothing changed is the kind of confirmation that
    trains people to stop reading confirmations."""
    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)
    export = _export(tmp_path)
    CloudTranscriptImporter(project).import_paths([export])

    summary = CloudTranscriptImporter(project).import_paths([export]).summary

    assert "之前就匯入過" in summary


def test_a_different_conversation_still_gets_its_own_file(
    tmp_path: Path,
) -> None:
    # The other direction: dedupe by content must not collapse two real
    # conversations into one. A hash that matched too eagerly would silently
    # lose an import, which is worse than a duplicate.
    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)
    CloudTranscriptImporter(project).import_paths([_export(tmp_path, "A 案")])
    CloudTranscriptImporter(project).import_paths(
        [_export(tmp_path, "B 案")]
    )

    transcripts = list(
        (project / ".aicc" / "imported-context" / "transcripts").glob("*.md")
    )
    assert len(transcripts) == 2


def test_the_existing_file_is_left_exactly_as_it_was(tmp_path: Path) -> None:
    """Re-importing must not rewrite the file to refresh a timestamp.

    The index points at it, and a change with no information in it is still a
    change - it would make every re-import look like the transcript had been
    edited.
    """
    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)
    export = _export(tmp_path)
    CloudTranscriptImporter(project).import_paths([export])
    written = next(
        (project / ".aicc" / "imported-context" / "transcripts").glob("*.md")
    )
    before = written.read_text(encoding="utf-8")

    CloudTranscriptImporter(project).import_paths([export])

    assert written.read_text(encoding="utf-8") == before


def test_the_imported_file_still_names_where_it_came_from(
    tmp_path: Path,
) -> None:
    # ROADMAP P7「從摘要追溯到原始對話來源」's first link: the transcript has
    # to say which export it came out of, or the trail starts nowhere.
    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)
    export = _export(tmp_path)
    CloudTranscriptImporter(project).import_paths([export])

    written = next(
        (project / ".aicc" / "imported-context" / "transcripts").glob("*.md")
    ).read_text(encoding="utf-8")

    assert f"- Source: {export}" in written
    assert "- Imported at:" in written


def test_same_title_different_content_is_not_collapsed(tmp_path: Path) -> None:
    """The case that actually exercises the hash.

    Found by negative control: hashing only the provider left every test
    green, because the title is in the filename too and was doing the
    separating. Two conversations that happen to share a title - 「與客戶討論」
    twice, a month apart - would then collapse into one, and the second import
    would silently do nothing.
    """
    import json as _json

    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)

    def export(name: str, said: str) -> Path:
        path = tmp_path / name
        path.write_text(
            _json.dumps(
                [
                    {
                        "title": "與客戶討論",
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
        return path

    CloudTranscriptImporter(project).import_paths([export("a.json", "第一次談的內容")])
    CloudTranscriptImporter(project).import_paths([export("b.json", "第二次談的內容")])

    transcripts = list(
        (project / ".aicc" / "imported-context" / "transcripts").glob("*.md")
    )
    assert len(transcripts) == 2, "同標題不同內容被併成一份，第二次匯入等於消失了"


# ---- Claude.ai 的匯出格式（ROADMAP P0／P7） -------------------------------


CLAUDE_LIST = [
    {
        "uuid": "abc",
        "name": "開戶討論",
        "chat_messages": [
            {
                "sender": "human",
                "text": "委任書怎麼寫",
                "content": [{"type": "text", "text": "委任書怎麼寫"}],
            },
            {"sender": "assistant", "text": "要三個欄位"},
        ],
    }
]
CLAUDE_SINGLE = {
    "uuid": "abc",
    "name": "開戶討論",
    "chat_messages": [{"sender": "human", "text": "委任書怎麼寫"}],
}


def _write(tmp_path: Path, raw) -> Path:
    import json as _json

    path = tmp_path / "export.json"
    path.write_text(_json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


def _imported_body(project: Path) -> str:
    written = next(
        (project / ".aicc" / "imported-context" / "transcripts").glob("*.md")
    )
    return written.read_text(encoding="utf-8").split("## Transcript", 1)[-1]


def test_a_claude_export_becomes_a_readable_conversation(
    tmp_path: Path,
) -> None:
    """It used to come out as a raw JSON dump.

    And it reported success while doing it - so the blob reached the search
    index, could be selected into a prompt, and cost tokens as unreadable
    JSON. Measured before the fix: the body started `{ "uuid": "abc", …`.
    """
    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)
    result = CloudTranscriptImporter(project).import_paths(
        [_write(tmp_path, CLAUDE_LIST)]
    )

    assert result.success
    body = _imported_body(project)
    assert "## human" in body
    assert "委任書怎麼寫" in body
    assert "要三個欄位" in body
    assert '"uuid"' not in body, "又變回 JSON dump 了"


def test_a_single_claude_conversation_is_accepted(tmp_path: Path) -> None:
    # The dict form was rejected outright: 「Unsupported transcript JSON shape」
    # for a perfectly ordinary export of one conversation.
    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)
    result = CloudTranscriptImporter(project).import_paths(
        [_write(tmp_path, CLAUDE_SINGLE)]
    )

    assert result.success, result.errors
    assert "委任書怎麼寫" in _imported_body(project)


def test_a_claude_conversation_keeps_its_own_name(tmp_path: Path) -> None:
    """Claude calls it `name`, ChatGPT `title`.

    Reading only `title` named every Claude conversation after the export
    file, so a whole export produced files all called the same thing.
    """
    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)
    CloudTranscriptImporter(project).import_paths(
        [_write(tmp_path, CLAUDE_LIST)]
    )

    written = next(
        (project / ".aicc" / "imported-context" / "transcripts").glob("*.md")
    )
    assert "開戶討論" in written.name
    assert "export" not in written.name


def test_a_claude_export_is_labelled_claude(tmp_path: Path) -> None:
    # Claude's export has no provider field, so it is recognised by its shape.
    # Guessing "chatgpt" for everything is invisible until somebody sorts
    # their transcripts by where they came from.
    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)
    CloudTranscriptImporter(project).import_paths(
        [_write(tmp_path, CLAUDE_LIST)]
    )

    written = next(
        (project / ".aicc" / "imported-context" / "transcripts").glob("*.md")
    )
    assert written.name.startswith("claude_")


def test_an_unrecognised_shape_fails_instead_of_importing_json(
    tmp_path: Path,
) -> None:
    """A blob that imports 「successfully」 is worse than a rejection.

    It reaches the index, gets selected into prompts, and the user never looks
    again because the app said it worked.
    """
    from app.services.cloud_transcript_import import CloudTranscriptImporter

    project = _project(tmp_path)
    result = CloudTranscriptImporter(project).import_paths(
        [_write(tmp_path, {"messages": [{"weird": 1}]})]
    )

    assert not result.success
    assert "認不出這個對話的格式" in result.errors[0]
    assert not list(
        (project / ".aicc" / "imported-context" / "transcripts").glob("*.md")
    )
