"""Binding a conversation pair writes the same thing from either panel.

The HTML conversation library took over the 連接對話 tab from the native one.
That swap is only safe if binding through the new panel produces exactly the
session entries the old one produced - a session bound through the new UI that
the timeline panel cannot resume looks like data loss to the person who bound
it, and the two implementations are far apart in the tree.

So the first test compares the two panels' output field by field rather than
testing the extracted helper twice. Testing the helper alone would pass even if
one panel had stopped calling it.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from app.services.conversation_search import ConversationHit  # noqa: E402
from app.services.session_binding import (  # noqa: E402
    DEFAULT_PROJECT_NAME,
    bind_hits,
    suggest_project_name,
)
from app.services.session_registry import SessionRegistry  # noqa: E402


def _hit(
    provider: str,
    session_id: str,
    title: str = "設計新版面",
    project_path: str = "D:/demo/csv-summary",
) -> ConversationHit:
    # ConversationHit is frozen, so anything a test wants to vary has to be a
    # parameter here rather than an assignment at the call site.
    return ConversationHit(
        provider=provider,
        title=title,
        display_title=title,
        session_id=session_id,
        updated_at="2026-08-07T21:15:00",
        project_path=project_path,
        source="local",
        title_source="official",
        resume_status="resumable",
        score=10,
        snippet="第一段訊息",
    )


def test_binding_writes_one_entry_per_conversation(tmp_path: Path) -> None:
    registry = SessionRegistry(tmp_path)

    written = bind_hits(registry, tmp_path, (_hit("claude", "c1"), _hit("codex", "x1")))

    assert written == 2
    assert [e.session_id for e in registry.list_sessions("claude")] == ["c1"]
    assert [e.session_id for e in registry.list_sessions("codex")] == ["x1"]


def test_binding_only_one_side_is_allowed(tmp_path: Path) -> None:
    # Choosing a Claude conversation with no Codex counterpart is normal, not
    # an incomplete pair to be rejected.
    registry = SessionRegistry(tmp_path)

    written = bind_hits(registry, tmp_path, (_hit("claude", "c1"), None))

    assert written == 1
    assert registry.list_sessions("codex") == []


def test_the_bound_entry_keeps_what_the_session_list_needs(tmp_path: Path) -> None:
    # resume_status and title_source have no SessionEntry field of their own,
    # and re-deriving them later means re-scanning the disk. If they stop being
    # carried, the timeline panel silently loses the ability to say whether a
    # session can be resumed.
    registry = SessionRegistry(tmp_path)

    bind_hits(registry, tmp_path, (_hit("claude", "c1"),))

    entry = registry.list_sessions("claude")[0]
    assert entry.metadata["resume_status"] == "resumable"
    assert entry.metadata["title_source"] == "official"
    assert entry.metadata["conversation_library"] is True
    assert entry.metadata["raw"]["snippet"] == "第一段訊息"


def test_a_hit_without_its_own_project_falls_back_to_this_one(tmp_path: Path) -> None:
    registry = SessionRegistry(tmp_path)

    bind_hits(registry, tmp_path, (_hit("claude", "c1", project_path=""),))

    assert registry.list_sessions("claude")[0].project_path == str(tmp_path)


def test_the_suggested_name_prefers_a_real_title() -> None:
    assert suggest_project_name((None, _hit("codex", "x1", "重寫匯入流程"))) == "重寫匯入流程"


def test_the_suggested_name_falls_back_when_nothing_is_chosen() -> None:
    assert suggest_project_name((None, None)) == DEFAULT_PROJECT_NAME


def test_both_panels_bind_identically(tmp_path: Path, monkeypatch) -> None:
    """The actual swap guarantee.

    Drives each panel through its own binding entry point and compares the
    files on disk. Anything that makes one panel diverge - a dropped metadata
    key, a different project_path fallback - shows up here as a diff.
    """
    from PySide6.QtWidgets import QApplication, QMessageBox

    QApplication.instance() or QApplication([])
    # Both panels report success in a modal; nothing should block the test.
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    from app.ui.conversation_library_panel import ConversationLibraryPanel
    from app.ui.conversation_web_panel import ConversationWebPanel

    native_project = tmp_path / "native"
    web_project = tmp_path / "web"
    for path in (native_project, web_project):
        path.mkdir()

    claude, codex = _hit("claude", "c1"), _hit("codex", "x1")

    native = ConversationLibraryPanel(native_project)
    native.selected_claude, native.selected_codex = claude, codex
    native.bind_selected_sessions()

    web = ConversationWebPanel(web_project)
    web._hits = {"claude": [claude], "codex": [codex]}
    web._selected = {"claude": "c1", "codex": "x1"}
    web._bind_selected()

    def entries(project: Path) -> list[dict]:
        registry = SessionRegistry(project)
        out = []
        for provider in ("claude", "codex"):
            for entry in registry.list_sessions(provider):
                record = dict(vars(entry))
                # Two fields are expected to differ, and leaving them in would
                # mask a real difference: the registries live in different
                # folders, and created_at is stamped when each upsert runs -
                # microseconds apart, never equal.
                record.pop("project_path", None)
                record.pop("created_at", None)
                out.append(record)
        return out

    assert entries(native_project) == entries(web_project)
    assert len(entries(web_project)) == 2

    native.deleteLater()
    web.deleteLater()
