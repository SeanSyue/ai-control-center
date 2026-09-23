"""換一個額度組合，不該把你的權限設定換掉。

三個內建組合（省額度／標準／深入）是**額度**預設：模型、推理強度、輪數、
上下文大小。而它們每一個都寫死 `claude_permission_mode="auto"`，加上
`apply_settings` 覆蓋每一個欄位，於是：

    在設定頁把某個客戶專案設成「手動授權」，接著在原生面板碰一下組合
    下拉選單，它**無聲地變回「安全自動」**。

沒有任何地方會講。而網頁設定頁自己在「全自動」旁邊印著「客戶或 KYC 專案
請維持每次問」——兩個面板寫的是同一份設定。

這一組釘的是**使用者選的東西活不活得下來**，不是程式碼長什麼樣。
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.services.ai_settings import AIExecutionSettings, AISettingsStore  # noqa: E402
from app.ui.ai_settings_panel import AISettingsPanel  # noqa: E402

PROFILES = ("省額度", "標準", "深入")
# 使用者選得到的四個值。`auto` 是預設，所以拿它當「使用者選的」測不出東西。
NOT_THE_DEFAULT = ("default", "acceptEdits", "plan")


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    QApplication.instance() or QApplication([])
    project = tmp_path / "案子"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("任務\n", encoding="utf-8")
    widget = AISettingsPanel(project)
    yield widget
    widget.deleteLater()


def _choose_permission(widget: AISettingsPanel, mode: str) -> None:
    index = widget.claude_permission_combo.findData(mode)
    assert index >= 0, f"介面上沒有 {mode} 這個選項"
    widget.claude_permission_combo.setCurrentIndex(index)


# --- 這一組存在的理由 --------------------------------------------------------


@pytest.mark.parametrize("mode", NOT_THE_DEFAULT)
@pytest.mark.parametrize("profile_name", PROFILES)
def test_switching_budget_preset_keeps_your_permission(
    panel: AISettingsPanel, profile_name: str, mode: str
) -> None:
    """三個組合 × 三個非預設權限，全部都要活下來。

    只測一個組合或只測一個權限值都不夠：漏掉的那一格正是會出事的那一格。
    """
    _choose_permission(panel, mode)

    panel._on_profile_changed(profile_name)

    assert panel.claude_permission_combo.currentData() == mode


@pytest.mark.parametrize("profile_name", PROFILES)
def test_the_preset_still_changes_what_it_is_for(
    panel: AISettingsPanel, profile_name: str
) -> None:
    """前提檢查：組合還是要真的套用。

    少了這條，「權限沒被改掉」可以因為**整個組合都沒有生效**而通過，
    而那是完全不同的狀況——也更糟。
    """
    _choose_permission(panel, "plan")
    expected = AISettingsStore.profile(profile_name)

    panel._on_profile_changed(profile_name)

    settings = panel.current_settings()
    assert settings.context_profile == expected.context_profile
    assert settings.planning_rounds == expected.planning_rounds
    assert settings.claude_effort == expected.claude_effort
    # 而權限仍然是使用者選的那個。
    assert settings.claude_permission_mode == "plan"


def test_the_four_options_are_all_reachable(panel: AISettingsPanel) -> None:
    """原生面板有四個，而網頁設定頁只有三個（缺「唯讀規劃」）。

    兩個面板寫的是同一份設定，所以少的那一個不是「網頁比較簡潔」，是
    **在網頁那一頁選不到、而且看不出自己選不到**。這條先釘住原生這邊四個
    都在；網頁補齊之前，`plan` 只能從原生面板設定。
    """
    available = {
        panel.claude_permission_combo.itemData(index)
        for index in range(panel.claude_permission_combo.count())
    }

    assert available == set(AIExecutionSettings.VALID_CLAUDE_PERMISSION_MODES)


@pytest.mark.parametrize("profile_name", PROFILES)
def test_a_preset_on_a_fresh_panel_still_has_a_permission(
    panel: AISettingsPanel, profile_name: str
) -> None:
    # 沒有人動過那一格時，組合自己的值就是答案——不要因為「保留使用者選擇」
    # 而留下一個空的權限模式。
    panel._on_profile_changed(profile_name)

    assert (
        panel.claude_permission_combo.currentData()
        in AIExecutionSettings.VALID_CLAUDE_PERMISSION_MODES
    )
