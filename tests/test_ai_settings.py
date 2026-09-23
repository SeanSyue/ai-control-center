from __future__ import annotations

import json
from pathlib import Path

from app.services.ai_settings import AIExecutionSettings, AISettingsStore


def test_budget_profile_defaults() -> None:
    settings = AISettingsStore.profile("省額度")

    assert settings.claude_model == "sonnet"
    assert settings.claude_effort == "medium"
    assert settings.codex_model == "gpt-5.5"
    assert settings.codex_effort == "medium"
    assert settings.planning_rounds == 0
    assert settings.max_fix_attempts == 1
    # 出廠沿用同一段對話，不是每次任務都重開。老闆 2026-08-21：
    # 「要像影片中的使用同一個對話框 不然記憶中斷加超浪費 TOKEN
    #   也沒辦法訓練自己的 ai」
    assert settings.force_new_sessions is False
    assert settings.auto_session_bootstrap is True
    assert settings.auto_memory_maintenance is True
    assert settings.context_profile == "minimal"
    # 設定頁上「預計 N 次模型呼叫」那一句。**2026-08-27 從 1 變成 6**：
    # 出廠序列換成開發序列之後，這張表跑的是執行＋複查（2）、一次修正
    # （2）、維護共同記憶（2）——而計畫序列那條路上只有出計畫那一次。
    #
    # 這個數字不是裝飾：`queue_scheduler.is_high_usage` 拿它跟 5 比，
    # 所以出廠設定現在**算高用量**（見
    # `tests/test_queue_scheduler.py::test_the_factory_settings_now_count_as_high_usage`）。
    assert settings.expected_model_calls == 6


def test_planning_call_count() -> None:
    settings = AIExecutionSettings(
        workflow_mode="planning",
        planning_rounds=2,
    )
    assert settings.expected_model_calls == 5


def test_project_settings_round_trip(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    store = AISettingsStore(project)
    settings = AIExecutionSettings(
        workflow_mode="development",
        planning_rounds=3,
        claude_model="sonnet",
        claude_effort="medium",
        codex_model="gpt-5.5",
        codex_effort="medium",
        max_fix_attempts=0,
        auto_session_bootstrap=False,
        auto_memory_maintenance=False,
        context_profile="strict",
        profile_name="自訂",
    )

    store.save_project(settings)
    loaded = store.load_project()

    assert loaded.workflow_mode == "development"
    assert loaded.claude_effort == "medium"
    assert loaded.codex_effort == "medium"
    assert loaded.max_fix_attempts == 0
    assert loaded.auto_session_bootstrap is False
    raw = json.loads(
        (project / ".aicc" / "ai-settings.json").read_text(
            encoding="utf-8"
        )
    )
    assert raw["codex_model"] == "gpt-5.5"
    assert raw["auto_session_bootstrap"] is False
    assert raw["auto_memory_maintenance"] is False
    assert raw["context_profile"] == "strict"


def test_development_call_count_includes_memory_maintenance() -> None:
    settings = AIExecutionSettings(
        workflow_mode="development",
        max_fix_attempts=1,
        auto_memory_maintenance=True,
    )
    assert settings.expected_model_calls == 6

    settings = AIExecutionSettings(
        workflow_mode="development",
        max_fix_attempts=1,
        auto_memory_maintenance=False,
    )
    assert settings.expected_model_calls == 4


def test_long_task_settings_support_safe_auto_and_unlimited_values() -> None:
    budget = AISettingsStore.profile("省額度")
    standard = AISettingsStore.profile("標準")

    assert budget.claude_permission_mode == "auto"
    assert budget.claude_timeout_minutes == 30
    assert budget.claude_max_turns == 4
    assert standard.claude_timeout_minutes == 180
    assert standard.claude_max_turns == 20

    unlimited = AIExecutionSettings(
        claude_permission_mode="auto",
        claude_timeout_minutes=0,
        claude_max_turns=0,
    ).normalized()
    assert unlimited.claude_timeout_seconds is None
    assert unlimited.claude_max_turns == 0


def test_context_profiles_follow_budget_standard_and_deep() -> None:
    budget = AISettingsStore.profile("省額度")
    standard = AISettingsStore.profile("標準")
    deep = AISettingsStore.profile("深入")

    assert budget.context_profile == "minimal"
    assert standard.context_profile == "balanced"
    assert deep.context_profile == "strict"

    invalid = AIExecutionSettings(context_profile="too-much").normalized()
    assert invalid.context_profile == "minimal"
