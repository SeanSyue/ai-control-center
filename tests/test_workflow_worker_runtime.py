from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.adapters.cli_runner import CliResult
from app.core.workflow_worker import WorkflowWorker
from app.i18n import t
from app.services.ai_settings import AIExecutionSettings
from app.services.project_verification import (
    TESTS_FAILED,
    TESTS_NOT_DECLARED,
)
from tests.fakes import (
    VERIFY_COMMAND,
    VERIFY_TIMEOUT_SECONDS,
    calls_after_intake,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
    with_intake,
)


def make_project(tmp_path: Path, declare_verification: bool = True) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# Task\n\nDo the thing.\n", encoding="utf-8"
    )
    # A real project has to say how it is verified before the workflow will
    # run it at all. The other half of that rule is
    # test_a_project_that_never_declared_how_to_verify_stops_instead_of_red.
    if declare_verification:
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


def _run_worker(
    tmp_path: Path,
    monkeypatch,
    script,
    settings=None,
    declare_verification: bool = True,
):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = make_project(tmp_path, declare_verification=declare_verification)

    runner_class = scripted_cli_runner_class(with_intake(script, settings))
    monkeypatch.setattr(
        "app.core.orchestrator.CliRunner", runner_class
    )

    worker = WorkflowWorker(project, settings=settings or _settings())
    completed_results = []
    failed_messages = []
    stages = []
    worker.completed.connect(completed_results.append)
    worker.failed.connect(failed_messages.append)
    worker.stage_changed.connect(stages.append)

    worker.run()

    application.processEvents()
    return (
        completed_results,
        failed_messages,
        stages,
        calls_after_intake(runner_class.calls, settings),
    )


def test_development_happy_path_completes_with_pass(
    tmp_path: Path, monkeypatch
) -> None:
    script = [
        cli_result('{"result": "done, no questions"}'),  # claude
        cli_result("1 passed"),  # pytest
        cli_result(
            '{"status": "PASS", "summary": "looks good", '
            '"issues": [], "instructions": [], "question": ""}'
        ),  # codex review
    ]

    completed, failed, stages, calls = _run_worker(tmp_path, monkeypatch, script)

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "COMPLETED"
    assert result["review_status"] == "PASS"
    assert result["tests_success"] is True
    assert len(calls) == 3
    assert calls[0]["arguments"][0] == "claude"
    # The verification step runs *what this project declared*, not a command
    # the engine picked. This assertion used to read
    # `== ["python", "-m", "pytest", "-q"]`, and that literal was the bug:
    # `python` is resolved off PATH, the machine this shipped to resolved it
    # to a stub with no pytest, and the engine had no way to tell that
    # 「這台機器上沒有 pytest」 apart from 「測試紅了」.
    assert calls[1]["arguments"] == VERIFY_COMMAND
    assert calls[1]["timeout_seconds"] == VERIFY_TIMEOUT_SECONDS
    assert calls[2]["arguments"][0] == "codex"


def test_claude_failure_reports_claude_error_with_no_further_calls(
    tmp_path: Path, monkeypatch
) -> None:
    script = [
        cli_result("", success=False, stderr="claude crashed"),
    ]

    completed, failed, stages, calls = _run_worker(tmp_path, monkeypatch, script)

    assert not failed
    assert len(completed) == 1
    assert completed[0]["workflow_status"] == "CLAUDE_ERROR"
    # Must not call tests/codex after a failed Claude call.
    assert len(calls) == 1


def test_claude_question_escalates_through_decision_gate_to_ask_user(
    tmp_path: Path, monkeypatch
) -> None:
    script = [
        cli_result(
            "一半\nASK_USER:\n要用哪個資料庫？\nEND_ASK_USER"
        ),  # claude asks a question
        cli_result(
            '{"decision": "ASK_USER", "reason": "needs a human", '
            '"answer": "", "question": "要用哪個資料庫？"}'
        ),  # codex decision gate escalates
    ]

    completed, failed, stages, calls = _run_worker(tmp_path, monkeypatch, script)

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "ASK_USER"
    assert result["review_status"] == "ASK_USER"
    assert "要用哪個資料庫" in result["review_question"]
    assert len(calls) == 2


def test_claude_question_auto_resolved_by_decision_gate_continues_workflow(
    tmp_path: Path, monkeypatch
) -> None:
    script = [
        cli_result(
            "一半\nASK_USER:\n可以用預設值嗎？\nEND_ASK_USER"
        ),  # claude asks
        cli_result(
            '{"decision": "APPROVED", "reason": "low risk", '
            '"answer": "用預設值", "question": ""}'
        ),  # codex approves
        cli_result('{"result": "done after continuing"}'),  # claude continues
        cli_result("1 passed"),  # pytest
        cli_result(
            '{"status": "PASS", "summary": "ok", '
            '"issues": [], "instructions": [], "question": ""}'
        ),  # codex review
    ]

    completed, failed, stages, calls = _run_worker(tmp_path, monkeypatch, script)

    assert not failed
    assert len(completed) == 1
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert len(calls) == 5


def test_persistent_test_failure_reaches_fix_limit(
    tmp_path: Path, monkeypatch
) -> None:
    completed, failed, stages, calls = _run_worker(
        tmp_path,
        monkeypatch,
        script=[
            cli_result('{"result": "done"}'),  # claude
            cli_result("1 failed", success=False),  # pytest fails
        ],
        settings=_settings(max_fix_attempts=0),
    )

    assert not failed
    assert len(completed) == 1
    assert completed[0]["workflow_status"] == "FIX_LIMIT_REACHED"
    assert len(calls) == 2


def test_codex_review_failure_reports_codex_error(
    tmp_path: Path, monkeypatch
) -> None:
    script = [
        cli_result('{"result": "done"}'),
        cli_result("1 passed"),
        cli_result("", success=False, stderr="codex crashed"),
    ]

    completed, failed, stages, calls = _run_worker(tmp_path, monkeypatch, script)

    assert not failed
    assert completed[0]["workflow_status"] == "CODEX_ERROR"


def test_codex_review_unparsable_output_reports_invalid_response(
    tmp_path: Path, monkeypatch
) -> None:
    script = [
        cli_result('{"result": "done"}'),
        cli_result("1 passed"),
        cli_result("not valid json at all"),
    ]

    completed, failed, stages, calls = _run_worker(tmp_path, monkeypatch, script)

    assert not failed
    assert completed[0]["workflow_status"] == "CODEX_INVALID_RESPONSE"


def test_planning_mode_single_round_completes(
    tmp_path: Path, monkeypatch
) -> None:
    script = [
        cli_result("最終計畫內容"),  # codex plan, round 0, final
    ]

    completed, failed, stages, calls = _run_worker(
        tmp_path,
        monkeypatch,
        script,
        settings=_settings(workflow_mode="planning", planning_rounds=0),
    )

    assert not failed
    assert len(completed) == 1
    assert completed[0]["workflow_status"] == "PLANNING_COMPLETED"

    # **這裡本來是 `assert len(calls) == 1`，2026-08-26 換掉。**
    #
    # 這支測試的名字說的是「討論回合 0 的時候不要多跑一輪計畫審查」，
    # 而那個 `1` 順手把另一件完全不同的事也鎖成綠燈：**計畫序列沒有測試
    # 驗證、沒有駁回迴圈、也沒有拍板**（`_approval_phase` 只長在開發序列
    # 那一條路上，`steps/approval.py:168` 自己寫著「擋不住計畫序列走進來，
    # 因為它根本走不到這裡」）。
    #
    # 那三件事只要補上任何一件，紅的會是這一條——而讀到它的人看到的是一個
    # 叫「單回合會完成」的測試在抱怨呼叫次數，最省事的修法是把 1 改成 2。
    # **一條測試守著它名字沒說的東西，下一個人會照著名字把它改掉。**
    # 那三件缺席寫進交接文件裡讓老闆決定要不要補，不留在這裡當綠燈。
    #
    # 下面兩行守的是它名字說的那件事：出計畫那一步跑了一次，
    # 而計畫審查**一輪都沒有跑**。
    assert stages.count(t("step.codex_drafting_plan")) == 1, stages
    assert not [stage for stage in stages if "輪計畫" in stage], stages
    assert calls[0]["arguments"][0] == "codex"


def test_fix_retry_loop_runs_and_succeeds_on_second_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    # Regression-safety test: an earlier version of this suite only
    # covered FIX_LIMIT_REACHED via max_fix_attempts=0, which trips the
    # limit before run_claude_fix is ever called - the fix -> retry
    # back-edge (loop back to run_tests after a fix) was never actually
    # exercised. This test forces at least one real trip around that
    # loop before succeeding.
    script = [
        cli_result('{"result": "v1"}'),  # claude
        cli_result("1 failed", success=False),  # pytest fails
        cli_result('{"result": "v2, fixed"}'),  # claude fix (attempt 1)
        cli_result("2 passed"),  # pytest passes on retry
        cli_result(
            '{"status": "PASS", "summary": "ok", '
            '"issues": [], "instructions": [], "question": ""}'
        ),  # codex review
    ]

    completed, failed, stages, calls = _run_worker(
        tmp_path,
        monkeypatch,
        script,
        settings=_settings(max_fix_attempts=1),
    )

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "COMPLETED"
    assert result["fix_attempts"] == 1
    assert len(calls) == 5


def test_claude_error_during_fix_attempt_is_reported(
    tmp_path: Path, monkeypatch
) -> None:
    script = [
        cli_result('{"result": "v1"}'),  # claude
        cli_result("1 failed", success=False),  # pytest fails
        cli_result("", success=False, stderr="claude fix crashed"),  # fix fails
    ]

    completed, failed, stages, calls = _run_worker(
        tmp_path,
        monkeypatch,
        script,
        settings=_settings(max_fix_attempts=1),
    )

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "CLAUDE_ERROR"
    assert result["fix_attempts"] == 1
    assert len(calls) == 3


def test_codex_review_fix_status_at_limit_reports_fix_limit_reached(
    tmp_path: Path, monkeypatch
) -> None:
    # Distinct from the test-failure-triggered FIX_LIMIT_REACHED case:
    # here the tests PASS but Codex review itself asks for a fix, and
    # there is no budget left to attempt one.
    script = [
        cli_result('{"result": "done"}'),  # claude
        cli_result("1 passed"),  # pytest
        cli_result(
            '{"status": "FIX", "summary": "needs a tweak", '
            '"issues": ["minor issue"], "instructions": ["fix it"], '
            '"question": ""}'
        ),  # codex review asks for a fix
    ]

    completed, failed, stages, calls = _run_worker(
        tmp_path,
        monkeypatch,
        script,
        settings=_settings(max_fix_attempts=0),
    )

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "FIX_LIMIT_REACHED"
    assert result["review_status"] == "FIX"
    assert len(calls) == 3


def test_decision_gate_exhausts_after_three_continue_attempts(
    tmp_path: Path, monkeypatch
) -> None:
    # The anti-infinite-loop guard: if Claude keeps asking questions and
    # Codex keeps auto-approving (CONTINUE) 3 times in a row without the
    # question actually going away, _resolve_claude_questions must give
    # up and escalate to the user rather than looping forever.
    def asks(question: str) -> str:
        return f"進行中\nASK_USER:\n{question}\nEND_ASK_USER"

    approve = (
        '{"decision": "APPROVED", "reason": "low risk", '
        '"answer": "繼續", "question": ""}'
    )

    script = [
        cli_result(asks("問題 A")),  # claude asks (initial run_claude)
        cli_result(approve),  # codex approves attempt 1
        cli_result(asks("問題 B")),  # claude asks again
        cli_result(approve),  # codex approves attempt 2
        cli_result(asks("問題 C")),  # claude asks again
        cli_result(approve),  # codex approves attempt 3
        cli_result(asks("問題 D")),  # claude asks again - loop now exhausted
    ]

    completed, failed, stages, calls = _run_worker(tmp_path, monkeypatch, script)

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "ASK_USER"
    assert "連續自動處理 3 個" in result["review_question"]
    assert len(calls) == 7


def test_a_project_that_never_declared_how_to_verify_stops_instead_of_red(
    tmp_path: Path, monkeypatch
) -> None:
    """The false red light, as a test.

    Before the verification step was declared by the project, this run
    reached step four, ran a `python` that had no pytest, and came back
    with return code 1 and 「No module named pytest」. Nothing downstream
    could tell that apart from a real failure, so the engine spent its one
    fix attempt asking the author to repair code that was never broken and
    then ended - never reaching review or the memory update.

    Two things are asserted, and the second matters as much as the first:
    the run reports 「沒得驗」 rather than 「紅的」, and it does so having
    made **no** CLI calls at all. Stopping before the author is dispatched
    is what makes this cost a settings edit instead of a paid model call
    and a working tree somebody has already changed.
    """
    completed, failed, stages, calls = _run_worker(
        tmp_path,
        monkeypatch,
        script=[],
        declare_verification=False,
    )

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "VERIFY_NOT_CONFIGURED"
    assert result["tests_status"] == "NOT_DECLARED"
    assert result["tests_success"] is False
    assert result["review_status"] == "NOT_RUN"
    assert "verify_command" in result["verification_problem"]
    assert calls == []


def test_a_declaration_that_vanishes_mid_run_still_is_not_a_red_light(
    tmp_path: Path, monkeypatch
) -> None:
    """The guard at the top of the run is not the only guard.

    A project.json edited (or deleted) while a run is in flight would get
    past the up-front check and arrive at the verification step with
    nothing to run. `run_tests` raises there rather than returning a failed
    result, precisely so that this path cannot quietly rejoin the
    「測試紅了」 channel.
    """
    from app.services.project_verification import (
        VerifyCommandLookup,
        read_verify_command as real_read,
    )

    seen: list[int] = []

    def vanishing(project_path):
        seen.append(1)
        if len(seen) == 1:
            return real_read(project_path)
        return VerifyCommandLookup(
            command=None, problem="verify_command 在執行途中被拿掉了。"
        )

    monkeypatch.setattr(
        "app.core.orchestrator.read_verify_command", vanishing
    )

    completed, failed, stages, calls = _run_worker(
        tmp_path,
        monkeypatch,
        script=[cli_result('{"result": "done, no questions"}')],  # claude
    )

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "VERIFY_NOT_CONFIGURED"
    assert result["tests_status"] == "NOT_DECLARED"
    # The author did run - that is the point of this path - but nothing
    # tried to verify its work, and nothing pretended to.
    assert len(calls) == 1
    # And the run's own record says why it stopped. `inspect_run` reads this
    # file; a run folder that just ends is the same puzzle for a human as a
    # false red is for the engine.
    timeline = (
        tmp_path / "project" / ".aicc" / "timeline" / "timeline.jsonl"
    ).read_text(encoding="utf-8")
    assert "verification_not_declared" in timeline


def test_the_common_not_declared_path_also_leaves_a_reason_on_disk(
    tmp_path: Path, monkeypatch
) -> None:
    """The rare path logged; the ordinary one left the folder empty.

    Two guards stop a run that cannot verify itself: the one at the top of
    `_run_development` (a project that never declared anything - the common
    case, and the only one an ordinary user will ever meet) and the one
    inside `run_tests` (the declaration removed mid-run - rare). Only the
    second one wrote to the timeline, so the case that actually happens
    produced a run folder with no `timeline.jsonl` at all: a run that just
    stops, with the sentence naming the file, the field and the fix
    reachable only from a dialog that has already closed.

    `inspect_run.py` reads these two files. If the reason is not in them,
    it does not exist as far as anybody looking afterwards is concerned.
    """
    completed, failed, stages, calls = _run_worker(
        tmp_path,
        monkeypatch,
        script=[],
        declare_verification=False,
    )

    assert not failed
    assert calls == []
    project = tmp_path / "project"

    timeline = (
        project / ".aicc" / "timeline" / "timeline.jsonl"
    ).read_text(encoding="utf-8")
    assert "verification_not_declared" in timeline
    # Not just the event name - the sentence that says what to do.
    assert "verify_command" in timeline

    run_id = completed[0]["run_id"]
    run_json = json.loads(
        (project / ".aicc" / "runs" / run_id / "run.json").read_text(
            encoding="utf-8"
        )
    )
    assert "verify_command" in run_json["verification_problem"]


def _timed_out_result() -> CliResult:
    """What `CliRunner` returns when it kills a command at the deadline."""
    return CliResult(
        command="pytest",
        success=False,
        return_code=-1,
        timed_out=True,
        stdout="collected 1224 items",
        stderr="Command timed out after 456 seconds.",
    )


def test_a_verification_timeout_is_not_a_red_light(
    tmp_path: Path, monkeypatch
) -> None:
    """逾時 and 測試紅了 used to be the same `success=False`.

    Downstream could not tell them apart, so a deadline that fired sent the
    author off to 「修正造成測試失敗的問題」 - a paid call, and the only fix
    attempt this run gets, spent on a bug nobody has any evidence for. The
    run then ended reporting FIX_LIMIT_REACHED, which says 「試過了，還是
    紅的」 about a suite that may well be entirely green.

    The two assertions that matter: no third CLI call (the author was not
    dispatched), and a status of its own that the UI can paint as 「做了
    一半」 rather than 「壞了」.
    """
    script = [
        cli_result('{"result": "done, no questions"}'),  # claude
        _timed_out_result(),  # verification, killed at the deadline
    ]

    completed, failed, stages, calls = _run_worker(
        tmp_path, monkeypatch, script
    )

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "VERIFY_TIMED_OUT"
    assert result["tests_status"] == "TIMED_OUT"
    assert result["tests_success"] is False
    # Nobody was sent to fix anything, and nobody was sent to review it.
    assert result["fix_attempts"] == 0
    assert result["review_status"] == "NOT_RUN"
    assert len(calls) == 2
    # And it says what to do about it - the field to raise, not 「改程式」.
    assert "verify_timeout_seconds" in result["verification_problem"]


def _never_started_result() -> CliResult:
    """What `CliRunner` returns when nothing was ever launched.

    The -1 is a placeholder - there is no process and therefore no exit
    code - and that is precisely the danger: a project declaring
    `{"exit_codes": [0]}` would have its rule applied to the placeholder
    and get back 「離開碼 -1 不在宣告的通過清單 [0] 內」, a FAILED verdict
    with a fluent reason and nothing behind it.
    """
    return CliResult(
        command="notes.txt",
        success=False,
        return_code=-1,
        never_started=True,
        stdout="",
        stderr="Command was not found in PATH: notes.txt",
    )


def test_a_verification_that_never_started_is_not_a_red_light(
    tmp_path: Path, monkeypatch
) -> None:
    """Nothing ran, so nobody may be told the tests are red.

    Discovered by measurement, not reasoning: declaring an existing but
    non-executable file (`["./notes.txt"]`) got 「宣告好了，可以跑」 from the
    reader and 「Command was not found in PATH」 from the runner, and the
    engine then judged the runner's placeholder exit code and reported
    **FAILED**. The workflow's next move is to dispatch the author to fix
    「造成測試失敗的問題」 - the single paid fix attempt this run gets, spent
    on a bug that does not exist.

    Two assertions carry it, the same two as the timeout ending: no third
    CLI call, and a status that is not FAILED.
    """
    script = [
        cli_result('{"result": "done, no questions"}'),  # claude
        _never_started_result(),  # verification never launched
    ]

    completed, failed, stages, calls = _run_worker(
        tmp_path, monkeypatch, script
    )

    assert not failed
    assert len(completed) == 1
    result = completed[0]
    assert result["workflow_status"] == "VERIFY_NOT_CONFIGURED"
    assert result["tests_status"] == TESTS_NOT_DECLARED
    assert result["tests_status"] != TESTS_FAILED
    # Nobody was sent to fix anything; the third call never happened.
    assert result["fix_attempts"] == 0
    assert result["review_status"] == "NOT_RUN"
    assert len(calls) == 2
    # And it names the field to look at rather than saying 「改程式」.
    assert "verify_command" in result["verification_problem"]


def test_a_real_failure_still_reaches_the_author(
    tmp_path: Path, monkeypatch
) -> None:
    """The other half of the timeout split, so it cannot be over-applied.

    Separating 逾時 from 失敗 must not turn 失敗 into 「不用管」. A command
    that ran to the end and did not meet the project's declared success
    rule is still a red light, and the author is still dispatched.
    """
    script = [
        cli_result('{"result": "v1"}'),  # claude
        cli_result("1 failed", success=False),  # verification, really red
        cli_result('{"result": "v2, fixed"}'),  # claude fix
        cli_result("2 passed"),  # verification passes
        cli_result(
            '{"status": "PASS", "summary": "ok", '
            '"issues": [], "instructions": [], "question": ""}'
        ),
    ]

    completed, failed, stages, calls = _run_worker(
        tmp_path, monkeypatch, script
    )

    assert not failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert completed[0]["tests_status"] == "PASSED"
    assert completed[0]["fix_attempts"] == 1
    assert len(calls) == 5


def _run_with_declared_success(
    tmp_path: Path, monkeypatch, script, success: dict, settings=None
):
    """Same as `_run_worker`, but the project declares its own 「算過」 rule."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    application = QApplication.instance() or QApplication([])
    project = make_project(tmp_path, declare_verification=False)
    declare_verify_command(project, success=success)

    runner_class = scripted_cli_runner_class(with_intake(script, settings))
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(project, settings=settings or _settings())
    completed: list = []
    failed: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    application.processEvents()
    return completed, failed, calls_after_intake(runner_class.calls, settings)


SUMMARY_LINE_RULE = {"output_pattern": r"(?m)^\d+ passed in [\d.]+s\s*$"}


def test_the_project_decides_what_passes_not_the_exit_code(
    tmp_path: Path, monkeypatch
) -> None:
    """The engine's last leftover opinion, removed.

    This repo's own suite prints 「1224 passed in 129.22s」 and then exits
    0xC0000005 because QtWebEngine faults during teardown. Under
    `success = returncode == 0` the very first real run would have called
    that a failure and spent its one fix attempt repairing code that was
    never broken - the exact bug `verify_command` was introduced to remove,
    one layer further down. Its own CLAUDE.md says 「看最後一行不要看 exit
    code」; the engine was doing the opposite.

    Here the project declares 「看輸出的摘要行」, and a non-zero exit code
    with a green summary is a pass.
    """
    crashed_but_green = CliResult(
        command="pytest",
        success=False,  # what CliRunner says: returncode != 0
        return_code=3221225477,  # 0xC0000005, QtWebEngine teardown
        stdout="1224 passed in 129.22s",
        stderr="",
    )
    script = [
        cli_result('{"result": "done, no questions"}'),  # claude
        crashed_but_green,  # verification
        cli_result(
            '{"status": "PASS", "summary": "ok", '
            '"issues": [], "instructions": [], "question": ""}'
        ),  # codex review
    ]

    completed, failed, calls = _run_with_declared_success(
        tmp_path, monkeypatch, script, SUMMARY_LINE_RULE
    )

    assert not failed
    assert len(completed) == 1
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert completed[0]["tests_status"] == "PASSED"
    assert completed[0]["tests_success"] is True
    # No fix attempt was spent, and the reviewer was reached.
    assert completed[0]["fix_attempts"] == 0
    assert len(calls) == 3


def test_a_declared_pattern_that_does_not_match_is_still_a_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """The negative of the test above - the rule has to be able to say no.

    Exit code 0 with a summary line that says something failed is what a
    wrapper script swallowing its child's status looks like, and the point
    of declaring a pattern is that the pattern decides, in both directions.
    """
    exit_zero_but_red = CliResult(
        command="pytest",
        success=True,  # what CliRunner says: returncode == 0
        return_code=0,
        stdout="3 failed, 1221 passed in 129.22s",
        stderr="",
    )
    script = [
        cli_result('{"result": "v1"}'),  # claude
        exit_zero_but_red,  # verification: the declared rule says no
    ]

    completed, failed, calls = _run_with_declared_success(
        tmp_path,
        monkeypatch,
        script,
        SUMMARY_LINE_RULE,
        settings=_settings(max_fix_attempts=0),
    )

    assert not failed
    assert completed[0]["workflow_status"] == "FIX_LIMIT_REACHED"
    assert completed[0]["tests_status"] == "FAILED"
    assert completed[0]["tests_success"] is False
