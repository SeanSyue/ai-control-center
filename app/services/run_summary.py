"""這一次要怎麼跑，濃縮成一行字。

2026-08-27 從 `app/ui/ai_settings_panel.py::_update_summary` 抽出來的，
理由寫在那一輪的交接裡：

> **照原樣推過來當字串。** 畫面自己拼的話，那句話會在引擎多一個欄位的
> 那天少講一件事，而**它看起來完全正常**。

原生面板與網頁那一段現在讀的是**同一支**。分成兩份的話它們會漂，
而漂掉的樣子——兩行字都還在、都還讀得通、只是講的不是同一件事——
在畫面上一個字都看不出來。

這一頁已經為「畫面自己拼一份事實」壞過兩次（座位上限、`gate_floor`）。
"""
from __future__ import annotations

from app.i18n import t
from app.services.ai_settings import AIExecutionSettings

__all__ = ["summary_line"]


def summary_line(settings: AIExecutionSettings) -> str:
    """「本次：⋯」那一行。

    純函式：只吃 settings，不碰畫面、不讀硬碟。所以網頁那一側拿得到
    一模一樣的結果，而測試不必開一個 Qt 視窗才驗得了它。
    """
    planning = settings.workflow_mode == "planning"
    mode_text = t("sequence.planning") if planning else t("sequence.development")

    if planning:
        detail = t("run.summary.detail.planning", 
            rounds=settings.planning_rounds,
            calls=settings.expected_model_calls,
        )
    else:
        detail = t("run.summary.detail.development", 
            fixes=settings.max_fix_attempts,
            calls=settings.expected_model_calls,
        )

    session_note = t(
        "ai.hint.new_session" if settings.force_new_sessions
        else "ai.hint.resume_session"
    )
    bootstrap_note = t(
        "ai.hint.bootstrap_on" if settings.auto_session_bootstrap
        else "ai.hint.bootstrap_off"
    )
    memory_note = t(
        "ai.hint.memory_on" if settings.auto_memory_maintenance
        else "ai.hint.memory_off"
    )
    context_note = {
        "minimal": t("ai.hint.context_minimal"),
        "balanced": t("ai.hint.context_balanced"),
        "strict": t("ai.hint.context_strict"),
    }.get(settings.context_profile, t("ai.hint.context_minimal"))

    timeout_text = (
        t("common.unlimited")
        if settings.claude_timeout_minutes == 0
        else t("run.summary.timeout_minutes", 
            minutes=settings.claude_timeout_minutes
        )
    )
    turns_text = (
        t("common.unlimited")
        if settings.claude_max_turns == 0
        else str(settings.claude_max_turns)
    )

    # **整句一個鍵。**拆成七小段的話，翻譯的人看不出它們會被接成一句話，
    # 而接出來的順序在別的語言裡是錯的。
    return t("run.summary", 
        mode=mode_text,
        claude_model=settings.claude_model,
        claude_effort=settings.claude_effort,
        permission=settings.claude_permission_mode,
        timeout=timeout_text,
        turns=turns_text,
        codex_model=settings.codex_model,
        codex_effort=settings.codex_effort,
        detail=detail,
        context=context_note,
        session=session_note,
        bootstrap=bootstrap_note,
        memory=memory_note,
    )
