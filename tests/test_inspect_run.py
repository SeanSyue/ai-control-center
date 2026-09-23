"""The single-seat retry and the tool that drives it (ROADMAP P10 Stage C).

The retry is the part with teeth, and the assertions are mostly about what it
**refuses** to do. A retry that quietly runs the recorded prompt as a different
agent, or that reports success without leaving the artifact the next run reads,
is worse than no retry: both look like they worked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.cli_runner import CliResult
from app.core.lineup import AgentSlot, Lineup
from app.core.orchestrator import Orchestrator
from app.services.ai_settings import AIExecutionSettings
from app.services.step_log import StepLog
from tools import inspect_run


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n做一件事。\n", encoding="utf-8")
    return project


def make_orchestrator(project: Path, lineup=None, outputs=None):
    orchestrator = Orchestrator(
        project,
        run_id="run-1",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
        lineup=lineup,
    )
    queue = list(outputs or [])

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        stdout, success = queue.pop(0) if queue else ("ok", True)
        command = arguments[0] if arguments else "fake"
        if command == "claude":
            stdout = json.dumps({"result": stdout})
        return CliResult(
            command=command,
            success=success,
            return_code=0 if success else 1,
            stdout=stdout,
            stderr="" if success else "boom",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    return orchestrator


def test_a_retry_sends_the_recorded_prompt_verbatim(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    first = make_orchestrator(project, outputs=[("第一次", True)])
    first.run_claude()
    original = StepLog(project).read("run-1")[0].prompt

    second = make_orchestrator(project, outputs=[("第二次", True)])
    sent: list[str] = []
    inner = second.runner.run

    def capture(arguments, timeout_seconds=300, input_text=None):
        # Both providers now hand the prompt over on standard input;
        # argv carries flags only.
        sent.append(input_text or "")
        return inner(arguments, timeout_seconds, input_text)

    second.runner.run = capture  # type: ignore[method-assign]
    second.retry_step(0)

    assert sent == [original], (
        "重跑送出去的必須跟當初失敗的那一段一模一樣，"
        "否則重跑不出原本的問題，看起來卻像問題消失了"
    )


def test_a_retry_is_recorded_as_a_new_step_pointing_at_the_old_one(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("第一次", True)]).run_claude()
    make_orchestrator(project, outputs=[("第二次", True)]).retry_step(0)

    steps = StepLog(project).read("run-1")

    assert len(steps) == 2
    assert steps[1].index == 1
    assert steps[1].metadata.get("retry_of") == 0
    # Appended, never overwritten: the failure that prompted the retry has to
    # stay readable, or the log stops being a record of what happened.
    assert steps[0].metadata.get("retry_of") is None


def test_a_retry_refuses_when_the_lineup_now_seats_somebody_else(
    tmp_path: Path,
) -> None:
    """The refusal that matters.

    Re-dispatching a recorded prompt to whoever sits there today produces a
    result filed under a step that never happened that way, and afterwards
    nothing on disk says the agent changed.
    """
    project = make_project(tmp_path)
    planned = Lineup.classic().with_planner("codex")
    make_orchestrator(project, lineup=planned, outputs=[("計畫", True)]).run_work_plan()

    moved = Lineup.of(
        [
            AgentSlot("planner", "claude"),
            AgentSlot("author", "claude"),
            AgentSlot("reviewer", "codex"),
        ]
    )
    with pytest.raises(ValueError, match="陣容已經改過"):
        make_orchestrator(project, lineup=moved).retry_step(0)


def test_a_retry_of_a_step_that_does_not_exist_says_what_does(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("第一次", True)]).run_claude()

    with pytest.raises(ValueError, match="序號 0 到 0"):
        make_orchestrator(project).retry_step(7)


def test_a_successful_planner_retry_leaves_the_plan_where_the_next_run_looks(
    tmp_path: Path,
) -> None:
    """Otherwise the retry is theatre.

    The plan would be on screen and not on disk, and the next full run would
    plan again - paying twice for the thing the retry was supposed to save.
    """
    project = make_project(tmp_path)
    planned = Lineup.classic().with_planner("codex")

    failed = make_orchestrator(project, lineup=planned, outputs=[("", False)])
    failed.run_work_plan()
    assert failed.stored_work_plan() == ""

    retried = make_orchestrator(
        project, lineup=planned, outputs=[("這次想出來了", True)]
    )
    retried.retry_step(0)

    assert retried.stored_work_plan() == "這次想出來了"


# ---- the tool ------------------------------------------------------------


def test_the_tool_lists_the_steps_and_names_the_failure(
    tmp_path: Path, capsys
) -> None:
    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("", False)]).run_claude()

    assert inspect_run.main([str(project), "run-1"]) == 0

    out = capsys.readouterr().out
    assert "[0]" in out
    assert "author" in out
    assert "最後一個失敗的是第 0 格" in out


def test_the_tool_does_not_spend_money_without_yes(
    tmp_path: Path, capsys
) -> None:
    """The one rule this tool has.

    `--retry` calls a CLI, and a CLI call is money. A tool that spends it on a
    typo is not a debugging aid.
    """
    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("", False)]).run_claude()
    before = len(StepLog(project).read("run-1"))

    assert inspect_run.main([str(project), "run-1", "--retry", "0"]) == 0

    out = capsys.readouterr().out
    assert "會花錢" in out
    assert "--yes" in out
    assert len(StepLog(project).read("run-1")) == before, (
        "沒有 --yes 卻真的跑了一格"
    )


def test_the_tool_says_so_when_a_run_predates_the_step_log(
    tmp_path: Path, capsys
) -> None:
    # 「沒有顯示」跟「什麼都沒發生」不可以長得一樣。
    project = make_project(tmp_path)
    (project / ".aicc" / "runs" / "old-run").mkdir(parents=True)

    assert inspect_run.main([str(project)]) == 0

    assert "沒有逐格紀錄" in capsys.readouterr().out


# ---- 這一輪帶了什麼（ROADMAP P0.5） --------------------------------------


def test_the_run_view_shows_what_that_run_carried(
    tmp_path: Path, capsys
) -> None:
    """`ContextLedger` has recorded this per run all along.

    Only the daily totals were ever shown, so 「這一輪帶了什麼」 was answerable
    from a file on disk and from nowhere on screen.
    """
    from app.services.context_ledger import ContextLedger

    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("done", True)]).run_claude()
    ContextLedger(project).record_sent(
        "run-1",
        {
            "contract_characters": 2400,
            "memory_characters": 5000,
            "document_characters": 9000,
            "raw_evidence_characters": 0,
            "total_characters": 16400,
            "estimated_tokens": 4100,
        },
        profile="balanced",
    )
    ContextLedger(project).record_resend_skipped("run-1", 9000)

    assert inspect_run.main([str(project), "run-1"]) == 0

    out = capsys.readouterr().out
    assert "共同記憶" in out
    assert "16,400" in out
    assert "沒有重送" in out
    assert "9,000" in out


def test_no_saved_tokens_figure_is_invented(tmp_path: Path, capsys) -> None:
    """「省下多少 token」 would need a version of the same work done without
    AICC to compare against, and no such number exists.

    「沒有重送」 is measured - it is exactly the payload a naive implementation
    would have sent again. Pinned because a plausible-looking saving is the
    single easiest thing to add here and the hardest to notice as invented.
    """
    from app.services.context_ledger import ContextLedger

    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("done", True)]).run_claude()
    ContextLedger(project).record_resend_skipped("run-1", 9000)

    inspect_run.main([str(project), "run-1"])

    out = capsys.readouterr().out
    assert "節省" not in out
    assert "省下" not in out


def test_a_run_that_carried_nothing_prints_no_empty_section(
    tmp_path: Path, capsys
) -> None:
    """A section of zeros is true here and still worth leaving out.

    A run against a project with no documents and no shared memory really did
    carry nothing - 「合計 0 字」 would not be a lie. It would just be four
    lines saying so, on every run of every fresh project, which is how a
    useful section becomes one people scroll past.
    """
    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("done", True)]).run_claude()

    inspect_run.main([str(project), "run-1"])

    assert "這一輪帶進去的 context" not in capsys.readouterr().out


# ---- 這一輪動了共同記憶什麼（ROADMAP P2） --------------------------------


PATCH = """--- PROJECT_BRIEF.md (before)
+++ PROJECT_BRIEF.md (after)
-舊的一行
+新的一行
+又一行

--- DECISIONS.md (before)
+++ DECISIONS.md (after)
+決定了一件事
"""


def _with_memory_patch(tmp_path: Path) -> Path:
    from app.services.timeline_store import TimelineStore

    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("done", True)]).run_claude()
    TimelineStore(project).save_artifact(
        "run-1", "memory-update-diff.patch", PATCH
    )
    return project


def test_the_run_view_says_how_much_memory_changed(
    tmp_path: Path, capsys
) -> None:
    """The patch has been written on every memory-maintaining run all along.

    Nothing ever showed it, so 「這一輪改了共同記憶什麼」 was answerable only
    by knowing the artifact's filename.
    """
    project = _with_memory_patch(tmp_path)

    assert inspect_run.main([str(project), "run-1"]) == 0

    out = capsys.readouterr().out
    assert "共同記憶：動了 2 個檔案" in out
    assert "+3 −1 行" in out
    assert "PROJECT_BRIEF.md" in out
    assert "--memory" in out
    # The listing stays a listing - the patch itself is behind the flag.
    assert "舊的一行" not in out


def test_the_full_diff_is_behind_the_flag(tmp_path: Path, capsys) -> None:
    project = _with_memory_patch(tmp_path)

    assert inspect_run.main([str(project), "run-1", "--memory"]) == 0

    out = capsys.readouterr().out
    assert "+新的一行" in out
    assert "-舊的一行" in out


def test_the_diff_says_it_was_already_applied(tmp_path: Path, capsys) -> None:
    """Otherwise it reads as a review queue.

    A patch on screen with no verdict next to it looks like something waiting
    for approval - and this one is not: memory maintenance writes it and moves
    on unless the safety scan objected. Saying so is the difference between
    a record and a false promise of oversight.
    """
    project = _with_memory_patch(tmp_path)

    inspect_run.main([str(project), "run-1", "--memory"])

    out = capsys.readouterr().out
    assert "已經套用" in out
    assert "還沒做" in out


def test_a_run_that_did_not_touch_memory_says_nothing(
    tmp_path: Path, capsys
) -> None:
    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("done", True)]).run_claude()

    inspect_run.main([str(project), "run-1"])

    assert "共同記憶" not in capsys.readouterr().out


def test_asking_for_a_diff_that_is_not_there_says_so(
    tmp_path: Path, capsys
) -> None:
    # 「沒有顯示」跟「沒有動到」不可以長得一樣，而這裡回非零，
    # 所以它放進檢查清單時擋得住人。
    project = make_project(tmp_path)
    make_orchestrator(project, outputs=[("done", True)]).run_claude()

    assert inspect_run.main([str(project), "run-1", "--memory"]) == 1
    assert "沒有動到共同記憶" in capsys.readouterr().out
