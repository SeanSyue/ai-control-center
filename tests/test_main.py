from __future__ import annotations

from pathlib import Path

from app.main import resolve_initial_project_path
from app.services.project_registry import ProjectEntry, ProjectRegistry


def test_resolve_initial_project_path_prefers_last_project_when_valid(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    last = tmp_path / "last-project"
    last.mkdir()
    registry = ProjectRegistry()
    registry.save([], str(last))

    result = resolve_initial_project_path(registry)

    assert result == last


def test_resolve_initial_project_path_falls_back_to_a_registered_project(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    registered = tmp_path / "registered-project"
    registered.mkdir()
    registry = ProjectRegistry()
    registry.save(
        [ProjectEntry(name=registered.name, path=str(registered))],
        "",
    )

    result = resolve_initial_project_path(registry)

    assert result == registered


def test_resolve_initial_project_path_creates_a_default_folder_on_a_fresh_install(
    tmp_path: Path, monkeypatch
) -> None:
    # A brand-new install: nothing has ever been registered, and there is no
    # dev sandbox/demo_project fixture on disk (that folder is never
    # packaged). This must not point at a path that doesn't exist.
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    registry = ProjectRegistry()

    result = resolve_initial_project_path(registry)

    assert result == tmp_path / "home" / "Documents" / "AI Control Center"
    assert result.is_dir()
