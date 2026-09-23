from __future__ import annotations

from pathlib import Path

from app.services.security_scan import SecurityScanner


def test_security_scanner_finds_secret_like_text(tmp_path: Path) -> None:
    file_path = tmp_path / "README.md"
    file_path.write_text("LINE_CHANNEL_ACCESS_TOKEN=abc123" + "x" * 48, encoding="utf-8")

    report = SecurityScanner().scan_project(tmp_path)

    assert report.has_blocking_findings is True
    assert report.findings[0].severity == "high"
    assert "README.md" in report.findings[0].path


def test_security_scanner_skips_runtime_folders(tmp_path: Path) -> None:
    runtime = tmp_path / "_update_backup"
    runtime.mkdir()
    (runtime / "old.py").write_text("token=abc" + "x" * 48, encoding="utf-8")

    report = SecurityScanner().scan_project(tmp_path)

    assert report.has_blocking_findings is False
    assert report.skipped_files == 1
