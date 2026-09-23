"""Renaming a seat: replacing its 「你是⋯⋯」 line (ROADMAP P10 Stage D).

A custom identity lives on the `AgentSlot` the composer writes, so these
tests drive the same path a real run does - build a line-up, rename a seat,
read the prompt that comes out.

The half that matters most is the *negative* one: a custom identity must not
be able to delete the safety sentences welded to the original opening line.
The second-most is the surprising one: renaming a seat renames **every step
that seat runs**, and the author runs several with different shipped titles.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.cli_runner import CliResult
from app.core import prompt_builder
from app.core.lineup import (
    ROLE_AUTHOR,
    ROLE_PLANNER,
    AgentSlot,
    Lineup,
)
from app.core.orchestrator import Orchestrator
from app.services.ai_settings import AIExecutionSettings


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n測試。\n", encoding="utf-8")
    return project


def capture_prompts(
    tmp_path: Path, identities: dict[str, str] | None = None
) -> dict[str, str]:
    """Run a couple of steps against a line-up whose seats may be renamed.

    `identities` is keyed by **seat**, because that is where a custom
    「你是⋯⋯」 lives now: on the `AgentSlot` the composer writes. It used to be
    a constructor argument with no production caller - a seam kept alive only
    by these tests. Reading it off the line-up means these tests now exercise
    the same path a real run does.
    """
    lineup = Lineup.classic().with_planner()
    for role, identity in (identities or {}).items():
        lineup = lineup.with_identity(role, identity)
    orchestrator = Orchestrator(
        make_project(tmp_path),
        run_id="persona-run",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
        lineup=lineup,
    )
    captured: dict[str, str] = {}

    def fake_dispatch(
        role: str,
        prompt: str,
        event_type: str,
        metadata: dict | None = None,
        *,
        read_only: bool = False,
        title_prefix: str = "Codex",
    ) -> CliResult:
        captured[event_type] = prompt
        return CliResult(
            command="fake", success=True, return_code=0, stdout="", stderr=""
        )

    orchestrator._run_role_prompt = fake_dispatch  # type: ignore[method-assign]
    orchestrator.run_work_plan()
    orchestrator.run_claude()
    orchestrator.run_claude_memory_review("## PROJECT_BRIEF.md\n草案\n")
    return captured


def test_a_custom_identity_reaches_the_real_prompt(tmp_path: Path) -> None:
    default = capture_prompts(tmp_path / "a")["task"]
    custom = capture_prompts(
        tmp_path / "b", {ROLE_AUTHOR: "你是這個案子的合規審查員。"}
    )["task"]

    assert "你是此專案的開發工程師。" in default
    assert "你是這個案子的合規審查員。" in custom
    # Replaced, not appended - two job titles in one prompt is worse than
    # either one alone.
    assert "你是此專案的開發工程師。" not in custom


def test_a_custom_identity_cannot_delete_the_safety_rails(
    tmp_path: Path,
) -> None:
    """The whole reason identity and rails are separate fields.

    The planner's original opening line was one sentence of job title followed
    by three prohibitions. Had it stayed one string, "let the user rename the
    planner" would also have been "let the user delete 不得修改檔案".
    """
    custom = capture_prompts(
        tmp_path, {ROLE_PLANNER: "你是這個案子的架構師。"}
    )["work_plan"]

    assert "你是這個案子的架構師。" in custom
    assert "這是唯讀的規劃階段，不得修改任何檔案、" in custom
    assert "不得執行 Git 或建立 commit。" in custom


def test_renaming_one_seat_does_not_repaint_another(tmp_path: Path) -> None:
    """Identities belong to seats, so one rename must stay in its own seat."""
    prompts = capture_prompts(
        tmp_path, {ROLE_AUTHOR: "你是這個案子的合規審查員。"}
    )

    assert "你是這個案子的合規審查員。" in prompts["task"]
    assert "你是這件事的規劃者。" in prompts["work_plan"]
    assert "你是這個案子的合規審查員。" not in prompts["work_plan"]


def test_renaming_a_seat_renames_every_step_that_seat_runs(
    tmp_path: Path,
) -> None:
    """The consequence of keying by seat, pinned because it surprises.

    The author runs several steps with several *different* shipped job titles
    - 開發工程師 while writing, 共同記憶審查者 while checking a memory draft.
    One box replaces all of them. That is the behaviour 「每個 NODE 一個身分」
    asks for, but it is not what someone editing one box would guess, so the
    composer is given `shipped_identities` (plural) to say so.
    """
    prompts = capture_prompts(
        tmp_path, {ROLE_AUTHOR: "你是這個案子的合規審查員。"}
    )

    assert "你是這個案子的合規審查員。" in prompts["task"]
    assert "你是這個案子的合規審查員。" in prompts["memory_review"]
    assert "你是此專案的共同記憶審查者。" not in prompts["memory_review"]
    # ...and the read-only rail of that step survives the rename.
    assert "現在是唯讀檢查，不得修改檔案。" in prompts["memory_review"]


def test_an_over_long_identity_is_trimmed_not_rejected(tmp_path: Path) -> None:
    """It is prepended to every prompt that seat runs, so it cannot be a book.

    Trimmed rather than refused: this is a box a person types into, and losing
    what they wrote because it was one character over is worse than a short
    job title.
    """
    from app.core.lineup import IDENTITY_MAX_CHARS

    slot = AgentSlot(ROLE_AUTHOR, "claude", identity="你" * 500)

    assert len(slot.identity) == IDENTITY_MAX_CHARS


@pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
def test_a_blank_identity_keeps_the_shipped_one(blank: str) -> None:
    """A cleared field must not produce a prompt that opens with nothing."""
    persona = prompt_builder.resolve_persona("task", blank)
    assert persona.opening() == "你是此專案的開發工程師。"


def test_an_unregistered_step_raises_rather_than_inventing_a_job_title() -> None:
    with pytest.raises(KeyError):
        prompt_builder.resolve_persona("步驟不存在")


def test_the_decision_gate_has_no_replaceable_identity() -> None:
    """Deliberate omission, recorded so it is not "fixed" later by accident.

    The Decision Gate is the component that decides whether a question has to
    reach a person. Letting its job title be rewritten is letting the user
    edit the thing that protects them - and unlike the other steps there is no
    upside, because nobody needs their gate to have a different personality.
    """
    assert "decision_gate" not in prompt_builder.DEFAULT_PERSONAS
    source = Path("app/core/prompt_builder.py").read_text(encoding="utf-8")
    assert "你是 AI Control Center 的 Decision Gate。" in source


def test_every_prompt_building_step_has_a_registered_persona(
    tmp_path: Path,
) -> None:
    """Keeps `DEFAULT_PERSONAS` and the golden snapshot's step list in step.

    `planning_final` is absent on purpose: it is `planning` run with
    `final_round=True`, one step in two phases, and giving the final round its
    own job title would let the two disagree about who is speaking.
    """
    from tests.test_prompt_golden import make_orchestrator, exercise_every_step

    orchestrator, captured = make_orchestrator(tmp_path)
    exercise_every_step(orchestrator)

    steps = {step["event_type"] for step in captured}
    unregistered = steps - set(prompt_builder.DEFAULT_PERSONAS)
    assert unregistered == {"decision_gate", "planning_final"}, (
        "有步驟沒有註冊 persona。每個步驟的「你是⋯⋯」都必須有一份出廠設定，"
        f"否則 Stage D 會替它編一個職稱。未註冊：{unregistered}"
    )


def test_step_roles_matches_what_the_engine_actually_dispatches(
    tmp_path: Path,
) -> None:
    """`STEP_ROLES` is a restatement, so it is checked, not trusted.

    The composer needs to answer 「這一格的身分會影響哪些步驟」 without running
    anything, which means a second copy of "who runs what". A second copy that
    nothing compares is a second copy that drifts - and the way it fails is
    the worst kind: the box says renaming the author affects four steps while
    the run renames five, and nobody finds out until a prompt reads wrong.

    Checked against the golden snapshot's captured dispatches, which are the
    real thing rather than another restatement.
    """
    from tests.test_prompt_golden import (
        exercise_every_step,
        exercise_the_planner_seat,
        make_orchestrator,
    )

    orchestrator, captured = make_orchestrator(tmp_path)
    exercise_every_step(orchestrator)
    planned, planned_captured = make_orchestrator(
        tmp_path / "planned", Lineup.classic().with_planner()
    )
    exercise_the_planner_seat(planned)

    observed = {
        step["event_type"]: step["role"]
        for step in (captured + planned_captured)
        # 前兩個沒有人格所以沒有條目：決策關卡的稱謂不是使用者的，
        # 而 planning_final 是 planning 的第二階段。
        #
        # `consult` 是第三種，2026-08-24 加的：它**有**人格，但它是**任何
        # 一格**都會跑的一步（任何一格派工時都可能觸發討論），所以它不屬於
        # 某一個位子。硬要塞進 `STEP_ROLES` 就得挑一個角色，而挑哪一個都錯。
        if step["event_type"]
        not in ("decision_gate", "planning_final", *prompt_builder.STEPS_EVERY_SEAT_RUNS)
    }

    assert observed == prompt_builder.STEP_ROLES


def test_the_composer_is_told_every_title_a_rename_would_replace(
    tmp_path: Path,
) -> None:
    """Plural, because one box replacing three titles is not guessable.

    A composer showing only the first would be making a promise the engine
    does not keep.
    """
    author_titles = prompt_builder.shipped_identities_for_role(ROLE_AUTHOR)

    assert "你是此專案的開發工程師。" in author_titles
    assert "你是此專案的共同記憶審查者。" in author_titles
    assert "你是此專案的實作顧問。" in author_titles
    assert len(author_titles) >= 3

    # The planner runs one step, so its box replaces exactly one title. Pinned
    # from the other side: a helper that always returned "several" would be
    # just as wrong.
    assert prompt_builder.shipped_identities_for_role(ROLE_PLANNER) == (
        "你是這件事的規劃者。",
    )
