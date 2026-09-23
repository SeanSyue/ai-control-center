from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.ui.ai_settings_panel import AISettingsPanel


def test_ai_settings_panel_long_task_controls_startup(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 任務\n測試。\n",
        encoding="utf-8",
    )

    panel = AISettingsPanel(project)
    settings = panel.current_settings()

    assert settings.claude_permission_mode == "auto"
    assert settings.claude_timeout_minutes == 30
    assert settings.auto_session_bootstrap is True
    assert settings.auto_memory_maintenance is True
    assert panel.claude_turns_spin.minimum() == 0
    assert panel.claude_timeout_combo.count() >= 6
    assert panel.auto_bootstrap_checkbox.isChecked() is True
    assert panel.auto_memory_checkbox.isChecked() is True
    assert panel.context_profile_combo.objectName() == "contextProfileCombo"
    assert settings.context_profile == "minimal"
    assert "上下文 Minimal" in panel.summary_label.text()

    panel.deleteLater()
    app.processEvents()


def test_ai_settings_panel_is_collapsed_by_default_and_persists(
    tmp_path: Path,
) -> None:
    from PySide6.QtCore import QSettings

    app = QApplication.instance() or QApplication([])
    settings = QSettings("Sean", "AI Control Center")
    original_value = settings.value("ai_settings_panel/expanded", None)
    settings.remove("ai_settings_panel/expanded")
    settings.sync()

    project = tmp_path / "project-collapsed"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# Task\n",
        encoding="utf-8",
    )

    try:
        panel = AISettingsPanel(project)
        assert panel.is_expanded() is False
        assert panel.settings_body.isVisible() is False
        assert panel.task_card.isVisible() is False
        assert "Claude" in panel.summary_label.text()
        assert "Codex" in panel.summary_label.text()
        assert "啟動提示" in panel.summary_label.text()
        assert "記憶維護" in panel.summary_label.text()

        panel.set_expanded(True)
        assert panel.is_expanded() is True
        panel.deleteLater()
        app.processEvents()

        panel2 = AISettingsPanel(project)
        assert panel2.is_expanded() is True
        panel2.deleteLater()
        app.processEvents()
    finally:
        if original_value is None:
            settings.remove("ai_settings_panel/expanded")
        else:
            settings.setValue("ai_settings_panel/expanded", original_value)
        settings.sync()
