from __future__ import annotations

import pytest

from app.adapters.cli_runner import CliResult
from app.core import orchestrator_parsing as parsing


# --- parse_claude_question -------------------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("no marker here", ""),
        ("ASK_USER:要用哪個資料庫？", "要用哪個資料庫？"),
        (
            "前言\nASK_USER:要用哪個資料庫？END_ASK_USER\n後續文字",
            "要用哪個資料庫？",
        ),
        ("ASK_USER:   前後有空白   ", "前後有空白"),
        ("", ""),
    ],
)
def test_parse_claude_question(output: str, expected: str) -> None:
    assert parsing.parse_claude_question(output) == expected


# --- parse_review ------------------------------------------------------------


def test_parse_review_valid_pass() -> None:
    result = parsing.parse_review(
        '{"status": "pass", "summary": "ok", "issues": [], "instructions": []}'
    )
    assert result == {
        "status": "PASS",
        "summary": "ok",
        "issues": [],
        # 【異議分兩級】：帶等級的那一份，以及拆好的兩堆。空清單也要在——
        # 呼叫端才分得出「這一輪沒有意見」與「這個欄位不存在」。
        "graded_issues": [],
        "blocking_issues": [],
        "note_issues": [],
        "instructions": [],
        # 【駁回直接送給該負責的人】：FIX 一定要指名一格。PASS 不用，
        # 所以這裡是空字串——但這一格必須在，呼叫端才分得出「沒指名」
        # 與「這個欄位不存在」。
        "target": "",
        "question": "",
    }


def test_parse_review_normalizes_non_list_issues_and_instructions() -> None:
    result = parsing.parse_review(
        '{"status": "FIX", "issues": "single issue", '
        '"instructions": "single instruction"}'
    )
    assert result["issues"] == ["single issue"]
    assert result["instructions"] == ["single instruction"]


def test_parse_review_malformed_json_returns_unknown() -> None:
    result = parsing.parse_review("not json at all")
    assert result["status"] == "UNKNOWN"
    assert result["issues"] == []
    assert result["instructions"] == []


def test_parse_review_non_dict_json_returns_unknown() -> None:
    result = parsing.parse_review("[1, 2, 3]")
    assert result["status"] == "UNKNOWN"


def test_parse_review_invalid_status_returns_unknown() -> None:
    result = parsing.parse_review('{"status": "MAYBE"}')
    assert result["status"] == "UNKNOWN"


def test_parse_review_status_matches_parse_review() -> None:
    output = '{"status": "ask_user"}'
    assert parsing.parse_review_status(output) == "ASK_USER"
    assert (
        parsing.parse_review_status(output)
        == parsing.parse_review(output)["status"]
    )


# --- parse_decision_gate ------------------------------------------------------


def test_parse_decision_gate_valid_approved() -> None:
    result = parsing.parse_decision_gate(
        '{"decision": "approved", "reason": "low risk", "answer": "go ahead"}'
    )
    assert result["decision"] == "APPROVED"
    assert result["reason"] == "low risk"
    assert result["answer"] == "go ahead"
    assert result["question"] == ""


def test_parse_decision_gate_malformed_json_asks_user() -> None:
    result = parsing.parse_decision_gate("  garbage output  ")
    assert result["decision"] == "ASK_USER"
    assert result["parsed"] is False
    # Was `== "garbage output"`. Escalating to a human is right; passing the
    # raw CLI text off as the question is not - the caller uses
    # `decision.get("question") or question`, so anything here wins over the
    # real question it is still holding. Found by opening the audit page and
    # reading 「ASK_USER: 要覆寫 demo.py 嗎？」, machine marker and all, in a
    # record meant to be handed to someone else.
    assert result["question"] == ""
    # The raw text is not lost - it is diagnostic, so it belongs in `reason`,
    # which is where the page shows 「停下來的原因」.
    assert "無法解析" in result["reason"]


def test_parse_decision_gate_non_dict_json_asks_user() -> None:
    result = parsing.parse_decision_gate("[1, 2]")
    assert result["decision"] == "ASK_USER"


def test_parse_decision_gate_unknown_decision_value_falls_back_to_ask_user() -> (
    None
):
    result = parsing.parse_decision_gate('{"decision": "MAYBE_LATER"}')
    assert result["decision"] == "ASK_USER"


# --- parse_memory_review ------------------------------------------------------


def test_parse_memory_review_valid_pass() -> None:
    result = parsing.parse_memory_review(
        '{"status": "pass", "summary": "ok", "memory_update": "## X\\n"}'
    )
    assert result["status"] == "PASS"
    assert result["memory_update"] == "## X\n"


def test_parse_memory_review_valid_ask_user() -> None:
    result = parsing.parse_memory_review(
        '{"status": "ask_user", "question": "safe to write this?"}'
    )
    assert result["status"] == "ASK_USER"
    assert result["question"] == "safe to write this?"


def test_parse_memory_review_malformed_json_defaults_to_pass() -> None:
    # Documents current behavior: unparsable Claude output silently falls
    # back to PASS (keeping Codex's memory draft) rather than escalating.
    # This may or may not be the ideal contract - it is pinned here so a
    # future intentional change to this fallback is a deliberate decision,
    # not an accidental regression caught only in production.
    result = parsing.parse_memory_review("not json")
    assert result["status"] == "PASS"
    assert result["memory_update"] == ""


def test_parse_memory_review_non_dict_json_defaults_to_pass() -> None:
    result = parsing.parse_memory_review("[1, 2, 3]")
    assert result["status"] == "PASS"


def test_parse_memory_review_unrecognized_status_defaults_to_pass() -> None:
    result = parsing.parse_memory_review('{"status": "MAYBE"}')
    assert result["status"] == "PASS"


# --- should_skip_memory_maintenance ------------------------------------------


def test_should_skip_memory_maintenance_true_for_docs_and_tests_only() -> None:
    assert parsing.should_skip_memory_maintenance(
        ["tests/test_x.py", "docs/notes.md", "README.md"]
    )


def test_should_skip_memory_maintenance_false_for_source_change() -> None:
    assert not parsing.should_skip_memory_maintenance(
        ["app/services/foo.py"]
    )


def test_should_skip_memory_maintenance_false_for_empty_list() -> None:
    # Documents current behavior: no changed files reported is treated as
    # "not low impact" (does not skip), not as "nothing changed, safe to
    # skip".
    assert not parsing.should_skip_memory_maintenance([])


def test_should_skip_memory_maintenance_normalizes_windows_paths() -> None:
    assert parsing.should_skip_memory_maintenance(["tests\\test_x.py"])


# --- normalize_claude_result / normalize_codex_result -------------------------


def _cli_result(stdout: str) -> CliResult:
    return CliResult(
        command="claude",
        success=True,
        return_code=0,
        stdout=stdout,
        stderr="",
    )


def test_normalize_claude_result_extracts_result_field_and_session_id() -> None:
    original_stdout = '{"session_id": "abc123", "result": "final answer"}'
    raw = _cli_result(original_stdout)
    normalized = parsing.normalize_claude_result(raw)
    assert normalized.stdout == "final answer"
    assert normalized.session_id == "abc123"
    assert normalized.raw_stdout == original_stdout


def test_normalize_claude_result_non_json_stdout_passes_through_unchanged() -> (
    None
):
    raw = _cli_result("plain text, not JSON")
    normalized = parsing.normalize_claude_result(raw)
    assert normalized.stdout == "plain text, not JSON"
    assert normalized.session_id == ""


def test_normalize_codex_result_extracts_last_agent_message_and_session() -> (
    None
):
    raw = _cli_result(
        '{"type": "thread.started", "thread_id": "t-1"}\n'
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "first"}}\n'
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "second"}}\n'
    )
    normalized = parsing.normalize_codex_result(raw)
    assert normalized.stdout == "second"
    assert normalized.session_id == "t-1"
    assert len(normalized.events) == 3


def test_normalize_codex_result_ignores_malformed_lines() -> None:
    raw = _cli_result("not json\n{\"type\": \"thread.started\", \"thread_id\": \"t-2\"}\n")
    normalized = parsing.normalize_codex_result(raw)
    assert normalized.session_id == "t-2"
    assert len(normalized.events) == 1
