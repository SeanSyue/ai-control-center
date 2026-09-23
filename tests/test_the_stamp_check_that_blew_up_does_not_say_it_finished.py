"""拍板檢查自己爆掉的那一次，畫面不准說「工作流程已完成」。

2026-08-24 掃出來的 FG04／FG30。當時的形狀是：

    try:
        return self.chain().readiness(...)
    except Exception:
        return None            # ← 呼叫端把 None 當成「不用擋」

於是**同一支函式**兩種情況、兩種畫面：

| 發生什麼 | 使用者看到 |
|---|---|
| 少一個章 | `NOT_DECIDABLE`「還缺：【某某】的驗證章」 |
| 拍板檢查丟例外 | 薄荷色的**「工作流程已完成」**，`review_status=PASS` |

第二列沒有任何一個字說過「這一輪的章我沒算出來」。而老闆下一次要做的，
是親手從頭走一遍五節點——**那一格綠燈會讓整趟測試白跑**。

## 這裡守的是「講出來」，不是「不要爆炸」

例外照舊被吞掉：記帳不准變成一種新的失敗方式。這幾條守的是吞掉之後
**說了什麼**——結局是 `NOT_DECIDABLE`（設計上就不是失敗），畫面那一句是
「跑完了，但拍板的判定自己算不出來」。

## 它擋不住什麼

* **擋不住 `readiness()` 算出一個錯的答案。**這裡的前提是它自己知道自己
  沒算成（丟了例外）。算成了但算錯，這幾條全綠。
* **擋不住 `RunStamper.readiness` 以外的路徑回 `None`。**
  `approval.is_undecidable` 裡那條 `verdict is None` 還是放行的——它被
  `test_accounting_that_cannot_compute_does_not_kill_the_run` 釘著，而那條
  編碼的是老闆的原則，改它要他點頭。

  **規則層那一條已經收掉了**（2026-08-25）：`ApprovalChain.readiness` 回
  `None` 的話，`RunStamper.readiness` 把它收成 `UNCOMPUTABLE`，走不到那條
  放行——見 `test_a_blank_from_the_rules_layer_is_not_a_pass`。剩下擋不住的
  是**直接換掉 `RunStamper.readiness` 本身**的路徑（今天只有測試替身這樣做）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import outcomes  # noqa: E402
from app.core.lineup import (  # noqa: E402
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

HAPPY = [
    cli_result('{"result": "做完了"}'),
    cli_result("1 passed"),
    cli_result(PASS_REVIEW),
]

BOOM = "章的檔案讀到一半壞了"


def blow_up_the_stamp_check(monkeypatch) -> None:
    """讓拍板檢查在**規則層裡面**丟例外。

    **換掉 `ApprovalChain.readiness`，不是換掉 `RunStamper.readiness`**：
    要驗的正是「引擎那一層的 `try/except` 把例外吞掉之後說了什麼」。換掉
    引擎那一層就等於把被測的東西拿掉，然後量一個自己造出來的回傳值。
    """
    from app.services.approval_chain import ApprovalChain

    def boom(self, versions, required):  # noqa: ANN001, ARG001
        raise RuntimeError(BOOM)

    monkeypatch.setattr(ApprovalChain, "readiness", boom)


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / ".aicc").mkdir(parents=True)
    (project / ".aicc" / "task.md").write_text(
        "# 做一個錯題整理小工具\n", encoding="utf-8"
    )
    declare_verify_command(project)
    ProjectRegistry.initialize_project(project)
    return project


def _run(project: Path, monkeypatch):
    QApplication.instance() or QApplication([])
    runner_class = scripted_cli_runner_class(list(HAPPY))
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
    return completed, failed


def _blown_up_run(tmp_path: Path, monkeypatch) -> dict:
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(Lineup.of(WHOLE))
    blow_up_the_stamp_check(monkeypatch)
    completed, failed = _run(project, monkeypatch)
    assert failed == [], failed
    assert completed, "整輪執行連一份回報都沒有交出來"
    return completed[0]


def test_a_stamp_check_that_blew_up_is_not_called_finished(
    tmp_path: Path, monkeypatch
) -> None:
    """**這一條是這份檔案存在的理由。**

    改壞的樣子就是它原本的樣子：`except Exception: return None`。那時候
    這一輪的 `workflow_status` 是 `COMPLETED`、`review_status` 是 `PASS`，
    首頁泳道是薄荷色的——而拍板的帳一次都沒算成。
    """
    result = _blown_up_run(tmp_path, monkeypatch)

    assert result["workflow_status"] != "COMPLETED", (
        "拍板檢查整個沒跑成，畫面照樣說「工作流程已完成」"
    )
    assert result["workflow_status"] == "NOT_DECIDABLE"


def test_it_is_not_reported_as_a_failure_either(
    tmp_path: Path, monkeypatch
) -> None:
    """也不准畫成紅的。

    測試真的過了、複查真的同意了——那些事實不因為記帳爆掉而改變。講成失敗，
    使用者會去修一個沒壞的東西，而那個紅燈跟一次真的失敗長得一模一樣。
    """
    result = _blown_up_run(tmp_path, monkeypatch)

    assert result["review_status"] == "PASS"
    assert result["tests_success"] is True
    outcome = outcomes.describe(result)
    assert outcome.group == outcomes.GROUP_RERUN, (
        f"記帳爆掉被分到「{outcome.group_label}」那一組"
    )


def test_the_screen_says_the_judgement_itself_could_not_be_computed(
    tmp_path: Path, monkeypatch
) -> None:
    """畫面上那一句要講「判定自己算不出來」，不是講「完成」也不是留白。

    留白的話，使用者看到的是一列沒有說明的「還不能拍板」——他會去補章，
    而缺的可能是零個章。
    """
    result = _blown_up_run(tmp_path, monkeypatch)
    outcome = outcomes.describe(result)

    assert "算不出來" in outcome.stage, (
        f"畫面那一句沒說算不出來：{outcome.stage!r}"
    )
    assert "完成" not in outcome.stage
    assert outcome.style != "done"


def test_the_ending_has_a_row_in_the_outcome_table(
    tmp_path: Path, monkeypatch
) -> None:
    """這種收場要查得到「畫面該說什麼」。

    `app/core/outcomes.py` 沒有預設列——查不到就丟 `UnknownOutcome`，而
    落地的那份紀錄裡 `outcome_group` 會是空字串。**空的分組在首頁上是
    一格沒有顏色、沒有標籤的東西**，比講錯還難查。

    （這一條同時是那個「借 `READY` 那一列」的租約：
    `run_stamps.UNCOMPUTABLE_SCREEN_KEY` 一改成表上沒有的字，這裡就紅。）
    """
    result = _blown_up_run(tmp_path, monkeypatch)

    assert result.get("outcome_group"), (
        f"{result['decision_verdict']!r} 這個判定在 outcomes.py 裡查不到"
    )


def test_it_does_not_tell_him_to_go_find_a_missing_stamp(
    tmp_path: Path, monkeypatch
) -> None:
    """不准接「少一個章不是卡在那一關」那句尾巴。

    那句話是講給「章真的少了一個」聽的。記帳自己爆掉的時候接上去，等於叫
    使用者去找一個沒有人量過的缺口——**缺的可能是零個**，他會找到天亮。
    """
    result = _blown_up_run(tmp_path, monkeypatch)

    assert "少一個章" not in result["test_output"], (
        f"叫他去補一個沒有人量過的章：{result['test_output']!r}"
    )
    assert "算不出來" in result["test_output"]


def test_a_blank_from_the_rules_layer_is_not_a_pass(
    tmp_path: Path, monkeypatch
) -> None:
    """規則層回 `None` 的那一次，也不准變成「工作流程已完成」。

    **今天沒有人回得出 `None`**——`ApprovalChain.readiness` 每一條路都回一個
    `Readiness`。這一條守的是明天：`approval.is_undecidable` 裡 `verdict is
    None` 那條放行還在（拿掉它要動老闆定的那條測試，待他點頭），所以規則層
    只要新長出一條回 `None` 的路，那個 `None` 會一路走到綠燈，而**上面那五條
    一條都不會紅**——它們全部從「丟了例外」進去。

    收在 `RunStamper.readiness` 而不是收在 `is_undecidable`：那條放行是老闆
    的原則釘著的，這裡不動它，只是讓沒有人走得到。
    """
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(Lineup.of(WHOLE))

    from app.services.approval_chain import ApprovalChain

    monkeypatch.setattr(
        ApprovalChain, "readiness", lambda self, versions, required: None
    )
    completed, failed = _run(project, monkeypatch)
    assert failed == [], failed
    assert completed, "整輪執行連一份回報都沒有交出來"
    result = completed[0]

    assert result["workflow_status"] != "COMPLETED", (
        "規則層一個判定都沒算出來，畫面照樣說「工作流程已完成」"
    )
    assert result["workflow_status"] == "NOT_DECIDABLE"
    assert "算不出來" in outcomes.describe(result).stage


def test_the_exception_is_still_findable_afterwards(
    tmp_path: Path, monkeypatch
) -> None:
    """吞掉的例外要在時間軸上留下痕跡。

    畫面那一句對使用者夠了，對來修的人不夠：少了這一行，事後回頭只看得到
    一個沒有來由的 `NOT_DECIDABLE`，而「它為什麼爆掉」還是查不出來。
    """
    project = _project(tmp_path)
    SessionRegistry(project).save_lineup(Lineup.of(WHOLE))
    blow_up_the_stamp_check(monkeypatch)
    completed, failed = _run(project, monkeypatch)
    assert failed == [], failed

    line = (project / ".aicc" / "timeline" / "timeline.jsonl").read_text(
        encoding="utf-8"
    )
    events = [json.loads(row) for row in line.splitlines() if row.strip()]
    noted = [
        e for e in events if e["event_type"] == "stamp_readiness_uncomputable"
    ]
    assert noted, "拍板檢查爆掉了，而時間軸上一個字都沒有"
    assert BOOM in noted[0]["content"], (
        f"有記一筆，但沒記是哪一個例外：{noted[0]['content']!r}"
    )
    assert noted[0]["run_id"] == completed[0]["run_id"]
