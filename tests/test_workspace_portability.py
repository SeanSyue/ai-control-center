from __future__ import annotations

import zipfile
from pathlib import Path

from app.services.project_registry import ProjectRegistry
from app.services.workspace_portability import WorkspacePortabilityService


def test_export_project_excludes_runtime_and_secret_files(
    tmp_path: Path,
) -> None:
    project = tmp_path / "portable"
    (project / ".aicc" / "runtime").mkdir(parents=True)
    (project / ".aicc" / "knowledge").mkdir(parents=True)
    (project / ".git").mkdir()
    (project / ".env").write_text("SECRET=1", encoding="utf-8")
    (project / "README.md").write_text("hello", encoding="utf-8")
    (project / ".aicc" / "runtime" / "workflow.lock").write_text(
        "locked",
        encoding="utf-8",
    )

    archive = tmp_path / "portable.zip"
    result = WorkspacePortabilityService().export_project(project, archive)

    assert result.success is True
    with zipfile.ZipFile(archive, "r") as package:
        names = set(package.namelist())

    assert "README.md" in names
    assert ".env" not in names
    assert ".aicc/runtime/workflow.lock" not in names
    assert "AICC_EXPORT_MANIFEST.json" in names
    assert ".aicc/knowledge/HANDOFF_PACKAGE.md" in names


def test_export_preview_scans_included_files_and_reports_skipped_files(
    tmp_path: Path,
) -> None:
    project = tmp_path / "portable"
    project.mkdir()
    (project / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (project / "README.md").write_text("hello", encoding="utf-8")

    preview = WorkspacePortabilityService().preview_export(project)

    assert preview.success is True
    assert "README.md" in preview.included_files
    assert ".env" in preview.skipped_files
    assert preview.security_report is not None
    assert preview.security_report.has_blocking_findings is False


def test_export_blocks_secret_in_included_file(tmp_path: Path) -> None:
    project = tmp_path / "portable"
    project.mkdir()
    (project / "README.md").write_text(
        "api_key=sk-thisshouldnotbeexported123456789",
        encoding="utf-8",
    )

    archive = tmp_path / "portable.zip"
    result = WorkspacePortabilityService().export_project(project, archive)

    assert result.success is False
    assert "疑似 token" in result.summary or "secret" in result.summary
    assert not archive.exists()


def test_export_blocks_sensitive_kyc_project(tmp_path: Path) -> None:
    project = tmp_path / "portable"
    project.mkdir()
    (project / "README.md").write_text("hello", encoding="utf-8")
    ProjectRegistry.set_sensitivity(project, "sensitive_kyc")

    archive = tmp_path / "portable.zip"
    result = WorkspacePortabilityService().export_project(project, archive)

    assert result.success is False
    assert "sensitive-KYC" in result.summary
    assert not archive.exists()


def test_export_preview_reports_client_sensitivity(tmp_path: Path) -> None:
    project = tmp_path / "portable"
    project.mkdir()
    (project / "README.md").write_text("hello", encoding="utf-8")
    ProjectRegistry.set_sensitivity(project, "client")

    preview = WorkspacePortabilityService().preview_export(project)

    assert preview.success is True
    assert preview.sensitivity == "client"
    assert "客戶專案" in preview.summary


def test_import_project_restores_portable_archive(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("portable", encoding="utf-8")
    archive = tmp_path / "portable.zip"
    service = WorkspacePortabilityService()
    exported = service.export_project(source, archive)
    assert exported.success is True

    destination = tmp_path / "dest"
    imported = service.import_project(archive, destination)

    assert imported.success is True
    target = Path(imported.project_path)
    assert (target / "README.md").read_text(encoding="utf-8") == "portable"
    assert (target / ".aicc" / "project.json").is_file()
    assert (target / ".aicc" / "knowledge" / "PROJECT_BRIEF.md").is_file()
