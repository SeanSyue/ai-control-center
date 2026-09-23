from pathlib import Path

from app.services.session_registry import SessionEntry, SessionRegistry


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    return project


def test_registry_persists_selected_sessions(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    registry = SessionRegistry(project)

    registry.upsert(
        SessionEntry(
            provider="claude",
            session_id="claude-1",
            title="Claude session",
            project_path=str(project),
        ),
        select=True,
    )
    registry.upsert(
        SessionEntry(
            provider="codex",
            session_id="codex-1",
            title="Codex thread",
            project_path=str(project),
        ),
        select=True,
    )

    reloaded = SessionRegistry(project)
    assert reloaded.selected_session_id("claude") == "claude-1"
    assert reloaded.selected_session_id("codex") == "codex-1"
    assert reloaded.list_sessions("claude")[0].title == "Claude session"


def test_registry_clear_selected_does_not_delete_history(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    registry = SessionRegistry(project)
    registry.touch("claude", "session-1", title="One")

    registry.clear_selected("claude")

    assert registry.selected_session_id("claude") == ""
    assert [item.session_id for item in registry.list_sessions("claude")] == [
        "session-1"
    ]


def test_registry_rename_marks_custom_title(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    registry = SessionRegistry(project)
    registry.touch("claude", "session-1", title="Original")

    registry.rename("claude", "session-1", "黑底花紋設計")

    entry = registry.list_sessions("claude")[0]
    assert entry.title == "黑底花紋設計"
    assert entry.metadata["custom_title"] is True
