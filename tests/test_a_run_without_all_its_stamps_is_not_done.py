"""章齊了才算完成。少一個，這一輪就還不能拍板。

老闆 2026-08-22 拍板讓章**有牙齒**。在這之前它只記錄：鏈子建得起來、算得出
「能不能拍板」，而執行的成敗完全不受它影響。那一版是刻意的——先讓它在旁邊
跑一輪，看它算出來的東西對不對——而那一輪跑完了。

定案原文：

> 最終決策成立的條件，是一路上每個 node 都對**同一版內容**有章。
> **少一個章不是卡在那一關，是整件事還不能拍板。**

所以這裡的結局是 `NOT_DECIDABLE`，**不是失敗**：測試過了、複查也同意了，
只是這一版還缺一個能回溯的憑據。

- 講成失敗 → 使用者會去修一個沒壞的東西。
- 講成完成 → 那份紀錄事後回溯不出來，而那正是整套章存在的理由。
"""
from __future__ import annotations

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
from app.core.workflow_worker import WorkflowWorker  # noqa: E402
from app.services.ai_settings import AIExecutionSettings  # noqa: E402
from app.services.approval_chain import StampLog  # noqa: E402
from app.services.project_registry import ProjectRegistry  # noqa: E402
from app.services.session_registry import SessionRegistry  # noqa: E402
from tests.fakes import (  # noqa: E402
    calls_after_intake,
    cli_result,
    declare_verify_command,
    scripted_cli_runner_class,
)

PASS_REVIEW = (
    '{"status": "PASS", "summary": "看過了", "issues": [], '
    '"instructions": [], "question": ""}'
)

WHOLE = [
    AgentSlot("複查", "codex", duty=DUTY_REVIEW),
    AgentSlot("實作", "claude", duty=DUTY_WORK),
]


def missing_one(monkeypatch, who: str = "還沒做的那一關") -> None:
    """在「這一次該有哪些章」上多要一個沒有人蓋得出來的。

    **這個情境是造出來的，而且那是好事**：引擎現在會把使用者排的每一格都跑
    過（每一格擬做法、每一格執行、每一格複查、核准），所以真的缺章的自然情境
    已經被一個一個修掉了。

    但「缺章要擋」這條規則不該等到下一個缺口出現才被驗到——那正是它存在的
    理由。所以這裡直接對「該有哪些章」下手。
    """
    # **換掉定義它的那個模組**，不是換掉某一個呼叫端。呼叫端會搬家（2026-08-23
    # 這一條就從 workflow_worker 搬到 steps/approval.py），而綁在呼叫端的替換
    # 在搬家之後會安靜地什麼都沒換到——測試照樣是綠的。
    from app.core import run_stamps
    from app.services.approval_chain import Required, StampKind, SUBJECT_WORK

    real = run_stamps.requires_for

    def demanding(lineup):  # noqa: ANN001
        return real(lineup) + (
            Required(who, SUBJECT_WORK, StampKind.REVIEW),
        )

    monkeypatch.setattr(run_stamps, "requires_for", demanding)


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
    SessionRegistry(project).save_lineup(Lineup.of(slots))


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


HAPPY = [
    cli_result('{"result": "做完了"}'),
    cli_result("1 passed"),
    cli_result(PASS_REVIEW),
]


def test_a_complete_chain_still_completes(tmp_path: Path, monkeypatch) -> None:
    """章齊的時候，這一關一個字都不說。

    **這一條比底下那幾條重要**：一道新的關卡如果會擋掉本來就該過的執行，
    那它造成的損害比它防的多。
    """
    project = _project(tmp_path)
    _seat(project, WHOLE)

    completed, failed, _calls = _run(project, monkeypatch, HAPPY)

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED"
    assert not completed[0].get("decision_problem")


def test_a_missing_stamp_stops_it_being_called_done(
    tmp_path: Path, monkeypatch
) -> None:
    """少一個章 = 還不能拍板，而且要說出缺的是誰的哪一個。

    只說「還不能拍板」的話，使用者不知道要去補什麼——而補不了的缺跟一個假的
    通過一樣壞。
    """
    project = _project(tmp_path)
    _seat(project, WHOLE)
    missing_one(monkeypatch)

    completed, failed, _calls = _run(project, monkeypatch, HAPPY)

    assert failed == [], failed
    result = completed[0]
    assert result["workflow_status"] == "NOT_DECIDABLE"
    assert "還缺" in result["decision_problem"]
    assert "還沒做的那一關" in result["decision_problem"], (
        f"沒有指名缺的是誰的章：{result['decision_problem']!r}"
    )


def test_it_is_not_reported_as_a_failure(tmp_path: Path, monkeypatch) -> None:
    """測試過了、複查也同意了——那些事實不准被這一關改寫。

    講成失敗，使用者會去修一個沒壞的東西；而畫面上那個紅燈跟一次真的失敗
    長得一模一樣。
    """
    project = _project(tmp_path)
    _seat(project, WHOLE)
    missing_one(monkeypatch)

    completed, failed, _calls = _run(project, monkeypatch, HAPPY)

    assert failed == [], failed
    result = completed[0]
    assert result["review_status"] == "PASS"
    assert result["tests_success"] is True
    assert result["workflow_status"] != "CODEX_ERROR"
    assert "測試也過了" in result["test_output"], (
        "停在這裡了，但沒有講清楚這不是壞掉"
    )


def test_the_stamps_are_still_on_disk_when_it_stops(
    tmp_path: Path, monkeypatch
) -> None:
    """擋下來不代表沒有紀錄。

    這一輪蓋到的每一個章都還在檔案裡——事後要回答「它到底走到哪一步」，
    靠的就是那份。
    """
    project = _project(tmp_path)
    _seat(project, WHOLE)
    missing_one(monkeypatch)

    completed, failed, _calls = _run(project, monkeypatch, HAPPY)
    assert failed == [], failed

    chain = StampLog(project).read(completed[0]["run_id"])
    assert len(chain.stamps) >= 3, (
        f"擋下來之後連紀錄都沒有了：{chain.stamps}"
    )


def test_accounting_that_cannot_compute_does_not_kill_the_run(
    tmp_path: Path, monkeypatch
) -> None:
    """算不出來就放行。

    章是記帳，而**記帳不准變成一種新的失敗方式**——這條原則從 `RunStamper`
    第一天就在：記帳存在的理由是讓失敗變便宜，把它變成一種新的失敗，比沒有
    它更糟。
    """
    project = _project(tmp_path)
    _seat(project, WHOLE)

    monkeypatch.setattr(
        "app.core.run_stamps.RunStamper.readiness",
        lambda *a, **k: None,
    )

    completed, failed, _calls = _run(project, monkeypatch, HAPPY)

    assert failed == [], failed
    assert completed[0]["workflow_status"] == "COMPLETED", (
        "記帳算不出來，就把一次真的執行擋下來了"
    )
