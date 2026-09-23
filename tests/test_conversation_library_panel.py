from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox

from app.services.conversation_search import ConversationHit
from app.services.session_registry import SessionRegistry
from app.ui.conversation_library_panel import ConversationLibraryPanel


class FakeConversationSearch:
    def __init__(self) -> None:
        self.search_calls: list[tuple[Path, str, str]] = []

    def search(
        self,
        project_path: Path,
        provider: str = "all",
        query: str = "",
        limit: int = 100,
        include_snippets: bool = True,
    ):
        self.search_calls.append((project_path, provider, query))
        if provider == "claude":
            return [
                ConversationHit(
                    provider="claude",
                    title="Claude raw",
                    display_title="KYC MVP 程式開發3",
                    session_id="claude-1",
                    updated_at="2026-08-03T10:00:00+08:00",
                    project_path=str(project_path),
                    source="claude-local",
                    title_source="official",
                    resume_status="可續接",
                    score=1,
                    snippet="Claude preview",
                )
            ]
        return [
            ConversationHit(
                provider="codex",
                title="Codex raw",
                display_title="提案簡報梳理",
                session_id="codex-1",
                updated_at="2026-08-03T11:00:00+08:00",
                project_path="",
                source="codex-app-server",
                title_source="official",
                resume_status="可續接",
                score=1,
                snippet="Codex preview",
            )
        ]

    def render_preview(self, hit: ConversationHit, project_path: Path) -> str:
        return f"{hit.provider}:{hit.display_title}:{project_path.name}"


def test_conversation_library_scans_two_columns_and_previews(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    service = FakeConversationSearch()
    panel = ConversationLibraryPanel(tmp_path, search_service=service)  # type: ignore[arg-type]

    panel.search_input.setText("KYC")
    panel.scan_conversations()

    assert panel.claude_table.rowCount() == 1
    assert panel.codex_table.rowCount() == 1
    assert "找到 1 個 Claude 對話、1 個 Codex 對話" in panel.status_label.text()
    assert service.search_calls[-2][1] == "claude"
    assert service.search_calls[-1][1] == "codex"
    assert service.search_calls[-1][2] == "KYC"
    assert panel.selected_claude is not None
    assert panel.selected_codex is not None
    assert panel.claude_preview.toPlainText().startswith("claude:KYC MVP 程式開發3")
    assert panel.codex_preview.toPlainText().startswith("codex:提案簡報梳理")

    panel.deleteLater()
    app.processEvents()


def test_conversation_library_binds_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    service = FakeConversationSearch()
    panel = ConversationLibraryPanel(tmp_path, search_service=service)  # type: ignore[arg-type]
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

    panel.scan_conversations()
    panel.bind_selected_sessions()

    registry = SessionRegistry(tmp_path)
    assert registry.selected_session_id("claude") == "claude-1"
    assert registry.selected_session_id("codex") == "codex-1"
    assert registry.list_sessions("claude")[0].title == "KYC MVP 程式開發3"
    assert registry.list_sessions("codex")[0].title == "提案簡報梳理"
    panel.deleteLater()
    app.processEvents()


def test_conversation_library_emits_project_creation_request(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    service = FakeConversationSearch()
    panel = ConversationLibraryPanel(tmp_path, search_service=service)  # type: ignore[arg-type]
    emitted: list[str] = []
    panel.create_project_requested.connect(emitted.append)

    panel.scan_conversations()
    panel.request_project_from_pair()

    assert emitted == ["KYC MVP 程式開發3"]
    panel.deleteLater()
    app.processEvents()


def test_conversation_library_source_has_pairing_controls() -> None:
    source = Path("app/ui/conversation_library_panel.py").read_text(
        encoding="utf-8"
    )

    assert "conversationSearchInput" in source
    assert "claudeConversationTable" in source
    assert "codexConversationTable" in source
    assert "pairConversationsButton" in source
    assert "createProjectFromPairButton" in source
    assert "claudeConversationPreview" in source
    assert "codexConversationPreview" in source
