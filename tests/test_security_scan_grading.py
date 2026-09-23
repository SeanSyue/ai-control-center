"""The scanner has to be trusted to be useful, and it was not.

It gates two live paths - Drive upload and automatic memory maintenance - and
every pattern was scored "high", where one high finding stops a file. Measured
on this project's own knowledge folder: `DECISIONS.md` was refused, for two
matches on a long backup *filename* and one on a line documenting what a fake
key looks like. The single most important handoff document was the one file
that would not sync, and the report said only "已擋下 1".

A gate that cries wolf gets worked around, so these tests pin both directions:
the heuristics no longer block on their own, and the credential shapes that
matter still do.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.services.security_scan import SecurityScanner

# The exact string from DECISIONS.md that used to stop the file syncing.
BACKUP_NAME = "AIControlCenter-PRE-FILTER-REPO-BACKUP-2026-08-05"


def _scan(text: str):
    path = Path(tempfile.mkdtemp()) / "note.md"
    path.write_text(text, encoding="utf-8")
    return SecurityScanner().scan_paths([path])


def test_a_long_hyphenated_name_is_not_a_secret() -> None:
    report = _scan(f"備份檔叫 {BACKUP_NAME}.bundle，放在 D 槽。")

    assert report.findings == ()
    assert report.has_blocking_findings is False


def test_a_long_command_line_is_not_a_secret() -> None:
    report = _scan("git filter-repo --path .aicc/knowledge --invert-paths --force")

    assert report.has_blocking_findings is False


def test_a_bare_high_entropy_blob_is_reported_but_does_not_block() -> None:
    # Deliberate trade-off, and the reason it is safe: length alone cannot tell
    # a credential from a hash or a base64 image, and the shapes that really do
    # carry credentials are caught at high below. Still reported, so it is
    # visible to anyone reading the findings.
    report = _scan("payload " + "a1b2c3d4" * 8)

    assert report.findings, "完全沒報 —— 這條規則等於被刪掉了"
    assert report.findings[0].severity == "medium"
    assert report.has_blocking_findings is False


def test_an_env_var_style_token_still_blocks() -> None:
    # This is the one that nearly slipped. `\b` needs a non-word character
    # before the name, and `_` is a word character, so `LINE_CHANNEL_ACCESS_
    # TOKEN=...` never matched `access_token` at all - it only ever tripped the
    # length heuristic, by accident. Downgrading that heuristic without fixing
    # the name rules would have stopped blocking the most common real shape of
    # a leaked credential.
    report = _scan("LINE_CHANNEL_ACCESS_TOKEN=abc123" + "x" * 48)

    assert report.has_blocking_findings is True
    assert report.findings[0].kind == "access_token"


def test_the_other_assignment_shapes_still_block() -> None:
    for text, kind in (
        ("api_key=sk-thisshouldnotleak1234567890", "api_key"),
        ("MY_CLIENT_SECRET=hunter2hunter2hunter2", "secret"),
        ("DB_PASSWORD=p@ssw0rd", "password"),
        ("sk-abcdefghijklmnop1234", "openai_key"),
    ):
        report = _scan(text)
        assert report.has_blocking_findings is True, text
        assert report.findings[0].kind == kind, text


def test_ordinary_prose_produces_nothing() -> None:
    report = _scan("這一段是一般的中文技術敘述，講的是同步與記憶維護，沒有任何憑證。")

    assert report.findings == ()


def test_the_projects_own_knowledge_folder_is_not_wholesale_blocked() -> None:
    # The regression in its original form. One file still blocks - DECISIONS.md
    # line 60 literally reads `api_key=sk-...` while explaining what the
    # scanner catches, which is a correct match on a self-referential document
    # - so this asserts the false-positive class is gone, not that nothing is
    # ever flagged.
    knowledge = Path(__file__).resolve().parents[1] / ".aicc" / "knowledge"
    if not knowledge.is_dir():
        return   # a fresh clone has no knowledge folder; nothing to check

    report = SecurityScanner().scan_paths(sorted(knowledge.glob("*.md")))

    for finding in report.findings:
        assert finding.kind != "long_secret", (
            f"{Path(finding.path).name}:{finding.line} 又因為長度啟發式被標記"
        )
