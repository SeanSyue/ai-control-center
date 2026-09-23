"""匯出預覽要回答的那一個問題（ROADMAP P5）。

這個對話框出現的時刻，人正在決定要不要把一包東西交給別人。他心裡的問題不是
「總共幾個檔案」，是**「我的 .env 有沒有在裡面」**。

原本的摘要只有兩個數字。這裡的斷言幾乎都是在確認新的摘要真的回答了那個問題，
而不是換一種方式重述數字——列出三百個路徑也是同一種不回答，只是墨水比較多，
而且會讓「我看過了」變成一句感覺很盡責的假話。
"""
from __future__ import annotations

from pathlib import Path

from app.services.workspace_portability import (
    MAX_NAMED_SKIPS,
    WorkspacePortabilityService,
)


def make_project(root: Path, extra: dict[str, str] | None = None) -> Path:
    project = root / "案子"
    files = {
        "README.md": "# hi",
        ".env": "SECRET=abc",
        "src/main.py": "print(1)",
        ".aicc/task.md": "# 任務",
        ".aicc/sessions.json": "{}",
        ".aicc/knowledge/PROJECT_BRIEF.md": "brief",
        ".aicc/runtime/workflow.lock": "{}",
        ".aicc/decisions/decision-log.jsonl": "{}",
        ".git/config": "x",
        "__pycache__/a.pyc": "x",
    }
    files.update(extra or {})
    for relative, body in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return project


def summary_for(root: Path, extra: dict[str, str] | None = None) -> str:
    return WorkspacePortabilityService().preview_export(
        make_project(root, extra)
    ).summary


# ---- the question it has to answer --------------------------------------


def test_the_secret_is_named_not_counted(tmp_path: Path) -> None:
    """「本機專屬或含機密：2 個」 does not tell you whether yours is one of them."""
    text = summary_for(tmp_path)

    assert ".env" in text
    assert "本機專屬或含機密" in text


def test_the_decision_log_is_named_when_it_stays_behind(
    tmp_path: Path,
) -> None:
    """It deliberately does not ride along, and that is worth saying out loud.

    A workspace zip carries the project; the decision log has its own
    authorised export (P8). Somebody who assumed 「都在裡面」 and hands this to
    a colleague has not sent the audit trail, and would not find out from a
    count.
    """
    text = summary_for(tmp_path)

    assert ".aicc/decisions/decision-log.jsonl" in text


def test_the_aicc_entries_that_do_travel_are_shown(tmp_path: Path) -> None:
    # The AICC-specific ones are what a person would not think to expect in a
    # zip they are about to send.
    text = summary_for(tmp_path)

    assert ".aicc/task.md" in text
    assert ".aicc/sessions.json" in text
    assert "knowledge" in text


def test_bulk_exclusions_are_counted_not_listed(tmp_path: Path) -> None:
    """Nobody needs the list of `.git` objects, and printing it buries the
    two lines above it that do matter."""
    extra = {f".git/objects/{i:02d}/blob": "x" for i in range(40)}

    text = summary_for(tmp_path, extra)

    assert "版本控制與虛擬環境" in text
    assert "objects/00/blob" not in text


def test_a_long_list_of_named_files_is_capped(tmp_path: Path) -> None:
    """A list long enough to scroll is a list nobody reads."""
    extra = {f"pkg{i}/.env": "S=1" for i in range(MAX_NAMED_SKIPS + 8)}
    project = make_project(tmp_path, extra)
    preview = WorkspacePortabilityService().preview_export(project)
    secrets = preview.skipped_by_reason["secret_or_local"]

    text = preview.summary
    # Counted as "indented file lines under a named reason" rather than by
    # extension: the cap applies to the whole group, and the 12th entry here
    # happens to be `workflow.lock`, not a `.env`.
    named = [
        line
        for line in text.splitlines()
        if line.startswith("      ") and "/" in line or line.strip() == ".env"
    ]
    assert len(secrets) > MAX_NAMED_SKIPS
    assert f"還有 {len(secrets) - MAX_NAMED_SKIPS} 個" in text
    assert len([n for n in named if n.strip() in secrets]) == MAX_NAMED_SKIPS


def test_the_counts_still_agree_with_the_lists(tmp_path: Path) -> None:
    # The headline numbers are the one thing that was already right; a
    # rewrite of the prose must not quietly change them.
    preview = WorkspacePortabilityService().preview_export(make_project(tmp_path))

    assert len(preview.included_files) == len(set(preview.included_files))
    assert sum(
        len(names) for names in preview.skipped_by_reason.values()
    ) == len(preview.skipped_files)
    assert f"匯出 {len(preview.included_files)} 個檔案" in preview.summary
    assert f"排除 {len(preview.skipped_files)} 個" in preview.summary


# ---- the reason and the filter are one thing ----------------------------


def test_every_skipped_file_has_a_reason_and_every_kept_file_has_none(
    tmp_path: Path,
) -> None:
    """`_should_skip` is now derived from `skip_reason`, not a second copy.

    Kept as an explicit assertion because the bool version is still what the
    import path calls - if the two could disagree, a file could be excluded
    from the zip and reported as included, or the reverse.
    """
    service = WorkspacePortabilityService()
    preview = service.preview_export(make_project(tmp_path))

    for name in preview.skipped_files:
        assert service.skip_reason(name), name
        assert service._should_skip(name), name
    for name in preview.included_files:
        if name == "AICC_EXPORT_MANIFEST.json":
            continue
        assert service.skip_reason(name) == "", name
        assert not service._should_skip(name), name


def test_every_reason_key_has_words_for_it(tmp_path: Path) -> None:
    # A reason with no label renders as its own key - an English identifier in
    # the middle of a Chinese dialog, which reads as a bug even when the
    # decision behind it was right.
    service = WorkspacePortabilityService()
    preview = service.preview_export(make_project(tmp_path))

    for reason in preview.skipped_by_reason:
        assert reason in service._SKIP_REASON_KEYS
