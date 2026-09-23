import re
from pathlib import Path


def test_ai_settings_panel_exposes_model_effort_and_round_controls() -> None:
    source = Path("app/ui/ai_settings_panel.py").read_text(
        encoding="utf-8"
    )

    assert "self.rounds_spin.setRange(0, 5)" in source
    assert 'self.claude_model_combo.addItem("Claude Sonnet 5", "sonnet")' in source
    assert '("Medium", "medium")' in source
    assert 'self.codex_model_combo.addItem("GPT-5.5", "gpt-5.5")' in source
    assert "self.claude_speed_combo.setEnabled(False)" in source
    assert "self.codex_speed_combo.setEnabled(False)" in source
    assert "self.force_new_checkbox" in source


def test_main_window_has_page_scroll_and_settings_panel() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")
    workflow_worker_source = Path(
        "app/core/workflow_worker.py"
    ).read_text(encoding="utf-8")

    assert "self.page_scroll = QScrollArea()" in source
    assert "self.ai_settings_panel = AISettingsPanel" in source
    # 「規劃完成」這一種收場搬到 app/core/outcomes.py 了——畫面不再自己判定
    # 收場長什麼樣（2026-08-23：那條判定鏈的 else 讓十種收場被畫成紅燈）。
    assert '"PLANNING_COMPLETED"' in Path(
        "app/core/outcomes.py"
    ).read_text(encoding="utf-8")
    assert "describe_outcome(result)" in source
    assert "write_memory_from_text" in workflow_worker_source
    # 那句話 2026-08-24 搬進 `app/i18n.py` 了。**守的東西沒有變**：
    # 主視窗要畫得出那段「導入完成，接下來做什麼」的說明。
    # 比 key 而不是比中文——比中文的話它會在每次搬家時變紅，而搬家不是壞掉。
    assert '"project.imported.next_steps"' in source

    from app.i18n import STRINGS

    assert "既有專案已導入 AI Control Center" in STRINGS["project.imported.next_steps"]
    assert 'self._show_page("knowledge")' in source


def test_ai_settings_panel_exposes_long_task_controls() -> None:
    source = Path("app/ui/ai_settings_panel.py").read_text(
        encoding="utf-8"
    )

    assert "self.claude_permission_combo" in source
    assert '"ai.permission.auto"' in source
    assert '"auto"' in source
    assert "self.claude_timeout_combo" in source
    # 那個標籤 2026-08-24 搬進 `app/i18n.py` 了。**守的東西沒有變**：
    # 三小時那一格要對到 180 分鐘。
    assert '(t("ai.timeout.3h"), 180)' in source

    from app.i18n import STRINGS

    assert STRINGS["ai.timeout.3h"] == "3 小時"
    assert "self.claude_turns_spin.setRange(0, 200)" in source
    # 那個字 2026-08-24 搬進 `app/i18n.py` 了。**守的東西沒有變**：
    # 那一格在 0 的時候要顯示「不限」，不是顯示 0。
    assert 'self.claude_turns_spin.setSpecialValueText(t("common.unlimited"))' in source

    from app.i18n import STRINGS

    assert STRINGS["common.unlimited"] == "不限"
    assert 'file_name = "planning-request.md" if planning else "task.md"' in source
    assert '".aicc/"' in source


def test_main_tabs_are_before_ai_settings_panel() -> None:
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    # Match the shape, not the number: pinning the literal version made
    # every bump break tests in files that have nothing to do with it.
    assert re.search(r"v\d+\.\d+\.\d+-dev", source)
    # The pages sit above the AI settings panel, both now inside the column to
    # the right of the shell sidebar.
    assert source.index("content_column.addWidget(self.page_stack, 1)") < source.index(
        "content_column.addWidget(self.ai_settings_panel)"
    )
    assert "body_row.addWidget(self.shell_side)" in source
    assert "root_layout.addLayout(body_row, 1)" in source


def test_ai_settings_panel_has_collapsible_summary_source() -> None:
    source = Path("app/ui/ai_settings_panel.py").read_text(
        encoding="utf-8"
    )

    assert "self.toggle_button" in source
    assert "ai_settings_panel/expanded" in source
    assert "self.settings_body.setVisible(self._expanded)" in source
    assert "self.task_card.setVisible(self._expanded)" in source
    assert "def is_expanded" in source
    assert "auto_bootstrap_checkbox" in source
    assert "auto_session_bootstrap" in source
    assert "auto_memory_checkbox" in source
    assert "auto_memory_maintenance" in source
