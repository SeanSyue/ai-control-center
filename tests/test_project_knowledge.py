from __future__ import annotations

import os
import json
from pathlib import Path
from time import sleep

from app.services.project_knowledge import ProjectKnowledgeStore


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text(
        "def permission_policy():\n"
        "    # Root Admin Amon cannot be disabled.\n"
        "    return 'Amon root admin'\n",
        encoding="utf-8",
    )
    (project / "README.md").write_text(
        "# KYC System\n\nThis project manages customer cases.\n",
        encoding="utf-8",
    )
    return project


def test_rebuild_project_index_and_search(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)

    result = store.rebuild_project_index()
    documents = store.list_documents()
    chunks = store.search("Amon Root Admin 權限")

    assert not result.errors
    assert {item.display_name for item in documents} == {
        "README.md",
        "app.py",
    }
    assert chunks
    assert "Amon" in "\n".join(item.content for item in chunks)


def test_import_external_document_copies_and_indexes(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    external = tmp_path / "decision.md"
    external.write_text(
        "委派維護模式只有 30 到 60 分鐘有效期。",
        encoding="utf-8",
    )
    store = ProjectKnowledgeStore(project)

    result = store.import_files([external])
    documents = store.list_documents()
    imported = [item for item in documents if item.source_type == "imported"]

    assert result.imported == ("decision.md",)
    assert len(imported) == 1
    copied = project / imported[0].stored_path
    assert copied.is_file()
    assert copied.parent == project / ".aicc" / "imported-context"
    assert "委派維護模式" in store.document_preview(imported[0].document_id)


def test_build_context_uses_relevant_chunks_and_summary(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()
    store.write_project_brief("此專案是 KYC Case MVP。")

    context = store.build_context("Root Admin Amon 權限")
    stats = store.context_stats("Root Admin Amon 權限")

    assert "Shared Project Memory" in context
    assert "KYC Case MVP" in context
    assert "Amon" in context
    assert stats["documents"] >= 1
    assert stats["estimated_tokens"] >= 1


def test_remove_imported_document_can_delete_copy(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    external = tmp_path / "notes.txt"
    external.write_text("temporary project notes", encoding="utf-8")
    store = ProjectKnowledgeStore(project)
    store.import_files([external])
    document = store.list_documents()[0]
    copied = project / document.stored_path

    store.remove_document(document.document_id, delete_copy=True)

    assert not copied.exists()
    assert store.list_documents() == []


def test_import_docx_without_external_dependency(tmp_path: Path) -> None:
    import zipfile

    project = make_project(tmp_path)
    docx = tmp_path / "handoff.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>Root Admin 不可停用</w:t></w:r></w:p></w:body>'
        '</w:document>'
    )
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", xml)

    store = ProjectKnowledgeStore(project)
    result = store.import_files([docx])

    assert result.imported == ("handoff.docx",)
    assert "Root Admin" in store.document_preview(
        store.list_documents()[0].document_id
    )


def test_detects_and_refreshes_updated_imported_document(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    external = tmp_path / "policy.md"
    external.write_text("Initial policy: pytest only.", encoding="utf-8")
    store = ProjectKnowledgeStore(project)
    store.import_files([external])
    document = store.list_documents()[0]

    sleep(0.01)
    external.write_text("Updated policy: pytest and no packages.", encoding="utf-8")
    os.utime(external, None)

    statuses = store.document_update_statuses()
    assert statuses[document.document_id] == "updated"

    refreshed = store.refresh_document(document.document_id)

    assert refreshed is not None
    assert refreshed.status == "indexed"
    assert "Updated policy" in store.document_preview(refreshed.document_id)
    assert store.document_update_status(refreshed) == "current"


def test_refresh_document_records_missing_source_and_copy(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    external = tmp_path / "missing.md"
    external.write_text("This file will disappear.", encoding="utf-8")
    store = ProjectKnowledgeStore(project)
    store.import_files([external])
    document = store.list_documents()[0]
    copied = project / document.stored_path

    external.unlink()
    copied.unlink()

    refreshed = store.refresh_document(document.document_id)

    assert refreshed is not None
    assert refreshed.status == "missing"
    assert "missing" in refreshed.error.lower()
    assert store.document_preview(refreshed.document_id) == ""


def test_refresh_all_documents_reports_parse_errors(tmp_path: Path) -> None:
    import zipfile

    project = make_project(tmp_path)
    external = tmp_path / "brief.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>Initial brief</w:t></w:r></w:p></w:body>'
        '</w:document>'
    )
    with zipfile.ZipFile(external, "w") as archive:
        archive.writestr("word/document.xml", xml)
    store = ProjectKnowledgeStore(project)
    store.import_files([external])
    document = store.list_documents()[0]

    sleep(0.01)
    external.write_text("not a real zip archive", encoding="utf-8")
    os.utime(external, None)
    result = store.refresh_all_documents()
    refreshed = store.list_documents()[0]

    assert result.imported == ()
    assert result.errors
    assert refreshed.document_id == document.document_id
    assert refreshed.status == "error"
    assert refreshed.error


def test_shared_memory_files_are_created_and_used_in_context(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)

    paths = store.ensure_memory_files()
    store.write_memory_file(
        "DECISIONS.md",
        "Use SQLite for the local MVP. Do not add external services.",
    )
    context = store.build_context("database decision")
    manifest = store.export_manifest().read_text(encoding="utf-8")

    assert {path.name for path in paths} == {
        "PROJECT_BRIEF.md",
        "ARCHITECTURE.md",
        "DECISIONS.md",
        "SECURITY_MODEL.md",
        "PRODUCT_REVIEW.md",
        "BUSINESS_MODEL.md",
        "NEXT_STEPS.md",
        "OPEN_QUESTIONS.md",
    }
    assert "Shared Project Memory" in context
    assert "Use SQLite" in context
    assert "DECISIONS.md" in manifest


def test_manifest_tracks_generated_knowledge_files(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()
    store.write_session_bootstrap("Root Admin")

    manifest = json.loads(store.export_manifest().read_text(encoding="utf-8"))
    generated = {
        item["file_name"]: item["exists"]
        for item in manifest["generated_files"]
    }

    assert generated == {
        "TASK_CONTEXT.md": True,
        "HANDOFF_PACKAGE.md": True,
        "SESSION_BOOTSTRAP.md": True,
        "IMPORT_WIZARD_REPORT.md": False,
    }


def test_generated_file_statuses_track_existing_outputs(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)

    assert store.generated_file_statuses() == {
        "TASK_CONTEXT.md": False,
        "HANDOFF_PACKAGE.md": False,
        "SESSION_BOOTSTRAP.md": False,
        "IMPORT_WIZARD_REPORT.md": False,
    }

    store.write_handoff_package()

    assert store.generated_file_statuses() == {
        "TASK_CONTEXT.md": False,
        "HANDOFF_PACKAGE.md": True,
        "SESSION_BOOTSTRAP.md": False,
        "IMPORT_WIZARD_REPORT.md": False,
    }


def test_import_wizard_creates_handoff_bootstrap_and_report(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)

    path = store.run_import_wizard("Root Admin onboarding")
    text = path.read_text(encoding="utf-8")
    manifest = json.loads(store.export_manifest().read_text(encoding="utf-8"))
    generated = {
        item["file_name"]: item["exists"]
        for item in manifest["generated_files"]
    }

    assert path == project / ".aicc" / "knowledge" / "IMPORT_WIZARD_REPORT.md"
    assert "# AI Control Center 導入精靈報告" in text
    assert "## 結果摘要" in text
    assert "狀態：可開始接手" in text
    assert "產生檔案：4/4" in text
    assert "需要處理：" in text
    assert "沒有阻塞項目。" in text
    assert "Root Admin onboarding" in (
        project / ".aicc" / "knowledge" / "SESSION_BOOTSTRAP.md"
    ).read_text(encoding="utf-8")
    assert generated["HANDOFF_PACKAGE.md"] is True
    assert generated["SESSION_BOOTSTRAP.md"] is True
    assert generated["IMPORT_WIZARD_REPORT.md"] is True


def test_import_wizard_explains_empty_project_needs_documents(
    tmp_path: Path,
) -> None:
    project = tmp_path / "empty-project"
    project.mkdir()
    store = ProjectKnowledgeStore(project)

    text = store.run_import_wizard().read_text(encoding="utf-8")

    assert "狀態：需要處理" in text
    assert "已索引文件：0" in text
    assert "沒有掃描到可索引的專案文件" in text
    assert "掃描專案，或匯入可供 AI 參考的文件。" in text


def test_rejects_unknown_shared_memory_file(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)

    try:
        store.write_memory_file("SECRET.md", "not allowed")
    except ValueError as exc:
        assert "Unsupported memory file" in str(exc)
    else:
        raise AssertionError("unknown memory file should be rejected")


def test_structured_handoff_text_writes_multiple_memory_files(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)

    written = store.write_memory_from_text(
        """
## PROJECT_BRIEF.md
KYC local desktop MVP.

## ARCHITECTURE.md
PySide6 UI with SQLite storage.

## DECISIONS.md
Keep customer files local.

## SECURITY_MODEL.md
Do not scrape tokens or bypass official CLIs.

## PRODUCT_REVIEW.md
Session pairing is the primary UX risk.

## BUSINESS_MODEL.md
Start as a local-first desktop tool.

## NEXT_STEPS.md
Import existing projects.

## OPEN_QUESTIONS.md
Confirm production sync policy.
""".strip()
    )
    memory = store.list_memory_files()

    assert written == (
        "PROJECT_BRIEF.md",
        "ARCHITECTURE.md",
        "DECISIONS.md",
        "SECURITY_MODEL.md",
        "PRODUCT_REVIEW.md",
        "BUSINESS_MODEL.md",
        "NEXT_STEPS.md",
        "OPEN_QUESTIONS.md",
    )
    assert memory["PROJECT_BRIEF.md"] == "KYC local desktop MVP."
    assert "PySide6" in memory["ARCHITECTURE.md"]
    assert "local" in memory["DECISIONS.md"]


def test_unstructured_handoff_text_falls_back_to_project_brief(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)

    written = store.write_memory_from_text("Plain handoff summary.")

    assert written == ("PROJECT_BRIEF.md",)
    assert store.list_memory_files()["PROJECT_BRIEF.md"] == (
        "Plain handoff summary."
    )


def test_import_readiness_report_tracks_missing_work(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)

    report = store.import_readiness_report()

    assert report.ready is False
    assert report.document_count == 0
    assert "PROJECT_BRIEF.md" in report.missing_memory_files
    assert "Create shared memory files." in report.next_steps
    assert "Scan the project or import reference documents." in report.next_steps


def test_import_readiness_report_ready_after_memory_and_index(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)

    store.ensure_memory_files()
    store.rebuild_project_index()
    report = store.import_readiness_report()

    assert report.ready is True
    assert report.document_count >= 2
    assert report.memory_file_count == 8
    assert report.updated_document_count == 0
    assert report.missing_source_count == 0
    assert report.parse_error_count == 0
    assert report.next_steps == (
        "Ready for a Codex -> Claude -> Codex handoff task.",
    )


def test_format_readiness_report_is_reusable_markdown(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)
    store.ensure_memory_files()
    store.rebuild_project_index()

    text = store.format_readiness_report()

    assert text.startswith("## Import Readiness")
    assert "Status: Ready" in text
    assert "Shared memory files: 8/8" in text
    assert "Ready for a Codex -> Claude -> Codex handoff task." in text


def test_write_handoff_package_combines_readiness_memory_and_documents(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()
    store.write_memory_file(
        "DECISIONS.md",
        "Use SQLite for the local MVP.",
    )

    path = store.write_handoff_package()
    text = path.read_text(encoding="utf-8")

    assert path == project / ".aicc" / "knowledge" / "HANDOFF_PACKAGE.md"
    assert "# AI Control Center Handoff Package" in text
    assert "## Import Readiness" in text
    assert "## Shared Project Memory" in text
    assert "Use SQLite" in text
    assert "README.md" in text
    assert "app.py" in text


def test_write_task_context_saves_retrieved_context(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()
    store.write_memory_file("DECISIONS.md", "Root Admin cannot be disabled.")

    path = store.write_task_context("Root Admin policy")
    text = path.read_text(encoding="utf-8")

    assert path == project / ".aicc" / "knowledge" / "TASK_CONTEXT.md"
    assert "# AI Control Center Task Context" in text
    assert "Root Admin policy" in text
    assert "Root Admin cannot be disabled." in text
    assert "Shared memory characters" in text
    assert "Retrieved document characters" in text
    assert "Estimated tokens" in text
    assert "Contract characters" in text
    assert "Omitted document characters" in text
    assert "app.py" in text


def test_context_budget_profiles_limit_raw_evidence(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()
    store.write_memory_file("DECISIONS.md", "Root Admin cannot be disabled.")

    minimal = store.context_budget("Root Admin", context_profile="minimal")
    strict = store.context_budget("Root Admin", context_profile="strict")

    assert minimal.profile == "minimal"
    assert strict.profile == "strict"
    assert minimal.contract_characters > 0
    assert strict.total_characters >= minimal.total_characters
    assert minimal.estimated_tokens >= 1
    assert "omitted_document_characters" in minimal.to_dict()


def test_memory_update_diff_and_safety_scan(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)
    store.ensure_memory_files()
    store.write_memory_file("DECISIONS.md", "Use SQLite.")

    update = "## DECISIONS.md\nUse SQLite and pytest.\n"
    diff = store.preview_memory_update_diff(update)
    safety = store.scan_memory_update_safety(update)
    unsafe = store.scan_memory_update_safety("token=sk-thisshouldnotleak123456")

    assert "DECISIONS.md (before)" in diff
    assert "Use SQLite and pytest." in diff
    assert safety["safe"] is True
    assert unsafe["safe"] is False
    assert "possible-secret" in unsafe["findings"]


def test_write_session_bootstrap_includes_handoff_and_task_context(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    store = ProjectKnowledgeStore(project)
    store.rebuild_project_index()
    store.write_memory_file("DECISIONS.md", "Keep customer files local.")

    path = store.write_session_bootstrap("Root Admin handoff")
    text = path.read_text(encoding="utf-8")

    assert path == project / ".aicc" / "knowledge" / "SESSION_BOOTSTRAP.md"
    assert "# AI Control Center Session Bootstrap" in text
    assert "## Task-Specific Context" in text
    assert "## Project Handoff Package" in text
    assert "Root Admin handoff" in text
    assert "Keep customer files local." in text
    assert (project / ".aicc" / "knowledge" / "TASK_CONTEXT.md").is_file()
    assert (project / ".aicc" / "knowledge" / "HANDOFF_PACKAGE.md").is_file()
