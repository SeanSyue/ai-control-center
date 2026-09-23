"""每一條意見都要標明它有多重要，而且只有重要的那些會擋。

老闆 2026-08-21 的原話：

> **如果這項決議沒有重要到去中斷的話　這樣不合邏輯**

只有一種異議的話，兩件事會同時發生，而且沒有辦法都避開：

- 任何一句「順帶一提」都會讓整件事重跑一輪；
- 為了避開那件事，審查方會開始把有問題的東西講成建議——而那時候審查等於
  沒有牙齒，**畫面上卻看不出差別**。

所以定案是兩級：

> 【異議分兩級】提出的人必須當場標明「**須處理**」（不解決最終決策不能成立）
> 或「**供參考**」（不影響最終決策）。
> 不變式：只有須處理級卡住才中斷；**供參考級卡住就留在紀錄裡，流程照走**。

這一份守四件事：

1. 只有須處理級會讓這一輪回頭重做。
2. **供參考級不會被丟掉**——不擋，但要留下來。不然「不擋」就變成「聽過就
   算了」，而那正是使用者事後最想知道的那一半。
3. **沒有標級的一律算須處理。**分不出來的時候停下來問人比默默放行便宜；
   反過來的預設值會讓「忘記標級」跟「這件事不重要」變成同一件事。
4. 舊格式（一串純文字）照樣讀得進來，而且全部當成須處理——跟它們當年的
   行為一致。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.lineup import (  # noqa: E402
    DUTY_REVIEW,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.orchestrator_parsing import (  # noqa: E402
    FOR_INFO,
    MUST_FIX,
    parse_review,
)
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    calls_after_intake,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

SEATS = [
    AgentSlot("複查", "codex", duty=DUTY_REVIEW),
    AgentSlot("實作", "claude", duty=DUTY_WORK),
]

NOTE = "命名可以再一致一點，但不影響行為"
BLOCKER = "邊界條件沒處理，輸入空字串會炸"


def review(status: str, issues: list) -> str:
    return json.dumps(
        {
            "status": status,
            "summary": "看過了",
            "issues": issues,
            "instructions": ["修一修"],
            "target": "實作",
            "question": "",
        },
        ensure_ascii=False,
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 做一個錯題整理小工具\n", encoding="utf-8"
    )
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(SEATS))
    return project


def _run(project: Path, monkeypatch, script):
    QApplication.instance() or QApplication([])
    runner_class = scripted_cli_runner_class(script)
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
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    QApplication.instance().processEvents()
    return completed, failed, calls_after_intake(runner_class.calls)


# --- 剖析 -------------------------------------------------------------------


def test_the_two_levels_are_read_apart() -> None:
    got = parse_review(
        review(
            "FIX",
            [
                {"level": MUST_FIX, "what": BLOCKER},
                {"level": FOR_INFO, "what": NOTE},
            ],
        )
    )

    assert got["blocking_issues"] == [BLOCKER]
    assert got["note_issues"] == [NOTE]
    # 純文字那一份照舊給既有的呼叫端用，兩級都在裡面。
    assert got["issues"] == [BLOCKER, NOTE]


def test_an_unlabelled_objection_counts_as_the_blocking_kind() -> None:
    """沒有標級的一律算須處理。

    反過來的預設值會讓「忘記標級」跟「這件事不重要」變成同一件事——而那正是
    這兩級要拆開的原因。分不出來的時候，停下來問人比默默放行便宜。
    """
    got = parse_review(review("FIX", [BLOCKER, {"what": NOTE}]))

    assert got["blocking_issues"] == [BLOCKER, NOTE]
    assert got["note_issues"] == []


# --- 行為 -------------------------------------------------------------------


def test_a_note_only_review_does_not_send_it_back(
    tmp_path: Path, monkeypatch
) -> None:
    """整份意見都是供參考的時候，流程照走——**一次都不重跑**。

    腳本只給剛好夠一輪的長度：多一次修正派工會用完腳本並丟 AssertionError，
    所以這個長度就是斷言。
    """
    project = _project(tmp_path)

    completed, failed, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("FIX", [{"level": FOR_INFO, "what": NOTE}])),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED", (
        "只有一句「順帶一提」，整件事卻重跑了一輪"
    )
    assert len(calls) == 3


def test_the_notes_are_kept_where_somebody_can_read_them(
    tmp_path: Path, monkeypatch
) -> None:
    """不擋不等於丟掉。

    「不擋」變成「聽過就算了」的話，使用者事後最想知道的那一半就沒了——
    而畫面上會顯示一次乾乾淨淨的通過。
    """
    project = _project(tmp_path)

    completed, failed, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("FIX", [{"level": FOR_INFO, "what": NOTE}])),
        ],
    )

    assert failed == [], failed
    kept = completed[0]["advisory_notes"]
    assert [note["text"] for note in kept] == [NOTE], (
        f"供參考的意見不見了、或者進去兩次：{kept!r}"
    )
    # **誰說的也要帶著。**只寫得出「有人說了一句話」的話，使用者接下來第一個
    # 問題就沒有答案，而那個答案在引擎手上。
    assert kept[0]["role"], f"意見沒有署名：{kept!r}"


def test_one_blocking_objection_still_sends_it_back(
    tmp_path: Path, monkeypatch
) -> None:
    """有一條須處理級的，就照舊退回去重做——**旁邊有幾條供參考的都一樣**。

    這一條守的是反方向：兩級不是用來讓事情比較容易通過的。
    """
    project = _project(tmp_path)

    completed, failed, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(
                review(
                    "FIX",
                    [
                        {"level": FOR_INFO, "what": NOTE},
                        {"level": MUST_FIX, "what": BLOCKER},
                    ],
                )
            ),
            cli_result('{"result": "改好了"}'),
            cli_result("1 passed"),
            cli_result(review("PASS", [])),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert len(calls) == 6, (
        "有一條須處理級的意見，卻沒有回頭重做："
        f"{[c['arguments'][0] for c in calls]}"
    )


def test_the_reviewer_is_told_to_grade_every_objection(
    tmp_path: Path, monkeypatch
) -> None:
    """規則要寫在提示詞裡，否則它不會標。

    而且要寫出**沒標會怎樣**——「請標明等級」是一句叮嚀，「沒標的一律當成
    須處理」才是一條它有理由遵守的規則。
    """
    project = _project(tmp_path)

    _completed, failed, calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("PASS", [])),
        ],
    )
    assert failed == [], failed

    to_reviewer = calls[2]["input_text"] or ""
    assert MUST_FIX in to_reviewer and FOR_INFO in to_reviewer
    assert "沒有標級的意見會被當成須處理" in to_reviewer
    assert "只有供參考等級的意見時，請回傳 PASS" in to_reviewer, (
        "沒有告訴它「只有建議就回 PASS」——它會為了讓意見被看見而回 FIX，"
        "而那會讓整件事為了一句順帶一提重跑一輪"
    )


def test_the_notes_survive_a_run_that_cannot_be_decided(
    tmp_path: Path, monkeypatch
) -> None:
    """章不齊的那一條路以前直接回，意見整批掉光。

    而那正是使用者最需要看到的組合：**東西做完了、有人有話說、而且還不能
    拍板**。三件事一起發生的時候，掉的偏偏是唯一一件他能立刻處理的。
    """
    from app.services.approval_chain import Readiness, Verdict

    monkeypatch.setattr(
        "app.core.run_stamps.RunStamper.readiness",
        lambda self, deliverable, requires, criteria="": Readiness(
            verdict=Verdict.MISSING
        ),
    )
    project = _project(tmp_path)

    completed, failed, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("FIX", [{"level": FOR_INFO, "what": NOTE}])),
        ],
    )

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "NOT_DECIDABLE"
    kept = completed[0].get("advisory_notes")
    assert kept and [note["text"] for note in kept] == [NOTE], (
        f"章不齊的時候意見掉光了：{kept!r}"
    )


def test_a_run_that_cannot_be_decided_says_which_verdict(
    tmp_path: Path, monkeypatch
) -> None:
    """`NOT_DECIDABLE` 不是一種收場而是六種，判定名稱要帶出去。

    少了它，「缺兩個章」（再跑一次就好）跟「有檢查是紅的」（要先去修別的
    地方）在畫面上會是同一格——而那是叫人去按一顆一定會失敗的按鈕。
    """
    from app.core import outcomes
    from app.services.approval_chain import Readiness, Verdict

    monkeypatch.setattr(
        "app.core.run_stamps.RunStamper.readiness",
        lambda self, deliverable, requires, criteria="": Readiness(
            verdict=Verdict.FAILED
        ),
    )
    project = _project(tmp_path)

    completed, failed, _calls = _run(
        project,
        monkeypatch,
        [
            cli_result('{"result": "做完了"}'),
            cli_result("1 passed"),
            cli_result(review("PASS", [])),
        ],
    )

    assert failed == [], failed
    assert completed[0]["decision_verdict"] == "FAILED"
    assert outcomes.describe(completed[0]).group == outcomes.GROUP_FIX
