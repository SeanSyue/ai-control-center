from __future__ import annotations

from pathlib import Path


def test_main_window_exposes_decision_notification_policy() -> None:
    # Stage 1 Phase 9: no in-app popup, ever - only the Dashboard's 等待決策
    # column, this project's own start_button text, and (unchanged, since
    # it's an opt-in external channel the user configured themselves) the
    # T+10 LINE push remain as notification mechanisms.
    source = Path("app/ui/main_window.py").read_text(encoding="utf-8")

    assert "decision_notification_policy" in source
    assert '"popup_ever": False' in source
    assert '"reminder_log_after_minutes": 5' in source
    assert '"line_after_minutes": 10' in source
    assert '"sound_enabled": False' in source
