"""陣容裡每一個「擬做法」的節點都要被派工，而且看得到前面的產出。

2026-08-22 之前，引擎只跑**一個**規劃者（`Orchestrator.has_planner`）。使用者
排的五節點流程前兩格都是擬做法（發想、規劃），**第二格從來不會被派工**——
而執行紀錄會回報一切正常。他拿到的是一個跟他編的不一樣的流程，畫面上沒有
任何一個字說了這件事。

這一份守兩件事：

1. 每一個 plan 節點都真的被呼叫到。
2. 後面的節點**看得到前面那一個寫了什麼**。第二件才是重點——少了它，
   五個節點只是五次各自為政的呼叫，不是一條線。
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
    calls_after_intake,
    cli_result,
    declare_verify_command,
    setup_with,
    scripted_cli_runner_class,
)

PASS_REVIEW = (
    '{"status": "PASS", "summary": "ok", "issues": [], '
    '"instructions": [], "question": ""}'
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 做一個錯題整理小工具\n", encoding="utf-8"
    )
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    return project


def _seat(project: Path, slots: list[AgentSlot]) -> None:
    registry = SessionRegistry(project)
    registry.save_lineup(Lineup.of(slots))


def _run(project: Path, monkeypatch, script, intake=None):
    QApplication.instance() or QApplication([])
    # 前置（需求確認、先交做法）由假 CLI 照內容自己回答，不佔腳本的位子。
    # `intake` 是要在那份驗收標準裡塞哨兵的時候用的。
    runner_class = scripted_cli_runner_class(
        script,
        auto=setup_with(intake=intake) if intake else True,
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
    )
    completed: list = []
    failed: list = []
    stages: list = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.stage_changed.connect(stages.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, stages, calls_after_intake(runner_class.calls)


def test_two_planning_nodes_are_both_dispatched(tmp_path: Path, monkeypatch) -> None:
    """兩格擬做法就要派兩次工。

    腳本刻意只給剛好夠的回應：第二格沒被派到的話，最後一個回應會沒人用，
    而多派一次則會用完腳本並丟出 AssertionError。**兩個方向都會紅。**

    2026-08-22 起第一格做的是**需求確認**而不是擬做法——它是陣容裡第一個不
    做事的節點，所以由它跟使用者把需求問清楚，問完的那份東西就是驗收標準。
    它後面還有一格在擬做法，所以它不用自己再擬一次（`calls_after_intake` 把
    那一次派工從清單前面拿掉了，所以底下數的是它後面那幾次）。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("發想", "codex", duty=DUTY_PLAN),
            AgentSlot("規劃", "claude", duty=DUTY_PLAN),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複查", "codex", duty=DUTY_REVIEW),
        ],
    )

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("規劃的產出：照功能切三塊。"),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    # 需求確認（發想那一格，已被拿掉）之後還有四次：一格擬做法、一格實作、
    # 一次驗證、一格複查。
    assert len(calls) == 4, [c["arguments"][0] for c in calls]
    assert calls[0]["arguments"][0] == "claude"  # 規劃


def test_the_second_planning_node_can_see_what_the_first_wrote(
    tmp_path: Path, monkeypatch
) -> None:
    """**這一條才是重點。**

    每一格都被呼叫到，不代表它們連成一條線。第二格看不到第一格寫了什麼的話，
    那只是兩次各自為政的呼叫——而畫面上長得一模一樣。

    用一個不可能被猜到的句子當哨兵：它必須出現在下游的提示詞裡。
    """
    sentinel = "哨兵-8f21c-使用者要的是實體題庫書的錯題整理"
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("發想", "codex", duty=DUTY_PLAN),
            AgentSlot("規劃", "claude", duty=DUTY_PLAN),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複查", "codex", duty=DUTY_REVIEW),
        ],
    )

    _completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result("規劃的產出。"),
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
        # 第一格的產出就是它問清楚之後訂下的驗收標準。哨兵放在那裡面。
        intake=f"需求定案：\n1. 完成狀態：{sentinel}",
    )

    assert failed == [], failed

    # 送給實作那一格的提示詞裡，要看得到發想寫的東西。
    to_the_worker = calls[1]["input_text"] or ""
    assert sentinel in to_the_worker, (
        "第一格的產出沒有傳到下游——五個節點只是五次各自為政的呼叫"
    )
    # 而且要標明是誰寫的，不然下游分不出哪一段是誰的意見。
    assert "發想" in to_the_worker


def test_the_classic_two_seat_project_still_makes_exactly_three_calls(
    tmp_path: Path, monkeypatch
) -> None:
    """沒有擬做法的節點時，一次呼叫都不准多。

    這一條守的是**沒有多付錢**：改成通用迴圈之後，最容易的退化是每次執行
    都多跑一格空的規劃。
    """
    project = _project(tmp_path)   # 預設就是 author + reviewer

    completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert len(calls) == 3, [c["arguments"][0] for c in calls]


# --- 駁回要指名，而且照指名退 -------------------------------------------------


def _fix_review(target: str = "") -> str:
    import json

    body = {
        "status": "FIX",
        "summary": "畫面那一段對不上規格。",
        "issues": ["按鈕的位置跟計畫書不同"],
        "instructions": ["照計畫書把按鈕移回去"],
    }
    if target:
        body["target"] = target
    return json.dumps(body, ensure_ascii=False)


def _two_workers(project: Path) -> None:
    _seat(
        project,
        [
            AgentSlot("ui設計", "claude", duty=DUTY_WORK),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複查", "codex", duty=DUTY_REVIEW),
        ],
    )


def test_a_rejection_goes_to_the_seat_it_names(tmp_path: Path, monkeypatch) -> None:
    """**不是退回上一關，是退給該負責的那一格。**

    定案【駁回直接送給該負責的人】：誰該負責由職責決定，不由位置決定
    ——畫面設計有問題就退給做畫面的那一格，不是退給它前面那一格。

    這裡兩格都會產出東西，複查指名第一格（ui設計）。退給第二格的話，
    改的人不是出問題的人。
    """
    project = _project(tmp_path)
    _two_workers(project)

    _completed, failed, stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "ui 做好了"}'),
            cli_result('{"result": "串接做好了"}'),
            cli_result("1 passed"),
            cli_result(_fix_review(target="ui設計")),
            cli_result('{"result": "改好了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert any("ui設計" in s and "修正" in s for s in stages), stages
    assert not any("實作" in s and "修正" in s for s in stages), stages


def test_an_unnamed_rejection_is_refused_when_there_is_a_choice(
    tmp_path: Path, monkeypatch
) -> None:
    """沒指名，而且有得挑 —— 引擎不准自己挑一個。

    挑了就變回「退回上一關」，而那正是定案打掉的東西。它想退給的那一格
    跟我們挑的那一格，很可能不是同一個人——**而畫面上看起來完全正常**。
    """
    project = _project(tmp_path)
    _two_workers(project)

    completed, failed, _stages, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "ui 做好了"}'),
            cli_result('{"result": "串接做好了"}'),
            cli_result("1 passed"),
            cli_result(_fix_review()),   # 沒有 target
        ],
    )

    assert failed == [], failed
    result = completed[0]
    assert result["workflow_status"] == "CODEX_INVALID_RESPONSE"
    assert "沒有指名" in result.get("verification_problem", "")


def test_one_worker_needs_no_name_because_there_is_nothing_to_guess(
    tmp_path: Path, monkeypatch
) -> None:
    """**反方向：只有一格的時候不用指名。**

    這條規則要防的是引擎在多個候選之間自己挑，不是要求複查說一句廢話。
    候選只有一個，沒有可猜的空間——強制指名只會讓每一個兩格專案的複查
    多寫一行，而寫錯還會被判成無效。
    """
    project = _project(tmp_path)   # 預設兩格：author（work）＋ reviewer

    completed, failed, _stages, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做好了"}'),
            cli_result("1 passed"),
            cli_result(_fix_review()),   # 沒有 target，但只有一格會做事
            cli_result('{"result": "改好了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"


def test_a_rejection_naming_a_seat_that_cannot_build_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    """退給一個不會產出東西的位子 —— 它修不了。

    複查把畫面問題退給「發想」那一格是合理的誤判（那一格確實談過畫面），
    但它的職責是擬做法，不會動檔案。默默改派給別人的話，改的人不是被
    指名的人，而紀錄上看起來是。
    """
    project = _project(tmp_path)
    _seat(
        project,
        [
            AgentSlot("發想", "codex", duty=DUTY_PLAN),
            AgentSlot("實作", "claude", duty=DUTY_WORK),
            AgentSlot("複查", "codex", duty=DUTY_REVIEW),
        ],
    )

    completed, failed, _stages, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result("發想的產出。"),
            cli_result('{"result": "做好了"}'),
            cli_result("1 passed"),
            cli_result(_fix_review(target="發想")),
        ],
    )

    assert failed == [], failed
    result = completed[0]
    assert result["workflow_status"] == "CODEX_INVALID_RESPONSE"
    assert "修不了" in result.get("verification_problem", "")


def test_the_reviewer_is_told_who_it_may_name(tmp_path: Path, monkeypatch) -> None:
    """要它指名，就得先讓它知道有誰。

    不給名冊而要求指名，它只能猜一個名字出來——**而猜錯的名字在畫面上
    跟猜對的長得一模一樣**，只會變成一則「陣容裡沒有這一格」。
    """
    project = _project(tmp_path)
    _two_workers(project)

    _completed, failed, _stages, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "ui 做好了"}'),
            cli_result('{"result": "串接做好了"}'),
            cli_result("1 passed"),
            cli_result(PASS_REVIEW),
        ],
    )

    assert failed == [], failed
    to_reviewer = calls[-1]["input_text"] or ""
    assert "ui設計" in to_reviewer and "實作" in to_reviewer, "名冊沒送給複查"
    assert "target" in to_reviewer
