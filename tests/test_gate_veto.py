"""自動批准前的機器端檢查（ROADMAP P3）。

`GATE_FLOOR` 那四項本來只寫在送給 Codex 的 prompt 裡——那是**請它**升級，
靠它同意才有效。這裡是同一條底線在回程上被檢查一次：Codex 說可以自行處理，
但它要交給 Claude 的那句指示如果是 force-push、DROP TABLE 或帶著一把金鑰，
run 還是會停下來等人。

兩個方向都要測，而且第二個更容易寫錯：
* 該擋的有沒有擋（force-push、rm -rf、機密）
* **不該擋的有沒有放行**。一個一直在擋的閘門會被人閉著眼睛按過去，
  那比沒有閘門更糟，因為它看起來像有人在把關。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.orchestrator_parsing import gate_veto


# ---- 該擋的 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("先 git push --force origin main 再繼續", "強制推送"),
        ("執行 git reset --hard HEAD~3", "改寫 Git 歷史"),
        ("跑 rm -rf ./build 清乾淨", "破壞性刪除指令"),
        ("Remove-Item .\\dist -Recurse -Force", "破壞性刪除指令"),
        ("執行 DROP TABLE customers", "資料庫刪除"),
        ("DELETE FROM sessions 就好", "資料庫全表刪除"),
        ("用 icacls 把權限改掉", "修改權限"),
        ("reg add HKLM\\Software\\X /v Y /d Z", "修改系統設定"),
    ],
)
def test_a_dangerous_instruction_stops_the_auto_approval(
    answer: str, expected: str
) -> None:
    """Read from Codex's *answer*, not just the question.

    A gate that only inspected the question would miss 「問：要怎麼清掉舊分支？」
    answered with a force-push - and the answer is the instruction actually
    about to reach Claude.
    """
    assert gate_veto("要怎麼處理？", answer) == expected


def test_a_secret_in_the_exchange_stops_it_too() -> None:
    # Evidence rather than phrasing, so it needs the scanner rather than a
    # pattern - and it is the one category where 「Codex 說沒問題」 is worth
    # least, because the model has no way to know what the key unlocks.
    verdict = gate_veto(
        "要把這個設定寫進 .env 嗎？",
        "把 ANTHROPIC_API_KEY=sk-ant-0123456789abcdefghij 寫進去",
    )

    assert verdict == "疑似機密資訊"


def test_the_question_alone_is_enough() -> None:
    assert gate_veto("我可以直接 git push --force 嗎？") == "強制推送"


# ---- 不該擋的（這半邊更重要） ----------------------------------------------


@pytest.mark.parametrize(
    "question,answer",
    [
        ("要不要加一個測試？", "加，放在 tests/test_x.py"),
        ("這個函式要改名嗎？", "改成 extract_task_title"),
        # Talking *about* deletion is not deleting. A veto on the word 「刪除」
        # would fire on half of this project's own tasks.
        ("刪除功能的按鈕要放哪？", "放在卡片右下角，加一個確認對話框"),
        ("要不要把舊的匯出檔清一清？", "先留著，等使用者確認"),
        ("git commit 訊息要寫什麼？", "照 conventional commits 寫"),
        ("DELETE FROM 要加 WHERE 嗎？", "要，DELETE FROM sessions WHERE id = ?"),
    ],
)
def test_an_ordinary_exchange_is_let_through(question: str, answer: str) -> None:
    """A gate that always stops is a gate people click through.

    That is worse than no gate, because it looks like oversight while being
    none. These are all things a normal task says, and none of them is an
    instruction to do anything irreversible.
    """
    assert gate_veto(question, answer) == ""


def test_nothing_at_all_is_not_a_reason_to_stop() -> None:
    assert gate_veto("", "", "") == ""
    assert gate_veto("   ") == ""


# ---- 接到真正的決策路徑上 --------------------------------------------------


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text("# 任務\n做一件事\n", encoding="utf-8")
    return project


def resolve_with(tmp_path: Path, gate_json: str):
    """Drive the real gate path with a scripted Codex reply."""
    import json

    from app.adapters.cli_runner import CliResult
    from app.core.orchestrator import Orchestrator
    from app.services.ai_settings import AIExecutionSettings

    orchestrator = Orchestrator(
        make_project(tmp_path),
        run_id="run-1",
        settings=AIExecutionSettings(auto_session_bootstrap=False),
    )
    calls: list[list[str]] = []

    def fake_run(arguments, timeout_seconds=300, input_text=None):
        calls.append(list(arguments))
        command = arguments[0]
        if command == "codex":
            return CliResult(
                command=command, success=True, return_code=0,
                stdout=gate_json, stderr="",
            )
        return CliResult(
            command=command, success=True, return_code=0,
            stdout=json.dumps({"result": "done"}), stderr="",
        )

    orchestrator.runner.run = fake_run  # type: ignore[method-assign]
    outcome = orchestrator.resolve_claude_question_with_codex("要怎麼處理？")
    return orchestrator, outcome, calls


CONTINUE_WITH_FORCE_PUSH = (
    '{"decision": "APPROVED", "reason": "低風險", '
    '"answer": "執行 git push --force origin main", "question": ""}'
)
CONTINUE_HARMLESS = (
    '{"decision": "APPROVED", "reason": "低風險", '
    '"answer": "加一個測試就好", "question": ""}'
)


def test_a_vetoed_approval_never_reaches_claude(tmp_path: Path) -> None:
    """The point of doing this in code rather than in the prompt.

    Codex said APPROVED. Nothing about the prompt would have stopped this -
    the floor is only a sentence there.
    """
    orchestrator, outcome, calls = resolve_with(
        tmp_path, CONTINUE_WITH_FORCE_PUSH
    )

    assert outcome["action"] == "ASK_USER"
    assert "強制推送" in outcome["reason"]
    launched = [call[0] for call in calls]
    assert "claude" not in launched, "被否決之後不應該還去叫 Claude"


def test_the_veto_is_written_into_the_decision_log(tmp_path: Path) -> None:
    """An escalation nobody can see afterwards is not an audit trail.

    Recorded as ASK_USER with the reason naming which rule fired, so 「為什麼
    這件事來問我」 is answerable months later.
    """
    from app.services.long_task import DecisionLogStore

    orchestrator, _, _ = resolve_with(tmp_path, CONTINUE_WITH_FORCE_PUSH)

    entries = DecisionLogStore(orchestrator.project_path).list_entries()

    assert [e.action for e in entries] == ["ASK_USER"]
    assert "強制推送" in entries[0].reason
    # Not filed as a person having looked - nobody has yet.
    assert entries[0].answered_by == ""


def test_an_ordinary_approval_still_flows_through(tmp_path: Path) -> None:
    # The other half: the veto must not turn the gate into a wall.
    orchestrator, outcome, calls = resolve_with(tmp_path, CONTINUE_HARMLESS)

    assert outcome["action"] == "CONTINUE"
    assert "claude" in [call[0] for call in calls]


def test_the_veto_can_only_escalate_never_approve(tmp_path: Path) -> None:
    """A check that could *permit* something would be a hole, not a guard.

    Driven from the other direction: Codex says ASK_USER about something with
    no dangerous pattern in it at all, and the outcome is still ASK_USER.
    """
    _, outcome, calls = resolve_with(
        tmp_path,
        '{"decision": "ASK_USER", "reason": "需要使用者決定", '
        '"answer": "", "question": "要不要換一個做法？"}',
    )

    assert outcome["action"] == "ASK_USER"
    assert "claude" not in [call[0] for call in calls]
