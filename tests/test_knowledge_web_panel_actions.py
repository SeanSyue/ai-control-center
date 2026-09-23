"""Saving a memory file must work without first clicking one.

Found in the running app, not in a test: the page selects the first file by
itself when Python has not named one, and `save` sends the edited *text* rather
than the file name. With `_selected` still None on this side, the save was
dropped with no error - the file was unchanged and `.provenance.json` was never
created. Every existing test set `_selected` by hand, so all of them passed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.ui.knowledge_web_panel import KnowledgeWebPanel  # noqa: E402


def _panel(tmp_path: Path) -> KnowledgeWebPanel:
    QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    project.mkdir()
    ProjectRegistry.initialize_project(project)
    panel = KnowledgeWebPanel(project)
    panel.store.ensure_memory_files()
    return panel


def test_saving_without_a_prior_click_writes_the_first_file(tmp_path: Path) -> None:
    panel = _panel(tmp_path)
    # Deliberately no select action - exactly what happens when someone opens
    # the tab, presses 編輯 on the file the page pre-selected, and saves.
    assert panel._selected is None

    panel.collect_state()          # the push that tells the page what is selected
    panel.handle_action("save", "# 改過的內容\n")

    first = panel.collect_state()["files"][0]
    assert "改過的內容" in first["source"]
    assert panel.store.read_provenance()[first["id"]]["by"] == "you"


def test_the_page_and_python_agree_on_the_selection(tmp_path: Path) -> None:
    # The underlying cause: two sides resolving "which file" separately.
    panel = _panel(tmp_path)

    state = panel.collect_state()

    assert state["selected"] == panel._selected
    assert state["selected"] == state["files"][0]["id"]


def test_an_explicit_selection_still_wins(tmp_path: Path) -> None:
    panel = _panel(tmp_path)
    panel.handle_action("select", "DECISIONS.md")

    panel.handle_action("save", "- 決策內容\n")

    assert panel.collect_state()["selected"] == "DECISIONS.md"
    assert panel.store.read_provenance()["DECISIONS.md"]["by"] == "you"
    # ...and the first file was not the one written.
    assert "決策內容" not in (panel._memory_dir() / "PROJECT_BRIEF.md").read_text(
        encoding="utf-8"
    )
