from __future__ import annotations

import pytest

from app.core import prompt_builder


# --- decision_gate_sensitivity_note ------------------------------------------


@pytest.mark.parametrize(
    ("sensitivity", "expected_marker"),
    [
        ("personal", "標準判斷規則"),
        ("client", "傾向 ASK_USER"),
        ("sensitive_kyc", "一律必須 ASK_USER"),
    ],
)
def test_decision_gate_sensitivity_note_known_levels(
    sensitivity: str,
    expected_marker: str,
) -> None:
    assert expected_marker in prompt_builder.decision_gate_sensitivity_note(
        sensitivity
    )


def test_decision_gate_sensitivity_note_unknown_level_falls_back_to_default() -> (
    None
):
    assert (
        prompt_builder.decision_gate_sensitivity_note("made_up_level")
        == prompt_builder.DEFAULT_SENSITIVITY_NOTE
    )


# --- output_instruction -------------------------------------------------------


@pytest.mark.parametrize(
    "verbosity",
    ["concise", "standard", "detailed"],
)
def test_output_instruction_known_levels_are_distinct(verbosity: str) -> None:
    assert (
        prompt_builder.output_instruction(verbosity)
        == prompt_builder.VERBOSITY_INSTRUCTIONS[verbosity]
    )


def test_output_instruction_unknown_level_falls_back_to_concise() -> None:
    assert (
        prompt_builder.output_instruction("made_up_verbosity")
        == prompt_builder.VERBOSITY_INSTRUCTIONS["concise"]
    )


# --- claude_safety_instruction -------------------------------------------------


def test_claude_safety_instruction_contains_ask_user_format() -> None:
    text = prompt_builder.claude_safety_instruction()
    assert "ASK_USER:" in text
    assert "END_ASK_USER" in text
    assert "git commit" in text


# --- build_decision_gate_prompt -------------------------------------------------


def test_build_decision_gate_prompt_interpolates_all_inputs() -> None:
    prompt = prompt_builder.build_decision_gate_prompt(
        task="做一個登入頁",
        source="claude",
        question="要用哪個資料庫？",
        memory_context="先前決定：用 SQLite。",
        sensitivity="personal",
    )
    assert "做一個登入頁" in prompt
    assert "要用哪個資料庫？" in prompt
    assert "先前決定：用 SQLite。" in prompt
    assert "專案敏感度：personal" in prompt
    # Stable structural markers a refactor should not accidentally drop,
    # without asserting on the full prompt text verbatim (which changes
    # often as prompt wording is iterated on in this repo).
    assert '"decision": "APPROVED | REJECTED | ASK_USER"' in prompt


def test_build_decision_gate_prompt_empty_memory_context_shows_placeholder() -> (
    None
):
    prompt = prompt_builder.build_decision_gate_prompt(
        task="t",
        source="claude",
        question="q",
        memory_context="",
        sensitivity="personal",
    )
    assert "尚未建立共同記憶" in prompt


@pytest.mark.parametrize(
    ("sensitivity", "expected_marker"),
    [
        ("personal", "標準判斷規則"),
        ("client", "傾向 ASK_USER"),
        ("sensitive_kyc", "一律必須 ASK_USER"),
    ],
)
def test_build_decision_gate_prompt_carries_correct_sensitivity_note(
    sensitivity: str,
    expected_marker: str,
) -> None:
    # This is the actual safety-relevant branch: a client/sensitive_kyc
    # project must produce a prompt that pushes Codex toward ASK_USER.
    prompt = prompt_builder.build_decision_gate_prompt(
        task="t",
        source="claude",
        question="q",
        memory_context="m",
        sensitivity=sensitivity,
    )
    assert f"專案敏感度：{sensitivity}" in prompt
    assert expected_marker in prompt


def test_build_decision_gate_prompt_unrecognized_sensitivity_still_produces_a_note() -> (
    None
):
    prompt = prompt_builder.build_decision_gate_prompt(
        task="t",
        source="claude",
        question="q",
        memory_context="m",
        sensitivity="made_up_level",
    )
    assert prompt_builder.DEFAULT_SENSITIVITY_NOTE in prompt
