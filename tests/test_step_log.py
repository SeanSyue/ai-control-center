"""The record a single-seat re-run will read (ROADMAP P10 Stage C).

Two halves worth separating: the store on its own (what survives a corrupt
file, a resumed process, a missing run id), and the wiring (does a real
workflow actually leave one behind, and is what it leaves the same text the
CLI was given).

The wiring half is the one that matters. A store nothing writes to is the
easiest thing in the world to get green.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.core.workflow_worker import WorkflowWorker
from app.services.ai_settings import AIExecutionSettings
from app.services.session_registry import SessionRegistry
from app.services.step_log import StepLog, StepRecord
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


def _record(index: int = 0, **overrides) -> StepRecord:
    base = dict(
        index=index,
        role="author",
        provider="claude",
        event_type="task",
        read_only=False,
        prompt="你是此專案的開發工程師。",
        output="做完了",
        success=True,
        started_at="2026-08-10T01:00:00",
        finished_at="2026-08-10T01:00:12",
    )
    base.update(overrides)
    return StepRecord(**base)


# ---- the store -----------------------------------------------------------


def test_steps_come_back_in_the_order_they_happened(tmp_path: Path) -> None:
    log = StepLog(make_project(tmp_path))
    log.append("run-1", _record(0, event_type="work_plan"))
    log.append("run-1", _record(1, event_type="task"))
    log.append("run-1", _record(2, event_type="review"))

    assert [s.event_type for s in log.read("run-1")] == [
        "work_plan",
        "task",
        "review",
    ]


def test_steps_sharing_an_event_type_are_all_kept(tmp_path: Path) -> None:
    """The reason this is not an artifact file.

    `save_artifact` is keyed by name, so Codex's planning rounds - all of them
    `event_type="planning"` - leave one file holding the last round of N. A
    re-run of round 2 would have had round 5's prompt.
    """
    log = StepLog(make_project(tmp_path))
    for round_number in range(4):
        log.append(
            "run-1",
            _record(
                round_number,
                event_type="planning",
                prompt=f"第 {round_number} 輪",
            ),
        )

    assert [s.prompt for s in log.read("run-1")] == [
        "第 0 輪",
        "第 1 輪",
        "第 2 輪",
        "第 3 輪",
    ]


def test_a_half_written_last_line_costs_that_step_only(tmp_path: Path) -> None:
    """The realistic corruption: the process was killed mid-append."""
    project = make_project(tmp_path)
    log = StepLog(project)
    log.append("run-1", _record(0))
    log.append("run-1", _record(1))
    with log.path_for("run-1").open("a", encoding="utf-8") as handle:
        handle.write('{"index": 2, "role": "rev')

    steps = log.read("run-1")

    assert [s.index for s in steps] == [0, 1]


def test_numbering_continues_in_a_new_process(tmp_path: Path) -> None:
    """A resumed run must not produce a second step 0.

    The index is read off the file rather than counted in memory precisely
    because the resume happens in a fresh `Orchestrator` that has no memory of
    the earlier half.
    """
    project = make_project(tmp_path)
    StepLog(project).append("run-1", _record(0))
    StepLog(project).append("run-1", _record(1))

    assert StepLog(project).next_index("run-1") == 2


def test_bookkeeping_never_takes_the_run_down_with_it(tmp_path: Path) -> None:
    """A run must not die because its own log could not be written.

    The log exists to make failures cheaper; turning it into a new way to fail
    would be a net loss. Forced by pointing the store at a path that cannot
    hold a directory.
    """
    project = make_project(tmp_path)
    blocker = project / ".aicc" / "runs"
    blocker.write_text("not a directory", encoding="utf-8")

    StepLog(project).append("run-1", _record(0))  # must not raise

    assert StepLog(project).read("run-1") == []


def test_the_last_failure_is_the_one_a_rerun_would_start_from(
    tmp_path: Path,
) -> None:
    """The last, not the first.

    A run carries on past some failures - a failed test triggers a fix round -
    so the earliest failure is usually one that was already dealt with.
    """
    log = StepLog(make_project(tmp_path))
    log.append("run-1", _record(0, event_type="task", success=False))
    log.append("run-1", _record(1, event_type="fix", success=True))
    log.append("run-1", _record(2, event_type="review", success=False))

    failure = log.last_failure("run-1")

    assert failure is not None
    assert failure.event_type == "review"


def test_no_failure_means_no_step_to_rerun(tmp_path: Path) -> None:
    log = StepLog(make_project(tmp_path))
    log.append("run-1", _record(0))

    assert log.last_failure("run-1") is None


# ---- the wiring ----------------------------------------------------------


def _run(project: Path, monkeypatch, script, **worker_kwargs):
    QApplication.instance() or QApplication([])
    runner_class = scripted_cli_runner_class(with_intake(script))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
    settings = AIExecutionSettings(
        workflow_mode="development",
        auto_memory_maintenance=False,
        auto_session_bootstrap=False,
        max_fix_attempts=1,
    )
    worker = WorkflowWorker(project, settings=settings, **worker_kwargs)
    failed: list = []
    worker.failed.connect(failed.append)
    worker.run()
    return failed, calls_after_intake(runner_class.calls)


PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)


def test_a_real_run_leaves_a_step_for_every_model_call(
    tmp_path: Path, monkeypatch
) -> None:
    """Every seat, and only seats.

    `pytest` is run by the same workflow and is deliberately absent: it is not
    a seat, nothing would re-run it through a model, and counting it would put
    a step in the log with no prompt.
    """
    project = make_project(tmp_path)
    registry = SessionRegistry(project)
    registry.save_lineup(registry.load_lineup().with_planner())

    failed, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("先改 CSS。"),
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
        run_id="run-1",
    )

    assert failed == []
    steps = StepLog(project).read("run-1")

    # `intake` 排在最前面：2026-08-22 起，派人動手之前要先跟使用者把需求問
    # 清楚，而那也是一次真的模型呼叫，所以它也要有一步紀錄。這裡刻意連它一起
    # 斷言——把它從清單裡濾掉的話，它哪天不跑了，這條測試不會紅。
    # `work_intent` 排在 `task` 前面：做事的那一格**第一次只交做法，不交
    # 成品**（定案）。那也是一次真的模型呼叫，所以它也要有一步紀錄，
    # 而且是唯讀的——下一步才輪到它動手。
    # `approval` 排在最後：老闆 2026-08-24 拍板「規定要最後一個 NODE 去驗證」
    # ——不管線排成什麼形狀，最後那一格都會被問「這一整條鏈站不站得住」。
    # 這個陣容沒有排核准那一格，所以驗簽章的是**最後一格**（複查那一格）。
    # 它也是一次真的模型呼叫，所以它也要有一步紀錄，而且是唯讀的。
    assert [s.event_type for s in steps] == [
        "intake",
        "work_plan",
        "work_intent",
        "task",
        "review",
        "approval",
    ]
    assert [s.index for s in steps] == [0, 1, 2, 3, 4, 5]
    assert [s.role for s in steps] == [
        "planner",
        "planner",
        "author",
        "author",
        "reviewer",
        "reviewer",
    ]
    assert [s.read_only for s in steps] == [
        True,
        True,
        True,
        False,
        True,
        True,
    ]


def test_the_recorded_prompt_is_the_one_the_cli_was_given(
    tmp_path: Path, monkeypatch
) -> None:
    """Byte-for-byte, because a re-run sends what is recorded.

    A record that is merely *similar* to what failed produces a re-run that
    cannot reproduce the failure, which is worse than no re-run at all - it
    looks like the problem went away.
    """
    project = make_project(tmp_path)

    failed, calls = _run(
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
    steps = StepLog(project).read("run-1")
    author_call = calls[0]
    sent = author_call["input_text"]

    # `calls` 已經把需求確認那一次拿掉了（`calls_after_intake`），紀錄那邊沒有
    # ——所以這裡要指名找那一步，不能靠位置。靠位置的話，前面再插一步就會變成
    # 拿甲的紀錄去比乙送出去的東西，而兩邊都是真的，只是不是同一件事。
    author_step = next(s for s in steps if s.event_type == "task")

    assert author_step.prompt == sent


def test_a_failed_step_is_recorded_as_failed(
    tmp_path: Path, monkeypatch
) -> None:
    """The half a re-run depends on.

    Recording only successes would leave `last_failure` permanently None, and
    the whole feature reading as "there is nothing to re-run".
    """
    project = make_project(tmp_path)

    _run(
        project,
        monkeypatch,
        [cli_result("", success=False, stderr="claude died")],
        run_id="run-1",
    )

    failure = StepLog(project).last_failure("run-1")

    assert failure is not None
    assert failure.event_type == "task"
    assert failure.success is False


def test_a_shipped_persona_is_not_recorded_as_a_custom_one(
    tmp_path: Path, monkeypatch
) -> None:
    """Stage D's debugging aid, checked from the boring side.

    If every step were flagged custom the flag would be worthless, and it
    would be worthless in the direction that costs time: every failure would
    look like a user-edited prompt.
    """
    project = make_project(tmp_path)

    _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "done"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
        run_id="run-1",
    )

    steps = StepLog(project).read("run-1")

    assert steps
    assert not any(s.custom_identity for s in steps)


def test_a_custom_persona_is_flagged(tmp_path: Path, monkeypatch) -> None:
    from app.adapters.cli_runner import CliResult
    from app.core.lineup import ROLE_AUTHOR, Lineup
    from app.core.orchestrator import Orchestrator

    orchestrator = Orchestrator(
        make_project(tmp_path),
        run_id="run-1",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
        lineup=Lineup.classic().with_identity(
            ROLE_AUTHOR, "你是這個案子的合規審查員。"
        ),
    )
    orchestrator.runner.run = lambda *a, **k: CliResult(  # type: ignore[method-assign]
        command="claude",
        success=True,
        return_code=0,
        stdout=json.dumps({"result": "ok"}),
        stderr="",
    )

    orchestrator.run_claude()

    steps = StepLog(orchestrator.project_path).read("run-1")
    assert [s.custom_identity for s in steps] == [True]


def test_a_box_holding_only_spaces_is_not_a_custom_persona(
    tmp_path: Path,
) -> None:
    """Found by negative control: trimming was untested from the outside.

    `resolve_persona` already treats whitespace as blank, so the prompt was
    correct either way - which is exactly why nothing caught it. The damage
    was one layer up: `custom_identity` is `bool(identity)`, so a box holding
    spaces would flag every step of that seat ⚑自訂prompt while the run used
    the shipped persona. That is the flag saying the opposite of the truth,
    in the direction that costs time - every failure would look like a
    user-edited prompt.
    """
    from app.adapters.cli_runner import CliResult
    from app.core.lineup import ROLE_AUTHOR, Lineup
    from app.core.orchestrator import Orchestrator

    orchestrator = Orchestrator(
        make_project(tmp_path),
        run_id="run-1",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
        lineup=Lineup.classic().with_identity(ROLE_AUTHOR, "   \n\t "),
    )
    orchestrator.runner.run = lambda *a, **k: CliResult(  # type: ignore[method-assign]
        command="claude", success=True, return_code=0,
        stdout=json.dumps({"result": "ok"}), stderr="",
    )

    orchestrator.run_claude()

    steps = StepLog(orchestrator.project_path).read("run-1")
    assert [s.custom_identity for s in steps] == [False]
    assert "你是此專案的開發工程師。" in steps[0].prompt
