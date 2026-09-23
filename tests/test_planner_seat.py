"""The optional third seat (ROADMAP P10 Stage B, 形狀一: 規劃者在最前).

Driven through `WorkflowWorker` with a scripted CLI, because the questions
worth asking are all about *sequence and count of paid calls* - how many CLI
invocations happen, in what order, and which prompt each one carried. None of
those are visible from the orchestrator methods in isolation.

The first test in this file is not about the planner at all. It covers the
wire the planner needed and did not have: the composer's line-up never
reached the engine.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.core.lineup import AgentSlot, Lineup
from app.core.workflow_worker import WorkflowWorker
from app.services.ai_settings import AIExecutionSettings
from app.services.session_registry import SessionRegistry
from tests.fakes import (
    calls_after_intake,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
    with_intake,
)


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 任務\n\n把設定頁修好。\n", encoding="utf-8"
    )
    declare_verify_command(project)
    return project


def _settings(**overrides) -> AIExecutionSettings:
    base = AIExecutionSettings(
        workflow_mode="development",
        auto_memory_maintenance=False,
        auto_session_bootstrap=False,
        max_fix_attempts=1,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _run(
    project: Path,
    monkeypatch,
    script,
    settings=None,
    **worker_kwargs,
):
    QApplication.instance() or QApplication([])
    runner_class = scripted_cli_runner_class(with_intake(script, settings))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project, settings=settings or _settings(), **worker_kwargs
    )
    completed: list = []
    failed: list = []
    stages: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.stage_changed.connect(stages.append)
    worker.run()
    return completed, failed, stages, calls_after_intake(runner_class.calls, settings)


def _prompts(calls: list[dict]) -> list[str]:
    """What each CLI invocation was actually asked.

    Both providers carry it the same way now - on standard input
    (`input_text`) - so the planner seat being Codex or Claude no longer
    changes where the prompt is read from. Claude used to carry it as the
    argument after `-p`, which a Windows command line cannot deliver at
    all once the prompt has more than one line.

    Non-model calls (the `pytest` run) have no prompt and come back "",
    which keeps the list index-aligned with `calls`.
    """
    out = []
    for call in calls:
        out.append(call.get("input_text") or "")
    return out


def seat_planner(project: Path, provider: str = "codex") -> None:
    registry = SessionRegistry(project)
    registry.save_lineup(registry.load_lineup().with_planner(provider))


PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)


# ---- the missing wire ----------------------------------------------------


def test_the_composed_lineup_reaches_the_engine(
    tmp_path: Path, monkeypatch
) -> None:
    """Composing a line-up used to change nothing about who ran.

    The composer wrote it into `sessions.json`; the engine built its own
    default from the two session-id arguments and never read the file. Setting
    both seats to Claude stored `reviewer: claude` and still ran Codex - a
    page whose controls all worked, driving nothing.

    Asserted on the *command that was launched*, not on the resolved line-up
    object: the object agreeing with the file proves the read, while the
    command proves the read is what the subprocess was built from.
    """
    project = make_project(tmp_path)
    registry = SessionRegistry(project)
    registry.save_lineup(
        Lineup.of(
            [
                AgentSlot("author", "claude"),
                AgentSlot("reviewer", "claude"),
            ]
        )
    )

    _, failed, _, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == []
    commands = [call["arguments"][0] for call in calls if call["arguments"]]
    assert "codex" not in commands, (
        "陣容排的是兩個 Claude，卻還是叫了 codex——"
        "編組頁的供應商設定沒有真的接到引擎"
    )


# ---- the planner ---------------------------------------------------------


def test_two_seats_never_call_a_planner(tmp_path: Path, monkeypatch) -> None:
    """The default project must cost exactly what it cost before.

    The scripted runner raises on a call the script has no result for, so the
    three-entry script *is* the assertion: a fourth invocation fails here.
    """
    project = make_project(tmp_path)

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == []
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert len(calls) == 3
    assert "規劃者正在擬定做法" not in stages
    assert not any("規劃者提出的做法" in p for p in _prompts(calls))


def test_the_planner_runs_first_and_its_plan_reaches_the_author(
    tmp_path: Path, monkeypatch
) -> None:
    project = make_project(tmp_path)
    seat_planner(project)

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("先改 CSS，不要動 Python。"),  # planner
            cli_result('{"result": "done"}'),  # author
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == []
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert len(calls) == 4
    # 2026-08-22 起，這一格會先跟使用者把需求問清楚（那一次派工由
    # `calls_after_intake` 從清單前面拿掉了），問完才擬做法。它是**唯一**一格
    # 在擬做法的，所以需求確認不會取代它的擬做法——取代掉的話，這條線上就沒有
    # 人擬做法了，而畫面上不會有任何一個字說了這件事。
    assert stages[0] == "規劃者正在跟你確認需求"
    assert "規劃者正在擬定做法" in stages

    prompts = _prompts(calls)
    assert "你是這件事的規劃者。" in prompts[0]
    # First, and read-only: the seat exists to think, and a planner that edits
    # files has become a second author.
    assert "不得修改任何檔案" in prompts[0]
    # And the author actually receives it. A planner whose output goes nowhere
    # is a paid call that changes nothing.
    assert "規劃者提出的做法" in prompts[1]
    assert "先改 CSS，不要動 Python。" in prompts[1]


def test_a_failed_planner_stops_the_run_before_anything_is_written(
    tmp_path: Path, monkeypatch
) -> None:
    """Not "carry on without a plan".

    Continuing would run a two-seat workflow while the line-up says three and
    report it as a normal result - the user gets a different workflow from the
    one they composed, with nothing on screen saying so.
    """
    project = make_project(tmp_path)
    seat_planner(project)

    completed, failed, _, calls = _run(
        project,
        monkeypatch,
        [cli_result("", success=False, stderr="planner died")],
    )

    assert failed == []
    assert completed[0]["workflow_status"] == "PLANNER_ERROR"
    assert len(calls) == 1, "規劃者失敗之後不應該還有人動手"


def test_resuming_after_an_answer_does_not_pay_for_a_second_plan(
    tmp_path: Path, monkeypatch
) -> None:
    """The resumed process continues the same Claude session, which has it.

    Re-running would be a second paid call producing a second, possibly
    different plan for work that is already half done.
    """
    project = make_project(tmp_path)
    seat_planner(project)

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "continued"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
        user_question="要不要一併改資料庫？",
        user_answer="不要。",
        run_id="resumed-run",
    )

    assert failed == []
    assert len(calls) == 3
    assert "規劃者正在擬定做法" not in stages


def test_a_stored_plan_is_reused_instead_of_re_run(
    tmp_path: Path, monkeypatch
) -> None:
    """Same run id, planner already paid for once.

    Covers the other resume shape: the run stopped and was restarted without a
    user answer. `stored_work_plan` is what stops that from re-planning.
    """
    project = make_project(tmp_path)
    seat_planner(project)
    from app.services.timeline_store import TimelineStore

    TimelineStore(project).save_artifact(
        "run-1", "work-plan.md", "已經想好的做法。"
    )

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
        run_id="run-1",
    )

    assert failed == []
    assert len(calls) == 3
    assert "規劃者正在擬定做法" not in stages
    assert "已經想好的做法。" in _prompts(calls)[0]


def test_a_planner_on_a_provider_that_cannot_write_is_still_allowed(
    tmp_path: Path, monkeypatch
) -> None:
    """The planner is read-only, so Codex is a legal choice for it.

    ⛔ 這一條原本的理由是「`_run_role_prompt` 會拒絕任何寫檔位子上的 Codex，
    而規劃者就坐在那條拒絕隔壁」。**那個拒絕 2026-09-01 拿掉了**（老闆：
    「我之前明明就說都可以」）。這一條照樣有用，只是理由換了：規劃者拿到的
    是唯讀那一種派工，而這裡釘住「唯讀的位子誰都坐得」。
    """
    project = make_project(tmp_path)
    seat_planner(project, "codex")

    _, failed, _, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("計畫"),
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == []
    assert calls[0]["arguments"][0] == "codex"


# ---- the hazard the line-up wire introduced -----------------------------


def test_a_role_nobody_is_seated_in_refuses_instead_of_running_as_claude(
    tmp_path: Path,
) -> None:
    """Unreachable before the engine read the stored line-up.

    The line-up used to be the built-in default, so every dispatched role
    always had a provider. Now a hand-edited `sessions.json` can drop a seat,
    and the dispatch's only other branch is "not codex, so Claude" - the step
    would have run as a vendor nobody chose. That is the same failure the
    Codex-cannot-write refusal exists to prevent, one door over.
    """
    from app.adapters.cli_runner import CliResult
    from app.core.lineup import AgentSlot, Lineup
    from app.core.orchestrator import Orchestrator

    project = make_project(tmp_path)
    orchestrator = Orchestrator(
        project,
        settings=_settings(),
        lineup=Lineup.of([AgentSlot("reviewer", "codex")]),
    )
    launched: list[str] = []

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        launched.append(arguments[0])
        return CliResult(
            command=arguments[0], success=True, return_code=0,
            stdout='{"result": "x"}', stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]

    with pytest.raises(NotImplementedError, match="沒有「author」這一格"):
        orchestrator.run_claude()

    assert launched == [], "拒絕之前不可以已經花錢跑過"


def test_planning_mode_does_not_run_the_planner_seat(
    tmp_path: Path, monkeypatch
) -> None:
    """A planner seated while the project runs the planning sequence.

    It has no duty there - the planning sequence already is planning - so it
    must not run, and `sequence_for` already says so. This pins the engine to
    the same answer: the two disagreeing would mean the composer draws a seat
    as 不會被執行 while the run pays for it.
    """
    project = make_project(tmp_path)
    seat_planner(project)

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("計畫初稿"),      # codex plan, round 0
            cli_result("實作檢查"),      # claude plan review
            cli_result("最終計畫"),      # codex final
        ],
        settings=_settings(workflow_mode="planning", planning_rounds=1),
    )

    assert failed == []
    assert completed[0]["workflow_status"] == "PLANNING_COMPLETED"
    assert "規劃者正在擬定做法" not in stages
