r"""被跳過的那一步，事後查得出來。

## 為什麼有這一支

2026-08-27 那一輪替「決策關卡排不到複查」補了一筆時間軸紀錄
（`decision_gate_no_seat`），理由寫得很清楚：

> 沒有留痕的跳過，事後跟「有跑過而且通過」長得一模一樣。
> `tools/inspect_run.py` 讀的就是這條時間軸——他事後問
> 「那一題到底有沒有人先替我看過」，答案只能從這裡來。

**那句話的後半是假的。**實機跑過一輪有逐格紀錄的執行：
`decision_gate_no_seat` 真的寫進了 `.aicc/runs/<id>/timeline.jsonl`，
而 `tools/inspect_run.py` 印出一整份報告，**一個字都沒有提到它**——
那支工具的 `show_run()` 讀的是 `StepLog`（派出去的工），從來沒有
呼叫過 `load_events()`。

事件寫下來了、沒有人讀得到，跟沒有寫下來的差別只有一個：
**前者讓人以為問得到。**

## 改壞的樣子

`show_skipped_steps()` 不印（或沒有被 `show_run()` 叫到），
或者那張 `SKIPPED_STEPS` 表裡少掉其中一種。

## 使用者會看到什麼

他事後想確認「那一題到底有沒有人先替我看過」，跑
`tools/inspect_run.py <專案> <run_id>`，看到的是一份完整的報告：
每一格都是 ✓、收場正常、context 帶了多少字都印得清清楚楚——
**而決策關卡那一步從頭到尾沒有出現過**。他會讀成「跑過了，而且通過」。

同一件事的另一半是共同記憶：那八份沒有被更新，而報告裡沒有一行講這件事。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.core.lineup import (
    DUTY_APPROVE,
    DUTY_PLAN,
    DUTY_WORK,
    AgentSlot,
    Lineup,
)
from app.core.orchestrator import Orchestrator
from app.services.ai_settings import AIExecutionSettings
from app.services.project_registry import ProjectRegistry
from app.services.session_registry import SessionRegistry
from app.services.step_log import StepLog
from tests.fakes import cli_result, declare_verify_command, scripted_cli_runner_class
from tools import inspect_run

# 一格「複查」都沒有的陣容（2026-08-26 實機那五格的形狀）。
NOBODY_REVIEWS = [
    AgentSlot("發想", "codex", duty=DUTY_PLAN),
    AgentSlot("執行", "claude", duty=DUTY_WORK),
    AgentSlot("審閱", "codex", duty=DUTY_APPROVE),
]

SAYS = json.dumps(
    {
        "result": "做完了",
        "status": "PASS",
        "summary": "看過了",
        "issues": [],
        "instructions": [],
        "question": "",
    },
    ensure_ascii=False,
)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 做一件事\n", encoding="utf-8")
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    SessionRegistry(project).save_lineup(Lineup.of(NOBODY_REVIEWS))
    return project


def _a_run_that_skipped_the_gate(tmp_path, monkeypatch) -> Path:
    """一輪**有逐格紀錄**的執行，中間決策關卡因為排不到複查而被跳過。

    有逐格紀錄這件事是刻意的：那正是這支工具會印出一整份漂亮報告的情況，
    也就是使用者最容易把「沒印出來」讀成「沒發生問題」的那一種。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = _project(tmp_path)
    runner_class = scripted_cli_runner_class(
        [cli_result(SAYS) for _ in range(6)], auto=False
    )
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
    orchestrator = Orchestrator(
        project,
        run_id="a-run",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
        lineup=Lineup.of(NOBODY_REVIEWS),
    )
    orchestrator.run_claude("做一件事", role="執行")
    decision = orchestrator.resolve_claude_question_with_codex(
        "要不要把舊的設定檔一起刪掉？"
    )
    assert decision["action"] == "ASK_USER", decision
    assert StepLog(project).read("a-run"), "這一輪連逐格紀錄都沒有，案例不成立"
    return project


def test_the_skipped_decision_gate_is_in_the_report(
    tmp_path, monkeypatch, capsys
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子：報告裡一個字都沒有提到那一題被跳過。

    使用者會看到什麼：一份每一格都 ✓ 的報告，而他問的那一題到底有沒有人
    先看過，答案不在裡面——他會讀成「跑過了，而且通過」。
    """
    project = _a_run_that_skipped_the_gate(tmp_path, monkeypatch)

    inspect_run.show_run(project, "a-run")
    printed = capsys.readouterr().out

    assert "決策關卡" in printed, (
        "跑一輪跳過決策關卡的執行，報告裡沒有一個字提到它：\n" + printed
    )
    # 為什麼被跳過，要跟「被跳過」一起講——不然他會去改一個沒有壞的東西。
    assert "複查" in printed, printed
    # 代號留著，讓他（或下一個工具）回去 timeline.jsonl 對得起來。
    assert "decision_gate_no_seat" in printed, printed


def test_it_says_why_instead_of_only_that_it_happened(
    tmp_path, monkeypatch, capsys
) -> None:
    """**不准只印代號。**

    改壞的樣子：報告裡只有 `decision_gate_no_seat` 一行英文代號。

    使用者會看到什麼：一個他看不懂的字串。這個專案的使用者不是資訊背景，
    而「那一題沒有人先替你看過」跟 `decision_gate_no_seat` 之間，
    他要自己跨過去。
    """
    project = _a_run_that_skipped_the_gate(tmp_path, monkeypatch)

    inspect_run.show_run(project, "a-run")
    printed = capsys.readouterr().out

    assert "沒有人先替你看過" in printed, printed


def test_a_run_that_only_skipped_is_not_silent(
    tmp_path, monkeypatch, capsys
) -> None:
    """**逐格紀錄是空的那一種也要講。**

    改壞的樣子：`show_run()` 在「沒有逐格紀錄」那一行就早退，於是一輪只跳過、
    一次工都沒派出去的執行，這支工具對它永遠不出聲。

    使用者會看到什麼：「沒有逐格紀錄。可能是 Stage C 之前跑的，或者 run_id
    打錯了。」——把他送去懷疑自己是不是打錯了 run_id，而真正發生的事是
    那一題沒有人替他看過。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = _project(tmp_path)
    runner_class = scripted_cli_runner_class([], auto=False)
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
    orchestrator = Orchestrator(
        project,
        run_id="a-run",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
        lineup=Lineup.of(NOBODY_REVIEWS),
    )
    orchestrator.resolve_claude_question_with_codex("要不要刪掉？")
    assert StepLog(project).read("a-run") == [], "這一輪不該有逐格紀錄"

    inspect_run.show_run(project, "a-run")
    printed = capsys.readouterr().out

    assert "決策關卡" in printed, (
        "一輪只跳過的執行，這支工具完全沒出聲：\n" + printed
    )


def test_the_other_skipped_step_is_covered_too(
    tmp_path, monkeypatch, capsys
) -> None:
    """**修好一個，另外那個不准還在。**

    維護共同記憶排不到職責的時候留的是 `memory_update_no_seat`，跟決策關卡
    那一筆同一個形狀、同一個問題。

    改壞的樣子：只收決策關卡那一種。

    使用者會看到什麼：這一輪的定案沒有被寫進那八份共同記憶，而報告裡沒有
    一行講這件事——下一輪讀到的共同記憶少了這一輪，沒有人知道為什麼。
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    project = _project(tmp_path)
    runner_class = scripted_cli_runner_class([], auto=False)
    monkeypatch.setattr("app.core.orchestrator.CliRunner", runner_class)
    orchestrator = Orchestrator(
        project,
        run_id="a-run",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
        lineup=Lineup.of(NOBODY_REVIEWS),
    )
    done = cli_result("做完了")
    outcome = orchestrator.maintain_memory_after_pass(
        claude_result=done,
        codex_review=done,
        git_diff="diff",
        changed_files=["a.py"],
    )
    assert outcome["status"] == "NO_SEAT", outcome

    inspect_run.show_run(project, "a-run")
    printed = capsys.readouterr().out

    assert "共同記憶" in printed, printed
    assert "memory_update_no_seat" in printed, printed
