"""做事的那一格，第一次只交做法，不交成品。

定案原文：

> 【執行角色第一次交做法，不交成品】不管產出是程式、文件、圖片還是別的，
> 第一次只能交：我理解的需求／我打算怎麼做／我不確定的地方。
> **否決：寫成規則叫它先別動手（那是叮嚀，會漏）。**
> 不變式：第一步的輸出根本沒有「成品」那一格；不准用「先做出來再補說明」達成。

**規劃者那一格不能取代這一步。**規劃者的做法是**別人**的理解；這一步是它自己
用自己的話重講一遍——講不出來就代表還沒對齊，而那件事只有它自己講得出來。

守四件事：

1. 它真的跑，而且是**唯讀的**——那一步的名字就是「還不動手」。
2. 它自己說的做法**真的送到下一次派工手上**，而且不是掛在「規劃者提出的」
   那個標題底下（兩格陣容裡根本沒有規劃者）。
3. 續跑沿用已經交過的那一份，不重問。
4. 有規劃者也照跑——那是兩份不同的東西。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import (  # noqa: E402
    DUTY_PLAN,
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    SETUP_INTENT_ONLY,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)
from tests.fakes import SETUP_STEPS  # noqa: E402

PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)

# 它自己說的那句話。必須出現在下一次派工的提示詞裡。
MINE = "哨兵-3d90a-我打算先把題庫的資料結構定下來，再做畫面"

TWO_SEATS = [
    AgentSlot("實作", "claude", duty=DUTY_WORK),
    AgentSlot("複查", "codex", duty=DUTY_REVIEW),
]


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 做一個錯題整理小工具\n", encoding="utf-8"
    )
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    return project


def _run(project: Path, monkeypatch, script, **worker_kwargs):
    QApplication.instance() or QApplication([])
    # 「先交做法」正是這一份在測的東西，所以那一步不自動回答；需求確認
    # 跟這裡要測的事無關，讓假 CLI 接掉。
    # 這一支測的是「先交做法」那一步本身，所以它自己接管——只留需求確認
    # 那一則現成的。
    #
    # 驗簽章（2026-08-24 起每次都跑）也用現成的：這一支不測它，而讓它
    # 吃腳本的話，每一條案例都要多寫一則跟它無關的回答。
    runner_class = scripted_cli_runner_class(
        script,
        auto=(
            ("你是此專案的需求確認者。", "需求定案：\n1. 完成狀態：做完。"),
            SETUP_STEPS[2],
        ),
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)

    worker = WorkflowWorker(
        project,
        settings=AIExecutionSettings(
            workflow_mode="development",
            auto_memory_maintenance=False,
            auto_session_bootstrap=False,
            max_fix_attempts=1,
        ),
        **worker_kwargs,
    )
    completed: list = []
    failed: list = []
    stages: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.stage_changed.connect(stages.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, stages, runner_class.calls


def _seat(project: Path, slots: list[AgentSlot]) -> None:
    SessionRegistry(project).save_lineup(Lineup.of(slots))


def test_it_runs_before_the_work_and_it_is_read_only(
    tmp_path: Path, monkeypatch
) -> None:
    """先說再做，而且說的時候不准動手。

    「叫它先別動手」如果只是一句規則，那是叮嚀——會漏，而且漏的時候沒有東西
    會變紅。所以這一步是**一次唯讀的派工**：它連權限都沒有。
    """
    project = _project(tmp_path)
    _seat(project, TWO_SEATS)

    completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result(MINE),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert "實作正在說它打算怎麼做" in stages

    first = calls[1]
    assert "你是這件事的執行者。動手之前" in (first["input_text"] or "")
    assert "--permission-mode" in first["arguments"]
    assert "plan" in first["arguments"], (
        "「先說再做」那一步拿到了寫入權限——它可以直接把東西做出來，"
        "然後那條定案就只剩一句話"
    )


def test_what_it_said_reaches_the_next_dispatch_under_its_own_heading(
    tmp_path: Path, monkeypatch
) -> None:
    """它自己說的做法要送到手上，而且**不能掛在「規劃者提出的」底下**。

    兩格陣容裡根本沒有規劃者。掛錯標題的話，它會照著一句不存在的話去理解自己
    拿到的東西——而畫面上完全看不出來。
    """
    project = _project(tmp_path)
    _seat(project, TWO_SEATS)

    _completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result(MINE),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )
    assert failed == [], failed

    to_worker = calls[2]["input_text"] or ""
    assert MINE in to_worker, "它自己說的做法沒有送到下一次派工手上"
    assert "你剛才自己說的做法" in to_worker
    assert "規劃者提出的做法" not in to_worker, (
        "這條線上沒有規劃者，提示詞卻說有"
    )


def test_resuming_does_not_ask_it_to_say_it_again(
    tmp_path: Path, monkeypatch
) -> None:
    """同一次執行，這一步只跑一次。

    重問會再等一輪，而且很可能拿到一份不一樣的做法——它接下來做的東西就跟
    它前面說的對不起來。
    """
    project = _project(tmp_path)
    _seat(project, TWO_SEATS)

    first, failed, _stages, _calls = _run(
        project,
        monkeypatch,
        [cli_result(MINE), cli_result("", success=False)],
    )
    assert failed == [], failed

    _second, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
        run_id=first[0]["run_id"],
    )

    assert failed == [], failed
    said = [
        c
        for c in calls
        if "你是這件事的執行者。動手之前" in (c["input_text"] or "")
    ]
    assert not said, "續跑時又問了一次「你打算怎麼做」"
    # 續跑的時候需求確認與「先交做法」都沿用，所以第一次派工就是實作。
    assert MINE in (calls[0]["input_text"] or ""), (
        "沿用了，但那份做法沒有被送到派工手上"
    )


def test_a_planner_does_not_replace_this_step(
    tmp_path: Path, monkeypatch
) -> None:
    """有規劃者也照跑。

    規劃者的做法是**別人**的理解；這一步是它自己用自己的話重講一遍。少了它，
    「沒對齊」這件事要等到東西做出來才會被發現。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("規劃", "codex", duty=DUTY_PLAN),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複查", "codex", duty=DUTY_REVIEW),
        ],
    )

    _completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("規劃者的做法。"),
            cli_result(MINE),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    to_worker = calls[3]["input_text"] or ""
    assert "規劃者提出的做法" in to_worker
    assert "你剛才自己說的做法" in to_worker, (
        "有規劃者就不問它自己怎麼想了——那兩份是不同的東西"
    )
