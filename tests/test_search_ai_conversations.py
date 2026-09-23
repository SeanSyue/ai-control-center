from __future__ import annotations

from app.services.session_scanner import SessionInfo, SessionMessage
from tools.search_ai_conversations import (
    best_snippet,
    normalize_title,
    redact,
    score_session,
)


def test_normalize_title_extracts_user_task_from_aicc_wrapper() -> None:
    session = SessionInfo(
        provider="claude",
        session_id="abc123",
        title="你是此專案的開發工程師。AI Control Center wrapper prompt",
        metadata={"title_source": "message_preview"},
    )
    messages = [
        SessionMessage(
            provider="claude",
            session_id="abc123",
            role="user",
            content=(
                "你是此專案的開發工程師。\n"
                "任務：在 demo_project 新增 subtract(a, b)。\n"
                "限制：不修改其他功能。"
            ),
        )
    ]

    assert normalize_title(session, messages) == (
        "在 demo_project 新增 subtract(a, b)"
    )


def test_official_title_wins_over_message_preview() -> None:
    session = SessionInfo(
        provider="codex",
        session_id="thread-1",
        title="KYC MVP 程式開發3",
        metadata={"title_source": "official"},
    )

    assert normalize_title(session, []) == "KYC MVP 程式開發3"


def test_score_session_uses_title_project_and_messages() -> None:
    session = SessionInfo(
        provider="codex",
        session_id="thread-1",
        title="KYC proposal",
        project_path=r"C:\Projects\KYC",
    )
    messages = [
        SessionMessage(
            provider="codex",
            session_id="thread-1",
            role="user",
            content="Please review the KYC permission policy.",
        )
    ]

    assert score_session(["KYC", "permission"], session, messages) >= 10
    assert score_session(["unrelated"], session, messages) == 0


def test_snippet_is_short_and_redacted() -> None:
    session = SessionInfo(
        provider="claude",
        session_id="s1",
        title="Token test",
        metadata={"preview": "api_key=sk-thisshouldnotleak1234567890 KYC"},
    )

    snippet = best_snippet(["KYC"], session, [])

    assert "[REDACTED]" in snippet
    assert "sk-thisshouldnotleak" not in snippet


def test_redact_hides_long_secret_like_values() -> None:
    text = "token=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ012345"

    assert redact(text) == "[REDACTED]"
